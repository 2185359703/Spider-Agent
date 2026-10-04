from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, select

from auto_spider.config import get_settings
from auto_spider.db.models import AgentExecution, ExecutionLease, OnboardingTask, WorkflowRun


def reconcile_stopped_runs(factory):
    """Finish stop requests after worker death, without starting another Agent."""
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.execution_lease_seconds)
    with factory() as session:
        ids = list(
            session.scalars(
                select(WorkflowRun.run_id).where(
                    WorkflowRun.control.in_(["PAUSE", "CANCEL"]),
                    WorkflowRun.status.not_in(["CANCELLED", "COMPLETED", "BLOCKED"]),
                )
            )
        )
    for run_id in ids:
        with factory() as session:
            run = session.scalar(select(WorkflowRun).where(WorkflowRun.run_id == run_id))
            if (
                run.status == "RUNNING"
                and run.heartbeat_at
                and run.heartbeat_at.replace(tzinfo=UTC) > cutoff
            ):
                continue
            executions = list(
                session.scalars(
                    select(AgentExecution).where(
                        AgentExecution.run_id == run_id,
                        AgentExecution.status.in_(["RUNNING", "DISCONNECTED", "PAUSED"]),
                    )
                )
            )
            action = run.control
        confirmed = True
        headers = {"X-Session-API-Key": settings.openhands_session_api_key or ""}
        with httpx.Client(
            base_url=settings.openhands_server_url, headers=headers, timeout=10
        ) as client:
            for execution in executions:
                try:
                    response = client.post(f"/api/conversations/{execution.conversation_id}/pause")
                    response.raise_for_status()
                    response = client.get(f"/api/conversations/{execution.conversation_id}")
                    response.raise_for_status()
                    if response.json().get("execution_status", "").lower() not in {
                        "paused",
                        "finished",
                        "error",
                        "stuck",
                        "idle",
                    }:
                        confirmed = False
                except httpx.HTTPError:
                    confirmed = False
        if not confirmed:
            continue
        with factory.begin() as session:
            run = session.scalar(
                select(WorkflowRun).where(WorkflowRun.run_id == run_id).with_for_update()
            )
            if run.control != action:
                continue
            # Never release a lease a new, live worker has just acquired.
            lease = session.scalar(select(ExecutionLease).where(ExecutionLease.run_id == run_id))
            if lease and lease.expires_at.replace(tzinfo=UTC) > datetime.now(UTC):
                continue
            run.status = "PAUSED" if action == "PAUSE" else "CANCELLED"
            task = session.scalar(
                select(OnboardingTask).where(OnboardingTask.task_id == run.task_id)
            )
            if task.current_run_id == run_id:
                task.status = run.status
            for execution in session.scalars(
                select(AgentExecution).where(
                    AgentExecution.run_id == run_id,
                    AgentExecution.status.in_(["RUNNING", "DISCONNECTED", "PAUSED"]),
                )
            ):
                execution.status = run.status
            session.execute(delete(ExecutionLease).where(ExecutionLease.run_id == run_id))


def reconcile_stale_runs(factory):
    """Close orphaned RUNNING rows after a worker vanished before creating an Agent execution."""
    settings = get_settings()
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=settings.execution_lease_seconds)
    recovered = []
    with factory.begin() as session:
        runs = session.scalars(
            select(WorkflowRun)
            .where(WorkflowRun.status == "RUNNING", WorkflowRun.started_at < cutoff)
            .with_for_update()
        ).all()
        for run in runs:
            if run.heartbeat_at and run.heartbeat_at.replace(tzinfo=UTC) > cutoff:
                continue
            lease = session.scalar(
                select(ExecutionLease).where(ExecutionLease.run_id == run.run_id)
            )
            if lease and lease.expires_at.replace(tzinfo=UTC) > now:
                continue
            active_agent = session.scalar(
                select(AgentExecution.execution_id).where(
                    AgentExecution.run_id == run.run_id,
                    # DISCONNECTED is recoverable.  Keeping it in the live
                    # set made a lost Agent Server session pin the workflow in
                    # RUNNING forever, because no worker could reclaim the
                    # graph checkpoint and reconnect.
                    AgentExecution.status.in_(["CREATED", "RUNNING"]),
                )
            )
            if active_agent:
                continue
            run.status = "INTERRUPTED"
            run.error_code = "STALE_EXECUTION"
            run.error_message = "工作流超过租约时间且没有活动执行者，已标记为中断"
            run.finished_at = now
            task = session.scalar(
                select(OnboardingTask).where(OnboardingTask.task_id == run.task_id)
            )
            if task and task.current_run_id == run.run_id and task.status in {
                "ANALYZING",
                "REPAIRING",
            }:
                task.status = "INTERRUPTED"
                task.next_action = "REQUEST_INPUT"
            recovered.append(run.run_id)
    return recovered
