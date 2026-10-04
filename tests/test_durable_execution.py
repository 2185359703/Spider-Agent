from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from auto_spider.config import get_settings
from auto_spider.db.models import (
    CodeSubmission,
    ExecutionLease,
    FailureBundle,
    GraphCheckpoint,
    OnboardingReport,
    PlatformSpec,
    RepairRun,
    WorkflowDispatch,
    WorkflowRun,
)
from auto_spider.services.dispatch import dispatch_pending, execute_dispatch, queue_workflow
from auto_spider.services.execution import ExecutionContext, RunBusy
from auto_spider.services.run_control import control_run
from auto_spider.workflows.runner import WorkflowRunner
from tests.fakes import (
    FakeAnalyzer,
    FakeCodingGateway,
    FakeRepairGateway,
    fake_publication,
    fake_validation,
)
from tests.test_workflow import make_task


def runner(tmp_path, **overrides):
    options = dict(
        analyzer=FakeAnalyzer(),
        coder=FakeCodingGateway(),
        repairer=FakeRepairGateway(),
        validator=fake_validation,
        publisher=fake_publication,
    )
    options.update(overrides)
    result = WorkflowRunner(**options)
    result.evidence.root = tmp_path / "evidence"
    return result


class CountingCoder(FakeCodingGateway):
    calls = 0

    def generate(self, *args, **kwargs):
        self.calls += 1
        return super().generate(*args, **kwargs)


def test_new_worker_recovers_checkpoint_without_repeating_generation(db_session, tmp_path):
    task = make_task(db_session)
    coder = CountingCoder()

    def crash(*_):
        raise SystemExit("worker killed during validation")

    with pytest.raises(SystemExit):
        runner(tmp_path, coder=coder, validator=crash).run_onboarding(db_session, task.task_id)
    run_id = db_session.query(WorkflowRun).one().run_id
    assert db_session.query(GraphCheckpoint).count() > 0
    result = runner(tmp_path, coder=coder).run_onboarding(db_session, task.task_id)
    assert result["status"] == "WAITING_MANUAL_RUN"
    assert result["run_id"] == run_id
    assert coder.calls == 1
    runner(tmp_path, coder=coder).run_onboarding(db_session, task.task_id)
    assert coder.calls == 1
    assert db_session.query(CodeSubmission).count() == 1
    assert db_session.query(OnboardingReport).count() == 1


def test_validation_failure_publishes_candidate_for_manual_review(db_session, tmp_path):
    task = make_task(db_session)
    order = []

    def validate(generated, state):
        order.append(f"validate:{state['attempt']}")
        return {
            **fake_validation(generated, state),
            "pytest_status": "FAIL" if state["attempt"] == 0 else "PASS",
        }

    def publish(*args, **kwargs):
        order.append("commit")
        return fake_publication(*args, **kwargs)

    result = runner(tmp_path, validator=validate, publisher=publish).run_onboarding(
        db_session, task.task_id
    )
    assert order == ["validate:0", "commit"]
    assert result["status"] == "WAITING_MANUAL_RUN"
    specs = db_session.query(PlatformSpec).order_by(PlatformSpec.spec_version).all()
    assert [s.spec_json["spec_revision"] for s in specs] == [1]
    assert db_session.query(OnboardingReport).count() == 1
    assert db_session.query(CodeSubmission).count() == 1
    assert db_session.query(FailureBundle).count() == 0


def test_repair_budget_survives_redelivery(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_REPAIR_ATTEMPTS", "2")
    get_settings.cache_clear()
    task = make_task(db_session)

    def fail(generated, state):
        return {**fake_validation(generated, state), "compile_status": "FAIL"}

    runtime = runner(tmp_path, validator=fail)
    result = runtime.run_onboarding(db_session, task.task_id)
    assert result["status"] == "WAITING_MANUAL_RUN"
    assert db_session.query(RepairRun).count() == 0
    assert db_session.query(CodeSubmission).count() == 1
    runtime.run_onboarding(db_session, task.task_id)
    assert db_session.query(RepairRun).count() == 0


@pytest.mark.parametrize("action,expected", [("PAUSE", "PAUSED"), ("CANCEL", "CANCELLED")])
def test_control_during_validation_stops_before_commit(db_session, tmp_path, action, expected):
    task = make_task(db_session)

    def stop(generated, state):
        factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
        with factory() as s:
            control_run(s, task.task_id, action, "control-test-0001")
        return fake_validation(generated, state)

    runtime = runner(tmp_path, validator=stop)
    result = runtime.run_onboarding(db_session, task.task_id)
    assert result["status"] == expected
    assert db_session.query(CodeSubmission).count() == 0
    if action == "PAUSE":
        control_run(db_session, task.task_id, "RESUME", "resume-test-0001")
        result = runner(tmp_path).run_onboarding(db_session, task.task_id)
        assert result["status"] == "WAITING_MANUAL_RUN"
        assert db_session.query(WorkflowRun).count() == 1
    else:
        with pytest.raises(ValueError, match="NOT_RESUMABLE"):
            control_run(db_session, task.task_id, "RESUME", "resume-test-0001")


def test_lease_rejects_parallel_owner_and_recovers_expired_same_run(db_session):
    task = make_task(db_session)
    run = WorkflowRun(run_id="lease-run", task_id=task.task_id, run_type="onboarding")
    db_session.add(run)
    db_session.commit()
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    with ExecutionContext(factory, run.run_id, "repository:platform") as first:
        with pytest.raises(RunBusy):
            with ExecutionContext(factory, run.run_id, "repository:platform"):
                pytest.fail("duplicate acquired lease")
        first.retain_lease = True
    lease = db_session.scalar(select(ExecutionLease))
    lease.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    with ExecutionContext(factory, run.run_id, "repository:platform") as recovered:
        recovered.guard()
    assert db_session.query(ExecutionLease).count() == 0


def test_outbox_survives_broker_failure_and_redelivery(db_session, tmp_path):
    task = make_task(db_session)
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    dispatch = queue_workflow(db_session, task.task_id, "initial")
    db_session.commit()

    def unavailable(_):
        raise ConnectionError("broker unavailable")

    dispatch_pending(factory, unavailable)
    db_session.expire_all()
    assert dispatch.status == "PENDING"
    dispatch.next_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    delivered = []
    dispatch_pending(factory, delivered.append)
    assert delivered == [dispatch.dispatch_id]
    execute_dispatch(factory, dispatch.dispatch_id, runner(tmp_path))
    execute_dispatch(factory, dispatch.dispatch_id, runner(tmp_path))
    db_session.expire_all()
    assert db_session.query(WorkflowDispatch).one().status == "DONE"
    assert db_session.query(CodeSubmission).count() == 1


def test_unexecuted_validation_cannot_pass(db_session, tmp_path):
    task = make_task(db_session)
    result = runner(tmp_path, validator=lambda *_: {}).run_onboarding(db_session, task.task_id)
    assert result["adoptable"] is False
    assert db_session.query(CodeSubmission).count() == 0


def test_timeout_stops_without_commit_and_can_retry_same_run(db_session, tmp_path):
    task = make_task(db_session)
    coder = CountingCoder()
    runtime = runner(tmp_path, coder=coder)

    def timeout(generated, state):
        runtime.execution.started -= get_settings().execution_timeout_seconds + 1
        return fake_validation(generated, state)

    runtime.validator = timeout
    result = runtime.run_onboarding(db_session, task.task_id)
    assert result["status"] == "TIMED_OUT"
    assert db_session.query(CodeSubmission).count() == 0
    control_run(db_session, task.task_id, "RETRY", "timeout-retry-0001")
    result = runner(tmp_path, coder=coder).run_onboarding(db_session, task.task_id)
    assert result["status"] == "WAITING_MANUAL_RUN"
    assert coder.calls == 1
