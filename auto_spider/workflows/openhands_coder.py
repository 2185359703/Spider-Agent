from __future__ import annotations

import json
import subprocess
from pathlib import Path

from auto_spider.ai.openhands_gateway import OpenHandsGateway
from auto_spider.ai.skill_loader import load_trusted_agent_skills
from auto_spider.git.policy import validate_changed_files
from auto_spider.git.worktree import WorktreeManager
from auto_spider.schemas import PlatformSpec


class OpenHandsCodingGateway:
    """Generate files only. Validation, reporting and publication are separate nodes."""

    def __init__(self, *, worktrees=None, gateway=None):
        self.worktrees = worktrees or WorktreeManager()
        self.gateway = gateway or OpenHandsGateway()

    def generate(
        self,
        platform_key,
        *,
        task_id,
        run_id,
        spec,
        evidence_refs,
        execution,
        event_callback=None,
        **kwargs,
    ):
        context = self.worktrees.prepare(task_id, run_id, mutation_enabled=True)
        result = self.gateway.run(
            self._prompt(platform_key, spec, evidence_refs),
            context.path,
            execution=execution,
            task_id=task_id,
            step_key="generate_code:0",
            spec=spec,
            event_callback=event_callback,
        )
        return self.output(context.path, context.baseline_ref, platform_key, result.final_response)

    @staticmethod
    def output(path, baseline_ref, platform_key, response):
        changed = OpenHandsCodingGateway._changed_files(path)
        invalid = validate_changed_files(changed, platform_key)
        if invalid:
            raise RuntimeError(f"CODE_SCOPE_VIOLATION: {invalid}")
        patch_path = path / f"tests/fixtures/{platform_key}/platform-spec.patch.json"
        patch_error = None
        try:
            patch = (
                json.loads(patch_path.read_text(encoding="utf-8")) if patch_path.is_file() else None
            )
        except json.JSONDecodeError as exc:
            patch = None
            patch_error = [{"loc": ["platform-spec.patch.json"], "msg": str(exc)}]
        return {
            "workspace": str(path),
            "baseline_ref": baseline_ref,
            "changed_files": changed,
            "spec_patch": patch,
            "spec_patch_error": patch_error,
            "agent_response": response,
            "simulated": False,
        }

    @staticmethod
    def _prompt(platform_key, spec, evidence_refs):
        schema_json = json.dumps(
            PlatformSpec.model_json_schema(), ensure_ascii=False, separators=(",", ":")
        )
        return (
            f"<trusted_agent_skill>\n{load_trusted_agent_skills()}\n</trusted_agent_skill>\n"
            f"平台键: {platform_key}\nPlatformSpec canonical JSON:\n"
            f"{json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}\n"
            "PlatformSpec JSON Schema（字段类型与枚举必须遵守）:\n"
            f"{schema_json}\n"
            f"证据引用: {evidence_refs}\n"
            "读取工具的 area=evidence 提供当前任务的脱敏网页与真实网络响应。先读取证据，"
            "接口未确认、页面是动态渲染、响应与预期不一致、分页或字段证据不足时，"
            "必须主动调用 CollectorBrowserTool（Playwright CLI）重新取证，"
            "不得用入口页假冒列表接口。"
            "先 open 入口、snapshot 获取元素引用，点击实习筛选/翻页，再 requests 和 response-body "
            "确认真实接口、请求方法/请求体、响应格式。HTML/XML/文本或加密包装均可能正常，先确认协议。"
            "工具返回真实输出与 area=evidence 可读取的证据引用，Spec patch 必须引用这些新证据。"
            "不能编造请求或样本，浏览器只用于取证，采集器仍使用纯 HTTP。\n"
            "生成采集器、TOML、真实样本 fixture 和测试。两个业务 ID 保持 null，"
            "TOML 省略 platform_id。"
            "运行权限由工具白名单执行；本轮不提供终端。后端负责运行测试、回传失败包和创建本地提交。"
            "不要声称运行过未执行的测试。不得自行提交或推送。\n"
            "若证据要求改变 Spec 的 endpoints、fields、pagination、filters，"
            f"同时写 tests/fixtures/{platform_key}/platform-spec.patch.json，"
            '格式为 {"reason":"依据", "evidence_refs":["已有证据ID"], "patch":{"fields":{...}}}。'
            "patch 中每个顶层部分必须完整提供；"
            "不得改变 identity、generation、normalization、validation。"
            "写入工具会即时校验补丁，失败时文件不会保存。根据返回的字段错误修正，"
            "直到成功写入才结束。不要自造枚举；加密 JSON 的 response_format 仍为 json，"
            "decode 是对象；解码后的 JSON 选择器 source 仍为 response_body。"
        )

    @staticmethod
    def _changed_files(worktree: Path) -> list[str]:
        changed = set()
        for command in (
            ["diff", "--name-only", "-z"],
            ["diff", "--cached", "--name-only", "-z"],
            ["ls-files", "--others", "--exclude-standard", "-z"],
        ):
            result = subprocess.run(
                ["git", "-C", str(worktree), *command],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            changed.update(path for path in result.stdout.split("\0") if path)
        return sorted(changed)
