from __future__ import annotations

from typing import Any
from uuid import uuid4

from auto_spider.config import get_settings
from auto_spider.schemas import (
    AdapterFamily,
    AdapterSpec,
    Confidence,
    EndpointSpec,
    EndpointsSpec,
    FieldSpec,
    FieldsSpec,
    GenerationSpec,
    IdentitySpec,
    InternshipFilterSpec,
    PaginationMode,
    PaginationSpec,
    PlatformSpec,
    SelectorKind,
    SelectorSource,
    SelectorSpec,
    SpecStatus,
)


def build_fake_platform_spec(
    *,
    task_id: str,
    platform_key: str,
    source_name: str,
    entry_url: str,
    observation: dict[str, Any],
    evidence_id: str,
) -> PlatformSpec:
    found = observation["list_found"] is True
    detail_found = observation["detail_found"] is True
    evidence_refs = [evidence_id] if found else []
    selector_confidence = Confidence.HIGH if found else Confidence.LOW
    list_endpoint = (
        EndpointSpec(
            method="GET",
            url_template=f"{entry_url}#list",
            response_format="json",
            items_selector=SelectorSpec(
                source=SelectorSource.RESPONSE_BODY,
                kind=SelectorKind.JSON_PATH,
                expression="$.data.items",
                multiple=True,
                transforms=["to_list"],
                confidence=selector_confidence,
                evidence_refs=evidence_refs,
            ),
            evidence_refs=evidence_refs,
            confidence=selector_confidence,
        )
        if found
        else None
    )
    detail_endpoint = (
        EndpointSpec(
            method="GET",
            url_template=f"{entry_url}#detail/{{source_id}}",
            response_format="json",
            evidence_refs=evidence_refs,
            confidence=selector_confidence,
        )
        if detail_found
        else None
    )

    def field_selector(
        expression: str,
        source: SelectorSource = SelectorSource.DETAIL,
    ) -> SelectorSpec:
        return SelectorSpec(
            source=source,
            kind=SelectorKind.JSON_PATH,
            expression=expression,
            transforms=["to_text", "normalize_text"],
            confidence=selector_confidence,
            evidence_refs=evidence_refs,
        )

    fields = FieldsSpec(
        source_id=FieldSpec(
            required=True,
            selectors=[
                SelectorSpec(
                    source=SelectorSource.LIST_ITEM,
                    kind=SelectorKind.JSON_PATH,
                    expression="$.id",
                    transforms=["to_string", "strip"],
                    confidence=selector_confidence,
                    evidence_refs=evidence_refs,
                )
            ]
            if found
            else [],
        ),
        title=FieldSpec(
            required=True,
            selectors=[field_selector("$.title")] if detail_found else [],
        ),
        source_url=FieldSpec(
            required=True,
            selectors=[field_selector("$.url")] if detail_found else [],
        ),
        location=FieldSpec(selectors=[field_selector("$.location")] if detail_found else []),
        description=FieldSpec(selectors=[field_selector("$.description")] if detail_found else []),
        requirements=FieldSpec(
            selectors=[field_selector("$.requirements")] if detail_found else []
        ),
        publish_time=FieldSpec(selectors=[field_selector("$.publishedAt")] if detail_found else []),
    )
    pagination_mode = PaginationMode.PAGE if found else PaginationMode.NONE
    pagination = PaginationSpec(
        mode=pagination_mode,
        page_param="page" if found else None,
        size_param="limit" if found else None,
        page_size=50,
        max_pages=50,
        termination=["empty_page", "repeated_page_fingerprint", "max_pages"] if found else [],
        evidence_refs=evidence_refs,
        confidence=selector_confidence,
    )
    filters = InternshipFilterSpec(
        evidence_refs=evidence_refs,
        confidence=selector_confidence,
        inferred=not found,
        inference_reason="离线 fake analyzer 未发现列表证据" if not found else None,
    )
    generation = GenerationSpec(
        baseline_ref=get_settings().collector_baseline_ref,
        allowed_files=[
            f"collectors/{platform_key}.py",
            f"config/platforms/{platform_key}.toml",
            f"tests/test_{platform_key}.py",
            f"tests/fixtures/{platform_key}/*",
        ],
        test_commands=[
            f"python -m compileall collectors/{platform_key}.py",
            f"pytest tests/test_{platform_key}.py",
            f"ruff check collectors/{platform_key}.py tests/test_{platform_key}.py",
        ],
        simulated=True,
    )
    spec = PlatformSpec(
        spec_id=f"spec-{uuid4().hex}",
        spec_revision=1,
        task_id=task_id,
        platform_key=platform_key,
        source_name=source_name,
        identity=IdentitySpec(
            entry_url=entry_url,
            normalized_url=entry_url,
            platform_id=None,
            entity_id=None,
        ),
        adapter=AdapterSpec(
            family=AdapterFamily.CUSTOM_HTTP,
            collector_template="custom_http",
        ),
        endpoints=EndpointsSpec(list=list_endpoint, detail=detail_endpoint),
        pagination=pagination,
        fields=fields,
        filters={"internship": filters},
        generation=generation,
        status=SpecStatus.DRAFT,
        confidence_summary={},
    )
    return spec.model_copy(update={"confidence_summary": spec.confidence_counts()})
