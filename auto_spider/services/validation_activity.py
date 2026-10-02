"""Observable command activity, scoped to one workflow execution."""

import re
from contextlib import contextmanager
from contextvars import ContextVar

from auto_spider.services.browser_evidence import sanitize_text

_sink = ContextVar("validation_activity_sink", default=None)
_header_secret = re.compile(
    r"(?im)\b((?:authorization|cookie|x-csrf-token|api[_-]?key|access[_-]?token|password|secret)"
    r"\s*[\"']?\s*[:=]\s*)[^\r\n]+"
)


def sanitize_validation_text(value):
    return _header_secret.sub(r"\1[REDACTED]", sanitize_text(value))


def safe_detail(value):
    if isinstance(value, dict):
        return {key: safe_detail(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_detail(item) for item in value]
    if isinstance(value, str):
        return sanitize_validation_text(value)
    return value


@contextmanager
def validation_activity(sink):
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


def emit_validation(message, **detail):
    sink = _sink.get()
    if sink:
        sink(message, safe_detail({"actor": "系统", "action_type": "validation", **detail}))
