from __future__ import annotations

from typing import Any

from auto_spider.git.policy import validate_changed_files
from auto_spider.services.policies import classify_observation
from auto_spider.workflows.types import AnalysisResult


class FakeAnalyzer:
    """Test-only deterministic analyzer; never selected by production settings."""

    def inspect(
        self,
        entry_url: str,
        platform_key: str,
        task_id: str | None = None,
        run_id: str | None = None,
    ) -> AnalysisResult:
        observation = classify_observation(entry_url)
        found = observation["list_found"] is True
        evidence = [f"analysis/{platform_key}/network.json"]

        def selector(path, source="list_item"):
            return {
                "source": source,
                "kind": "json_path",
                "expression": path,
                "confidence": "high",
                "evidence_refs": evidence,
            }

        draft = {
            "adapter": {"family": "custom_http"},
            "endpoints": {
                "list": {
                    "url_template": f"https://fixtures.example.test/{platform_key}/api/list",
                    "items_selector": {**selector("$.items", "response_body"), "multiple": True},
                    "confidence": "high",
                    "evidence_refs": evidence,
                }
                if found
                else None,
                "detail": {
                    "url_template": f"https://fixtures.example.test/{platform_key}/api/jobs/{{{{source_id}}}}",
                    "evidence_refs": evidence,
                }
                if observation["detail_found"]
                else None,
            },
            "fields": {
                name: {
                    "required": name in {"source_id", "title", "source_url"},
                    "selectors": [selector("$." + name)] if found else [],
                }
                for name in (
                    "source_id",
                    "title",
                    "source_url",
                    "description",
                    "requirements",
                    "publish_time",
                )
            },
            "pagination": {
                "mode": "page",
                "page_param": "page",
                "size_param": "limit",
                "termination": ["empty_page", "max_pages"],
                "confidence": "high",
                "evidence_refs": evidence,
            }
            if found
            else {},
            "filters": {"internship": {"confidence": "high", "evidence_refs": evidence}},
        }
        return AnalysisResult(
            observation=observation,
            list_endpoint=(
                f"https://fixtures.example.test/{platform_key}/api/list" if found else None
            ),
            detail_endpoint=(
                f"https://fixtures.example.test/{platform_key}/api/jobs/{{source_id}}"
                if observation["detail_found"]
                else None
            ),
            evidence_refs=[
                f"analysis/{platform_key}/entry.json",
                f"analysis/{platform_key}/network.json",
            ],
            spec_draft=draft,
        )


class FakeCodingGateway:
    """Test-only candidate generator; production always uses OpenHands."""

    def generate(self, platform_key: str, **_kwargs: Any) -> dict[str, Any]:
        changed_files = [
            f"collectors/{platform_key}.py",
            f"config/platforms/{platform_key}.toml",
            f"tests/test_{platform_key}.py",
        ]
        return {
            "workspace": "test-workspace",
            "baseline_ref": "1" * 40,
            "changed_files": changed_files,
            "commit_message": f"feat(collectors): 接入 {platform_key} 招聘岗位采集",
            "simulated": False,
            "validation": {
                "compile_status": "PASS",
                "live_status": "PASS",
                "pytest_status": "PASS",
                "ruff_status": "PASS",
                "contract_status": "PASS",
                "business_status": "PASS",
            },
        }


class FakeRepairGateway:
    """Test-only repair gateway; production always uses OpenHands."""

    def repair(
        self, platform_key: str, issue_summary: str | None = None, **kwargs
    ) -> dict[str, Any]:
        changed_files = [f"collectors/{platform_key}.py", f"tests/test_{platform_key}.py"]
        return {
            "workspace": "test-workspace",
            "baseline_ref": kwargs.get("base_ref") or "1" * 40,
            "changed_files": changed_files,
            "diagnosis": {
                "code_fixable": True,
                "root_cause": issue_summary or "人工审查发现的可复现代码问题",
                "scope": changed_files,
            },
            "regression": {
                "pytest_status": "PASS",
                "ruff_status": "PASS",
                "contract_status": "PASS",
            },
            "simulated": False,
        }

    @staticmethod
    def validate(platform_key: str, changed_files: list[str]) -> list[str]:
        return validate_changed_files(changed_files, platform_key)


def fake_validation(generated, state):
    return {
        f"{name}_status": "PASS"
        for name in ("compile", "pytest", "ruff", "contract", "business", "live")
    }


def fake_publication(workspace, **kwargs):
    import hashlib

    key = kwargs["platform_key"]
    return {
        "commit_sha": hashlib.sha1(kwargs["run_id"].encode()).hexdigest(),
        "branch_name": f"codex/candidate/{key}/{kwargs['run_id']}",
        "baseline_ref": kwargs["baseline"],
        "changed_files": [f"collectors/{key}.py"],
    }
