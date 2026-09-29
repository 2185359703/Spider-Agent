from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from auto_spider.ai.openhands_gateway import OpenHandsGateway
from auto_spider.config import get_settings
from auto_spider.git.policy import validate_changed_files
from auto_spider.git.worktree import WorktreeManager
from auto_spider.validators.candidate import validate_candidate


class OpenHandsRepairGateway:
    """Repair a candidate commit in a fresh worktree and create a new candidate."""

    def __init__(
        self,
        *,
        worktrees: WorktreeManager | None = None,
        gateway: OpenHandsGateway | None = None,
    ) -> None:
        self.worktrees = worktrees or WorktreeManager()
        self.gateway = gateway or OpenHandsGateway()

    def repair(
        self,
        platform_key: str,
        *,
        task_id: str,
        run_id: str,
        base_ref: str,
        spec: dict[str, Any],
        failure_bundle: dict[str, Any],
    ) -> dict[str, Any]:
        context = self.worktrees.prepare(
            task_id,
            run_id,
            mutation_enabled=True,
            ref=base_ref,
        )
        prompt = self._prompt(platform_key, spec, failure_bundle)
        result = self.gateway.run(prompt, context.path)
        changed_files = OpenHandsRepairGateway._changed_files(context.path)
        invalid = validate_changed_files(changed_files, platform_key)
        if invalid:
            raise RuntimeError(f"CODE_SCOPE_VIOLATION: {invalid}")
        if not changed_files:
            raise RuntimeError("REPAIR_EMPTY: OpenHands Agent 未产生修复文件修改")
        validation = validate_candidate(context.path, platform_key, changed_files)
        if not validation.passed:
            raise RuntimeError(f"REPAIR_VALIDATION_FAILED: {validation.as_dict()}")
        commit_sha = self._commit(context.path, platform_key, changed_files)
        branch_name = f"ai/repair/{platform_key}/{task_id}"
        push_status = self._push_candidate(branch_name)
        return {
            "changed_files": changed_files,
            "commit_sha": commit_sha,
            "baseline_ref": base_ref,
            "branch_name": branch_name,
            "push_status": push_status,
            "agent_response": result.final_response,
            "validation": validation.as_dict(),
            "simulated": False,
        }

    @staticmethod
    def _prompt(
        platform_key: str,
        spec: dict[str, Any],
        failure_bundle: dict[str, Any],
    ) -> str:
        return (
            "你正在候选采集器的独立修复 worktree 中工作。\n"
            f"平台键: {platform_key}\n"
            f"PlatformSpec: {spec}\n"
            f"失败纠错包: {failure_bundle}\n"
            "只允许修改 PlatformSpec generation.allowed_files 中的文件；"
            "不得读取或写入生产凭证、Cookie、数据库或当前工作区；"
            "必须修复失败原因、补充回归测试，并保持统一 RawJobRecord 契约。"
        )

    @staticmethod
    def _changed_files(worktree: Path) -> list[str]:
        commands = (
            ["diff", "--name-only"],
            ["diff", "--cached", "--name-only"],
            ["ls-files", "--others", "--exclude-standard"],
        )
        changed: set[str] = set()
        for command in commands:
            result = subprocess.run(
                ["git", "-C", str(worktree), *command],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            changed.update(line.strip() for line in result.stdout.splitlines() if line.strip())
        return sorted(changed)

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
                f"fix(collectors): 修复 {platform_key} 采集器",
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

    def _push_candidate(self, branch_name: str) -> str:
        settings = get_settings()
        if not settings.aicoding_push_enabled:
            return "DISABLED"
        remote = subprocess.run(
            ["git", "-C", str(self.worktrees.repository.path), "remote", "get-url", "origin"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
        if remote.rstrip("/") != settings.aicoding_remote_url.rstrip("/"):
            raise RuntimeError("AICODING_REMOTE_MISMATCH")
        subprocess.run(
            [
                "git",
                "-C",
                str(self.worktrees.repository.path),
                "push",
                "origin",
                f"HEAD:refs/heads/{branch_name}",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return "PUSHED"
