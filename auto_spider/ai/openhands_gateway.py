from __future__ import annotations

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
    ) -> None:
        settings = get_settings()
        self.server_url = (server_url or settings.openhands_server_url).rstrip("/")
        self.session_api_key = session_api_key or settings.openhands_session_api_key
        self.llm_model = llm_model or settings.openhands_llm_model
        self.llm_api_key = llm_api_key or settings.openhands_llm_api_key

    def run(
        self,
        prompt: str,
        workspace: Path,
        *,
        review_only: bool = False,
    ) -> AgentResult:
        try:
            from openhands.sdk import LLM, Conversation, Workspace
            from openhands.tools.preset.default import get_default_agent
            from pydantic import SecretStr
        except ImportError as exc:
            raise RuntimeError(
                "OpenHands SDK/Agent Server 依赖未安装，无法启动 agent runner"
            ) from exc

        llm_kwargs = {"model": self.llm_model}
        if self.llm_api_key:
            llm_kwargs["api_key"] = SecretStr(self.llm_api_key)
        llm = LLM(**llm_kwargs)
        agent = get_default_agent(llm=llm, cli_mode=True)
        workspace_config: dict[str, str] = {
            "host": self.server_url,
            "working_dir": str(workspace),
        }
        if self.session_api_key:
            workspace_config["api_key"] = self.session_api_key
        remote_workspace = Workspace(**workspace_config)
        if review_only:
            prompt = "只读审查模式：不得修改文件、创建提交或执行破坏性命令。\n" + prompt
        conversation = Conversation(agent=agent, workspace=remote_workspace)
        try:
            conversation.send_message(prompt)
            result = conversation.run()
            final_response = getattr(result, "final_response", None) or str(result)
        finally:
            conversation.close()
        return AgentResult(
            final_response=final_response,
            changed_files=[],
            simulated=False,
        )
