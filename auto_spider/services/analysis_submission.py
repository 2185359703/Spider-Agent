"""Task-owned, typed analysis artifacts emitted through a dedicated Agent tool."""

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from auto_spider.ai.workspace_access import WorkspaceAccess
from auto_spider.schemas import AdapterSpec, EndpointsSpec, FieldsSpec, FiltersSpec, PaginationSpec
from auto_spider.services.browser_evidence import sanitize_text
from auto_spider.services.spec_normalizer import normalize_spec_draft


class DiscoveryObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation_code: Literal[
        "INTERNSHIPS_FOUND",
        "NO_INTERNSHIPS_OBSERVED",
        "NO_JOBS_OBSERVED",
        "NO_JOB_LIST_FOUND",
        "ACCESS_RESTRICTED",
        "INCONCLUSIVE",
    ]
    summary: str = Field(default="", max_length=4000)
    list_found: bool | None = None
    detail_found: bool | None = None
    pagination_verified: bool | None = None
    pagination_required: bool = True
    list_count: int | None = Field(default=None, ge=0)
    internship_count: int | None = Field(default=None, ge=0)
    valid_record_count: int | None = Field(default=None, ge=0)
    scope_complete: bool = False


class DiscoveryDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    adapter: AdapterSpec
    endpoints: EndpointsSpec
    fields: FieldsSpec
    pagination: PaginationSpec
    filters: FiltersSpec

    @model_validator(mode="before")
    @classmethod
    def normalize_evidence_backed_aliases(cls, value: Any) -> Any:
        if isinstance(value, dict):
            normalized, _ = normalize_spec_draft(value)
            return normalized
        return value


class AnalysisSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation: DiscoveryObservation
    spec_draft: DiscoveryDraft
    evidence_refs: list[str] = Field(min_length=1, max_length=100)


def submit_analysis(policy, data):
    if not policy.get("analysis_output"):
        raise ValueError("ANALYSIS_SUBMISSION_NOT_GRANTED")
    model = AnalysisSubmission.model_validate(data)
    payload = model.model_dump(mode="json")
    access = WorkspaceAccess(policy)
    aliases = {}

    def check_ref(ref):
        if not isinstance(ref, str):
            raise ValueError("ANALYSIS_EVIDENCE_REF_INVALID")
        parts = PurePosixPath(ref.replace("\\", "/")).parts
        if ref.startswith("/") or ".." in parts or ":" in ref:
            raise ValueError("ANALYSIS_EVIDENCE_PATH_DENIED")
        path = access.path(ref, area="evidence")
        if not path.is_file():
            # A bare filename is permitted only when it identifies exactly one
            # real capture in this execution's evidence root, never a nearby ID.
            candidates = [
                p
                for p in Path(policy["evidence"]).glob("**/agent-browser/*/*.json")
                if p.name == Path(ref).name and not p.is_symlink()
            ]
            if len(candidates) != 1:
                raise ValueError(f"ANALYSIS_EVIDENCE_NOT_FOUND: {ref}")
            path = access.path(
                candidates[0].relative_to(policy["evidence"]).as_posix(), area="evidence"
            )
        prefix = policy["evidence_prefix"]
        actual = f"{prefix}/{path.relative_to(Path(policy['evidence'])).as_posix()}"
        aliases[ref] = actual
        return actual

    payload["evidence_refs"] = list(
        dict.fromkeys(check_ref(ref) for ref in payload["evidence_refs"])
    )

    def walk(value):
        if isinstance(value, dict):
            if "evidence_refs" in value:
                value["evidence_refs"] = [
                    aliases.get(ref) or check_ref(ref) for ref in value["evidence_refs"]
                ]
                payload["evidence_refs"].extend(value["evidence_refs"])
            for key, child in value.items():
                if key != "evidence_refs":
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload["spec_draft"])
    payload["evidence_refs"] = list(dict.fromkeys(payload["evidence_refs"]))
    endpoint = payload["spec_draft"]["endpoints"].get("list")
    detail = payload["spec_draft"]["endpoints"].get("detail")
    payload.update(
        list_endpoint=endpoint["url_template"] if endpoint else None,
        detail_endpoint=detail["url_template"] if detail else None,
        list_method=endpoint["method"] if endpoint else "GET",
        list_query=endpoint["query"] if endpoint else {},
        list_body=endpoint["body"] if endpoint else {},
    )
    target = access.path(policy["analysis_output"], area="evidence")
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if sanitize_text(text) != text:
        raise ValueError("UNSANITIZED_ANALYSIS_DATA")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise ValueError("ANALYSIS_OUTPUT_PATH_DENIED")
    target.write_text(text, encoding="utf-8")
    return {
        "saved": True,
        "evidence_ref": f"{policy['evidence_prefix']}/{policy['analysis_output']}",
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "note": "分析结构校验通过并保存；后端继续校验字段证据和分页证明",
    }
