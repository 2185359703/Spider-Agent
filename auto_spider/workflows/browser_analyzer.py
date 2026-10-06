from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from auto_spider.config import get_settings
from auto_spider.services.browser_evidence import BrowserEvidenceCollector

from .types import AnalysisResult

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "source_id": (
        "id",
        "jobId",
        "job_id",
        "positionId",
        "position_id",
        "postId",
        "post_id",
        "sourceId",
        "code",
        "uuid",
    ),
    "title": (
        "title",
        "name",
        "jobTitle",
        "job_title",
        "positionName",
        "position_name",
        "postName",
        "post_name",
    ),
    "source_url": (
        "url",
        "detailUrl",
        "detail_url",
        "jobUrl",
        "job_url",
        "positionUrl",
        "position_url",
        "link",
    ),
    "location": ("location", "city", "workLocation", "work_location", "address"),
    "description": (
        "description",
        "jobDescription",
        "job_description",
        "jobDesc",
        "job_desc",
        "responsibility",
        "responsibilities",
        "content",
    ),
    "requirements": (
        "requirements",
        "requirement",
        "jobRequirements",
        "job_requirements",
        "jobRequire",
        "job_require",
        "qualifications",
        "qualification",
    ),
    "publish_time": (
        "publishTime",
        "publishedAt",
        "publish_time",
        "releaseTime",
        "release_time",
        "updateTime",
        "updatedAt",
        "createTime",
        "createdAt",
    ),
    "department": ("department", "dept", "departmentName", "deptName"),
    "employment_type": (
        "employmentType",
        "employment_type",
        "jobNature",
        "job_nature",
        "positionNature",
        "positionNatureCode",
        "type",
    ),
    "job_type": ("jobType", "job_type", "competencyType", "category", "categoryName"),
}

_JOB_KEYS = {key for names in _FIELD_ALIASES.values() for key in names}
_DETAIL_MARKERS = re.compile(r"(?:detail|info|post|position|job|recruit)", re.I)
_PAGE_KEYS = ("page", "pageNo", "page_num", "pageNum", "offset", "start")
_SIZE_KEYS = ("limit", "pageSize", "page_size", "size", "count")
_CURSOR_KEYS = ("cursor", "nextCursor", "next_cursor", "continuationToken")


def _path_child(path: str, key: str) -> str:
    """Return a JSONPath for a normal object key without inventing Python code."""

    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        return f"{path}.{key}"
    return f"{path}['{key.replace(chr(39), chr(92) + chr(39))}']"


def _walk_json(value: Any, path: str = "$"):
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk_json(child, _path_child(path, str(key)))
    elif isinstance(value, list):
        for child in value[:100]:
            yield from _walk_json(child, f"{path}[*]")


def _json_body(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        value = json.loads(text)
    except (OSError, ValueError):
        return None
    if isinstance(value, dict) and "response" in value and ("action" in value or "output" in value):
        return value["response"]
    return value


def _value_present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _list_candidate(payload: Any) -> tuple[str, list[dict[str, Any]]] | None:
    candidates: list[tuple[int, str, list[dict[str, Any]]]] = []
    for path, value in _walk_json(payload):
        if not isinstance(value, list) or not value:
            continue
        records = [item for item in value[:20] if isinstance(item, dict)]
        if not records or len(records) < max(1, len(value[:20]) // 2):
            continue
        keys = set().union(*(item.keys() for item in records))
        matched = keys & _JOB_KEYS
        if not matched:
            continue
        score = len(matched) * 10 + min(len(records), 10)
        if "id" in keys or keys & {"jobId", "positionId", "postId", "sourceId"}:
            score += 12
        if keys & {"title", "name", "jobTitle", "positionName", "postName"}:
            score += 12
        # Prefer a shallow business list over a nested list of tags or cities.
        score -= path.count("[*]")
        candidates.append((score, path, records))
    if not candidates:
        return None
    _, path, records = max(candidates, key=lambda item: item[0])
    return path, records


def _field_key(records: list[dict[str, Any]], names: tuple[str, ...]) -> str | None:
    keys = set().union(*(record.keys() for record in records))
    for name in names:
        if name in keys and any(_value_present(record.get(name)) for record in records):
            return name
    return None


def _selector(field_name: str, key: str, evidence: list[str], *, source="list_item"):
    return {
        "source": source,
        "kind": "json_path",
        "expression": f"$.{key}",
        "multiple": isinstance(key, str) and key.endswith("[]"),
        "confidence": "high",
        "inferred": False,
        "evidence_refs": list(dict.fromkeys(evidence)),
        "transforms": ["to_text", "normalize_text"]
        if field_name in {"title", "description", "requirements"}
        else [],
    }


def _replace_request_values(
    value: Any, *, page_key: str | None, size_key: str | None, cursor_key: str | None
):
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            key_text = str(key)
            if page_key and key_text == page_key:
                result[key] = "{{page}}" if page_key.lower().startswith("page") else "{{offset}}"
            elif size_key and key_text == size_key:
                result[key] = "{{page_size}}"
            elif cursor_key and key_text == cursor_key:
                result[key] = "{{cursor}}"
            else:
                result[key] = _replace_request_values(
                    child,
                    page_key=page_key,
                    size_key=size_key,
                    cursor_key=cursor_key,
                )
        return result
    if isinstance(value, list):
        return [
            _replace_request_values(
                child,
                page_key=page_key,
                size_key=size_key,
                cursor_key=cursor_key,
            )
            for child in value
        ]
    return value


def _request_pagination(request: dict[str, Any] | None):
    if not request:
        return None, None, None, None
    query = request.get("query") if isinstance(request.get("query"), dict) else {}
    body = {}
    if isinstance(request.get("post_data"), str):
        try:
            parsed = json.loads(request["post_data"])
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            body = parsed
    keys = set(query) | set(body)
    page_key = next((key for key in _PAGE_KEYS if key in keys), None)
    cursor_key = next((key for key in _CURSOR_KEYS if key in keys), None)
    size_key = next((key for key in _SIZE_KEYS if key in keys), None)
    if cursor_key:
        mode = "cursor"
    elif page_key and str(page_key).lower() in {"offset", "start"}:
        mode = "offset"
    elif page_key:
        mode = "page"
    else:
        mode = "none"
    return mode, page_key, size_key, cursor_key


def _detail_candidate(
    responses: list[dict[str, Any]],
    requests: list[dict[str, Any]],
    list_url: str | None,
    body_by_index: dict[int, Any],
    list_records: list[dict[str, Any]],
):
    list_ids = {
        str(record.get(name))
        for name in _FIELD_ALIASES["source_id"]
        for record in list_records
        if _value_present(record.get(name))
    }
    for index, item in enumerate(responses):
        url = item.get("url")
        if not isinstance(url, str) or not url or url == list_url:
            continue
        if "json" not in str(item.get("content_type", "")).lower():
            continue
        if not _DETAIL_MARKERS.search(url):
            continue
        payload = body_by_index.get(index)
        if not isinstance(payload, dict):
            continue
        keys = set().union(
            *(node.keys() for _, node in _walk_json(payload) if isinstance(node, dict))
        )
        if not keys & (_JOB_KEYS | {"description", "requirements", "jobRequire"}):
            continue
        request = next((candidate for candidate in requests if candidate.get("url") == url), None)
        parsed = urlsplit(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        dynamic = False
        for key, value in list(query.items()):
            if str(value) in list_ids or key.lower() in {"id", "jobid", "positionid", "postid"}:
                query[key] = "{{source_id}}"
                dynamic = True
        body = None
        method = str((request or {}).get("method") or "GET").upper()
        if request and isinstance(request.get("post_data"), str):
            try:
                parsed_body = json.loads(request["post_data"])
            except ValueError:
                parsed_body = None
            if isinstance(parsed_body, dict):
                body = parsed_body
                for key, value in list(body.items()):
                    if str(value) in list_ids or key.lower() in {
                        "id",
                        "jobid",
                        "positionid",
                        "postid",
                    }:
                        body[key] = "{{source_id}}"
                        dynamic = True
        if not dynamic:
            # A fixed detail URL would cause every generated record to fetch the
            # same job. Keep it out of the Spec until a real ID binding is seen.
            continue
        clean_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        return {
            "method": method if method in {"GET", "POST", "PUT", "PATCH"} else "GET",
            "url_template": clean_url,
            "query": query,
            "body": body or {},
            "response_format": "json",
            "confidence": "medium",
            "inferred": True,
            "evidence_refs": [],
        }, url
    return None, None


class BrowserAnalyzer:
    """Turn bounded Playwright evidence into a conservative analysis result."""

    def __init__(self, collector: BrowserEvidenceCollector | None = None) -> None:
        self.collector = collector

    def inspect(
        self,
        entry_url: str,
        platform_key: str,
        task_id: str | None = None,
        run_id: str | None = None,
        execution=None,
        event_callback=None,
    ) -> AnalysisResult:
        settings = get_settings()
        if self.collector is None and execution is not None and settings.agent_browser_enabled:
            from auto_spider.workflows.agent_site_analyzer import inspect_with_agent

            return inspect_with_agent(
                entry_url, platform_key, task_id, run_id, execution, event_callback
            )
        collector = self.collector or BrowserEvidenceCollector()
        evidence_root = settings.evidence_root / (task_id or "browser") / (run_id or "run")
        result = collector.capture(entry_url, evidence_root / "browser")
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
        list_endpoint_observed = list_response["url"] if list_response else None
        if list_endpoint_observed:
            parsed_list_url = urlsplit(list_endpoint_observed)
            list_endpoint = urlunsplit(
                (parsed_list_url.scheme, parsed_list_url.netloc, parsed_list_url.path, "", "")
            )
        else:
            list_endpoint = None
        list_index = (
            next(
                (
                    index
                    for index, item in enumerate(network.get("responses", []))
                    if item.get("url") == list_endpoint_observed
                ),
                None,
            )
            if list_endpoint
            else None
        )
        request = next(
            (
                item
                for item in network.get("requests", [])
                if item.get("url") == list_endpoint_observed
            ),
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
        body_by_index: dict[int, Any] = {}
        for body_ref in network.get("response_bodies", []):
            index = body_ref.get("index")
            if not isinstance(index, int):
                continue
            body_path = network_path.parent / str(body_ref.get("path") or "")
            payload = _json_body(body_path) if body_path.is_file() else None
            body_by_index[index] = payload
            if index != list_index:
                continue
            response_file = body_ref.get("path")
            if response_file:
                body_payload = payload
                if isinstance(body_payload, dict):
                    if isinstance(body_payload.get("data"), str):
                        response_shape = "encrypted_data_field"
                    elif isinstance(body_payload.get("data"), dict):
                        response_shape = "data_object"
                    else:
                        response_shape = "object"
        network_ref = str(network_path.relative_to(settings.evidence_root)).replace("\\", "/")
        page_ref = str(
            (evidence_root / "browser" / "page.html").relative_to(settings.evidence_root)
        ).replace("\\", "/")
        list_payload = body_by_index.get(list_index) if list_index is not None else None
        list_candidate = _list_candidate(list_payload)
        list_items_path, list_records = list_candidate or (None, [])
        body_requests = network.get("requests", [])
        request_query: dict[str, Any] = {}
        if request and isinstance(request.get("url"), str):
            request_query = dict(parse_qsl(urlsplit(request["url"]).query, keep_blank_values=True))
        pagination_source = dict(request or {})
        pagination_source["query"] = request_query
        pagination_mode, pagination_param, size_param, cursor_param = _request_pagination(
            pagination_source
        )
        detail_spec, detail_url = _detail_candidate(
            network.get("responses", []),
            body_requests,
            list_endpoint_observed,
            body_by_index,
            list_records,
        )
        detail_index = next(
            (
                index
                for index, item in enumerate(network.get("responses", []))
                if detail_url and item.get("url") == detail_url
            ),
            None,
        )
        detail_payload = body_by_index.get(detail_index) if detail_index is not None else None
        detail_record_path = "$"
        detail_record: dict[str, Any] = {}
        if isinstance(detail_payload, dict):
            detail_candidates = [
                (path, value)
                for path, value in _walk_json(detail_payload)
                if isinstance(value, dict) and value.keys() & _JOB_KEYS
            ]
            if detail_candidates:
                detail_record_path, detail_record = max(
                    detail_candidates,
                    key=lambda item: len(item[1].keys() & _JOB_KEYS),
                )
        list_refs = [network_ref]
        if response_file:
            list_refs.append(
                str(
                    (evidence_root / "browser" / response_file).relative_to(settings.evidence_root)
                ).replace("\\", "/")
            )
        detail_refs = [network_ref]
        for body_ref in network.get("response_bodies", []):
            if body_ref.get("index") != detail_index or not body_ref.get("path"):
                continue
            detail_refs.append(
                str(
                    (evidence_root / "browser" / str(body_ref["path"])).relative_to(
                        settings.evidence_root
                    )
                ).replace("\\", "/")
            )
            break
        field_draft: dict[str, Any] = {}
        for field_name, aliases in _FIELD_ALIASES.items():
            key = _field_key(list_records, aliases)
            selectors = []
            if key and list_candidate:
                selectors.append(_selector(field_name, key, list_refs))
            if not selectors and detail_record:
                detail_key = _field_key([detail_record], aliases)
                if detail_key:
                    selectors.append(
                        {
                            **_selector(field_name, detail_key, detail_refs, source="detail"),
                            "expression": _path_child(detail_record_path, detail_key),
                        }
                    )
            field_draft[field_name] = {
                "required": field_name in {"source_id", "title", "source_url"},
                "selectors": selectors,
            }
        list_query = request_query
        list_body = request_body
        replaced_query = _replace_request_values(
            list_query,
            page_key=pagination_param,
            size_key=size_param,
            cursor_key=cursor_param,
        )
        replaced_body = _replace_request_values(
            list_body or {},
            page_key=pagination_param,
            size_key=size_param,
            cursor_key=cursor_param,
        )
        pagination_evidence = list_refs if pagination_mode != "none" else []
        pagination_draft = {
            "mode": pagination_mode,
            "page_param": pagination_param if pagination_mode in {"page", "offset"} else None,
            "size_param": size_param,
            "cursor_param": cursor_param if pagination_mode == "cursor" else None,
            "page_start": 1,
            "page_size": 50,
            "max_pages": 50,
            "termination": ["max_pages"] if pagination_mode != "none" else [],
            "evidence_refs": pagination_evidence,
            "confidence": "low" if pagination_mode != "none" else "low",
            "inferred": pagination_mode != "none",
        }
        if size_param:
            original_size = (list_query or {}).get(size_param)
            if original_size is None and list_body:
                original_size = list_body.get(size_param)
            if isinstance(original_size, int) and original_size > 0:
                pagination_draft["page_size"] = min(original_size, 500)
        filters = {
            "internship": {
                "match_scope": [
                    field_name
                    for field_name in ("title", "employment_type", "description", "requirements")
                    if field_draft[field_name]["selectors"]
                ],
                "include_keywords": ["实习", "intern", "internship"],
                "exclude_keywords": [],
                "match_mode": "any",
                "must_match": True,
                "confidence": "low",
                "inferred": True,
                "inference_reason": (
                    "入口页面或岗位响应出现实习关键词，尚未验证筛选条件的完整语义。"
                ),
                "evidence_refs": [page_ref, *list_refs],
            },
            "location": {"include": [], "exclude": []},
        }
        spec_draft = {
            "adapter": {
                "family": "custom_http",
                "runtime_mode": "http",
                "browser_required_for_discovery": True,
                "browser_required_for_runtime": False,
                "collector_template": "custom_http",
                "version": "1.0",
            },
            "endpoints": {
                "list": (
                    {
                        "method": str((request or {}).get("method") or "GET").upper(),
                        "url_template": list_endpoint,
                        "query": replaced_query,
                        "body": replaced_body,
                        "response_format": "json",
                        "items_selector": {
                            "source": "response_body",
                            "kind": "json_path",
                            "expression": list_items_path,
                            "multiple": True,
                            "confidence": "high",
                            "inferred": False,
                            "evidence_refs": list_refs,
                        }
                        if list_items_path
                        else None,
                        "confidence": "high" if list_items_path else "low",
                        "inferred": False,
                        "evidence_refs": list_refs,
                    }
                    if list_endpoint
                    else None
                ),
                "detail": detail_spec,
                "related": [],
            },
            "fields": field_draft,
            "pagination": pagination_draft,
            "filters": filters,
        }
        if detail_spec:
            detail_spec["evidence_refs"] = detail_refs
        # A single capture cannot prove a page-based protocol.  Do not invent
        # ``page=1`` (or a detail URL) when the browser did not expose it.
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
                "detail_found": bool(detail_spec),
                "pagination_verified": False if pagination_mode != "none" else None,
                "list_count": len(list_records) if list_records else None,
                "internship_count": (
                    sum(
                        1
                        for item in list_records
                        if re.search(
                            r"(实习|intern|internship)",
                            json.dumps(item, ensure_ascii=False),
                            re.I,
                        )
                    )
                    if list_records
                    else None
                ),
                "valid_record_count": 0,
                "browser_status_code": result.status_code,
                "browser_request_count": result.request_count,
                "browser_response_count": result.response_count,
                "list_method": (request or {}).get("method", "GET"),
                "list_query": list_query,
                "list_body": list_body,
                "list_response_file": response_file,
                "list_response_shape": response_shape,
                "pagination_mode": pagination_mode,
                "pagination_param": pagination_param,
                "size_param": size_param,
                "spec_draft": spec_draft,
            },
            list_endpoint=list_endpoint,
            detail_endpoint=detail_url,
            evidence_refs=[
                network_ref,
                page_ref,
            ],
            list_method=(request or {}).get("method", "GET"),
            list_query=list_query,
            list_body=request_body,
            list_response_file=response_file,
            list_response_shape=response_shape,
            spec_draft=spec_draft,
        )
