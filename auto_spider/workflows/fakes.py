from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from auto_spider.git.policy import validate_changed_files
from auto_spider.services.policies import classify_observation


@dataclass(frozen=True)
class FakeAnalysis:
    observation: dict[str, Any]
    list_endpoint: str | None
    detail_endpoint: str | None
    evidence_refs: list[str]


class FakeAnalyzer:
    """Deterministic analyzer used for the first workflow milestone and tests."""

    def inspect(self, entry_url: str, platform_key: str) -> FakeAnalysis:
        observation = classify_observation(entry_url)
        found = observation["list_found"] is True
        return FakeAnalysis(
            observation=observation,
            list_endpoint=f"{entry_url}#list" if found else None,
            detail_endpoint=f"{entry_url}#detail" if observation["detail_found"] else None,
            evidence_refs=[
                f"analysis/{platform_key}/entry.json",
                f"analysis/{platform_key}/network.json",
            ],
        )


class FakeCodingGateway:
    def generate(self, platform_key: str) -> dict[str, Any]:
        changed_files = [
            f"collectors/{platform_key}.py",
            f"config/platforms/{platform_key}.toml",
            f"tests/test_{platform_key}.py",
        ]
        return {
            "changed_files": changed_files,
            "commit_message": f"feat(collectors): 接入 {platform_key} 招聘岗位采集",
            "simulated": True,
            "validation": {
                "pytest_status": "PASS",
                "ruff_status": "PASS",
                "contract_status": "PASS",
                "business_status": "PASS",
            },
        }


class FakeRepairGateway:
    def repair(self, platform_key: str, issue_summary: str | None) -> dict[str, Any]:
        changed_files = [f"collectors/{platform_key}.py", f"tests/test_{platform_key}.py"]
        return {
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
            "simulated": True,
        }

    @staticmethod
    def validate(platform_key: str, changed_files: list[str]) -> list[str]:
        return validate_changed_files(changed_files, platform_key)
