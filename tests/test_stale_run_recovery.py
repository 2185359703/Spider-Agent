from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import sessionmaker

from auto_spider.db.models import AgentExecution, WorkflowRun
from auto_spider.services.agent_control import reconcile_stale_runs
from tests.test_workflow import make_task


def factory_for(session):
    return sessionmaker(bind=session.get_bind(), expire_on_commit=False)


def test_orphaned_running_run_is_marked_interrupted_without_touching_waiting_task(db_session):
    task = make_task(db_session)
    task.status = "WAITING_MANUAL_REVIEW"
    run = WorkflowRun(
        run_id="stale-run",
        task_id=task.task_id,
        run_type="onboarding",
        status="RUNNING",
        started_at=datetime.now(UTC) - timedelta(hours=2),
    )
    db_session.add(run)
    db_session.commit()

    recovered = reconcile_stale_runs(factory_for(db_session))

    db_session.refresh(run)
    db_session.refresh(task)
    assert recovered == ["stale-run"]
    assert run.status == "INTERRUPTED"
    assert run.error_code == "STALE_EXECUTION"
    assert task.status == "WAITING_MANUAL_REVIEW"


def test_live_agent_is_not_reclaimed(db_session):
    task = make_task(db_session)
    run = WorkflowRun(
        run_id="live-run",
        task_id=task.task_id,
        run_type="onboarding",
        status="RUNNING",
        started_at=datetime.now(UTC) - timedelta(hours=2),
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(
        AgentExecution(
            execution_id="e" * 32,
            task_id=task.task_id,
            run_id=run.run_id,
            step_key="inspect_site:0",
            conversation_id="00000000-0000-4000-8000-000000000001",
            workspace_path="/tmp/workspace",
            server_url="http://agent",
            prompt_hash="f" * 64,
            status="RUNNING",
        )
    )
    db_session.commit()

    assert reconcile_stale_runs(factory_for(db_session)) == []
    db_session.refresh(run)
    assert run.status == "RUNNING"


def test_disconnected_agent_is_reclaimed_for_checkpoint_resume(db_session):
    task = make_task(db_session)
    run = WorkflowRun(
        run_id="disconnected-run",
        task_id=task.task_id,
        run_type="onboarding",
        status="RUNNING",
        started_at=datetime.now(UTC) - timedelta(hours=2),
    )
    db_session.add(run)
    db_session.flush()
    db_session.add(
        AgentExecution(
            execution_id="d" * 32,
            task_id=task.task_id,
            run_id=run.run_id,
            step_key="patch_code:1",
            conversation_id="00000000-0000-4000-8000-000000000002",
            workspace_path="/tmp/workspace",
            server_url="http://agent",
            prompt_hash="e" * 64,
            status="DISCONNECTED",
        )
    )
    db_session.commit()

    assert reconcile_stale_runs(factory_for(db_session)) == ["disconnected-run"]
    db_session.refresh(run)
    assert run.status == "INTERRUPTED"
