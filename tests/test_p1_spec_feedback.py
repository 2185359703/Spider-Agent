from copy import deepcopy

from auto_spider.db.models import CodeSubmission, PlatformSpec, RepairRun
from tests.fakes import FakeCodingGateway, FakeRepairGateway
from tests.test_durable_execution import runner
from tests.test_workflow import make_task


def test_invalid_generated_spec_is_repaired_instead_of_aborting(db_session, tmp_path):
    task = make_task(db_session)
    calls = []

    class BadSpec(FakeCodingGateway):
        def generate(self, *args, **kwargs):
            generated = super().generate(*args, **kwargs)
            pagination = deepcopy(kwargs["spec"]["pagination"])
            pagination["termination"] = ["total_count"]
            generated["spec_patch"] = {
                "reason": "协议已观察",
                "patch": {"pagination": pagination},
                "evidence_refs": kwargs["spec"]["fields"]["source_id"]["selectors"][0][
                    "evidence_refs"
                ],
            }
            return generated

    class Repair(FakeRepairGateway):
        def repair(self, *args, **kwargs):
            bundle = kwargs["failure_bundle"]
            errors = bundle["validation"]["spec_patch_errors"]
            assert list(errors[0]["loc"])[:2] == ["pagination", "termination"]
            assert "$defs" in bundle["spec_patch_schema"]
            calls.append("received structured schema feedback")
            return super().repair(*args, **kwargs)

    result = runner(tmp_path, coder=BadSpec(), repairer=Repair()).run_onboarding(
        db_session, task.task_id
    )
    assert result["status"] == "WAITING_MANUAL_RUN"
    assert len(calls) == db_session.query(RepairRun).count() == 1
    assert db_session.query(CodeSubmission).count() == 1
    assert db_session.query(PlatformSpec).one().spec_version == 1
