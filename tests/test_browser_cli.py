import json
from pathlib import Path

import pytest

from auto_spider.ai.browser_cli import BrowserCLI, redact


def browser(tmp_path, monkeypatch, execution="a" * 32, group="b" * 32, calls=None):
    monkeypatch.setattr("auto_spider.ai.browser_cli.public_url", lambda url: url)
    evidence = tmp_path / "evidence"
    calls = calls if calls is not None else []

    def run(command, cwd, env):
        assert "DATABASE_URL" not in env and "LLM_API_KEY" not in env
        calls.append(command)
        return "Real browser output"

    policy = {
        "browser_group": group,
        "task_id": execution,
        "evidence": str(evidence),
        "browser_evidence": str(evidence / "agent-browser" / execution),
    }
    return BrowserCLI(execution, policy, root=tmp_path / "runtime", runner=run), calls


def test_batch_reuses_browser_and_separates_company_pages(tmp_path, monkeypatch):
    first, calls = browser(tmp_path, monkeypatch)
    first.execute("open", url="https://one.example.com")
    first.release()
    second, _ = browser(tmp_path, monkeypatch, execution="c" * 32, calls=calls)
    second.execute("open", url="https://two.example.com")
    second.release()
    actions = [command[2] for command in calls]
    assert actions == ["open", "tab-new", "tab-close", "tab-new", "tab-close"]
    assert first.session == second.session
    assert first.folder.exists()
    assert second.close()["released"]
    assert not first.folder.exists()


def test_owner_prevents_interleaved_companies_and_cleanup_preserves_other_batches(
    tmp_path, monkeypatch
):
    first, _ = browser(tmp_path, monkeypatch)
    first.execute("open", url="https://one.example.com")
    second, _ = browser(tmp_path, monkeypatch, execution="c" * 32)
    with pytest.raises(ValueError, match="BATCH_BUSY"):
        second.execute("open", url="https://two.example.com")
    assert second.release()["released"] is False
    other, _ = browser(tmp_path, monkeypatch, execution="d" * 32, group="e" * 32)
    other.execute("open", url="https://other.example.com")
    first.release()
    first.close()
    assert other.folder.exists()


def test_cli_denies_arbitrary_execution_and_saves_sanitized_evidence(tmp_path, monkeypatch):
    instance, _ = browser(tmp_path, monkeypatch)
    for action in ("run-code", "eval", "upload", "kill-all", "close-all"):
        with pytest.raises(ValueError, match="COMMAND_DENIED"):
            instance.execute(action)
    with pytest.raises(ValueError, match="SNAPSHOT_REF"):
        instance.execute("click", ref="--run-code=evil")
    result = instance.execute("open", url="https://one.example.com")
    evidence = Path(instance.policy["evidence"]) / result["evidence_ref"]
    assert json.loads(evidence.read_text())["action"] == "open"
    assert redact({"authorization": "Bearer secret", "title": "intern"}) == {
        "authorization": "[REDACTED]",
        "title": "intern",
    }


def test_public_url_blocks_private_and_file_urls(monkeypatch):
    from auto_spider.ai.browser_cli import public_url

    monkeypatch.setattr(
        "socket.getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("127.0.0.1", 443))]
    )
    with pytest.raises(ValueError, match="PRIVATE_NETWORK"):
        public_url("http://internal.example")
    for value in ("file:///etc/passwd", "http://user:password@example.com"):
        with pytest.raises(ValueError, match="URL_DENIED"):
            public_url(value)


def test_browser_action_budget_is_per_company_and_reopen_reuses_page(tmp_path, monkeypatch):
    first, calls = browser(tmp_path, monkeypatch)
    first.execute("open", url="https://one.example.com")
    first.execute("open", url="https://one.example.com/other")
    assert [c[2] for c in calls] == ["open", "tab-new", "goto"]
    receipt = first.receipt()
    receipt["owner_commands"] = 100
    first.save(receipt)
    with pytest.raises(ValueError, match="ACTION_LIMIT"):
        first.execute("snapshot")
    assert first.close()["released"] is False
    first.release()
    second, _ = browser(tmp_path, monkeypatch, execution="c" * 32, calls=calls)
    with pytest.raises(ValueError, match="OPEN_REQUIRED"):
        second.execute("requests")
    second.execute("open", url="https://two.example.com")
    assert second.receipt()["owner_commands"] == 1


def test_cli_navigation_error_is_evidence_and_can_be_reclaimed(tmp_path, monkeypatch):
    instance, _ = browser(tmp_path, monkeypatch)

    def failing_runner(command, cwd, env):
        raise RuntimeError("Browser is not running")

    instance.runner = failing_runner
    result = instance.execute("open", url="https://one.example.com")
    assert result["error"] is True
    capture = Path(instance.policy["evidence"]) / result["evidence_ref"]
    assert "not running" in json.loads(capture.read_text())["output"]
    assert instance.release()["released"]
    assert instance.close()["released"]
    assert capture.exists()
