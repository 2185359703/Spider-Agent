from copy import deepcopy
from datetime import UTC, datetime

from auto_spider.services.collection_quality import assess_collection_quality


def job(sid="1"):
    return {
        "source_id": sid,
        "source_url": "https://example.com/jobs/" + sid,
        "publish_time": None,
        "raw_content": {
            "job": {"title": "研发实习生", "description": "研发业务系统", "requirements": "在校生"}
        },
    }


def test_quality_keeps_missing_time_and_reports_body_duplicate_filter_time_and_pagination():
    records = [job(), job()]
    third = job("2")
    third["raw_content"]["job"].update(
        title="正式工程师",
        employment_type="Full time",
        description="<p>&nbsp;</p>",
        requirements="",
    )
    third["publish_time"] = 9999999999999
    records.append(third)
    before = deepcopy(records)
    result = assess_collection_quality(
        records, pagination={"scope_complete": True, "expected_total": 10}
    )
    codes = {f["code"] for f in result["findings"]}
    assert {
        "EMPTY_JOB_BODY",
        "DUPLICATE_SOURCE_ID",
        "NON_INTERNSHIP_RECORD",
        "FUTURE_PUBLISH_TIME",
        "PAGINATION_OMISSION",
    } <= codes
    assert result["status"] == "FAIL" and records == before


def test_unproved_pagination_is_partial_and_not_reported_complete():
    result = assess_collection_quality([job()])
    assert result["pagination_status"] == "PARTIAL" and result["status"] == "PARTIAL"
    assert result["metrics"]["missing_publish_time"] == 1


def test_millisecond_and_iso_dates_are_valid_and_international_is_not_intern():
    first = job()
    first["publish_time"] = 1780000000000
    second = job("2")
    second["raw_content"]["job"].update(title="International marketing", description="不同岗位正文")
    second["publish_time"] = "2026-09-29T09:30:00+08:00"
    result = assess_collection_quality(
        [first, second],
        pagination={"termination_verified": True},
        now=datetime(2026, 9, 30, tzinfo=UTC),
    )
    assert any(
        f["code"] == "INTERNSHIP_NOT_CONFIRMED" and f["sample_indices"] == [2]
        for f in result["findings"]
    )
    assert not any(f["code"] == "INVALID_PUBLISH_TIME" for f in result["findings"])


def test_quality_uses_spec_scope_and_rejects_duplicate_urls():
    first = job("custom-1")
    first["source_url"] = "https://example.com/jobs/shared"
    first["raw_content"]["job"].update(
        title="校园项目",
        description="暑期 intern 项目",
        requirements="在校生",
        employment_type="全职",
    )
    second = job("custom-2")
    second["source_url"] = first["source_url"]
    second["raw_content"]["job"].update(
        title="软件开发岗位",
        description="正式岗位",
        requirements="熟悉 Python",
        employment_type="正式",
    )
    result = assess_collection_quality(
        [first, second],
        filters={
            "internship": {
                "match_scope": ["title", "description", "requirements", "employment_type"],
                "include_keywords": ["intern"],
                "exclude_keywords": ["正式", "全职"],
            }
        },
        pagination={"termination_verified": True},
    )
    codes = {finding["code"] for finding in result["findings"]}
    assert {"DUPLICATE_SOURCE_URL", "NON_INTERNSHIP_RECORD"} <= codes


def test_explicit_internship_title_overrides_contradictory_formal_metadata():
    record = job("formal-metadata-intern")
    record["publish_time"] = 1780000000000
    record["raw_content"]["job"].update(
        title="机器学习算法实习生",
        employment_type="正式",
        recruitment_type="正式",
        job_type="internship",
        description="参与机器学习模型开发。",
        requirements="在校生优先。",
    )
    result = assess_collection_quality(
        [record],
        pagination={"termination_verified": True},
    )
    codes = {finding["code"] for finding in result["findings"]}
    assert "NON_INTERNSHIP_RECORD" not in codes
    assert result["status"] == "PASS"


def test_quality_reads_publish_time_from_nested_job_when_top_level_is_empty():
    record = job("nested-publish-time")
    record["publish_time"] = None
    record["raw_content"]["job"]["publish_time"] = "2026-09-29T09:30:00+08:00"
    result = assess_collection_quality(
        [record],
        pagination={"termination_verified": True},
        now=datetime(2026, 9, 30, tzinfo=UTC),
    )
    assert result["metrics"]["missing_publish_time"] == 0
    assert not any(f["code"] == "INVALID_PUBLISH_TIME" for f in result["findings"])


def test_quality_detects_duplicate_urls_after_tracking_parameter_normalization():
    first = job("tracking-1")
    first["source_url"] = "https://example.com/jobs/1?utm_source=mail&lang=zh"
    second = job("tracking-2")
    second["source_url"] = "https://EXAMPLE.com/jobs/1?lang=zh&utm_medium=campaign"
    result = assess_collection_quality(
        [first, second], pagination={"termination_verified": True}
    )
    assert any(f["code"] == "DUPLICATE_SOURCE_URL" for f in result["findings"])


def test_quality_honours_match_mode_all_and_must_match():
    record = job("all-keywords")
    record["raw_content"]["job"].update(
        title="研发实习生", description="参与后端开发", requirements="在校生"
    )
    result = assess_collection_quality(
        [record],
        filters={
            "internship": {
                "match_scope": ["title", "description"],
                "include_keywords": ["实习", "intern"],
                "match_mode": "all",
                "must_match": True,
            }
        },
        pagination={"termination_verified": True},
    )
    assert any(f["code"] == "INTERNSHIP_NOT_CONFIRMED" for f in result["findings"])
    result = assess_collection_quality(
        [record],
        filters={
            "internship": {
                "match_scope": ["title", "description"],
                "include_keywords": ["实习", "intern"],
                "match_mode": "all",
                "must_match": False,
            }
        },
        pagination={"termination_verified": True},
    )
    assert not any(f["code"] == "INTERNSHIP_NOT_CONFIRMED" for f in result["findings"])


def test_quality_cross_checks_pagination_diagnostics_against_output():
    first, second = job("diag-1"), job("diag-2")
    result = assess_collection_quality(
        [first, second],
        pagination={
            "termination_verified": True,
            "expected_total": "2",
            "unique_source_ids": 1,
            "record_count": 3,
        },
    )
    codes = {finding["code"] for finding in result["findings"]}
    assert {"PAGINATION_DIAGNOSTIC_MISMATCH", "PAGINATION_RECORD_COUNT_MISMATCH"} <= codes
    assert result["status"] == "FAIL"


def test_quality_reports_invalid_url_without_raising():
    record = job("bad-url")
    record["source_url"] = "https://[broken-host/jobs/1"
    result = assess_collection_quality([record], pagination={"termination_verified": True})
    assert any(f["code"] == "MISSING_SOURCE_URL" for f in result["findings"])
