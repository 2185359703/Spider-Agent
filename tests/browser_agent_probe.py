"""Opt-in real model/browser batch check, using a separate SQLite database.

Run `python -m tests.browser_agent_probe` in the API container. No collector is
generated, committed, adopted or run; only authorized public recruitment pages
are read. The resulting manifest is kept with the integration evidence.
"""

import hashlib
import json
import sys
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from auto_spider.ai.openhands_gateway import OpenHandsGateway
from auto_spider.config import get_settings
from auto_spider.db.base import Base
from auto_spider.db.models import AgentExecution, OnboardingBatch, OnboardingTask, WorkflowRun
from auto_spider.services.evidence import EvidenceStore
from auto_spider.services.execution import ExecutionContext
from auto_spider.services.resource_cleanup import cleanup_resources


def main(mode="run"):
    settings = get_settings()
    if mode == "cleanup":
        manifests = list((settings.evidence_root / "runtime-verification").glob("*/manifest.json"))
        path = max(
            (p for p in manifests if json.loads(p.read_text()).get("status") == "AWAITING_RESTART"),
            key=lambda p: p.stat().st_mtime,
        )
        manifest = json.loads(path.read_text())
        engine = create_engine(f"sqlite:///{path.parent / 'browser-probe.db'}")
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        manifest["cleanup"] = cleanup_resources(factory)
        assert manifest["cleanup"]["agent_sessions_released"] == 2
        assert manifest["cleanup"]["batches_released"] == 1
        assert manifest["cleanup"]["retryable_errors"] == 0
        manifest["server_restart_cleanup"] = "PASS"
        manifest["status"] = "PASS"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        print(json.dumps(manifest, ensure_ascii=False), flush=True)
        engine.dispose()
        return
    settings.agent_max_iterations = 8
    batch_id = uuid4().hex
    root = settings.evidence_root / "runtime-verification" / batch_id
    root.mkdir(parents=True)
    engine = create_engine(
        f"sqlite:///{root / 'browser-probe.db'}", connect_args={"check_same_thread": False}
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with factory.begin() as session:
        session.add(
            OnboardingBatch(
                batch_id=batch_id, client_request_id=batch_id, requested_count=2, accepted_count=2
            )
        )
    companies = [
        ("zulong_probe", "https://campus.zulong.com/campus_apply/zulong/25158/#/jobs"),
        ("lilith_probe", "https://lilithgames.jobs.feishu.cn/intern/"),
    ]
    manifest = {"batch_id": batch_id, "companies": [], "simulated": False}
    for key, entry in companies:
        task_id, run_id = uuid4().hex, uuid4().hex
        workspace = settings.worktree_root / task_id / run_id / "browser-probe"
        workspace.mkdir(parents=True)
        with factory.begin() as session:
            session.add(
                OnboardingTask(
                    task_id=task_id,
                    batch_id=batch_id,
                    entry_url=entry,
                    normalized_url=entry,
                    platform_key=key,
                    status="ANALYZING",
                )
            )
            session.flush()
            session.add(WorkflowRun(run_id=run_id, task_id=task_id, run_type="browser-probe"))
        actions = []

        def event_callback(event, actions=actions, key=key):
            action = getattr(event, "action", None)
            if type(action).__name__ == "CollectorBrowserAction":
                actions.append(action.action)
                print(json.dumps({"company": key, "browser_action": action.action}), flush=True)

        with ExecutionContext(factory, run_id, f"browser-probe:{task_id}") as context:
            result = OpenHandsGateway().run(
                "这是浏览器工具接入验证，只读公开招聘页面。必须调用 CollectorBrowserTool "
                f"open 打开 {entry}，然后 snapshot 和 requests。若页面加载慢，可重读 snapshot；"
                "这三个动作后结束并简短说明实际观察到什么。不要进一步探索、生成文件、"
                "提交或采集岗位。不要主动 close，平台会释放公司页面并保留批次浏览器。",
                workspace,
                review_only=True,
                execution=context,
                task_id=task_id,
                step_key="browser-probe",
                event_callback=event_callback,
                spec={"generation": {"allowed_files": []}, "evidence": {}},
            )
        assert {"open", "snapshot", "requests"} <= set(actions), actions
        with factory.begin() as session:
            execution = session.scalar(
                select(AgentExecution).where(AgentExecution.run_id == run_id)
            )
            assert execution.status == "COMPLETED"
            policy = json.loads(
                (settings.agent_policy_root / f"{execution.execution_id}.json").read_text()
            )
            expected_group = hashlib.sha256(f"batch:{batch_id}".encode()).hexdigest()[:32]
            assert policy["browser_group"] == expected_group
            captures = list((settings.evidence_root / task_id).glob("**/agent-browser/*/*.json"))
            assert len(captures) >= 3
            assert any(json.loads(p.read_text()).get("action") == "requests" for p in captures)
            launches = {json.loads(p.read_text()).get("browser_launches") for p in captures}
            assert launches == {1}, "The batch browser unexpectedly restarted"
            EvidenceStore().register_browser_files(session, task_id=task_id, run_id=run_id)
            task = session.scalar(select(OnboardingTask).where(OnboardingTask.task_id == task_id))
            task.status = "WAITING_MANUAL_REVIEW"
        manifest["companies"].append(
            {
                "task_id": task_id,
                "run_id": run_id,
                "key": key,
                "actions": actions,
                "browser_group": expected_group,
                "capture_count": len(captures),
                "response_present": bool(result.final_response),
            }
        )
    if mode == "prepare":
        manifest["status"] = "AWAITING_RESTART"
        (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        print(json.dumps(manifest, ensure_ascii=False), flush=True)
        engine.dispose()
        return
    manifest["cleanup"] = cleanup_resources(factory)
    assert manifest["cleanup"]["agent_sessions_released"] == 2
    assert manifest["cleanup"]["batches_released"] == 1
    assert manifest["cleanup"]["retryable_errors"] == 0
    manifest["status"] = "PASS"
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps(manifest, ensure_ascii=False), flush=True)
    engine.dispose()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "run")
