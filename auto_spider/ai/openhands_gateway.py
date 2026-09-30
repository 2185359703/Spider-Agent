from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from auto_spider.config import get_settings


@dataclass(frozen=True)
class AgentResult:
    final_response: str
    changed_files: list[str]
    simulated: bool


class OpenHandsGateway:
    """OpenHands SDK client connected to a remote Agent Server."""

    def __init__(
        self,
        *,
        server_url: str | None = None,
        session_api_key: str | None = None,
        llm_model: str | None = None,
        llm_api_key: str | None = None,
        llm_base_url: str | None = None,
        llm_api_mode: str | None = None,
    ) -> None:
        settings = get_settings()
        self.server_url = (server_url or settings.openhands_server_url).rstrip("/")
        self.session_api_key = session_api_key or settings.openhands_session_api_key
        self.llm_model = llm_model or settings.openhands_llm_model
        self.llm_api_key = llm_api_key or settings.openhands_llm_api_key
        self.llm_base_url = llm_base_url or settings.openhands_llm_base_url
        self.llm_api_mode = llm_api_mode or settings.openhands_llm_api_mode

    def run(
        self,
        prompt: str,
        workspace: Path,
        *,
        review_only: bool = False,
        event_callback: Callable[[object], None] | None = None,
        token_callback: Callable[[object], None] | None = None,
    ) -> AgentResult:
        self._ensure_stream_encoding()
        try:
            from openhands.sdk import LLM, Conversation, Workspace
            from openhands.sdk.event import MessageEvent
            from openhands.tools.preset.default import get_default_agent
            from pydantic import SecretStr
        except ImportError as exc:
            raise RuntimeError(
                "OpenHands SDK/Agent Server 依赖未安装，无法启动 agent runner"
            ) from exc

        llm_kwargs = {"model": self.llm_model}
        if self.llm_base_url:
            llm_kwargs["base_url"] = self.llm_base_url
        if self.llm_api_mode:
            llm_kwargs["api_mode"] = self.llm_api_mode
        if self.llm_api_key:
            llm_kwargs["api_key"] = SecretStr(self.llm_api_key)
        # The configured OpenAI-compatible endpoint requires streaming responses.
        llm_kwargs["stream"] = True
        llm = LLM(**llm_kwargs)
        agent = get_default_agent(llm=llm, cli_mode=True)
        workspace_config: dict[str, str] = {
            "host": self.server_url,
            "working_dir": self._remote_workspace_path(workspace),
        }
        if self.session_api_key:
            workspace_config["api_key"] = self.session_api_key
        remote_workspace = Workspace(**workspace_config)
        if review_only:
            prompt = "只读审查模式：不得修改文件、创建提交或执行破坏性命令。\n" + prompt
        callbacks = [event_callback] if event_callback is not None else None
        token_callbacks = [token_callback] if token_callback is not None else None
        conversation = Conversation(
            agent=agent,
            workspace=remote_workspace,
            callbacks=callbacks,
            token_callbacks=token_callbacks,
        )
        try:
            conversation.send_message(prompt)
            result = conversation.run()
            final_response = getattr(result, "final_response", None) or self._final_response(
                conversation, MessageEvent
            )
            if not final_response and result is not None:
                final_response = str(result)
        finally:
            conversation.close()
        return AgentResult(
            final_response=final_response,
            changed_files=[],
            simulated=False,
        )

    @staticmethod
    def _ensure_stream_encoding() -> None:
        """Celery's LoggingProxy omits ``encoding``; libtmux reads it on import."""
        import sys

        for name in ("stdout", "stderr"):
            stream = getattr(sys, name, None)
            if stream is None or getattr(stream, "encoding", None):
                continue
            try:
                stream.encoding = "utf-8"
            except Exception:
                # The fallback is only for unusual logging proxies. OpenHands
                # can still use the underlying stream for output.
                continue

    @staticmethod
    def _remote_workspace_path(workspace: Path) -> str:
        settings = get_settings()
        local_root = settings.worktree_root.resolve()
        resolved = workspace.resolve()
        try:
            relative = resolved.relative_to(local_root)
        except ValueError:
            return str(workspace)
        return f"{settings.openhands_workspace_root.rstrip('/')}/{relative.as_posix()}"

    @staticmethod
    def _final_response(conversation: object, message_event_type: type) -> str:
        events = getattr(getattr(conversation, "state", None), "events", ())
        for event in reversed(list(events)):
            if not isinstance(event, message_event_type) or getattr(event, "source", None) != (
                "agent"
            ):
                continue
            content = getattr(getattr(event, "llm_message", None), "content", ())
            text_parts = [getattr(item, "text", "") for item in content]
            response = "\n".join(part for part in text_parts if part).strip()
            if response:
                return response
        return ""
