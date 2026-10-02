from datetime import UTC, datetime
from pathlib import Path

from auto_spider.db.models import (
    CodeSubmission,
    FailureBundle,
    OnboardingBatch,
    OnboardingReport,
    OnboardingTask,
    WorkflowCheckpoint,
)
from auto_spider.schemas import (
    ManualReviewRequest,
    ManualRunRequest,
    ObservationCode,
    ReviewFieldIssue,
    ReviewSampleDecision,
    ReviewStatus,
)
from auto_spider.services.tasks import create_manual_run, create_review
from auto_spider.workflows.graph import build_graph
from auto_spider.workflows.runner import WorkflowRunner
from tests.fakes import (
    FakeAnalyzer,
    FakeCodingGateway,
    FakeRepairGateway,
    fake_publication,
    fake_validation,
)


def make_task(session, url: str = "https://example.com/jobs?fixture=jobs") -> OnboardingTask:
    batch = OnboardingBatch(
        batch_id="batch-test",
        client_request_id="workflow-request",
        requested_count=1,
        accepted_count=1,
        created_by="test",
    )
    task = OnboardingTask(
        task_id="task-test",
        batch_id=batch.batch_id,
        entry_url=url,
        normalized_url=url,
        platform_name="Example",
        platform_key="example_company",
        platform_id=None,
        entity_id=None,
        created_by="test",
    )
    session.add_all([batch, task])
    session.commit()
    return task


def fake_runner() -> WorkflowRunner:
    return WorkflowRunner(
        analyzer=FakeAnalyzer(),
        coder=FakeCodingGateway(),
        repairer=FakeRepairGateway(),
        validator=fake_validation,
        publisher=fake_publication,
    )


def test_onboarding_creates_report_candidate_and_checkpoints(db_session, tmp_path: Path) -> None:
    task = make_task(db_session)
    runner = fake_runner()
    runner.evidence.root = tmp_path / "evidence"
    report = runner.run_onboarding(db_session, task.task_id)

    assert report["observation_code"] == ObservationCode.INTERNSHIPS_FOUND
    assert report["adoptable"] is True
    refreshed = db_session.get(OnboardingTask, task.id)
    assert refreshed.platform_id is None
    assert refreshed.entity_id is None
    assert refreshed.status == "WAITING_MANUAL_RUN"
    assert db_session.query(OnboardingReport).count() == 1
    assert db_session.query(WorkflowCheckpoint).count() >= 5


def test_onboarding_reports_no_job_list_without_candidate(db_session, tmp_path: Path) -> None:
    task = make_task(db_session, "https://example.com/careers")
    runner = fake_runner()
    runner.evidence.root = tmp_path / "evidence"
    report = runner.run_onboarding(db_session, task.task_id)

    assert report["observation_code"] == ObservationCode.NO_JOB_LIST_FOUND
    assert report["adoptable"] is False
    refreshed = db_session.get(OnboardingTask, task.id)
    assert refreshed.status == "BLOCKED"


def test_manual_review_creates_failure_bundle_and_repair_candidate(
    db_session, tmp_path: Path
) -> None:
    task = make_task(db_session)
    runner = fake_runner()
    runner.evidence.root = tmp_path / "evidence"
    runner.run_onboarding(db_session, task.task_id)
    candidate = db_session.query(CodeSubmission).one().commit_sha
    manual = create_manual_run(
        db_session,
        task,
        ManualRunRequest(
            code_revision=candidate,
            environment_fingerprint="test",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            artifact_manifest_ref="evidence/manual/manifest.json",
            client_request_id="manual-review-0001",
            result={
                "samples": [
                    {
                        "source_id": "intern-1",
                        "title": "实习岗位",
                        "description": "岗位描述",
                        "source_url": "https://example.com/jobs/intern-1",
                        "location": None,
                    }
                ]
            },
        ),
    )
    _, bundle = create_review(
        db_session,
        task,
        ManualReviewRequest(
            review_status=ReviewStatus.CODE_FIX_REQUIRED,
            manual_run_id=manual.manual_run_id,
            code_revision=candidate,
            issue_summary="地点字段为空",
            field_issues=[
                ReviewFieldIssue(
                    field="location",
                    issue_type="missing",
                    description="样本缺少工作地点",
                    sample_indices=[1],
                    code_fixable=True,
                )
            ],
            sample_decisions=[ReviewSampleDecision(sample_index=1, status="ISSUE", issue_refs=[0])],
            client_request_id="review-0001",
        ),
    )
    assert bundle is not None
    result = runner.run_repair(db_session, task.task_id, bundle.bundle_id)
    assert result["invalid_files"] == []
    refreshed = db_session.get(OnboardingTask, task.id)
    assert refreshed.status == "WAITING_MANUAL_RUN"
    assert db_session.query(FailureBundle).one().status == "REPAIRED"
    assert (
        db_session.query(FailureBundle).one().bundle_json["field_issues"][0]["field"] == "location"
    )
    assert (
        db_session.query(FailureBundle).one().bundle_json["sample_decisions"][0]["status"]
        == "ISSUE"
    )


def test_langgraph_definition_compiles() -> None:
    graph = build_graph()
    assert graph is not None
