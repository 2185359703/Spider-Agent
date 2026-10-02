import json

from sqlalchemy import select

from auto_spider.ai.workspace_access import WorkspaceAccess
from auto_spider.db.models import AgentExecution, EvidenceFile, WorkflowRun
from auto_spider.services.evidence import EvidenceStore
from tests.test_workflow import make_task


def test_browser_evidence_is_indexed_once_and_survives_cache_cleanup(db_session, tmp_path):
    task = make_task(db_session)
    run_id, execution_id = "a" * 32, "b" * 32
    db_session.add(WorkflowRun(run_id=run_id, task_id=task.task_id, run_type="test"))
    db_session.flush()
    db_session.add(
        AgentExecution(
            execution_id=execution_id,
            task_id=task.task_id,
            run_id=run_id,
            step_key="inspect_site",
            conversation_id="convo",
            workspace_path="checkout",
            server_url="http://agent.test",
            prompt_hash="hash",
            status="COMPLETED",
        )
    )
    db_session.commit()
    root = tmp_path / "evidence"
    capture = root / task.task_id / run_id / "agent-browser" / execution_id / "0001-open.json"
    capture.parent.mkdir(parents=True)
    capture.write_text(json.dumps({"action": "open", "output": "real page evidence"}))
    store = EvidenceStore(root)
    store.register_browser_files(db_session, task_id=task.task_id, run_id=run_id)
    store.register_browser_files(db_session, task_id=task.task_id, run_id=run_id)
    rows = list(db_session.scalars(select(EvidenceFile)))
    assert len(rows) == 1 and rows[0].file_type == "browser_cli"
    assert rows[0].relative_path == capture.relative_to(root).as_posix()
    assert capture.exists()


def test_tool_can_read_returned_evidence_reference_within_owned_root(tmp_path):
    root = tmp_path / "evidence"
    root.mkdir()
    (root / "capture.json").write_text('{"action":"snapshot"}')
    access = WorkspaceAccess(
        {
            "workspace": str(tmp_path),
            "evidence": str(root),
            "mode": "read",
            "allowed_files": [],
            "evidence_prefix": "task/run",
        }
    )
    assert "snapshot" in access.read("task/run/capture.json", area="evidence")
