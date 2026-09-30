from __future__ import annotations

import json
import re
from typing import Any

from auto_spider.config import get_settings
from auto_spider.services.browser_evidence import BrowserEvidenceCollector

from .types import AnalysisResult


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
    ) -> AnalysisResult:
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

        ranked_responses = sorted(candidate_responses, key=endpoint_score, reverse=True)
        job_urls = [item["url"] for item in ranked_responses]
        list_response = ranked_responses[0] if ranked_responses else None
        list_endpoint = list_response["url"] if list_response else None
        list_index = (
            next(
                (
                    index
                    for index, item in enumerate(network.get("responses", []))
                    if item.get("url") == list_endpoint
                ),
                None,
            )
            if list_endpoint
            else None
        )
        request = next(
            (item for item in network.get("requests", []) if item.get("url") == list_endpoint),
            None,
        )
        request_body: dict[str, Any] | None = None
        if request and request.get("post_data"):
            try:
                parsed_body = json.loads(request["post_data"])
            except (TypeError, ValueError):
                parsed_body = None
            if isinstance(parsed_body, dict):
                request_body = parsed_body
        response_file = None
        response_shape = None
        for body_ref in network.get("response_bodies", []):
            if body_ref.get("index") != list_index:
                continue
            response_file = body_ref.get("path")
            if response_file:
                body_path = network_path.parent / response_file
                try:
                    body_payload = json.loads(body_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    body_payload = None
                if isinstance(body_payload, dict):
                    if isinstance(body_payload.get("data"), str):
                        response_shape = "encrypted_data_field"
                    elif isinstance(body_payload.get("data"), dict):
                        response_shape = "data_object"
                    else:
                        response_shape = "object"
            break
        pagination_mode = "offset" if request_body and "offset" in request_body else "page"
        pagination_param = "offset" if pagination_mode == "offset" else "page"
        size_param = "limit" if request_body and "limit" in request_body else None
        list_found = bool(job_urls) or bool(
            re.search(r"(职位|岗位|实习|intern|recruit)", page_html, re.IGNORECASE)
        )
        internship_signal = bool(re.search(r"(实习|intern|internship)", page_html, re.IGNORECASE))
        observation_code = (
            "INTERNSHIPS_FOUND"
            if internship_signal
            else ("NO_INTERNSHIPS_OBSERVED" if list_found else "NO_JOB_LIST_FOUND")
        )
        return AnalysisResult(
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
                "list_method": (request or {}).get("method", "GET"),
                "list_query": (request or {}).get("query", {}),
                "list_body": request_body,
                "list_response_file": response_file,
                "list_response_shape": response_shape,
                "pagination_mode": pagination_mode,
                "pagination_param": pagination_param,
                "size_param": size_param,
            },
            list_endpoint=list_endpoint,
            detail_endpoint=None,
            evidence_refs=[
                str(network_path.relative_to(settings.evidence_root)).replace("\\", "/"),
                str(
                    (evidence_root / "browser" / "page.html").relative_to(settings.evidence_root)
                ).replace("\\", "/"),
            ],
            list_method=(request or {}).get("method", "GET"),
            list_query=(request or {}).get("query", {}),
            list_body=request_body,
            list_response_file=response_file,
            list_response_shape=response_shape,
        )
