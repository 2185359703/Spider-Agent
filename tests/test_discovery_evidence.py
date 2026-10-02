import json

import pytest

from auto_spider.services.discovery_evidence import verify_discovery_draft


def test_high_confidence_requires_real_selector_values(tmp_path):
    ref = "body.json"
    (tmp_path / ref).write_text(
        json.dumps(
            {
                "output": "POST https://example.test/jobs",
                "response": {"data": {"jobs": [{"jobTitle": "研发实习", "jobId": "17"}]}},
            }
        )
    )

    def selector(path):
        return {
            "source": "list_item",
            "kind": "json_path",
            "expression": path,
            "confidence": "high",
            "evidence_refs": [ref],
        }

    draft = {
        "endpoints": {
            "list": {
                "url_template": "https://example.test/jobs",
                "evidence_refs": [ref],
                "items_selector": {"expression": "$.data.jobs"},
            }
        },
        "fields": {
            "title": {"selectors": [selector("$.jobTitle")]},
            "source_id": {"selectors": [selector("$.id")]},
        },
    }
    result = verify_discovery_draft(draft, [ref], tmp_path)
    assert result["fields"]["title"]["selectors"][0]["confidence"] == "high"
    assert result["fields"]["source_id"]["selectors"][0]["confidence"] == "low"
    assert draft["fields"]["source_id"]["selectors"][0]["confidence"] == "high"
    draft["fields"]["title"]["selectors"][0]["evidence_refs"] = ["other-task/body.json"]
    with pytest.raises(ValueError, match="EVIDENCE_UNCONFIRMED"):
        verify_discovery_draft(draft, [ref], tmp_path)


@pytest.mark.parametrize(
    "draft,error",
    [
        ({"endpoints": True}, "endpoints"),
        ({"endpoints": {"list": []}}, "endpoints.list"),
        ({"endpoints": {"list": {"items_selector": []}}}, "items_selector"),
        ({"endpoints": {"related": [False]}}, "related"),
        ({"fields": {"title": True}}, "fields.title"),
        ({"fields": {"title": {"selectors": {}}}}, "selectors"),
        ({"fields": {"title": {"selectors": [False]}}}, "selectors 元素"),
        ({"pagination": []}, "pagination"),
        ({"filters": {"internship": []}}, "filters.internship"),
    ],
)
def test_malformed_draft_returns_structured_error(tmp_path, draft, error):
    with pytest.raises(ValueError, match="ANALYSIS_SPEC_TYPE") as raised:
        verify_discovery_draft(draft, [], tmp_path)
    assert error in str(raised.value)
