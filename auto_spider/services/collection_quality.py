"""Deterministic quality findings; retain source records and explicit evidence gaps."""

import hashlib
import html
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


def plain_text(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = "\n".join(str(item) for item in value)
    value = re.sub(r"<[^>]*>", "", html.unescape(str(value))).replace("\xa0", " ")
    return "\n".join(" ".join(line.split()) for line in value.splitlines() if line.strip())


def published_datetime(value):
    if value in (None, "", 0, "0"):
        return None
    if isinstance(value, (int, float)) or str(value).strip().isdigit():
        stamp = float(value)
        return datetime.fromtimestamp(stamp / 1000 if stamp > 1e12 else stamp, UTC)
    parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))


def assess_collection_quality(records, *, pagination=None, filters=None, now=None):
    """Check a collection against the deterministic PlatformSpec rules.

    ``filters`` is optional for backwards compatibility with old collectors;
    when supplied it is the serialized ``PlatformSpec.filters`` object.  The
    check deliberately keeps the original records and reports findings so a
    reviewer can inspect the source evidence instead of silently dropping a
    questionable job.
    """
    now = now or datetime.now(UTC)
    findings, source_ids, content_hashes = [], {}, {}
    source_urls = {}
    missing_time = 0

    internship = (filters or {}).get("internship", {}) if isinstance(filters, dict) else {}
    include_keywords = [
        str(value).strip().lower()
        for value in internship.get("include_keywords", ["实习", "intern", "internship"])
        if str(value).strip()
    ]
    exclude_keywords = [
        str(value).strip().lower()
        for value in internship.get("exclude_keywords", ["正式", "全职", "校招正式岗"])
        if str(value).strip()
    ]
    match_scope = internship.get(
        "match_scope", ["title", "employment_type", "category", "description", "requirements"]
    )

    def finding(code, severity, field, indices, message, actual=None, expected=None):
        findings.append(
            {
                "code": code,
                "severity": severity,
                "field": field,
                "sample_indices": indices,
                "message": message,
                "actual": actual,
                "expected": expected,
            }
        )

    for index, record in enumerate(records, 1):
        if not isinstance(record, dict):
            finding("INVALID_RECORD", "error", "other", [index], "岗位记录不是对象")
            continue
        raw = record.get("raw_content") or {}
        job = (
            raw.get("job") if isinstance(raw, dict) and isinstance(raw.get("job"), dict) else record
        )
        title, description, requirements = (
            plain_text(job.get(k)) for k in ("title", "description", "requirements")
        )
        sid = str(record.get("source_id") or job.get("source_id") or "").strip()
        url = record.get("source_url") or job.get("source_url")
        if not sid:
            finding("MISSING_SOURCE_ID", "error", "source_id", [index], "岗位来源 ID 缺失")
        elif sid in source_ids:
            finding(
                "DUPLICATE_SOURCE_ID",
                "error",
                "source_id",
                [source_ids[sid], index],
                "同一岗位在本轮重复出现",
                sid,
            )
        else:
            source_ids[sid] = index
        if not title:
            finding("MISSING_TITLE", "error", "title", [index], "岗位标题缺失")
        if (
            not url
            or urlsplit(str(url)).scheme not in {"http", "https"}
            or not urlsplit(str(url)).hostname
        ):
            finding("MISSING_SOURCE_URL", "error", "source_url", [index], "岗位详情链接缺失或无效")
        else:
            normalized_url = str(url).strip()
            if normalized_url in source_urls:
                finding(
                    "DUPLICATE_SOURCE_URL",
                    "error",
                    "source_url",
                    [source_urls[normalized_url], index],
                    "同一详情链接在本轮重复出现",
                    normalized_url,
                )
            else:
                source_urls[normalized_url] = index
        if not description and not requirements:
            finding("EMPTY_JOB_BODY", "error", "description", [index], "岗位描述和要求均无正文")
        digest = hashlib.md5(f"{title}\n{description}\n{requirements}".encode()).hexdigest()
        if title and (description or requirements):
            if digest in content_hashes and sid != content_hashes[digest][0]:
                finding(
                    "DUPLICATE_CONTENT",
                    "warning",
                    "other",
                    [content_hashes[digest][1], index],
                    "不同来源 ID 的标题和正文相同，需要核对",
                )
            else:
                content_hashes[digest] = (sid, index)
        scope_values = {
            "title": title,
            "employment_type": plain_text(job.get("employment_type")),
            "job_type": plain_text(job.get("job_type")),
            "category": plain_text(job.get("category")),
            "commitment": plain_text(job.get("commitment")),
            "description": description,
            "requirements": requirements,
        }
        match_text = " ".join(
            scope_values.get(str(key), "") for key in match_scope if str(key) in scope_values
        ).lower()
        type_text = " ".join(
            scope_values.get(key, "")
            for key in ("employment_type", "job_type", "category", "commitment")
        ).lower()
        def contains_keyword(text, keyword):
            if re.fullmatch(r"[a-z0-9][a-z0-9 _-]*", keyword):
                return bool(re.search(rf"\b{re.escape(keyword)}\b", text, re.I))
            return keyword in text

        positive = any(contains_keyword(match_text, keyword) for keyword in include_keywords)
        # The job title is the strongest business signal for an internship
        # collection. Some recruiting systems expose contradictory metadata
        # such as title="算法实习生" with employment_type="正式". Keep such a
        # record in the requested dataset instead of rejecting it because of
        # the platform's generic employment field.
        title_positive = any(
            contains_keyword(title.lower(), keyword) for keyword in include_keywords
        )
        negative = any(contains_keyword(match_text, keyword) for keyword in exclude_keywords)
        formal = bool(
            re.search(r"正式|全职|\bfull[- ]?time\b|\bpermanent\b", type_text, re.I)
        )
        if (negative or formal) and not title_positive:
            finding(
                "NON_INTERNSHIP_RECORD",
                "error",
                "internship_filter",
                [index],
                "岗位类型明确不属于实习",
                match_text or title,
            )
        elif not positive and not title_positive:
            finding(
                "INTERNSHIP_NOT_CONFIRMED",
                "warning",
                "internship_filter",
                [index],
                "标题和岗位类型尚未确认实习属性",
            )
        value = record.get("publish_time", job.get("publish_time"))
        try:
            published = published_datetime(value)
            if published is None:
                missing_time += 1
            elif published > now + timedelta(days=1):
                finding(
                    "FUTURE_PUBLISH_TIME",
                    "error",
                    "publish_time",
                    [index],
                    "发布时间超过当前时间一天",
                    str(value),
                )
            elif published < datetime(2000, 1, 1, tzinfo=UTC):
                finding(
                    "INVALID_PUBLISH_TIME",
                    "error",
                    "publish_time",
                    [index],
                    "发布时间不在合理范围内",
                    str(value),
                )
        except (ValueError, TypeError, OverflowError, OSError):
            finding(
                "INVALID_PUBLISH_TIME",
                "error",
                "publish_time",
                [index],
                "发布时间格式无法解析",
                str(value),
            )
    if missing_time:
        finding(
            "MISSING_PUBLISH_TIME",
            "warning",
            "publish_time",
            [],
            f"{missing_time} 条岗位未提供发布时间",
        )
    pagination = pagination or {}
    fingerprints = pagination.get("page_fingerprints")
    if isinstance(fingerprints, list):
        seen = {}
        repeated = []
        for page_index, fingerprint in enumerate(fingerprints, 1):
            if fingerprint in seen:
                repeated.extend([seen[fingerprint], page_index])
            else:
                seen[fingerprint] = page_index
        if repeated:
            finding(
                "PAGINATION_REPEATED_PAGE",
                "error",
                "pagination",
                sorted(set(repeated)),
                "分页响应指纹重复，可能遗漏或重复采集",
            )
    pagination_status = "PARTIAL"
    expected = pagination.get("expected_total")
    if pagination.get("scope_complete") is True and isinstance(expected, int):
        pagination_status = "PASS" if len(source_ids) >= expected else "FAIL"
        if pagination_status == "FAIL":
            finding(
                "PAGINATION_OMISSION",
                "error",
                "pagination",
                [],
                "完整范围内采集数量少于接口总数",
                len(source_ids),
                expected,
            )
    elif pagination.get("termination_verified") is True:
        pagination_status = "PASS"
    else:
        finding(
            "PAGINATION_EVIDENCE_INSUFFICIENT",
            "warning",
            "pagination",
            [],
            "分页终止或采集范围尚未得到验证",
        )
    errors = [f for f in findings if f["severity"] == "error"]
    return {
        "status": "FAIL"
        if errors
        else "PARTIAL"
        if any(f["code"] != "MISSING_PUBLISH_TIME" for f in findings)
        else "PASS",
        "pagination_status": pagination_status,
        "findings": findings,
        "metrics": {
            "record_count": len(records),
            "unique_source_ids": len(source_ids),
            "error_count": len(errors),
            "warning_count": len(findings) - len(errors),
            "missing_publish_time": missing_time,
        },
    }
