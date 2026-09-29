import json

from auto_spider.services.browser_evidence import (
    sanitize_headers,
    sanitize_text,
    sanitize_url,
)
from auto_spider.workflows.browser_analyzer import BrowserAnalyzer


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


def test_browser_analyzer_ignores_static_assets(tmp_path, monkeypatch) -> None:
    class FakeResult:
        status_code = 200
        request_count = 2
        response_count = 2

    class FakeCollector:
        def capture(self, entry_url, output_dir):
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "page.html").write_text("实习招聘", encoding="utf-8")
            (output_dir / "network.json").write_text(
                json.dumps(
                    {
                        "responses": [
                            {
                                "url": "https://example.com/assets/main.css",
                                "status": 200,
                                "content_type": "text/css",
                            },
                            {
                                "url": "https://example.com/api/jobs",
                                "status": 200,
                                "content_type": "application/json",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            return FakeResult()

    from auto_spider.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "evidence_root", tmp_path)
    result = BrowserAnalyzer(FakeCollector()).inspect(
        "https://example.com/jobs",
        "example",
        "task",
        "run",
    )
    assert result.list_endpoint == "https://example.com/api/jobs"
