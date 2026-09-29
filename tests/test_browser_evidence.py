from auto_spider.services.browser_evidence import (
    sanitize_headers,
    sanitize_text,
    sanitize_url,
)


def test_evidence_sanitizers_remove_secrets() -> None:
    assert "signature=%5BREDACTED%5D" in sanitize_url(
        "https://example.com/jobs?signature=abc&limit=10"
    )
    assert (
        sanitize_headers({"Authorization": "Bearer secret", "Accept": "application/json"})[
            "Authorization"
        ]
        == "[REDACTED]"
    )
    assert "[REDACTED]" in sanitize_text('{"token":"secret"}')
