"""Close a successful empty human run without inventing jobs or adopting unverified code."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from auto_spider.db.models import (
    CodeSubmission,
    ManualReview,
    ManualRun,
    OnboardingReport,
    WorkflowEvent,
    WorkflowRun,
)
from auto_spider.services.dispatch import queue_workflow
from auto_spider.services.evidence import sanitize


def confirm_empty_result(session, task, run, candidate, request, actor, *, commit=True):
    if run.status != "WAITING_REVIEW" or run.result_json.get("error_msg"):
        raise ValueError("EMPTY_REVIEW_REQUIRES_SUCCESSFUL_COLLECTION")
    latest = session.scalar(
        select(ManualRun.manual_run_id)
        .where(ManualRun.task_id == task.task_id, ManualRun.code_revision == run.code_revision)
        .order_by(ManualRun.id.desc())
        .limit(1)
    )
    if latest != run.manual_run_id:
        raise ValueError("EMPTY_REVIEW_STALE_RUN")
    if candidate.adoption_status == "adopted" and session.scalar(
        select(CodeSubmission.id)
        .where(
            CodeSubmission.task_id == task.task_id, CodeSubmission.adoption_status == "candidate"
        )
        .limit(1)
    ):
        raise ValueError("EMPTY_REVIEW_STALE_CODE")
    conclusion = sanitize(
        {
            "observation_code": request.observation_code,
            "comment": request.comment.strip(),
            "reviewer": actor.user_id,
            "at": datetime.now(UTC).isoformat(),
            "scope": run.result_json.get("options", {}),
            "code_revision": run.code_revision,
        }
    )
    review = ManualReview(
        review_id=uuid4().hex,
        task_id=task.task_id,
        manual_run_id=run.manual_run_id,
        code_revision=run.code_revision,
        review_status="NO_DATA_CONFIRMED",
        reviewer_id=actor.user_id,
        sample_count=0,
        sample_decisions=[],
        issue_details=[],
        issue_summary=conclusion["comment"],
        evidence_refs=[run.artifact_manifest_ref],
    )
    session.add(review)
    run.result_json = {**run.result_json, "empty_conclusion": conclusion}
    run.status = "REVIEWED"
    task.status = "NO_DATA_CONFIRMED"
    task.next_action = (
        "REQUEST_INPUT" if request.observation_code == "INCONCLUSIVE" else "CLOSE_WITH_REPORT"
    )
    report = OnboardingReport(
        report_id=uuid4().hex,
        task_id=task.task_id,
        run_id=candidate.run_id,
        report_version=task.policy_version,
        observation_code=request.observation_code,
        technical_status="PARTIAL",
        next_action=task.next_action,
        adoptable=False,
        report_json={
            "task_id": task.task_id,
            "run_id": candidate.run_id,
            "manual_run_id": run.manual_run_id,
            "candidate_commit": run.code_revision,
            "entry_url": task.entry_url,
            "observation_code": request.observation_code,
            "observation_source": "human_review",
            "technical_status": "PARTIAL",
            "adoptable": False,
            "next_action": task.next_action,
            "empty_conclusion": conclusion,
            "valid_record_count": 0,
            "source_record_count": run.result_json.get("source_record_count"),
            "evidence_refs": [run.artifact_manifest_ref, run.result_json.get("diagnostics_ref")],
            "unresolved": ["no_live_job_sample"],
            "platform_id": None,
            "entity_id": None,
            "simulated": False,
        },
    )
    session.add(report)
    task.last_report_id = report.report_id
    session.add(
        WorkflowEvent(
            event_id=uuid4().hex,
            task_id=task.task_id,
            run_id=candidate.run_id,
            event_type="EMPTY_COLLECTION_REVIEWED",
            idempotency_key=f"{task.task_id}:empty:{request.client_request_id}",
            payload_json={
                "manual_run_id": run.manual_run_id,
                "review_id": review.review_id,
                **conclusion,
            },
        )
    )
    current = session.scalar(select(WorkflowRun).where(WorkflowRun.run_id == task.current_run_id))
    if current and current.run_type == "manual_edit":
        current.status, current.finished_at = "COMPLETED", datetime.now(UTC)
    elif (
        current
        and current.execution_key
        and current.status in {"WAITING_MANUAL_RUN", "WAITING_MANUAL_REVIEW"}
    ):
        queue_workflow(session, task.task_id, f"empty-review:{run.manual_run_id}")
    session.commit() if commit else session.flush()
    return {
        "review_id": review.review_id,
        "status": task.status,
        "observation_code": request.observation_code,
    }
