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
    if (run is None or not run.execution_key) and not (
        action == "RETRY" and task.current_run_id is None
    ):
        raise ValueError("NATIVE_WORKFLOW_RUN_REQUIRED")
    enqueue = False
    if (
        action == "RETRY"
        and run is not None
        and (
            (run.status == "BLOCKED" and run.error_code == "REPAIR_BUDGET_EXCEEDED")
            or (
                run.run_type == "repair"
                and run.status == "FAILED"
                and run.error_code in {
                    "REPAIR_BASE_REVISION_MISMATCH",
                    "AUTO_FAILURE_RESUME_ORIGINAL_RUN",
                }
            )
        )
    ):
        # An automatic validation bundle may have been created before a
        # candidate commit existed, so it cannot be resumed as a repair run.
        # Preserve that blocked run as history and start a fresh onboarding
        # run; no adopted candidate or human data is overwritten.
        run.status = "FAILED"
        run.error_code = "RETRY_RESET_AFTER_REPAIR_BUDGET"
        run.error_message = "自动验证修复预算耗尽，按人工重试请求重新开始接入"
        run.finished_at = datetime.now(UTC)
        run.execution_key = None
        old_onboarding = session.scalar(
            select(WorkflowRun).where(
                WorkflowRun.task_id == task_id,
                WorkflowRun.execution_key == f"onboarding:{task_id}",
                WorkflowRun.status == "BLOCKED",
            )
        )
        if old_onboarding is not None:
            old_onboarding.execution_key = None
        task.current_run_id = None
        task.status, task.next_action = "SUBMITTED", "RESUME_WORKFLOW"
        enqueue = True
    elif action == "RETRY" and run is None:
        # A prior blocked/repair reset deliberately detached the task from
        # its historical run. Let the worker create a fresh onboarding run.
        old_onboarding = session.scalar(
            select(WorkflowRun).where(
                WorkflowRun.task_id == task_id,
                WorkflowRun.execution_key == f"onboarding:{task_id}",
                WorkflowRun.status == "BLOCKED",
            )
        )
        if old_onboarding is not None:
            old_onboarding.execution_key = None
        task.status, task.next_action = "SUBMITTED", "RESUME_WORKFLOW"
        enqueue = True
    elif action in {"RESUME", "RETRY"}:
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
            # A worker/Agent Server restart can leave the last execution row
            # as RUNNING even though this workflow is terminal.  Fence that
            # stale row before retry so the gateway may rotate its prompt hash
            # and remote conversation id.
            for execution in session.scalars(
                select(AgentExecution).where(
                    AgentExecution.run_id == run.run_id,
                    AgentExecution.status.in_([
                        "CREATED",
                        "RUNNING",
                        "TIMED_OUT",
                        "INTERRUPTED",
                        "PAUSED",
                    ]),
                )
            ):
                execution.status = "DISCONNECTED"
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
        "run_id": run.run_id if run is not None else None,
        "status": task.status,
        "idempotent": False,
        "enqueue": enqueue,
    }
    session.add(
        WorkflowEvent(
            event_id=uuid4().hex,
            task_id=task_id,
            run_id=run.run_id if run is not None else "",
            event_type=f"{action}_REQUESTED",
            idempotency_key=key,
            payload_json=payload,
        )
    )
    if enqueue:
        queue_workflow(session, task_id, f"{action}:{request_id}")
    session.commit()
    return payload
