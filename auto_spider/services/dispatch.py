from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from auto_spider.db.models import OnboardingTask, WorkflowDispatch, WorkflowRun
from auto_spider.services.browser_evidence import sanitize_text
from auto_spider.services.execution import RunBusy


def queue_workflow(session, task_id: str, key: str, bundle_id: str | None = None):
    dispatch_id = hashlib.sha256(f"{task_id}:{key}".encode()).hexdigest()
    existing = session.get(WorkflowDispatch, dispatch_id)
    if existing:
        return existing
    row = WorkflowDispatch(dispatch_id=dispatch_id, task_id=task_id, bundle_id=bundle_id)
    session.add(row)
    return row


def dispatch_pending(factory, publish, *, limit=20):
    """Transactional outbox with at-least-once delivery; graph leases deduplicate work."""
    now = datetime.now(UTC)
    with factory() as session:
        ids = list(
            session.scalars(
                select(WorkflowDispatch.dispatch_id)
                .where(
                    WorkflowDispatch.status.in_(["PENDING", "SENT"]),
                    WorkflowDispatch.next_at <= now,
                )
                .order_by(WorkflowDispatch.next_at)
                .limit(limit)
            )
        )
    for dispatch_id in ids:
        with factory.begin() as session:
            row = session.scalar(
                select(WorkflowDispatch)
                .where(WorkflowDispatch.dispatch_id == dispatch_id)
                .with_for_update()
            )
            if row.status not in {"PENDING", "SENT"} or row.next_at.replace(tzinfo=UTC) > now:
                continue
            task = session.scalar(
                select(OnboardingTask).where(OnboardingTask.task_id == row.task_id)
            )
            run = session.scalar(
                select(WorkflowRun).where(WorkflowRun.run_id == task.current_run_id)
            )
            if (
                run
                and run.status == "RUNNING"
                and run.heartbeat_at
                and run.heartbeat_at.replace(tzinfo=UTC) > now - timedelta(seconds=60)
            ):
                row.next_at = now + timedelta(seconds=30)
                continue
            row.status, row.next_at = "SENT", now + timedelta(seconds=90)
        try:
            publish(dispatch_id)
        except Exception as exc:
            with factory.begin() as session:
                row = session.get(WorkflowDispatch, dispatch_id)
                row.status, row.last_error = "PENDING", sanitize_text(str(exc))[:1000]
                row.next_at = now + timedelta(seconds=30)


def execute_dispatch(factory, dispatch_id, runner=None):
    from auto_spider.workflows.runner import WorkflowRunner

    with factory() as session:
        row = session.get(WorkflowDispatch, dispatch_id)
        if not row or row.status in {"DONE", "FAILED"}:
            return
        task_id, bundle_id = row.task_id, row.bundle_id
    try:
        with factory() as session:
            runtime = runner or WorkflowRunner()
            result = (
                runtime.run_repair(session, task_id, bundle_id)
                if bundle_id
                else runtime.run_onboarding(session, task_id)
            )
        with factory.begin() as session:
            row = session.get(WorkflowDispatch, dispatch_id)
            row.status = (
                "PENDING"
                if result.get("idempotent") and result.get("status") == "RUNNING"
                else "DONE"
            )
            row.next_at = datetime.now(UTC) + timedelta(seconds=30)
    except RunBusy:
        with factory.begin() as session:
            row = session.get(WorkflowDispatch, dispatch_id)
            row.status = "PENDING"
            row.next_at = datetime.now(UTC) + timedelta(seconds=15)
    except Exception as exc:
        with factory.begin() as session:
            row = session.get(WorkflowDispatch, dispatch_id)
            row.attempts += 1
            row.last_error = sanitize_text(str(exc))[:2000]
            transient = any(
                word in (type(exc).__name__ + str(exc)).lower()
                for word in ("connection", "readerror", "timeout", "disconnected", "503", "429")
            )
            row.status = "PENDING" if transient and row.attempts < 3 else "FAILED"
            row.next_at = datetime.now(UTC) + timedelta(seconds=30 * row.attempts)
