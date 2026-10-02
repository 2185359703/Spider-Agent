"""Reject invalid agent Spec patches at the file-tool boundary with repairable feedback."""

from copy import deepcopy
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from auto_spider.schemas import PlatformSpec

PATCH_SECTIONS = {"endpoints", "fields", "pagination", "filters"}


def validate_patch_evidence(policy, proposal):
    """Check exact, task-visible references; suggestions never silently rewrite evidence."""
    from auto_spider.ai.workspace_access import WorkspaceAccess

    def existing(value):
        if isinstance(value, dict):
            result = set(value.get("evidence_refs", []))
            for child in value.values():
                result.update(existing(child))
            return result
        if isinstance(value, list):
            return set().union(*(existing(child) for child in value))
        return set()

    known = existing(policy.get("platform_spec", {}))
    known.update(policy.get("allowed_evidence_refs", []))
    access = WorkspaceAccess(policy)
    invalid = []
    for ref in proposal["evidence_refs"]:
        if ref in known:
            continue
        confirmed = False
        for area in ("evidence", "failure"):
            try:
                path = access.path(ref, area=area)
                if path.is_file() and path.name != "platform-spec-schema.json":
                    confirmed = True
            except (ValueError, KeyError, OSError):
                continue
        if not confirmed:
            invalid.append(ref)
    if not invalid:
        return
    candidates = []
    for name, prefix in (("evidence", "evidence_prefix"), ("failure_evidence", "failure_prefix")):
        root = Path(policy.get(name, "/nonexistent"))
        for ref in invalid:
            for path in root.rglob(Path(ref).name):
                if path.is_file() and not path.is_symlink() and len(candidates) < 10:
                    candidates.append(
                        f"{policy.get(prefix, '')}/{path.relative_to(root).as_posix()}"
                    )
    raise ValueError(
        f"SPEC_PATCH_EVIDENCE_NOT_FOUND: {invalid}. "
        f"已保存的同名证据完整路径: {sorted(set(candidates))}"
    )


def validate_spec_patch(base: dict[str, Any], proposal: Any) -> PlatformSpec:
    if not isinstance(proposal, dict) or set(proposal) - {"reason", "evidence_refs", "patch"}:
        raise ValueError("SPEC_PATCH_FORMAT: 使用 reason、evidence_refs、patch 三个字段")
    if not isinstance(proposal.get("reason"), str) or not proposal["reason"].strip():
        raise ValueError("SPEC_PATCH_REASON_REQUIRED")
    refs = proposal.get("evidence_refs")
    if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) for ref in refs):
        raise ValueError("SPEC_PATCH_EVIDENCE_REQUIRED")
    patch = proposal.get("patch")
    if not isinstance(patch, dict) or not patch or set(patch) - PATCH_SECTIONS:
        raise ValueError("SPEC_PATCH_SCOPE: 只允许 endpoints、fields、pagination、filters")
    for name, value in patch.items():
        if not isinstance(value, dict):
            raise ValueError(f"SPEC_PATCH_SECTION: {name} 必须是完整对象")
        if name == "fields" and {"source_id", "title", "source_url"} - value.keys():
            raise ValueError("SPEC_PATCH_FIELDS: 完整提供 source_id、title、source_url 等字段")
    candidate = deepcopy(base)
    candidate.update(patch)
    candidate["spec_hash"] = None
    return PlatformSpec.model_validate(candidate)


def schema_feedback(error: Exception) -> str:
    import json

    errors = (
        error.errors(include_context=False, include_input=False)
        if isinstance(error, ValidationError)
        else str(error)
    )
    return (
        "SPEC_PATCH_INVALID: 文件未保存，请修正后再次写入。\n"
        + json.dumps(errors, ensure_ascii=False)
        + "\nresponse_format 使用 json/html/text；加密 JSON 仍使用 json，解码放入 decode 对象。"
        "选择器 source 使用 response_body/list_item/detail/related/url/response_header。"
        "分页 total_count_selector 使用完整 SelectorSpec 对象，终止规则使用 total_reached。"
    )
