import json

import pytest

from auto_spider.services.analysis_submission import AnalysisSubmission, submit_analysis


def payload(ref):
    return {
        "observation": {"observation_code": "NO_JOBS_OBSERVED", "list_found": True},
        "evidence_refs": [ref],
        "spec_draft": {
            "adapter": {"family": "custom_http"},
            "endpoints": {
                "list": {
                    "url_template": "https://example.com/api/jobs",
                    "items_selector": {
                        "source": "response_body",
                        "kind": "json_path",
                        "expression": "$.jobs",
                        "confidence": "high",
                        "evidence_refs": [ref],
                    },
                    "evidence_refs": [ref],
                }
            },
            "fields": {},
            "pagination": {},
            "filters": {},
        },
    }


def policy(tmp_path):
    root = tmp_path / "evidence"
    ref = "agent-browser/owned/0001-response-body.json"
    file = root / ref
    file.parent.mkdir(parents=True)
    file.write_text(json.dumps({"response": {"jobs": []}}), "utf-8")
    return {
        "workspace": str(tmp_path / "read-only-code"),
        "evidence": str(root),
        "evidence_prefix": "task/run",
        "mode": "read",
        "allowed_files": [],
        "analysis_output": "analysis-submissions/owned.json",
    }, ref


def test_analysis_tool_saves_typed_artifact_in_read_only_code_mode(tmp_path):
    access, ref = policy(tmp_path)
    result = submit_analysis(access, payload(ref))
    assert result["saved"] and result["evidence_ref"] == "task/run/analysis-submissions/owned.json"
    saved = json.loads((tmp_path / "evidence/analysis-submissions/owned.json").read_text("utf-8"))
    assert saved["evidence_refs"] == ["task/run/" + ref]
    assert (
        saved["spec_draft"]["endpoints"]["list"]["items_selector"]["evidence_refs"]
        == saved["evidence_refs"]
    )
    assert not (tmp_path / "read-only-code").exists()


def test_analysis_tool_rejects_guessed_evidence_and_schema_aliases(tmp_path):
    access, ref = policy(tmp_path)
    with pytest.raises(ValueError, match="NOT_FOUND"):
        submit_analysis(access, payload("0002-response-body.json"))
    with pytest.raises(ValueError, match="PATH"):
        submit_analysis(access, payload("../../secret.json"))
    wrong = payload(ref)
    wrong["spec_draft"]["endpoints"]["list"]["response_format"] = "json_encrypted"
    with pytest.raises(ValueError):
        AnalysisSubmission.model_validate(wrong)
    assert not (tmp_path / "evidence/analysis-submissions/owned.json").exists()
