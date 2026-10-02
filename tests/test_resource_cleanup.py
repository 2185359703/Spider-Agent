from sqlalchemy.orm import sessionmaker

from auto_spider.services.resource_cleanup import (
    batch_reclaimable,
    cleanup_resources,
    clear_checkout_cache,
)
from auto_spider.db.models import AgentExecution, WorkflowRun
from tests.test_workflow import make_task


def test_cache_cleanup_preserves_code_and_outside_paths(tmp_path):
    root = tmp_path / "worktrees"
    workspace = root / "task" / "run"
    workspace.mkdir(parents=True)
    code = workspace / "collector.py"
    code.write_text("real code")
    cache = workspace / ".pytest_cache"
    cache.mkdir()
    (cache / "tmp").write_text("cache")
    outside = tmp_path / "outside"
    outside.mkdir()
    assert clear_checkout_cache(outside, root) == 0
    assert clear_checkout_cache(workspace, root) == 5
    assert not cache.exists() and code.read_text() == "real code"


def test_active_or_paused_batch_is_never_reclaimed(db_session):
    task = make_task(db_session)
    task.status = "ANALYZING"
    db_session.commit()
    assert not batch_reclaimable(db_session, task.batch_id)
    task.status = "PAUSED"
    db_session.commit()
    assert not batch_reclaimable(db_session, task.batch_id)
    task.status = "WAITING_MANUAL_RUN"
    db_session.commit()
    assert batch_reclaimable(db_session, task.batch_id)


def test_cleanup_without_agents_only_closes_finished_batch(db_session):
    import httpx

    task = make_task(db_session)
    task.status = "WAITING_MANUAL_REVIEW"
    db_session.commit()
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    calls = []

    def handle(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"released": True})

    client = httpx.Client(base_url="http://agent.test", transport=httpx.MockTransport(handle))
    result = cleanup_resources(factory, client)
    assert result["batches_released"] == 1
    assert all("conversations" not in path for path in calls)
    assert db_session.get(type(task), task.id) is not None
    client.close()


def test_live_lease_keeps_finished_looking_batch(db_session):
    from datetime import UTC, datetime, timedelta

    from auto_spider.db.models import ExecutionLease, WorkflowRun

    task = make_task(db_session)
    task.status = "WAITING_MANUAL_REVIEW"
    db_session.add(WorkflowRun(run_id="r" * 32, task_id=task.task_id, run_type="test"))
    db_session.flush()
    lease = ExecutionLease(
        resource_key="lease",
        run_id="r" * 32,
        owner="owner",
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    db_session.add(lease)
    db_session.commit()
    assert not batch_reclaimable(db_session, task.batch_id)
    lease.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    assert batch_reclaimable(db_session, task.batch_id)


def test_disconnected_agent_can_enter_cleanup_reconciliation(db_session):
    task = make_task(db_session)
    task.status = "FAILED"
    db_session.add(WorkflowRun(run_id="d" * 32, task_id=task.task_id, run_type="onboarding"))
    db_session.flush()
    db_session.add(
        AgentExecution(
            execution_id="e" * 32,
            task_id=task.task_id,
            run_id="d" * 32,
            step_key="inspect_site:0",
            conversation_id="conversation",
            prompt_hash="hash",
            status="DISCONNECTED",
            workspace_path="/tmp/worktree",
            server_url="http://agent.test",
            mode="read",
            result_json={},
        )
    )
    db_session.commit()
    assert batch_reclaimable(db_session, task.batch_id)


def test_cleanup_retries_company_release_then_closes_batch_once(db_session, tmp_path, monkeypatch):
    import httpx

    from auto_spider.config import get_settings
    from auto_spider.db.models import AgentExecution, WorkflowRun

    settings = get_settings()
    monkeypatch.setattr(settings, "worktree_root", tmp_path / "worktrees")
    monkeypatch.setattr(settings, "agent_policy_root", tmp_path / "policies")
    settings.agent_policy_root.mkdir()
    task = make_task(db_session)
    task.status = "WAITING_MANUAL_REVIEW"
    run_id, execution_id = "a" * 32, "b" * 32
    db_session.add(WorkflowRun(run_id=run_id, task_id=task.task_id, run_type="test"))
    db_session.flush()
    row = AgentExecution(
        execution_id=execution_id,
        task_id=task.task_id,
        run_id=run_id,
        step_key="test",
        conversation_id="convo",
        prompt_hash="hash",
        status="COMPLETED",
        workspace_path=str(settings.worktree_root / "test"),
        server_url="http://agent.test",
        result_json={"final_response": "kept"},
    )
    db_session.add(row)
    db_session.commit()
    policy = settings.agent_policy_root / f"{execution_id}.json"
    policy.write_text("{}")
    calls = []

    def handle(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(
            200,
            json={
                "released": True,
                "bytes": 5,
                "execution_status": "finished",
            },
        )

    client = httpx.Client(base_url="http://agent.test", transport=httpx.MockTransport(handle))
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    result = cleanup_resources(factory, client)
    assert result["agent_sessions_released"] == 1 and result["cache_bytes_released"] == 5
    assert calls[0] == ("POST", f"/api/collector-browser/{execution_id}/close")
    assert "batches" in calls[-2][1] and not policy.exists()
    db_session.refresh(row)
    assert row.result_json["final_response"] == "kept"
    assert row.result_json["batch_browser_released_at"]
    calls.clear()
    result = cleanup_resources(factory, client)
    assert result["batches_released"] == result["agent_sessions_released"] == 0
    assert calls == [("POST", "/api/collector-browser/cleanup/idle")]
    client.close()
