from __future__ import annotations

import json
import re

from auto_spider.config import get_settings
from auto_spider.services.browser_evidence import BrowserEvidenceCollector

from .fakes import FakeAnalysis


class BrowserAnalyzer:
    """Turn bounded Playwright evidence into a conservative analysis result."""

    def __init__(self, collector: BrowserEvidenceCollector | None = None) -> None:
        self.collector = collector or BrowserEvidenceCollector()

    def inspect(
        self,
        entry_url: str,
        platform_key: str,
        task_id: str | None = None,
        run_id: str | None = None,
    ) -> FakeAnalysis:
        settings = get_settings()
        evidence_root = settings.evidence_root / (task_id or "browser") / (run_id or "run")
        result = self.collector.capture(entry_url, evidence_root / "browser")
        network_path = evidence_root / "browser" / "network.json"
        network = json.loads(network_path.read_text(encoding="utf-8"))
        page_html = (evidence_root / "browser" / "page.html").read_text(
            encoding="utf-8",
            errors="replace",
        )
        response_urls = [
            item for item in network.get("responses", []) if int(item.get("status") or 0) == 200
        ]
        candidate_responses = [
            item
            for item in response_urls
            if "json" in item.get("content_type", "").lower()
            and re.search(r"(job|position|recruit|career|api)", item["url"], re.IGNORECASE)
        ]

        def endpoint_score(item: dict) -> int:
            url = item["url"].lower()
            score = 0
            if any(
                marker in url
                for marker in (
                    "positions/simple",
                    "search/job/posts",
                    "/jobs",
                    "/positions",
                )
            ):
                score += 10
            if "/api/" in url:
                score += 2
            if any(marker in url for marker in ("check/city", "dictionary", "setting", "location")):
                score -= 5
            return score

        job_urls = [
            item["url"] for item in sorted(candidate_responses, key=endpoint_score, reverse=True)
        ]
        list_found = bool(job_urls) or bool(
            re.search(r"(职位|岗位|实习|intern|recruit)", page_html, re.IGNORECASE)
        )
        internship_signal = bool(re.search(r"(实习|intern|internship)", page_html, re.IGNORECASE))
        observation_code = (
            "INTERNSHIPS_FOUND"
            if internship_signal
            else ("NO_INTERNSHIPS_OBSERVED" if list_found else "NO_JOB_LIST_FOUND")
        )
        return FakeAnalysis(
            observation={
                "observation_code": observation_code,
                "list_found": list_found,
                "detail_found": None,
                "pagination_verified": None,
                "list_count": None,
                "internship_count": None,
                "valid_record_count": 0,
                "browser_status_code": result.status_code,
                "browser_request_count": result.request_count,
                "browser_response_count": result.response_count,
            },
            list_endpoint=job_urls[0] if job_urls else None,
            detail_endpoint=None,
            evidence_refs=[
                str(network_path.relative_to(settings.evidence_root)).replace("\\", "/"),
                str(
                    (evidence_root / "browser" / "page.html").relative_to(settings.evidence_root)
                ).replace("\\", "/"),
            ],
        )
