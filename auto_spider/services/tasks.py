from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from auto_spider.config import get_settings
from auto_spider.db.models import (
    CodeSubmission,
    FailureBundle,
    ManualReview,
    ManualRun,
    OnboardingBatch,
    OnboardingReport,
    OnboardingTask,
    PlatformSpec,
    WorkflowEvent,
    WorkflowRun,
)
from auto_spider.schemas import (
    CreateBatchRequest,
    ManualReviewRequest,
    ManualRunRequest,
    platform_key_from_url,
)
from auto_spider.services.dispatch import queue_workflow
from auto_spider.services.evidence import sanitize
from auto_spider.services.failure_bundles import build_failure_evidence


def new_id() -> str:
    return uuid4().hex


def now() -> datetime:
    return datetime.now(UTC)


def create_batch(
    session: Session, request: CreateBatchRequest, actor: str, *, commit: bool = True
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
    lane_count = get_settings().browser_lane_count
    for item_index, item in enumerate(request.items):
        url = str(item.entry_url)
        task = OnboardingTask(
            task_id=new_id(),
            batch_id=batch.batch_id,
            entry_url=url,
            normalized_url=url,
            platform_name=item.platform_name,
            platform_key=item.platform_key or platform_key_from_url(url),
            browser_lane=item_index % lane_count,
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
        queue_workflow(session, task.task_id, "initial")
    batch.status = "SUBMITTED"
    batch.accepted_count = len(tasks)
    session.commit() if commit else session.flush()
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
    session.refresh(task, with_for_update=True)
    request_hash = hashlib.sha256(
        json.dumps(sanitize(request.model_dump(mode="json")), sort_keys=True).encode()
    ).hexdigest()
    key = f"{task.task_id}:manual:{request.client_request_id}"
    previous = session.scalar(select(WorkflowEvent).where(WorkflowEvent.idempotency_key == key))
    if previous:
        if previous.payload_json.get("request_hash") != request_hash:
            raise ValueError("IDEMPOTENCY_CONFLICT")
        return session.scalar(
            select(ManualRun).where(
                ManualRun.manual_run_id == previous.payload_json["manual_run_id"]
            )
        )
    candidate = session.scalar(
        select(CodeSubmission)
        .where(
            CodeSubmission.task_id == task.task_id,
            CodeSubmission.commit_sha == request.code_revision,
            CodeSubmission.adoption_status == "candidate",
        )
        .order_by(CodeSubmission.id.desc())
        .limit(1)
    )
    if candidate is None or task.status not in {"WAITING_MANUAL_RUN", "WAITING_MANUAL_REVIEW"}:
        raise ValueError("MANUAL_RUN_CANDIDATE_MISMATCH")
    manual = ManualRun(
        manual_run_id=new_id(),
        task_id=task.task_id,
        code_revision=request.code_revision,
        command_profile=request.command_profile,
        environment_fingerprint=request.environment_fingerprint,
        started_at=request.started_at,
        finished_at=request.finished_at,
        artifact_manifest_ref=request.artifact_manifest_ref,
        result_json=sanitize(request.result),
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
            payload_json={
                "code_revision": request.code_revision,
                "manual_run_id": manual.manual_run_id,
                "request_hash": request_hash,
            },
        )
    )
    current = session.scalar(select(WorkflowRun).where(WorkflowRun.run_id == task.current_run_id))
    if current and current.run_type == "manual_edit":
        current.status = "WAITING_MANUAL_REVIEW"
    else:
        queue_workflow(session, task.task_id, f"manual:{request.client_request_id}")
    session.commit()
    return manual


def create_review(
    session: Session,
    task: OnboardingTask,
    request: ManualReviewRequest,
    actor_id: str | None = None,
    *,
    commit: bool = True,
) -> tuple[ManualReview, FailureBundle | None]:
    session.refresh(task, with_for_update=True)
    duplicate = session.scalar(
        select(ManualReview).where(
            ManualReview.task_id == task.task_id,
            ManualReview.manual_run_id == request.manual_run_id,
        )
    )
    if duplicate:
        if (
            duplicate.code_revision != request.code_revision
            or duplicate.review_status != request.review_status
            or duplicate.issue_details != [x.model_dump(mode="json") for x in request.field_issues]
            or duplicate.issue_summary != request.issue_summary
        ):
            raise ValueError("REVIEW_ALREADY_DECIDED")
        bundle = session.scalar(
            select(FailureBundle).where(FailureBundle.review_id == duplicate.review_id)
        )
        return duplicate, bundle

    manual = session.scalar(
        select(ManualRun).where(
            ManualRun.manual_run_id == request.manual_run_id, ManualRun.task_id == task.task_id
        )
    )
    latest_manual_id = session.scalar(
        select(ManualRun.manual_run_id)
        .where(ManualRun.task_id == task.task_id, ManualRun.code_revision == request.code_revision)
        .order_by(ManualRun.id.desc())
        .limit(1)
    )
    candidate = session.scalar(
        select(CodeSubmission)
        .where(
            CodeSubmission.task_id == task.task_id,
            CodeSubmission.commit_sha == request.code_revision,
            CodeSubmission.adoption_status == "candidate",
        )
        .order_by(CodeSubmission.id.desc())
        .limit(1)
    )
    if (
        manual is None
        or manual.manual_run_id != latest_manual_id
        or manual.code_revision != request.code_revision
        or candidate is None
        or task.status != "WAITING_MANUAL_REVIEW"
    ):
        raise ValueError("REVIEW_CANDIDATE_MISMATCH")
    if request.review_status == "PASS" and (
        request.field_issues
        or any(decision.status == "ISSUE" for decision in request.sample_decisions)
    ):
        raise ValueError("REVIEW_HAS_UNRESOLVED_ISSUES")

    review = ManualReview(
        review_id=new_id(),
        task_id=task.task_id,
        manual_run_id=request.manual_run_id,
        code_revision=request.code_revision,
        review_status=request.review_status,
        reviewer_id=actor_id or task.created_by,
        sample_count=request.sample_count,
        issue_summary=request.issue_summary,
        issue_details=[issue.model_dump(mode="json") for issue in request.field_issues],
        sample_decisions=[
            decision.model_dump(mode="json") for decision in request.sample_decisions
        ],
        evidence_refs=request.evidence_refs,
    )
    session.add(review)
    session.flush()
    bundle: FailureBundle | None = None
    if request.review_status == "PASS":
        task.status = "ADOPTED"
        task.next_action = "CLOSE_WITH_REPORT"
        candidate.adoption_status, candidate.adopted_at = "adopted", now()
        spec = session.scalar(
            select(PlatformSpec).where(
                PlatformSpec.task_id == task.task_id, PlatformSpec.is_current.is_(True)
            )
        )
        if spec:
            spec.status = "ADOPTED"
    elif request.review_status == "CODE_FIX_REQUIRED":
        issue_details = [issue.model_dump(mode="json") for issue in request.field_issues]
        known_fixability = [
            issue["code_fixable"]
            for issue in issue_details
            if issue.get("code_fixable") is not None
        ]
        code_fixable = True if known_fixability and all(known_fixability) else None
        issue_types = {issue.get("issue_type") for issue in issue_details}
        bundle_id = new_id()
        payload = build_failure_evidence(session, task, manual, candidate, request, bundle_id)
        bundle = FailureBundle(
            bundle_id=bundle_id,
            task_id=task.task_id,
            run_id=candidate.run_id,
            review_id=review.review_id,
            failure_type=(
                "PAGINATION_ERROR" if "pagination" in issue_types else "MANUAL_RESULT_MISMATCH"
            ),
            code_fixable=code_fixable,
            status="CREATED",
            bundle_json=payload,
            artifact_manifest_ref=payload["artifact_manifest_ref"],
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
    manual.status = "REVIEWED"
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
    current = session.scalar(select(WorkflowRun).where(WorkflowRun.run_id == task.current_run_id))
    if current and current.run_type == "manual_edit":
        current.status, current.finished_at = "COMPLETED", now()
    if not current or current.run_type != "manual_edit" or bundle:
        queue_workflow(
            session,
            task.task_id,
            f"review:{request.client_request_id}",
            bundle.bundle_id if bundle else None,
        )
    if commit:
        session.commit()
    else:
        session.flush()
    return review, bundle
