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


def assess_collection_quality(records, *, pagination=None, now=None):
    now = now or datetime.now(UTC)
    findings, source_ids, content_hashes = [], {}, {}
    missing_time = 0

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
        label = " ".join(
            plain_text(job.get(k))
            for k in ("employment_type", "job_type", "category", "commitment")
        )
        positive = bool(re.search(r"实习|\bintern(?:ship)?\b", title + " " + label, re.I))
        negative = bool(re.search(r"非实习|不是实习|校招正式岗", title + " " + label))
        formal = bool(re.search(r"正式|全职|\bfull[- ]?time\b|\bpermanent\b", label, re.I))
        if negative or (formal and not positive):
            finding(
                "NON_INTERNSHIP_RECORD",
                "error",
                "internship_filter",
                [index],
                "岗位类型明确不属于实习",
                label or title,
            )
        elif not positive:
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
