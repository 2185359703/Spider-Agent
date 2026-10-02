import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from auto_spider.services.agent_events import activity_event
from tests.test_browser_cli import browser


def test_conversation_error_preserves_sanitized_code_and_detail():
    event = type(
        "ConversationErrorEvent",
        (),
        {
            "code": "BadRequestError",
            "detail": 'Stream must be set to true; api_key="secret-value"',
        },
    )()
    message, detail = activity_event(event)
    assert "Stream must be set to true" in message
    assert detail["error_code"] == "BadRequestError"
    assert detail["action_type"] == "error"
    assert "secret-value" not in json.dumps([message, detail])


@pytest.mark.parametrize("exit_zero", [False, True])
def test_stale_ref_refreshes_snapshot_without_replaying_action(tmp_path, monkeypatch, exit_zero):
    from auto_spider.ai.collector_tools import CollectorObservation

    instance, calls = browser(tmp_path, monkeypatch)
    instance.execute("open", url="https://one.example.com")

    def run(command, cwd, env):
        calls.append(command)
        if command[2] == "fill":
            message = "### Error\nError: Ref e49 not found in the current page snapshot."
            if exit_zero:
                return message
            raise RuntimeError(message)
        return "### Snapshot\n- textbox ref=e859"

    instance.runner = run
    result = instance.execute("fill", ref="e49", value="实习")
    assert result["error"] is True
    assert result["error_code"] == "BROWSER_STALE_REF"
    assert result["recovery"]["status"] == "completed"
    assert "e859" in result["recovery"]["output"]
    assert [c[2] for c in calls][-2:] == ["fill", "snapshot"]
    event = SimpleNamespace(
        observation=CollectorObservation.from_text(json.dumps(result), is_error=True)
    )
    message, detail = activity_event(event)
    assert detail["severity"] == "WARNING" and detail["is_error"]
    assert "等待重新选择" in message


def test_stale_ref_with_failed_snapshot_stays_error(tmp_path, monkeypatch):
    from auto_spider.ai.collector_tools import CollectorObservation

    instance, _ = browser(tmp_path, monkeypatch)
    instance.execute("open", url="https://one.example.com")

    def run(command, cwd, env):
        raise RuntimeError(
            "Ref e49 not found in the current page snapshot."
            if command[2] == "click"
            else "Browser disconnected"
        )

    instance.runner = run
    result = instance.execute("click", ref="e49")
    assert result["recovery"]["status"] == "failed"
    event = SimpleNamespace(
        observation=CollectorObservation.from_text(json.dumps(result), is_error=True)
    )
    _, detail = activity_event(event)
    assert detail["is_error"] and detail.get("severity") != "WARNING"


def test_typed_conversation_creation_disables_autotitle_and_is_idempotent(tmp_path):
    from openhands.sdk import LLM, Agent

    from auto_spider.ai.openhands_gateway import OpenHandsGateway

    conversation_id = str(uuid4())
    posts = []

    def handler(request):
        if request.method == "GET":
            return (
                httpx.Response(200, json={"id": conversation_id}) if posts else httpx.Response(404)
            )
        body = json.loads(request.content)
        posts.append(body)
        return httpx.Response(200, json={"id": conversation_id})

    workspace = SimpleNamespace(
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://agent.test"),
        working_dir=str(tmp_path),
    )
    agent = Agent(llm=LLM(model="openai/test", api_key="fake-test-value"), tools=[])
    for _ in range(2):
        OpenHandsGateway._ensure_conversation(
            workspace, conversation_id, agent, 5, allow_create=True
        )
    assert len(posts) == 1 and posts[0]["autotitle"] is False
    assert posts[0]["conversation_id"] == conversation_id
    assert posts[0]["initial_message"] is None
    workspace.client.close()
