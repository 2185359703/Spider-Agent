import json

from auto_spider.config import get_settings
from auto_spider.workflows.agent_site_analyzer import parse_analysis


def test_parse_analysis_aliases_exact_short_refs(tmp_path, monkeypatch):
    root = tmp_path / "evidence"
    task = "a" * 32
    actual = root / task / "run" / "agent-browser" / ("b" * 32) / "0002-snapshot.json"
    actual.parent.mkdir(parents=True)
    actual.write_text(json.dumps({"action": "snapshot", "output": "real page"}), "utf-8")
    settings = get_settings()
    monkeypatch.setattr(settings, "evidence_root", root)
    response = {
        "observation": {"observation_code": "INCONCLUSIVE"},
        "evidence_refs": [f"{task}/run/agent-browser/{'b' * 32}/0002-snapshot.json"],
        "spec_draft": {
            "fields": {
                "title": {
                    "selectors": [
                        {
                            "source": "detail",
                            "kind": "css",
                            "expression": "h1",
                            "confidence": "low",
                            "evidence_refs": ["0002-snapshot.json"],
                        }
                    ]
                }
            },
            "endpoints": {},
            "pagination": {},
            "filters": {},
            "adapter": {"family": "unknown"},
        },
    }
    result = parse_analysis(json.dumps(response), settings, task)
    ref = f"{task}/run/agent-browser/{'b' * 32}/0002-snapshot.json"
    assert result.evidence_refs == [ref]
    assert result.spec_draft["fields"]["title"]["selectors"][0]["evidence_refs"] == [ref]


def test_parse_analysis_accepts_labeled_evidence_map(tmp_path, monkeypatch):
    root = tmp_path / "evidence"
    task = "c" * 32
    actual = root / task / "run" / "agent-browser" / ("d" * 32) / "0001-response-body.json"
    actual.parent.mkdir(parents=True)
    actual.write_text(json.dumps({"action": "response-body", "response": {"items": []}}), "utf-8")
    settings = get_settings()
    monkeypatch.setattr(settings, "evidence_root", root)
    response = {
        "observation": {"observation_code": "NO_JOBS_OBSERVED"},
        "evidence_refs": {
            "list_response": "agent-browser/" + "d" * 32 + "/0001-response-body.json"
        },
        "spec_draft": {
            "adapter": {"family": "unknown"},
            "endpoints": {},
            "fields": {},
            "pagination": {},
            "filters": {},
        },
    }
    result = parse_analysis(json.dumps(response), settings, task)
    assert result.evidence_refs == [actual.relative_to(root).as_posix()]


def test_parse_analysis_normalizes_case_insensitive_http_method(tmp_path, monkeypatch):
    root = tmp_path / "evidence"
    task = "e" * 32
    actual = root / task / "run" / "agent-browser" / ("f" * 32) / "0001-request.json"
    actual.parent.mkdir(parents=True)
    actual.write_text(json.dumps({"action": "request", "output": "POST /jobs"}), "utf-8")
    settings = get_settings()
    monkeypatch.setattr(settings, "evidence_root", root)
    response = {
        "observation": {"observation_code": "INTERNSHIPS_FOUND"},
        "evidence_refs": [actual.relative_to(root).as_posix()],
        "list_method": "post",
        "spec_draft": {
            "adapter": {"family": "custom_http"},
            "endpoints": {},
            "pagination": {},
            "fields": {},
            "filters": {},
        },
    }
    result = parse_analysis(json.dumps(response), settings, task)
    assert result.list_method == "POST"
