import json
from pathlib import Path

from auto_spider.ai.collector_tools import WriteAction, WriteExecutor
from auto_spider.ai.workspace_access import WorkspaceAccess
from auto_spider.schemas import PlatformSpec


def test_invalid_spec_patch_returns_schema_errors_without_writing(tmp_path):
    fixture = Path(__file__).parent / "fixtures/platform_specs/kuaishou/platform_spec.json"
    spec = PlatformSpec.model_validate_json(fixture.read_text("utf-8")).model_dump(mode="json")
    relative = "tests/fixtures/kuaishou/platform-spec.patch.json"
    access = WorkspaceAccess(
        {
            "workspace": str(tmp_path),
            "evidence": str(tmp_path),
            "mode": "write",
            "allowed_files": [relative],
            "platform_spec": spec,
        }
    )
    executor = WriteExecutor(access)
    endpoint = dict(spec["endpoints"])
    endpoint["list"] = {**endpoint["list"], "response_format": "json_encrypted"}
    proposal = {
        "reason": "真实浏览器响应",
        "evidence_refs": [spec["endpoints"]["list"]["evidence_refs"][0]],
        "patch": {"endpoints": endpoint},
    }
    result = executor(WriteAction(path=relative, content=json.dumps(proposal)))
    assert result.is_error and not (tmp_path / relative).exists()
    endpoint["list"]["response_format"] = "json"
    result = executor(WriteAction(path=relative, content=json.dumps(proposal)))
    assert not result.is_error and (tmp_path / relative).is_file()
