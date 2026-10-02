import json
from copy import deepcopy
from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from auto_spider.config import get_settings
from auto_spider.db.models import CodeSubmission, OnboardingReport, PlatformSpec, WorkflowStep
from auto_spider.schemas import PaginationSpec
from auto_spider.workflows.discovery_spec_repair import DiscoverySpecInvalid
from tests.fakes import FakeAnalyzer
from tests.test_durable_execution import runner
from tests.test_workflow import make_task


class BrokenDiscovery(FakeAnalyzer):
    def __init__(self, defect="offset"):
        self.defect = defect
        self.good = None

    def inspect(self, entry_url, platform_key, task_id=None, run_id=None):
        original = super().inspect(entry_url, platform_key, task_id, run_id)
        draft = deepcopy(original.spec_draft)
        refs = [f"{task_id}/{run_id}/browser/sample.json"]

        def rewrite(value):
            if isinstance(value, dict):
                if "evidence_refs" in value:
                    value["evidence_refs"] = refs
                for child in value.values():
                    rewrite(child)
            elif isinstance(value, list):
                for child in value:
                    rewrite(child)

        rewrite(draft)
        path = get_settings().evidence_root / refs[0]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "request_url": draft["endpoints"]["list"]["url_template"],
                    "detail_url": draft["endpoints"]["detail"]["url_template"].replace(
                        "{{source_id}}", "1"
                    ),
                    "response": {"items": [{name: "observed" for name in draft["fields"]}]},
                }
            ),
            "utf-8",
        )
        self.good = deepcopy(draft)
        if self.defect == "offset":
            draft["pagination"].update(mode="offset", page_param=None, cursor_param="offset")
            self.good["pagination"].update(mode="offset", page_param="offset", cursor_param=None)
        elif self.defect == "endpoint":
            draft["endpoints"]["list"]["method"] = "GUESS"
        elif self.defect == "selector":
            draft["fields"]["title"]["selectors"][0]["kind"] = "python"
        elif self.defect == "termination":
            draft["pagination"]["termination"] = []
        return replace(original, evidence_refs=refs, spec_draft=draft)


class Corrector:
    def __init__(self, analyzer, response=None):
        self.analyzer, self.response, self.calls = analyzer, response, []

    def repair(self, **kwargs):
        self.calls.append(kwargs)
        assert kwargs["errors"]
        return (
            self.response
            if self.response is not None
            else json.dumps(
                {
                    "reason": "按照现有响应证据和契约修正",
                    "spec_draft": self.analyzer.good,
                }
            )
        )


@pytest.mark.parametrize("defect", ["offset", "endpoint", "selector", "termination"])
def test_discovery_contract_errors_enter_bounded_correction(db_session, tmp_path, defect):
    task = make_task(db_session)
    analyzer = BrokenDiscovery(defect)
    corrector = Corrector(analyzer)
    result = runner(tmp_path, analyzer=analyzer, spec_repairer=corrector).run_onboarding(
        db_session, task.task_id
    )
    assert result["status"] == "WAITING_MANUAL_RUN"
    assert len(corrector.calls) == 1
    spec = db_session.query(PlatformSpec).one().spec_json
    assert spec["identity"]["platform_id"] is None
    assert "validated-analysis.json" in spec["evidence"]["manifest_ref"]
    original = json.loads(
        (
            get_settings().evidence_root / task.task_id / result["run_id"] / "analysis.json"
        ).read_text("utf-8")
    )
    assert original["spec_draft"] != analyzer.good
    assert db_session.query(CodeSubmission).count() == 1


def test_spec_correction_is_reused_after_worker_crash(db_session, tmp_path):
    task = make_task(db_session)
    analyzer = BrokenDiscovery()
    corrector = Corrector(analyzer)
    runtime = runner(tmp_path, analyzer=analyzer, spec_repairer=corrector)

    def crash(*args, **kwargs):
        raise SystemExit("after durable correction, before saving Spec")

    runtime._save_spec = crash
    with pytest.raises(SystemExit):
        runtime.run_onboarding(db_session, task.task_id)
    assert len(corrector.calls) == 1
    result = runner(tmp_path, analyzer=analyzer, spec_repairer=corrector).run_onboarding(
        db_session, task.task_id
    )
    assert result["status"] == "WAITING_MANUAL_RUN"
    assert len(corrector.calls) == 1


def test_invalid_corrections_exhaust_budget_without_resetting(db_session, tmp_path):
    task = make_task(db_session)
    analyzer = BrokenDiscovery()
    corrector = Corrector(analyzer, response="This is not JSON")
    for _ in range(2):
        with pytest.raises(DiscoverySpecInvalid):
            runner(tmp_path, analyzer=analyzer, spec_repairer=corrector).run_onboarding(
                db_session, task.task_id
            )
    assert len(corrector.calls) == 2
    assert corrector.calls[1]["previous_response"] == "This is not JSON"
    report = db_session.query(OnboardingReport).order_by(OnboardingReport.id.desc()).first()
    assert report.report_json["spec_repair_attempts"] == 2
    assert report.report_json["spec_validation_errors"]
    assert db_session.query(CodeSubmission).count() == 0
    assert (
        db_session.query(WorkflowStep)
        .filter(WorkflowStep.step_key.like("discovery_spec_correction:%"))
        .count()
        == 2
    )


def test_correction_cannot_expand_scope_or_replace_observation(tmp_path):
    from auto_spider.workflows.discovery_spec_repair import apply_correction

    analysis = {"observation": {"observation_code": "INCONCLUSIVE"}, "evidence_refs": []}
    with pytest.raises(ValueError):
        apply_correction(
            analysis,
            json.dumps(
                {
                    "reason": "guess",
                    "spec_draft": {"generation": {"allowed_files": ["*"]}},
                }
            ),
        )

    with pytest.raises(ValueError):
        apply_correction(
            analysis,
            json.dumps(
                {
                    "reason": "guess",
                    "spec_draft": {},
                    "observation": {"list_count": 999},
                }
            ),
        )
    with pytest.raises(ValueError, match="EVIDENCE_UNCONFIRMED"):
        apply_correction(
            analysis,
            json.dumps(
                {
                    "reason": "guess",
                    "spec_draft": {"pagination": {"evidence_refs": ["other/data"]}},
                }
            ),
        )


@pytest.mark.parametrize(
    "response",
    [
        "```json",
        "```json\n{}",
        "null",
        "[]",
        '{"reason":"shape","spec_draft":{"fields":{"title":42}}}',
        '{"reason":"shape","spec_draft":{"endpoints":{"list":true}}}',
    ],
)
def test_malformed_correction_is_a_contract_error(response):
    from auto_spider.workflows.discovery_spec_repair import apply_correction

    with pytest.raises(ValueError):
        apply_correction({"evidence_refs": []}, response)


@pytest.mark.parametrize(
    "mode,extra",
    [
        ("page", {}),
        ("offset", {"cursor_param": "offset"}),
        ("cursor", {"page_param": "cursor"}),
        ("next_url", {}),
    ],
)
def test_exported_schema_and_runtime_enforce_same_pagination_rules(mode, extra):
    value = {"mode": mode, "termination": ["empty_page"], **extra}
    assert list(Draft202012Validator(PaginationSpec.model_json_schema()).iter_errors(value))
    with pytest.raises(ValidationError):
        PaginationSpec.model_validate(value)
    valid = {"mode": "offset", "page_param": "offset", "termination": ["empty_page"]}
    Draft202012Validator(PaginationSpec.model_json_schema()).validate(valid)
    PaginationSpec.model_validate(valid)
