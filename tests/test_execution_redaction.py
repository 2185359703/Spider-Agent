import pytest

from auto_spider.services.browser_evidence import sanitize_text
from tests.test_execution_boundaries import access


@pytest.mark.parametrize(
    "text",
    [
        "GET https://example.com/jobs?token=secret-value&offset=2",
        'OPENHANDS_LLM_API_KEY="secret-value"',
        '{"authorization":"Bearer secret-value"}',
        '{"cookie":"secret-value"}',
    ],
)
def test_log_and_tool_output_do_not_retain_secrets(text):
    redacted = sanitize_text(text)
    assert "secret-value" not in redacted
    assert "[REDACTED]" in redacted
    assert sanitize_text(redacted) == redacted


def test_agent_write_rejects_literal_credentials(tmp_path):
    with pytest.raises(ValueError, match="UNSANITIZED"):
        access(tmp_path).write("collectors/example.py", 'API_KEY="secret-value"')
