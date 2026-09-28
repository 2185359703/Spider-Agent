from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from auto_spider.config import get_settings


@dataclass(frozen=True)
class AgentResult:
    final_response: str
    changed_files: list[str]
    simulated: bool


class CodexGateway:
    """Thin gateway around the official Codex Python SDK.

    The SDK is imported lazily so API/tests can run before the runner image is
    provisioned. Production workers must install openai-codex.
    """

    def __init__(self, model: str | None = None) -> None:
        self.model = model or get_settings().codex_model

    def run(
        self,
        prompt: str,
        workspace: Path,
        *,
        review_only: bool = False,
    ) -> AgentResult:
        try:
            from openai_codex import Codex, Sandbox
        except ImportError as exc:
            raise RuntimeError("openai-codex 未安装，无法启动 Codex runner") from exc

        sandbox = Sandbox.read_only if review_only else Sandbox.workspace_write
        with Codex() as codex:
            thread = codex.thread_start(model=self.model, sandbox=sandbox)
            result = thread.run(prompt)
        return AgentResult(
            final_response=result.final_response,
            changed_files=[],
            simulated=False,
        )
