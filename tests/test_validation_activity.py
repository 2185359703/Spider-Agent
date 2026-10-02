import sys

import pytest

from auto_spider.services.validation_activity import emit_validation, validation_activity


def test_activity_is_scoped_and_labels_platform_commands():
    events = []
    with validation_activity(lambda message, detail: events.append((message, detail))):
        emit_validation("命令启动", phase="start", command=["pytest"])
    emit_validation("不属于当前轮次")
    assert len(events) == 1
    assert events[0][1]["actor"] == "系统"


@pytest.mark.skipif(sys.platform != "linux", reason="Command sandbox runs in Docker")
def test_running_command_emits_progress_and_sanitized_result(tmp_path):
    from auto_spider.validators.candidate import _run_command

    events = []
    script = (
        "import time\nprint('first progress', flush=True)\n"
        "time.sleep(0.6)\nprint('Authorization: Bearer private-test-value', flush=True)\n"
    )
    with validation_activity(lambda message, detail: events.append((message, detail))):
        result = _run_command([sys.executable, "-c", script], tmp_path, timeout=10)
    assert result.status == "PASS"
    assert {event[1]["phase"] for event in events} == {"start", "output", "result"}
    output = "\n".join(event[1].get("output", "") for event in events)
    assert "first progress" in output
    assert "private-test-value" not in output
    assert "[REDACTED]" in output
