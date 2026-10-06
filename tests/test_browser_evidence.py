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


def test_browser_analyzer_derives_spec_from_captured_requests_and_bodies(tmp_path, monkeypatch):
    class FakeResult:
        status_code = 200
        request_count = 3
        response_count = 3

    class FakeCollector:
        def capture(self, entry_url, output_dir):
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "page.html").write_text("实习岗位", encoding="utf-8")
            (output_dir / "response-0.txt").write_text(
                json.dumps(
                    {
                        "data": {
                            "items": [
                                {
                                    "id": "17",
                                    "title": "后端实习生",
                                    "description": "参与服务开发",
                                    "requirements": "熟悉 Python",
                                }
                            ],
                            "total": 1,
                        }
                    }
                ),
                encoding="utf-8",
            )
            (output_dir / "response-1.txt").write_text(
                json.dumps(
                    {
                        "data": {
                            "id": "17",
                            "description": "参与服务开发",
                            "requirements": "熟悉 Python",
                        }
                    }
                ),
                encoding="utf-8",
            )
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
                                "url": "https://example.com/api/jobs?page=1",
                                "status": 200,
                                "content_type": "application/json",
                            },
                            {
                                "url": "https://example.com/api/job?id=17",
                                "status": 200,
                                "content_type": "application/json",
                            },
                        ],
                        "requests": [
                            {
                                "url": "https://example.com/api/jobs?page=1",
                                "method": "GET",
                                "post_data": "",
                            },
                            {
                                "url": "https://example.com/api/job?id=17",
                                "method": "GET",
                                "post_data": "",
                            },
                        ],
                        "response_bodies": [
                            {"index": 1, "path": "response-0.txt"},
                            {"index": 2, "path": "response-1.txt"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            return FakeResult()

    from auto_spider.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "evidence_root", tmp_path)
    result = BrowserAnalyzer(FakeCollector()).inspect(
        "https://example.com/jobs", "example", "task", "run"
    )
    draft = result.spec_draft
    assert draft is not None
    assert draft["endpoints"]["list"]["items_selector"]["expression"] == "$.data.items"
    assert draft["endpoints"]["list"]["query"]["page"] == "{{page}}"
    assert draft["endpoints"]["detail"]["url_template"] == "https://example.com/api/job"
    assert draft["endpoints"]["detail"]["query"] == {"id": "{{source_id}}"}
    assert any("response-1.txt" in ref for ref in draft["endpoints"]["detail"]["evidence_refs"])
    assert draft["fields"]["title"]["selectors"][0]["expression"] == "$.title"
    assert draft["fields"]["requirements"]["selectors"][0]["expression"] == "$.requirements"
    assert draft["fields"]["source_url"]["selectors"] == []
    assert "#list" not in json.dumps(draft) and "#detail" not in json.dumps(draft)
