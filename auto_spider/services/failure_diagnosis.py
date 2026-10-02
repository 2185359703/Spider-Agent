from __future__ import annotations

import json


def diagnose_failure(bundle: dict) -> dict:
    """Only retry problems that have a concrete code/configuration repair path."""
    serialized = json.dumps(bundle, ensure_ascii=False).lower()
    if any(
        term in serialized
        for term in (
            "code_scope_violation",
            "path_escape",
            "unsanitized_sensitive_data",
        )
    ):
        return {"code_fixable": False, "category": "POLICY_VIOLATION"}
    issues = bundle.get("field_issues", [])
    if any(issue.get("code_fixable") is False for issue in issues) and not any(
        issue.get("code_fixable") is True for issue in issues
    ):
        return {"code_fixable": False, "category": "REQUIRES_INPUT"}
    validation = bundle.get("validation", {})
    # A static-code failure is independently actionable even if another probe
    # suffered a network error in the same round.
    if any(validation.get(f"{name}_status") == "FAIL" for name in ("compile", "ruff", "contract")):
        return {"code_fixable": True, "category": "CODE_OR_CONFIG"}
    if any(
        term in serialized
        for term in (
            "name resolution",
            "sslerror",
            "certificate verify",
            "connection reset",
            "connection refused",
            "connectionerror",
            "readtimeout",
            "connecttimeout",
            "access_restricted",
            "captcha",
            "tool_unavailable",
            "collection_worker_lost",
            "not installed",
        )
    ):
        return {"code_fixable": False, "category": "ENVIRONMENT_OR_ACCESS"}
    if "validation" in bundle and any(
        validation.get(f"{name}_status", "NOT_RUN") == "NOT_RUN"
        for name in ("compile", "pytest", "ruff", "contract", "business", "live")
    ):
        return {"code_fixable": False, "category": "TOOL_UNAVAILABLE"}
    if bundle.get("spec_errors") and all(
        validation.get(f"{name}_status") == "PASS"
        for name in ("compile", "pytest", "ruff", "contract", "business", "live")
    ):
        if bundle.get("artifact_manifest_ref"):
            return {"code_fixable": True, "category": "SPEC_ALIGNMENT"}
        return {"code_fixable": False, "category": "EVIDENCE_INSUFFICIENT"}
    return {"code_fixable": True, "category": "CODE_OR_CONFIG"}
