from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from auto_spider.db.models import (
    FailureBundle,
    ManualReview,
    ManualRun,
    OnboardingBatch,
    OnboardingReport,
    OnboardingTask,
    WorkflowEvent,
)
from auto_spider.schemas import (
    CreateBatchRequest,
    ManualReviewRequest,
    ManualRunRequest,
    platform_key_from_url,
)


def new_id() -> str:
    return uuid4().hex


def now() -> datetime:
    return datetime.now(UTC)


def create_batch(
    session: Session, request: CreateBatchRequest, actor: str
) -> tuple[OnboardingBatch, list[OnboardingTask]]:
    existing = session.scalar(
        select(OnboardingBatch).where(
            OnboardingBatch.client_request_id == request.client_request_id
        )
    )
    if existing:
        tasks = list(
            session.scalars(
                select(OnboardingTask).where(OnboardingTask.batch_id == existing.batch_id)
            ).all()
        )
        return existing, tasks

    batch = OnboardingBatch(
        batch_id=new_id(),
        client_request_id=request.client_request_id,
        requested_count=len(request.items),
        created_by=actor,
    )
    session.add(batch)
    session.flush()
    tasks: list[OnboardingTask] = []
    for item in request.items:
        url = str(item.entry_url).rstrip("/")
        task = OnboardingTask(
            task_id=new_id(),
            batch_id=batch.batch_id,
            entry_url=url,
            normalized_url=url,
            platform_name=item.platform_name,
            platform_key=item.platform_key or platform_key_from_url(url),
            repository_key=item.repository_key,
            policy_version=item.policy_version,
            platform_id=None,
            entity_id=None,
            created_by=actor,
        )
        session.add(task)
        session.flush()
        session.add(
            WorkflowEvent(
                event_id=new_id(),
                event_type="TASK_CREATED",
                task_id=task.task_id,
                idempotency_key=f"{task.task_id}:created",
                payload_json={"entry_url": url, "platform_id": None, "entity_id": None},
            )
        )
        tasks.append(task)
    batch.status = "SUBMITTED"
    batch.accepted_count = len(tasks)
    session.commit()
    return batch, tasks


def get_task(session: Session, task_id: str) -> OnboardingTask | None:
    return session.scalar(select(OnboardingTask).where(OnboardingTask.task_id == task_id))


def latest_report(session: Session, task_id: str) -> OnboardingReport | None:
    return session.scalar(
        select(OnboardingReport)
        .where(OnboardingReport.task_id == task_id)
        .order_by(OnboardingReport.created_at.desc())
        .limit(1)
    )


def create_manual_run(
    session: Session,
    task: OnboardingTask,
    request: ManualRunRequest,
) -> ManualRun:
    manual = ManualRun(
        manual_run_id=new_id(),
        task_id=task.task_id,
        code_revision=request.code_revision,
        command_profile=request.command_profile,
        environment_fingerprint=request.environment_fingerprint,
        started_at=request.started_at,
        finished_at=request.finished_at,
        artifact_manifest_ref=request.artifact_manifest_ref,
        result_json=request.result,
        status="WAITING_REVIEW",
    )
    session.add(manual)
    task.status = "WAITING_MANUAL_REVIEW"
    task.next_action = "WAIT_REVIEW"
    session.add(
        WorkflowEvent(
            event_id=new_id(),
            event_type="MANUAL_RUN_REGISTERED",
            task_id=task.task_id,
            run_id=task.current_run_id,
            idempotency_key=f"{task.task_id}:manual:{request.client_request_id}",
            payload_json={"code_revision": request.code_revision},
        )
    )
    session.commit()
    return manual


def create_review(
    session: Session,
    task: OnboardingTask,
    request: ManualReviewRequest,
) -> tuple[ManualReview, FailureBundle | None]:
    duplicate = session.scalar(
        select(ManualReview).where(
            ManualReview.task_id == task.task_id,
            ManualReview.manual_run_id == request.manual_run_id,
        )
    )
    if duplicate:
        bundle = session.scalar(
            select(FailureBundle).where(FailureBundle.review_id == duplicate.review_id)
        )
        return duplicate, bundle

    review = ManualReview(
        review_id=new_id(),
        task_id=task.task_id,
        manual_run_id=request.manual_run_id,
        code_revision=request.code_revision,
        review_status=request.review_status,
        reviewer_id=task.created_by,
        sample_count=request.sample_count,
        issue_summary=request.issue_summary,
        issue_details=[issue.model_dump(mode="json") for issue in request.field_issues],
        evidence_refs=request.evidence_refs,
    )
    session.add(review)
    session.flush()
    bundle: FailureBundle | None = None
    if request.review_status == "PASS":
        task.status = "ADOPTED"
        task.next_action = "CLOSE_WITH_REPORT"
    elif request.review_status == "CODE_FIX_REQUIRED":
        issue_details = [issue.model_dump(mode="json") for issue in request.field_issues]
        known_fixability = [
            issue["code_fixable"]
            for issue in issue_details
            if issue.get("code_fixable") is not None
        ]
        code_fixable = True if known_fixability and all(known_fixability) else None
        issue_types = {issue.get("issue_type") for issue in issue_details}
        bundle = FailureBundle(
            bundle_id=new_id(),
            task_id=task.task_id,
            run_id=task.current_run_id or "",
            review_id=review.review_id,
            failure_type=(
                "PAGINATION_ERROR"
                if "pagination" in issue_types
                else "MANUAL_RESULT_MISMATCH"
            ),
            code_fixable=code_fixable,
            status="CREATED",
            bundle_json={
                "issue_summary": request.issue_summary,
                "field_issues": [issue.model_dump(mode="json") for issue in request.field_issues],
                "code_revision": request.code_revision,
                "evidence_refs": request.evidence_refs,
                "sanitized": True,
            },
        )
        session.add(bundle)
        task.status = "REPAIRING"
        task.next_action = "AUTO_REPAIR"
    else:
        task.status = "BLOCKED" if request.review_status != "REJECT" else "REJECTED"
        task.next_action = (
            "REQUEST_INPUT"
            if request.review_status == "BUSINESS_RULE_REVIEW"
            else "CLOSE_WITH_REPORT"
        )
    session.add(
        WorkflowEvent(
            event_id=new_id(),
            event_type="REVIEW_SUBMITTED",
            task_id=task.task_id,
            run_id=task.current_run_id,
            idempotency_key=f"{task.task_id}:review:{request.client_request_id}",
            payload_json={"review_status": request.review_status},
        )
    )
    session.commit()
    return review, bundle
