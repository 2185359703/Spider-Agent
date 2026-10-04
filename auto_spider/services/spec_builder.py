"""Build the trusted envelope around evidence-derived adapter instructions, never guessed paths."""

from copy import deepcopy
from typing import Any
from uuid import uuid4

from auto_spider.config import get_settings
from auto_spider.schemas import (
    AdapterFamily,
    AdapterSpec,
    EndpointsSpec,
    FieldsSpec,
    GenerationSpec,
    IdentitySpec,
    InternshipFilterSpec,
    PaginationSpec,
    PlatformSpec,
    SpecStatus,
)
from auto_spider.services.spec_normalizer import normalize_spec_draft

DISCOVERY_SECTIONS = {"adapter", "endpoints", "pagination", "fields", "filters"}


def build_platform_spec(
    *,
    task_id: str,
    platform_key: str,
    source_name: str,
    entry_url: str,
    observation: dict[str, Any],
    evidence_id: str,
) -> PlatformSpec:
    draft = deepcopy(observation.get("spec_draft") or {})
    # Repair only aliases that are proven by the captured list request before
    # any nested Pydantic model sees the draft.  This keeps trivial schema
    # mistakes out of the Agent Server/browser retry loop.
    draft, _ = normalize_spec_draft(draft, observation=observation)
    if not isinstance(draft, dict) or set(draft) - DISCOVERY_SECTIONS:
        raise ValueError("ANALYSIS_SPEC_SCOPE: 分析结果不能修改标识、生成边界或平台公共规范")
    endpoints = EndpointsSpec.model_validate(draft.get("endpoints", {}))
    fields = FieldsSpec.model_validate(draft.get("fields", {}))
    pagination = PaginationSpec.model_validate(draft.get("pagination", {}))
    adapter = AdapterSpec.model_validate(draft.get("adapter", {"family": AdapterFamily.UNKNOWN}))
    filters = draft.get("filters") or {
        "internship": InternshipFilterSpec(
            inferred=True, inference_reason="尚未确认本站的实习筛选证据"
        ).model_dump(mode="json")
    }
    generation = GenerationSpec(
        baseline_ref=get_settings().aicoding_baseline_ref,
        allowed_files=[
            f"collectors/{platform_key}.py",
            f"config/platforms/{platform_key}.toml",
            f"tests/test_{platform_key}.py",
            f"tests/fixtures/{platform_key}/*",
        ],
        test_commands=[
            f"python -m compileall collectors/{platform_key}.py",
            f"pytest tests/test_{platform_key}.py",
            "ruff check --isolated --select E,F,I,UP,B --target-version py312 "
            f"--line-length 100 collectors/{platform_key}.py tests/test_{platform_key}.py",
        ],
        simulated=False,
    )
    spec = PlatformSpec(
        spec_id=f"spec-{uuid4().hex}",
        spec_revision=1,
        task_id=task_id,
        platform_key=platform_key,
        source_name=source_name,
        identity=IdentitySpec(entry_url=entry_url, normalized_url=entry_url),
        adapter=adapter,
        endpoints=endpoints,
        fields=fields,
        pagination=pagination,
        filters=filters,
        generation=generation,
        status=SpecStatus.EVIDENCE_REVIEW,
    )
    # Full bodies may be provided in the list response; don't invent a separate detail API.
    spec.validation.detail_required = observation.get("detail_found") is True
    spec.validation.pagination_required = observation.get("pagination_required", True)
    # Keep the semantic evidence categories (entry/list/detail/pagination/
    # internship) alongside the concrete capture references.  Replacing the
    # defaults with a single manifest path made the UI unable to explain which
    # required proof was missing.
    spec.evidence.required_refs = list(
        dict.fromkeys(
            [
                *spec.evidence.required_refs,
                evidence_id,
                *(observation.get("evidence_refs") or []),
            ]
        )
    )
    blocking_errors = spec.candidate_blocking_errors()
    if blocking_errors:
        spec.status = SpecStatus.NEEDS_REVIEW
    else:
        # Schema, evidence references and deterministic blockers all pass at
        # this point; code generation can bind to a VALIDATED revision.
        spec.status = SpecStatus.VALIDATED
    spec.confidence_summary = spec.confidence_counts()
    return spec
