"""Release temporary resources for finished onboarding batches; retain durable business data."""

import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import select

from auto_spider.config import get_settings
from auto_spider.db.models import (
    AgentExecution,
    ExecutionLease,
    OnboardingTask,
    WorkflowDispatch,
    WorkflowRun,
)

FINISHED_PHASES = {
    "NO_DATA_CONFIRMED",
    "WAITING_MANUAL_RUN",
    "WAITING_MANUAL_REVIEW",
    "ADOPTED",
    "BLOCKED",
    "FAILED",
    "REJECTED",
    "CANCELLED",
    "TIMED_OUT",
}
TERMINAL_AGENTS = {"COMPLETED", "CANCELLED", "TIMED_OUT", "DISCONNECTED"}


def batch_reclaimable(session, batch_id):
    tasks = list(session.scalars(select(OnboardingTask).where(OnboardingTask.batch_id == batch_id)))
    if not tasks or any(t.status not in FINISHED_PHASES for t in tasks):
        return False
    ids = [t.task_id for t in tasks]
    if session.scalar(
        select(WorkflowDispatch.dispatch_id)
        .where(
            WorkflowDispatch.task_id.in_(ids),
            WorkflowDispatch.status.in_(["PENDING", "SENT", "RUNNING"]),
        )
        .limit(1)
    ):
        return False
    if session.scalar(
        select(AgentExecution.execution_id)
        .where(AgentExecution.task_id.in_(ids), AgentExecution.status.not_in(TERMINAL_AGENTS))
        .limit(1)
    ):
        return False
    if session.scalar(
        select(ExecutionLease.resource_key)
        .join(WorkflowRun, WorkflowRun.run_id == ExecutionLease.run_id)
        .where(WorkflowRun.task_id.in_(ids), ExecutionLease.expires_at > datetime.now(UTC))
        .limit(1)
    ):
        return False
    return True


def clear_checkout_cache(path, root):
    root = Path(root).resolve()
    path = Path(path)
    resolved = path.resolve()
    if root not in resolved.parents or path.is_symlink() or not resolved.is_dir():
        return 0
    reclaimed = 0
    for name in (".pytest_cache", ".ruff_cache", "__pycache__"):
        for folder in list(resolved.rglob(name)):
            checked = folder.resolve()
            if folder.is_symlink() or resolved not in checked.parents or not folder.is_dir():
                continue
            reclaimed += sum(
                p.stat().st_size for p in folder.rglob("*") if p.is_file() and not p.is_symlink()
            )
            shutil.rmtree(checked)
    return reclaimed


def cleanup_resources(factory, client=None):
    settings = get_settings()
    with factory() as session:
        batches = list(session.scalars(select(OnboardingTask.batch_id).distinct()))
        eligible = [bid for bid in batches if batch_reclaimable(session, bid)]
    own_client = client is None
    client = client or httpx.Client(
        base_url=settings.openhands_server_url,
        headers={"X-Session-API-Key": settings.openhands_session_api_key or ""},
        timeout=10,
    )
    result = {
        "batches_released": 0,
        "agent_sessions_released": 0,
        "cache_bytes_released": 0,
        "retryable_errors": 0,
    }
    try:
        for bid in eligible:
            try:
                # Lock and recheck immediately before reclamation, including active leases.
                with factory.begin() as session:
                    session.scalars(
                        select(OnboardingTask)
                        .where(OnboardingTask.batch_id == bid)
                        .with_for_update()
                    ).all()
                    if not batch_reclaimable(session, bid):
                        continue
                    executions = session.scalars(
                        select(AgentExecution)
                        .join(OnboardingTask, OnboardingTask.task_id == AgentExecution.task_id)
                        .where(
                            OnboardingTask.batch_id == bid,
                            AgentExecution.status.in_(TERMINAL_AGENTS),
                        )
                        .with_for_update()
                    ).all()
                    for row in executions:
                        if (row.result_json or {}).get("resources_released_at"):
                            continue
                        # Retry missed company release before attempting batch closure.
                        policy = settings.agent_policy_root.resolve() / f"{row.execution_id}.json"
                        if policy.is_file() and not policy.is_symlink():
                            released = client.post(
                                f"/api/collector-browser/{row.execution_id}/close"
                            )
                            if not released.is_success or not released.json().get("released"):
                                continue
                        response = client.get(f"/api/conversations/{row.conversation_id}")
                        if response.status_code != 404:
                            if not response.is_success or response.json().get(
                                "execution_status", ""
                            ).lower() not in {"finished", "paused", "error", "stuck", "idle"}:
                                continue
                            deleted = client.delete(f"/api/conversations/{row.conversation_id}")
                            if not deleted.is_success and deleted.status_code != 404:
                                continue
                        result["cache_bytes_released"] += clear_checkout_cache(
                            row.workspace_path, settings.worktree_root
                        )
                        if policy.is_file() and not policy.is_symlink():
                            policy.unlink()
                        row.result_json = {
                            **(row.result_json or {}),
                            "resources_released_at": datetime.now(UTC).isoformat(),
                        }
                        result["agent_sessions_released"] += 1
                    if not executions or not all(
                        (row.result_json or {}).get("batch_browser_released_at")
                        for row in executions
                    ):
                        group = hashlib.sha256(f"batch:{bid}".encode()).hexdigest()[:32]
                        response = client.post(f"/api/collector-browser/batches/{group}/close")
                        if response.is_success and response.json().get("released"):
                            result["batches_released"] += 1
                            result["cache_bytes_released"] += response.json().get("bytes", 0)
                            for row in executions:
                                row.result_json = {
                                    **(row.result_json or {}),
                                    "batch_browser_released_at": datetime.now(UTC).isoformat(),
                                }
            except (httpx.HTTPError, OSError, ValueError):
                # A transient failure must not stop other batches or discard their evidence.
                result["retryable_errors"] += 1
        try:
            client.post("/api/collector-browser/cleanup/idle")
        except httpx.HTTPError:
            result["retryable_errors"] += 1
    finally:
        if own_client:
            client.close()
    return result
