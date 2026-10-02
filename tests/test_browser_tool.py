"""Check the SDK Action boundary, not just the underlying CLI wrapper."""

from auto_spider.ai.collector_tools import BrowserExecutor, CollectorBrowserAction


def test_sdk_action_discriminator_is_not_forwarded_to_cli(monkeypatch):
    executor = BrowserExecutor("a" * 32, {})
    calls = []

    def execute(action, *, url, ref, value, index, static):
        calls.append((action, url))
        return {"action": action, "output": "actual tool result"}

    monkeypatch.setattr(executor.browser, "execute", execute)
    result = executor(CollectorBrowserAction(action="open", url="https://playwright.dev/"))
    assert calls == [("open", "https://playwright.dev/")]
    assert not result.is_error


def test_persisted_action_loads_with_builtin_browser_tool_registered():
    from openhands.sdk.tool.schema import Action
    from openhands.tools.browser_use.definition import BrowserAction as BuiltinBrowserAction

    action = CollectorBrowserAction(action="requests")
    assert action.model_dump()["kind"] != BuiltinBrowserAction.__name__
    restored = Action.model_validate(action.model_dump())
    assert isinstance(restored, CollectorBrowserAction) and restored.action == "requests"
