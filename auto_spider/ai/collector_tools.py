"""OpenHands tools deliberately expose no arbitrary terminal or unrestricted editor."""

from typing import Literal

from openhands.sdk.tool import ToolDefinition, ToolExecutor, register_tool
from openhands.sdk.tool.schema import Action, Observation
from pydantic import Field

from auto_spider.ai.browser_cli import BrowserCLI
from auto_spider.ai.workspace_access import WorkspaceAccess, load_policy
from auto_spider.services.analysis_submission import DiscoveryDraft, DiscoveryObservation


class ReadAction(Action):
    path: str = Field(description="Relative file/directory path. Use '.' to list the root.")
    area: Literal["workspace", "evidence", "failure"] = "workspace"
    offset: int = Field(default=0, ge=0)


class WriteAction(Action):
    path: str = Field(description="Exact relative path allowed by PlatformSpec.")
    content: str = Field(description="Complete UTF-8 file contents.")


class CollectorObservation(Observation):
    pass


class CollectorSubmitAnalysisAction(Action):
    observation: DiscoveryObservation
    spec_draft: DiscoveryDraft
    evidence_refs: list[str] = Field(min_length=1, max_length=100)


class SubmitAnalysisExecutor(ToolExecutor):
    def __init__(self, policy):
        self.policy = policy

    def __call__(self, action, conversation=None):
        import json

        from auto_spider.services.analysis_submission import submit_analysis

        try:
            result = submit_analysis(self.policy, action.model_dump(mode="json", exclude={"kind"}))
            return CollectorObservation.from_text(json.dumps(result, ensure_ascii=False))
        except (OSError, ValueError) as exc:
            return CollectorObservation.from_text(str(exc), is_error=True)


class CollectorSubmitAnalysisTool(ToolDefinition):
    @classmethod
    def create(cls, conv_state, policy_id: str):
        return [
            cls(
                action_type=CollectorSubmitAnalysisAction,
                observation_type=CollectorObservation,
                description="Submit typed recruitment analysis from real evidence. Use exact "
                "evidence references returned by browser tools. Save before finishing; "
                "if validation fails, correct the input and submit again. No source-code writes.",
                executor=SubmitAnalysisExecutor(load_policy(policy_id)),
            )
        ]


class CollectorBrowserAction(Action):
    action: Literal[
        "open",
        "goto",
        "snapshot",
        "click",
        "select",
        "fill",
        "press",
        "requests",
        "request",
        "request-headers",
        "request-body",
        "response-headers",
        "response-body",
        "console",
        "screenshot",
        "close",
    ]
    url: str | None = None
    ref: str | None = Field(
        default=None,
        description="Use the latest snapshot ref; navigation/filter changes invalidate old refs.",
    )
    value: str | None = None
    index: int | None = Field(default=None, ge=1)
    static: bool = False


class BrowserExecutor(ToolExecutor):
    def __init__(self, policy_id, policy):
        self.browser = BrowserCLI(policy_id, policy)

    def __call__(self, action, conversation=None):
        import json

        try:
            result = self.browser.execute(
                action.action,
                url=action.url,
                ref=action.ref,
                value=action.value,
                index=action.index,
                static=action.static,
            )
            return CollectorObservation.from_text(
                json.dumps(result, ensure_ascii=False), is_error=bool(result.get("error"))
            )
        except (OSError, ValueError, RuntimeError) as exc:
            return CollectorObservation.from_text(str(exc), is_error=True)


class CollectorBrowserTool(ToolDefinition):
    @classmethod
    def create(cls, conv_state, policy_id: str):
        return [
            cls(
                action_type=CollectorBrowserAction,
                observation_type=CollectorObservation,
                description="Use batch-reused Playwright CLI to investigate recruitment sites. "
                "Open, snapshot, click internship filters or pagination; inspect numbered "
                "requests and response bodies. Results are real evidence, never mocked. "
                "After navigation or filtering, get a new snapshot before choosing a ref. "
                "BROWSER_STALE_REF includes a recovery snapshot; reselect the target from it. "
                "No arbitrary shell/run-code, credential export, applications or uploads.",
                executor=BrowserExecutor(policy_id, load_policy(policy_id)),
            )
        ]


class ReadExecutor(ToolExecutor):
    def __init__(self, access: WorkspaceAccess):
        self.access = access

    def __call__(self, action, conversation=None):
        try:
            return CollectorObservation.from_text(
                self.access.read(action.path, action.area, action.offset)
            )
        except (OSError, ValueError) as exc:
            return CollectorObservation.from_text(str(exc), is_error=True)


class WriteExecutor(ToolExecutor):
    def __init__(self, access: WorkspaceAccess):
        self.access = access

    def __call__(self, action, conversation=None):
        try:
            if action.path.endswith("/platform-spec.patch.json"):
                import json

                from auto_spider.services.spec_patch import (
                    schema_feedback,
                    validate_patch_evidence,
                    validate_spec_patch,
                )

                try:
                    proposal = json.loads(action.content)
                    validate_spec_patch(self.access.policy.get("platform_spec", {}), proposal)
                    validate_patch_evidence(self.access.policy, proposal)
                except ValueError as exc:
                    return CollectorObservation.from_text(schema_feedback(exc), is_error=True)
            return CollectorObservation.from_text(self.access.write(action.path, action.content))
        except (OSError, ValueError) as exc:
            return CollectorObservation.from_text(str(exc), is_error=True)


class CollectorReadTool(ToolDefinition):
    @classmethod
    def create(cls, conv_state, policy_id: str):
        return [
            cls(
                action_type=ReadAction,
                observation_type=CollectorObservation,
                description="Read source files or redacted evidence in this task workspace.",
                executor=ReadExecutor(WorkspaceAccess(load_policy(policy_id))),
            )
        ]


class CollectorWriteTool(ToolDefinition):
    @classmethod
    def create(cls, conv_state, policy_id: str):
        policy = load_policy(policy_id)
        if policy["mode"] != "write":
            raise ValueError("READ_ONLY_WORKSPACE")
        return [
            cls(
                action_type=WriteAction,
                observation_type=CollectorObservation,
                description="Write only this company's allowed collector/config/test files.",
                executor=WriteExecutor(WorkspaceAccess(policy)),
            )
        ]


register_tool("CollectorReadTool", CollectorReadTool)
register_tool("CollectorWriteTool", CollectorWriteTool)
register_tool("CollectorBrowserTool", CollectorBrowserTool)
register_tool("CollectorSubmitAnalysisTool", CollectorSubmitAnalysisTool)
