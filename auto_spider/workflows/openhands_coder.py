from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from auto_spider.ai.openhands_gateway import OpenHandsGateway
from auto_spider.git.policy import validate_changed_files
from auto_spider.git.worktree import WorktreeManager


class OpenHandsCodingGateway:
    """Generate and commit a candidate inside a baseline worktree."""

    def __init__(
        self,
        *,
        worktrees: WorktreeManager | None = None,
        gateway: OpenHandsGateway | None = None,
    ) -> None:
        self.worktrees = worktrees or WorktreeManager()
        self.gateway = gateway or OpenHandsGateway()

    def generate(
        self,
        platform_key: str,
        *,
        task_id: str,
        run_id: str,
        spec: dict[str, Any],
        evidence_refs: list[str],
    ) -> dict[str, Any]:
        context = self.worktrees.prepare(task_id, run_id, mutation_enabled=True)
        prompt = self._prompt(platform_key, spec, evidence_refs)
        result = self.gateway.run(prompt, context.path)
        changed_files = self._changed_files(context.path)
        invalid = validate_changed_files(changed_files, platform_key)
        if invalid:
            raise RuntimeError(f"CODE_SCOPE_VIOLATION: {invalid}")
        if not changed_files:
            raise RuntimeError("CODE_GENERATION_EMPTY: OpenHands Agent 未产生文件修改")
        commit_sha = self._commit(context.path, platform_key, changed_files)
        return {
            "changed_files": changed_files,
            "commit_message": f"feat(collectors): 接入 {platform_key} 招聘岗位采集",
            "simulated": False,
            "commit_sha": commit_sha,
            "branch_name": f"ai/onboarding/{platform_key}/{task_id}",
            "baseline_ref": context.baseline_ref,
            "agent_response": result.final_response,
            "validation": {
                "pytest_status": "NOT_RUN",
                "ruff_status": "NOT_RUN",
                "contract_status": "NOT_RUN",
                "business_status": "NOT_RUN",
            },
        }

    @staticmethod
    def _prompt(platform_key: str, spec: dict[str, Any], evidence_refs: list[str]) -> str:
        return (
            "你正在独立 worktree 中生成招聘采集器候选提交。\n"
            f"平台键: {platform_key}\n"
            f"PlatformSpec: {spec}\n"
            f"证据引用: {evidence_refs}\n"
            "只允许修改 PlatformSpec generation.allowed_files 中的文件；"
            "不得读取或写入生产凭证、Cookie、数据库或当前工作区；"
            "必须生成采集器、TOML、fixture 和测试，并报告实际运行的验证命令。"
        )

    @staticmethod
    def _changed_files(worktree: Path) -> list[str]:
        result = subprocess.run(
            ["git", "-C", str(worktree), "diff", "--name-only"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    @staticmethod
    def _commit(worktree: Path, platform_key: str, changed_files: list[str]) -> str:
        subprocess.run(
            ["git", "-C", str(worktree), "add", "--", *changed_files],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "-C", str(worktree), "diff", "--cached", "--check"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(worktree),
                "-c",
                "user.name=Auto Spider",
                "-c",
                "user.email=auto-spider@example.invalid",
                "commit",
                "-m",
                f"feat(collectors): 接入 {platform_key} 招聘岗位采集",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        result = subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return result.stdout.strip()
