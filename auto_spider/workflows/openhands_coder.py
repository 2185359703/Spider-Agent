from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from auto_spider.ai.openhands_gateway import OpenHandsGateway
from auto_spider.ai.skill_loader import load_trusted_agent_skills
from auto_spider.config import get_settings
from auto_spider.git.policy import validate_changed_files
from auto_spider.git.worktree import WorktreeManager
from auto_spider.validators.candidate import validate_candidate


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
        expected_observation: str | None = None,
        event_callback: Callable[[object], None] | None = None,
    ) -> dict[str, Any]:
        context = self.worktrees.prepare(task_id, run_id, mutation_enabled=True)
        prompt = self._prompt(platform_key, spec, evidence_refs)
        result = self.gateway.run(prompt, context.path, event_callback=event_callback)
        changed_files = self._changed_files(context.path)
        invalid = validate_changed_files(changed_files, platform_key)
        if invalid:
            raise RuntimeError(f"CODE_SCOPE_VIOLATION: {invalid}")
        if not changed_files:
            raise RuntimeError("CODE_GENERATION_EMPTY: OpenHands Agent 未产生文件修改")
        validation = validate_candidate(
            context.path,
            platform_key,
            changed_files,
            live_url=str((spec.get("identity") or {}).get("entry_url") or "") or None,
            expected_observation=expected_observation,
        )
        if not validation.passed:
            raise RuntimeError(f"VALIDATION_FAILED: {validation.as_dict()}")
        commit_sha = self._commit(context.path, platform_key, changed_files)
        branch_name = f"ai/onboarding/{platform_key}/{task_id}"
        push_status = self._push_candidate(branch_name, commit_sha)
        return {
            "changed_files": changed_files,
            "commit_message": f"feat(collectors): 接入 {platform_key} 招聘岗位采集",
            "simulated": False,
            "commit_sha": commit_sha,
            "branch_name": branch_name,
            "push_status": push_status,
            "baseline_ref": context.baseline_ref,
            "agent_response": result.final_response,
            "validation": {
                "compile_status": validation.compile.status,
                "pytest_status": validation.pytest.status,
                "ruff_status": validation.ruff.status,
                "contract_status": validation.contract_status,
                "business_status": validation.business_status,
                "live_status": validation.live.status if validation.live else "NOT_RUN",
                "details": validation.as_dict(),
            },
        }

    @staticmethod
    def _prompt(platform_key: str, spec: dict[str, Any], evidence_refs: list[str]) -> str:
        manifest_ref = str((spec.get("evidence") or {}).get("manifest_ref") or "")
        evidence_dir = ""
        if manifest_ref:
            evidence_dir = "/srv/auto_spider/evidence/" + manifest_ref.rsplit("/", 1)[0]
        return (
            "<trusted_agent_skill>\n"
            f"{load_trusted_agent_skills()}\n"
            "</trusted_agent_skill>\n"
            "你正在独立 worktree 中生成招聘采集器候选提交。\n"
            f"平台键: {platform_key}\n"
            f"PlatformSpec: {spec}\n"
            f"证据引用: {evidence_refs}\n"
            f"证据目录: {evidence_dir or '未提供'}\n"
            "必须先读取证据目录中的 network.json、请求 body 和 response body，"
            "并用真实接口做一次脱敏探测；禁止根据 URL 或 fixture 猜测接口。"
            "如果 response body 是加密/编码数据，必须追踪已保存的前端脚本或网络证据，"
            "实现可重放的解码步骤后再生成采集器。fixture 只能覆盖真实响应结构，不能凭空编造字段。\n"
            "采集阶段 platform_id/entity_id 必须保持 null；生成 TOML 时必须省略 platform_id，"
            "只保留 platform_id_env，禁止分配 0、负数、最大值加一或任何临时编号。\n"
            "只允许修改 PlatformSpec generation.allowed_files 中的文件；"
            "不得读取或写入生产凭证、Cookie、数据库或当前工作区；"
            "必须生成采集器、TOML、fixture 和测试，并报告实际运行的验证命令。"
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

    def _push_candidate(self, branch_name: str, commit_sha: str) -> str:
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
                f"{commit_sha}:refs/heads/{branch_name}",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return "PUSHED"
