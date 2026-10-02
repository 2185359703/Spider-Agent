import json
from copy import deepcopy

import pytest

pytest.importorskip("openhands")

from auto_spider.ai.collector_tools import WriteAction, WriteExecutor
from auto_spider.ai.workspace_access import WorkspaceAccess
from auto_spider.services.spec_builder import build_platform_spec


def test_spec_tool_rejects_bad_schema_and_missing_refs_before_file_write(tmp_path):
    workspace = tmp_path / "worktree"
    evidence = tmp_path / "evidence" / "task" / "run"
    capture = evidence / "agent-browser" / "execution" / "0017-response-body.json"
    capture.parent.mkdir(parents=True)
    capture.write_text('{"action":"response-body","output":"observed response"}')
    spec = build_platform_spec(
        task_id="task",
        platform_key="example",
        source_name="Example",
        entry_url="https://example.com/jobs",
        evidence_id="initial-evidence",
        observation={
            "list_found": True,
            "list_endpoint": "https://example.com/api/jobs",
            "spec_draft": {
                "endpoints": {
                    "list": {
                        "method": "GET",
                        "url_template": "https://example.com/api/jobs",
                        "response_format": "json",
                    }
                }
            },
            "detail_found": True,
            "pagination_verified": True,
            "list_count": 1,
            "internship_count": 1,
            "valid_record_count": 1,
        },
    ).model_dump(mode="json")
    relative = "tests/fixtures/example/platform-spec.patch.json"
    executor = WriteExecutor(
        WorkspaceAccess(
            {
                "workspace": str(workspace),
                "evidence": str(evidence),
                "evidence_prefix": "task/run",
                "platform_spec": spec,
                "mode": "write",
                "allowed_files": [relative],
            }
        )
    )
    endpoints = deepcopy(spec["endpoints"])
    endpoints["list"]["response_format"] = "json_encrypted"
    proposal = {
        "reason": "实测响应",
        "evidence_refs": ["task/run/0017-response-body.json"],
        "patch": {"endpoints": endpoints},
    }
    bad = executor(WriteAction(path=relative, content=json.dumps(proposal)))
    assert bad.is_error and "response_format" in bad.text
    assert not (workspace / relative).exists()
    endpoints["list"]["response_format"] = "json"
    missing = executor(WriteAction(path=relative, content=json.dumps(proposal)))
    assert missing.is_error and "agent-browser/execution/0017-response-body.json" in missing.text
    assert not (workspace / relative).exists()
    proposal["evidence_refs"] = ["task/run/agent-browser/execution/0017-response-body.json"]
    accepted = executor(WriteAction(path=relative, content=json.dumps(proposal)))
    assert not accepted.is_error
    assert json.loads((workspace / relative).read_text()) == proposal
