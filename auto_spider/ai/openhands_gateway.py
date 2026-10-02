from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select

from auto_spider.ai.gateway import resolve_gateway_config
from auto_spider.config import get_settings
from auto_spider.db.models import AgentExecution, ExecutionLease, OnboardingTask
from auto_spider.services.browser_evidence import sanitize_text
from auto_spider.services.execution import ExecutionContext, ExecutionStopped


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
        self._llm_model_override = llm_model
        self._llm_api_key_override = llm_api_key
        self._llm_base_url_override = llm_base_url
        self._llm_api_mode_override = llm_api_mode
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
        execution: ExecutionContext,
        task_id: str,
        step_key: str,
        spec: dict,
        failure_manifest_ref: str | None = None,
        browser_enabled: bool | None = None,
    ) -> AgentResult:
        self._ensure_stream_encoding()
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
        logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)
        try:
            from openhands.sdk import LLM, Agent, Conversation, Tool, Workspace
            from openhands.sdk.event import MessageEvent
            from pydantic import SecretStr

            from auto_spider.ai import collector_tools  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "OpenHands SDK/Agent Server 依赖未安装，无法启动 agent runner"
            ) from exc
        # OpenHands configures the root logger. Do not let that enable SQL bind
        # parameter logging (which can contain imported samples or credentials).
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
        logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)

        settings = get_settings()
        gateway = resolve_gateway_config(settings, factory=execution.factory)
        overrides = {
            key: value
            for key, value in {
                "model": self._llm_model_override,
                "api_key": self._llm_api_key_override,
                "base_url": self._llm_base_url_override,
                "api_mode": self._llm_api_mode_override,
            }.items()
            if value is not None
        }
        if overrides:
            gateway = replace(gateway, **overrides)
        digest = hashlib.sha256(prompt.encode()).hexdigest()
        with execution.factory.begin() as session:
            record = session.scalar(
                select(AgentExecution).where(
                    AgentExecution.run_id == execution.run_id, AgentExecution.step_key == step_key
                )
            )
            if record is None:
                record = AgentExecution(
                    execution_id=uuid4().hex,
                    task_id=task_id,
                    run_id=execution.run_id,
                    step_key=step_key,
                    conversation_id=str(uuid4()),
                    prompt_hash=digest,
                    workspace_path=str(workspace),
                    server_url=self.server_url,
                    mode="read" if review_only else "write",
                    gateway_profile=gateway.profile,
                    gateway_model=gateway.model,
                    gateway_base_url=gateway.base_url,
                    gateway_api_mode=gateway.api_mode,
                    status="CREATED",
                    result_json={},
                )
                session.add(record)
            elif record.prompt_hash != digest or record.mode != (
                "read" if review_only else "write"
            ):
                raise RuntimeError("AGENT_EXECUTION_INPUT_CHANGED")
            elif record.gateway_profile:
                gateway = resolve_gateway_config(
                    settings,
                    snapshot={
                        "profile": record.gateway_profile,
                        "model": record.gateway_model,
                        "base_url": record.gateway_base_url,
                        "api_mode": record.gateway_api_mode,
                    },
                )
            else:
                record.gateway_profile = gateway.profile
                record.gateway_model = gateway.model
                record.gateway_base_url = gateway.base_url
                record.gateway_api_mode = gateway.api_mode
            if record.status == "COMPLETED":
                return AgentResult(record.result_json.get("final_response", ""), [], False)
            execution_id, conversation_id, previous_status = (
                record.execution_id,
                record.conversation_id,
                record.status,
            )
        llm_kwargs = {"model": gateway.runtime_model(), "stream": True}
        if gateway.base_url:
            llm_kwargs["base_url"] = gateway.base_url
        if gateway.api_mode:
            llm_kwargs["api_mode"] = gateway.api_mode
        if gateway.api_key:
            llm_kwargs["api_key"] = SecretStr(gateway.api_key)
        extra_headers = gateway.extra_headers(conversation_id)
        if extra_headers:
            llm_kwargs["extra_headers"] = extra_headers
        llm = LLM(**llm_kwargs)
        policy_root = settings.agent_policy_root.resolve()
        policy_root.mkdir(parents=True, exist_ok=True)
        manifest = spec.get("evidence", {}).get("manifest_ref") or ""
        evidence_dir = f"/srv/auto_spider/evidence/{task_id}/{execution.run_id}"
        if manifest:
            from pathlib import PurePosixPath

            parts = PurePosixPath(manifest).parts
            if not parts or parts[0] != task_id or ".." in parts:
                raise RuntimeError("EVIDENCE_SCOPE_VIOLATION")
            evidence_dir = f"/srv/auto_spider/evidence/{PurePosixPath(manifest).parent}"
        policy = {
            "task_id": task_id,
            "run_id": execution.run_id,
            "workspace": self._remote_workspace_path(workspace),
            "evidence": evidence_dir,
            "mode": "read" if review_only else "write",
            "allowed_files": spec.get("generation", {}).get("allowed_files", []),
            "platform_spec": spec,
            "browser_evidence": f"{evidence_dir}/agent-browser/{execution_id}",
            "evidence_prefix": evidence_dir.removeprefix("/srv/auto_spider/evidence/"),
        }
        if step_key.startswith("inspect_site:"):
            policy["analysis_output"] = f"analysis-submissions/{execution_id}.json"
        if failure_manifest_ref:
            from pathlib import PurePosixPath

            from auto_spider.db.models import EvidenceFile

            relative = PurePosixPath(failure_manifest_ref)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not relative.parts
                or relative.parts[0] != task_id
                or relative.name != "manifest.json"
            ):
                raise RuntimeError("FAILURE_EVIDENCE_SCOPE_VIOLATION")
            with execution.factory() as session:
                receipt = session.scalar(
                    select(EvidenceFile).where(
                        EvidenceFile.task_id == task_id,
                        EvidenceFile.relative_path == failure_manifest_ref,
                        EvidenceFile.file_type == "failure_manifest",
                    )
                )
                if receipt is None:
                    raise RuntimeError("FAILURE_EVIDENCE_UNREGISTERED")
                package_rows = session.scalars(
                    select(EvidenceFile).where(
                        EvidenceFile.task_id == task_id,
                        EvidenceFile.relative_path.like(relative.parent.as_posix() + "/%"),
                        EvidenceFile.file_type != "failure_schema",
                    )
                ).all()
                policy["allowed_evidence_refs"] = [
                    value for row in package_rows for value in (row.evidence_id, row.relative_path)
                ]
            policy["failure_evidence"] = f"/srv/auto_spider/evidence/{relative.parent}"
            policy["failure_prefix"] = relative.parent.as_posix()
        policy_path = policy_root / f"{execution_id}.json"
        with execution.factory() as session:
            task = session.scalar(select(OnboardingTask).where(OnboardingTask.task_id == task_id))
            policy["browser_group"] = hashlib.sha256(f"batch:{task.batch_id}".encode()).hexdigest()[
                :32
            ]
        policy_path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
        tool_names = ["CollectorReadTool"] + ([] if review_only else ["CollectorWriteTool"])
        use_browser = settings.agent_browser_enabled and browser_enabled is not False
        if use_browser:
            tool_names.append("CollectorBrowserTool")
        if policy.get("analysis_output"):
            tool_names.append("CollectorSubmitAnalysisTool")
        agent = Agent(
            llm=llm,
            tools=[Tool(name=name, params={"policy_id": execution_id}) for name in tool_names],
            include_default_tools=[],
        )
        workspace_config: dict[str, str] = {
            "host": self.server_url,
            "working_dir": self._remote_workspace_path(workspace),
        }
        if self.session_api_key:
            workspace_config["api_key"] = self.session_api_key
        remote_workspace = Workspace(**workspace_config)
        execution.retain_lease = True
        if review_only:
            prompt = "只读审查模式：不得修改文件、创建提交或执行破坏性命令。\n" + prompt
        callbacks = [event_callback] if event_callback is not None else None
        self._ensure_conversation(
            remote_workspace,
            conversation_id,
            agent,
            settings.agent_max_iterations,
            allow_create=previous_status == "CREATED",
        )
        conversation = Conversation(
            agent=agent,
            workspace=remote_workspace,
            callbacks=callbacks,
            conversation_id=UUID(conversation_id),
            delete_on_close=False,
            max_iteration_per_run=settings.agent_max_iterations,
            visualizer=None,
        )
        preserve_browser = False
        try:
            execution.guard()
            events = list(conversation.state.events)
            if not any(getattr(event, "source", None) == "user" for event in events):
                conversation.send_message(prompt, sender=execution_id)
            status = self._remote_status(remote_workspace, conversation_id)
            if status != "finished":
                self._record_status(execution, execution_id, "RUNNING")
                conversation.run(blocking=False)
                run_started = time.monotonic()
                while True:
                    execution.guard()
                    status = self._remote_status(remote_workspace, conversation_id)
                    if status == "finished":
                        break
                    if status in {"error", "stuck", "waiting_for_confirmation"}:
                        detail = self._remote_error(remote_workspace, conversation_id)
                        suffix = f": {detail}" if detail else ""
                        raise RuntimeError(f"AGENT_{status.upper()}: {conversation_id}{suffix}")
                    if status == "paused" and time.monotonic() - run_started >= 15:
                        raise ExecutionStopped("PAUSE")
                    time.sleep(settings.agent_poll_seconds)
            final_response = self._final_response(conversation, MessageEvent)
            self._record_status(
                execution,
                execution_id,
                "COMPLETED",
                result={"final_response": sanitize_text(final_response)},
            )
            execution.retain_lease = False
        except ExecutionStopped as exc:
            if exc.reason == "LEASE_LOST":
                raise
            try:
                conversation.pause()
                deadline = time.monotonic() + 30
                while self._remote_status(remote_workspace, conversation_id) == "running":
                    if time.monotonic() >= deadline:
                        execution.retain_lease = True
                        raise RuntimeError("AGENT_STOP_UNCONFIRMED") from exc
                    time.sleep(0.5)
            except Exception as pause_error:
                execution.retain_lease = True
                try:
                    self._record_status(
                        execution,
                        execution_id,
                        "DISCONNECTED",
                        error=f"AGENT_STOP_UNCONFIRMED: {pause_error}",
                    )
                except ExecutionStopped:
                    pass
                raise RuntimeError("AGENT_STOP_UNCONFIRMED") from pause_error
            self._record_status(
                execution,
                execution_id,
                {"PAUSE": "PAUSED", "CANCEL": "CANCELLED", "TIMEOUT": "TIMED_OUT"}.get(
                    exc.reason, "INTERRUPTED"
                ),
            )
            execution.retain_lease = False
            preserve_browser = exc.reason == "PAUSE"
            raise
        except Exception as exc:
            self._record_status(execution, execution_id, "DISCONNECTED", error=str(exc))
            # A remote agent can continue after a client disconnect. Preserve
            # the platform lease until recovery reconnects to this same session;
            # terminal remote failures are safe to release immediately.
            execution.retain_lease = True
            try:
                execution.retain_lease = not self._terminal_remote_status(
                    self._remote_status(remote_workspace, conversation_id)
                )
            except Exception:
                pass
            raise
        finally:
            try:
                conversation.close()
            finally:
                release_browser = use_browser and not preserve_browser
                if release_browser and execution.retain_lease:
                    # A failed stop can leave the durable execution lease in
                    # place while the remote conversation is already paused or
                    # finished. Release the shared batch browser in that case;
                    # keep it only while the remote agent is still running so
                    # another company cannot interleave browser actions.
                    try:
                        release_browser = self._terminal_remote_status(
                            self._remote_status(remote_workspace, conversation_id)
                        )
                    except Exception:
                        release_browser = False
                if release_browser:
                    try:
                        remote_workspace.client.post(
                            f"/api/collector-browser/{execution_id}/close"
                        ).raise_for_status()
                    except Exception:
                        # The per-session idle timeout and maintenance reaper retry cleanup.
                        logging.getLogger(__name__).warning(
                            "Browser resource cleanup will be retried: %s", execution_id
                        )
        return AgentResult(
            final_response=sanitize_text(final_response),
            changed_files=[],
            simulated=False,
        )

    @staticmethod
    def _terminal_remote_status(status: str) -> bool:
        return status in {
            "finished",
            "paused",
            "error",
            "stuck",
            "waiting_for_confirmation",
            "idle",
        }

    @staticmethod
    def _ensure_conversation(workspace, conversation_id, agent, max_iterations, *, allow_create):
        """Create via the typed API: SDK 1.49.6 silently ignores autotitle kwargs."""
        from openhands.sdk.conversation.request import StartConversationRequest
        from openhands.sdk.tool.registry import get_tool_module_qualnames
        from openhands.sdk.workspace import LocalWorkspace

        response = workspace.client.get(f"/api/conversations/{conversation_id}")
        if response.status_code != 404:
            response.raise_for_status()
            return
        if not allow_create:
            raise RuntimeError("AGENT_STATE_MISSING: 已有会话丢失，保留工作区等待处理")
        request = StartConversationRequest(
            conversation_id=UUID(conversation_id),
            agent=agent,
            workspace=LocalWorkspace(working_dir=workspace.working_dir),
            tool_module_qualnames=get_tool_module_qualnames(),
            max_iterations=max_iterations,
            initial_message=None,
            autotitle=False,
        )
        # Credentials travel only to the configured Agent Server, never to logs/evidence.
        response = workspace.client.post(
            "/api/conversations",
            json=request.model_dump(mode="json", context={"expose_secrets": True}),
        )
        if response.status_code == 409:
            response = workspace.client.get(f"/api/conversations/{conversation_id}")
        response.raise_for_status()
        returned_id = response.json().get("id") or response.json().get("conversation_id")
        if not returned_id or UUID(returned_id) != UUID(conversation_id):
            raise RuntimeError("AGENT_CONVERSATION_ID_MISMATCH")

    @staticmethod
    def _remote_status(workspace, conversation_id: str) -> str:
        return str(
            OpenHandsGateway._remote_payload(workspace, conversation_id).get(
                "execution_status", "unknown"
            )
        ).lower()

    @staticmethod
    def _remote_payload(workspace, conversation_id: str) -> dict:
        response = workspace.client.get(f"/api/conversations/{conversation_id}")
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _remote_error(workspace, conversation_id: str) -> str:
        payload = OpenHandsGateway._remote_payload(workspace, conversation_id)
        for key in ("error", "error_message", "last_error", "message"):
            value = payload.get(key)
            if value:
                return sanitize_text(str(value))[:500]
        return ""

    @staticmethod
    def _record_status(execution, execution_id, status, *, result=None, error=None):
        with execution.factory.begin() as session:
            lease = session.scalar(
                select(ExecutionLease).where(ExecutionLease.run_id == execution.run_id)
            )
            if lease is None or lease.owner != execution.owner:
                raise ExecutionStopped("LEASE_LOST")
            row = session.get(AgentExecution, execution_id)
            row.status = status
            if result is not None:
                row.result_json = result
            row.error_message = sanitize_text(error)[:2000] if error else None

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
