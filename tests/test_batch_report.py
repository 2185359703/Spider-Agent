from datetime import UTC, datetime

from auto_spider.db.models import (
    CodeSubmission,
    ManualRun,
    OnboardingBatch,
    OnboardingTask,
    WorkflowRun,
)
from auto_spider.services.batch_report import build_batch_report


def test_batch_report_projects_latest_manual_quality_summary(db_session):
    batch = OnboardingBatch(
        batch_id="batch-report",
        client_request_id="batch-report-request",
        requested_count=1,
        accepted_count=1,
    )
    task = OnboardingTask(
        task_id="task-report",
        batch_id=batch.batch_id,
        entry_url="https://example.com/jobs",
        normalized_url="https://example.com/jobs",
        platform_name="示例公司",
        platform_key="example_jobs",
        status="WAITING_MANUAL_REVIEW",
        next_action="WAIT_REVIEW",
    )
    run_id = "r" * 32
    workflow = WorkflowRun(run_id=run_id, task_id=task.task_id, run_type="onboarding")
    now = datetime.now(UTC)
    manual = ManualRun(
        manual_run_id="manual-report",
        task_id=task.task_id,
        code_revision="a" * 40,
        command_profile="admin-http-collection-v1",
        environment_fingerprint="linux-landlock",
        started_at=now,
        finished_at=now,
        artifact_manifest_ref="records.json",
        status="WAITING_REVIEW",
        result_json={
            "record_count": 3,
            "quality": {
                "status": "PARTIAL",
                "metrics": {"record_count": 3, "error_count": 0, "warning_count": 1},
                "findings": [{"code": "PAGINATION_EVIDENCE_INSUFFICIENT"}],
            },
        },
    )
    submission = CodeSubmission(
        submission_id="submission-report",
        task_id=task.task_id,
        run_id=run_id,
        branch_name="candidate",
        commit_sha="a" * 40,
        baseline_ref="b" * 40,
        changed_files=["collectors/example_jobs.py"],
        adoption_status="candidate",
    )
    db_session.add_all([batch, task, workflow, manual, submission])
    db_session.commit()

    # A retry can fail before producing artifacts. The report must preserve
    # that current failure while keeping the last successful sample count
    # visible for review.
    failed = ManualRun(
        manual_run_id="manual-report-failed",
        task_id=task.task_id,
        code_revision="c" * 40,
        command_profile="admin-http-collection-v1",
        environment_fingerprint="linux-landlock",
        started_at=now,
        finished_at=now,
        artifact_manifest_ref="pending",
        status="FAILED",
        result_json={"record_count": 0, "error_msg": "请求失败"},
    )
    db_session.add(failed)
    db_session.commit()

    report = build_batch_report(db_session, batch.batch_id)
    entry = report["entries"][0]
    assert entry["manual_run_id"] == "manual-report-failed"
    assert entry["manual_run_status"] == "FAILED"
    assert entry["record_count"] == 3
    assert entry["quality_status"] == "PARTIAL"
    assert entry["quality_metrics"]["warning_count"] == 1
    assert entry["quality_finding_count"] == 1
    assert entry["quality_source_manual_run_id"] == "manual-report"
