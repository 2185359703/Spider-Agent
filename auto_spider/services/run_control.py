from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from auto_spider.db.models import AgentExecution, OnboardingTask, WorkflowEvent, WorkflowRun
from auto_spider.services.dispatch import queue_workflow


def control_run(session, task_id, action, request_id):
    task = session.scalar(
        select(OnboardingTask).where(OnboardingTask.task_id == task_id).with_for_update()
    )
    if task is None:
        raise ValueError("TASK_NOT_FOUND")
    key = f"{task_id}:{action.lower()}:{request_id}"
    event = session.scalar(select(WorkflowEvent).where(WorkflowEvent.idempotency_key == key))
    if event:
        return {**event.payload_json, "idempotent": True, "enqueue": False}
    run = session.scalar(select(WorkflowRun).where(WorkflowRun.run_id == task.current_run_id))
    if run is None or not run.execution_key:
        raise ValueError("NATIVE_WORKFLOW_RUN_REQUIRED")
    enqueue = False
    if action in {"RESUME", "RETRY"}:
        if run.status in {"RUNNING", "QUEUED"} and not run.control:
            if (
                run.status == "RUNNING"
                and run.heartbeat_at
                and run.heartbeat_at.replace(tzinfo=UTC) > datetime.now(UTC) - timedelta(seconds=60)
            ):
                return {
                    "task_id": task_id,
                    "run_id": run.run_id,
                    "status": task.status,
                    "idempotent": True,
                    "enqueue": False,
                }
            enqueue = True  # Leases arbitrate stale vs actually-running requests.
        elif run.status in {"PAUSED", "FAILED", "INTERRUPTED", "TIMED_OUT"}:
            run.control, run.status = None, "QUEUED"
            task.status, task.next_action = "SUBMITTED", "RESUME_WORKFLOW"
            enqueue = True
        else:
            raise ValueError("RUN_NOT_RESUMABLE")
    else:
        if run.status in {"COMPLETED", "BLOCKED", "CANCELLED"}:
            raise ValueError("RUN_ALREADY_FINISHED")
        run.control = action
        remote_active = session.scalar(
            select(AgentExecution.execution_id)
            .where(
                AgentExecution.run_id == run.run_id,
                AgentExecution.status.in_(["RUNNING", "DISCONNECTED"]),
            )
            .limit(1)
        )
        if run.status in {"RUNNING", "QUEUED"} or remote_active:
            task.status = f"{action}_REQUESTED"
        else:
            task.status = run.status = "PAUSED" if action == "PAUSE" else "CANCELLED"
        task.next_action = "REQUEST_INPUT"
    payload = {
        "task_id": task_id,
        "run_id": run.run_id,
        "status": task.status,
        "idempotent": False,
        "enqueue": enqueue,
    }
    session.add(
        WorkflowEvent(
            event_id=uuid4().hex,
            task_id=task_id,
            run_id=run.run_id,
            event_type=f"{action}_REQUESTED",
            idempotency_key=key,
            payload_json=payload,
        )
    )
    if enqueue:
        queue_workflow(session, task_id, f"{action}:{request_id}")
    session.commit()
    return payload
