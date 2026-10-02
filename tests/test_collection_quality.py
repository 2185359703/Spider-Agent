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
