import json

import pytest

from auto_spider.workflows.agent_site_analyzer import parse_analysis_response


def valid_response():
    return {
        "observation": {"observation_code": "NO_JOBS_OBSERVED"},
        "evidence_refs": ["task/run/entry.json"],
        "spec_draft": {
            "adapter": {},
            "endpoints": {},
            "pagination": {},
            "fields": {},
            "filters": {},
        },
        "list_method": "POST",
        "list_query": {},
        "list_body": {},
    }


def test_analysis_response_accepts_json_fence():
    parsed = parse_analysis_response("```json\n" + json.dumps(valid_response()) + "\n```")
    assert parsed["observation"]["observation_code"] == "NO_JOBS_OBSERVED"


@pytest.mark.parametrize(
    "mutate,error",
    [
        (lambda value: value.update(observation=[]), "OBSERVATION"),
        (lambda value: value.update(evidence_refs=[1]), "EVIDENCE_REFS"),
        (lambda value: value.update(spec_draft=[]), "SPEC_DRAFT"),
        (lambda value: value.update(list_method="DELETE"), "LIST_METHOD"),
        (lambda value: value.update(list_body=[]), "list_body"),
    ],
)
def test_analysis_response_rejects_bad_types(mutate, error):
    value = valid_response()
    mutate(value)
    with pytest.raises(ValueError, match=error):
        parse_analysis_response(json.dumps(value))


def test_analysis_response_rejects_invalid_json():
    with pytest.raises(ValueError, match="JSON_INVALID"):
        parse_analysis_response("{not json")
