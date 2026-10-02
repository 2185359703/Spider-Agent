"""Resume the original P1 acceptance run after fixing a platform-level defect."""

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from uuid import UUID, uuid4


def main(batch):
    from auto_spider.config import get_settings

    settings = get_settings()
    batch = UUID(batch).hex
    root = settings.evidence_root / "p1-acceptance" / batch
    manifest = json.loads((root / "manifest.json").read_text("utf-8"))
    task_id = manifest["task_id"]
    from urllib.parse import urlsplit, urlunsplit

    uri = urlsplit(settings.redis_url)
    broker = urlunsplit((uri.scheme, uri.netloc, f"/{manifest['redis_database']}", "", ""))
    os.environ.update(
        DATABASE_URL=manifest.get("database_url", f"sqlite:///{root / 'acceptance.db'}?timeout=30"),
        REDIS_URL=broker,
        AICODING_REPO_PATH=str(root / "collector-repo"),
        AICODING_BASELINE_REF="38947448dfabd00d0a94b40d8a826dba98411d84",
        QUEUE_ENABLED="true",
        AGENT_MAX_ITERATIONS="60",
    )
    get_settings.cache_clear()
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from auto_spider.api.deps import get_session
    from auto_spider.api.main import app
    from auto_spider.db.models import (
        AgentExecution,
        CodeSubmission,
        FailureBundle,
        ManualRun,
        OnboardingTask,
        WorkflowDispatch,
        WorkflowRun,
    )

    engine = create_engine(os.environ["DATABASE_URL"], connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def dependency():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = dependency
    client = TestClient(
        app, headers={"X-User-Id": "p1-acceptance-reviewer", "X-User-Role": "admin"}
    )
    log = (root / "worker-resume.log").open("a", encoding="utf-8")
    worker = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "auto_spider.workers.celery_app:celery_app",
            "worker",
            "--pool=solo",
            "--concurrency=1",
            "--loglevel=WARNING",
            "--beat",
            "--schedule",
            f"/tmp/p1-beat-{batch}.db",
            "-Q",
            "analysis,coding,validation,reporting,maintenance",
            "-n",
            f"p1-resume-{batch[:8]}",
        ],
        env=os.environ.copy(),
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    edited = next(step for step in manifest["steps"] if step["step"] == "edit")
    original_run = next(step for step in manifest["steps"] if step["step"] == "human_collection")
    feedback = next(step for step in manifest["steps"] if step["step"] == "human_feedback")

    def receipt(name, **data):
        manifest["steps"].append({"step": name, "at": datetime.now(UTC).isoformat(), **data})
        (root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8"
        )
        print(json.dumps({"step": name, **data}, ensure_ascii=False), flush=True)

    def post(path, data):
        response = client.post(path, json=data)
        assert response.status_code < 300, response.text
        return response.json()

    try:
        result = post(
            f"/api/v1/onboarding/tasks/{task_id}/retry", {"client_request_id": uuid4().hex}
        )
        manifest["status"] = "RUNNING"
        manifest.pop("error", None)
        receipt("resume_original_repair", run_id=result["run_id"])
        deadline = time.monotonic() + 2400
        fixed = None
        while time.monotonic() < deadline:
            with factory() as session:
                task = session.scalar(
                    select(OnboardingTask).where(OnboardingTask.task_id == task_id)
                )
                candidate = session.scalar(
                    select(CodeSubmission)
                    .where(
                        CodeSubmission.task_id == task_id,
                        CodeSubmission.adoption_status == "candidate",
                    )
                    .order_by(CodeSubmission.id.desc())
                )
                if (
                    candidate
                    and candidate.commit_sha != edited["commit_sha"]
                    and task.status == "WAITING_MANUAL_RUN"
                ):
                    fixed = {
                        "submission_id": candidate.submission_id,
                        "commit_sha": candidate.commit_sha,
                    }
                    break
                if task.status in {"BLOCKED", "FAILED", "TIMED_OUT", "CANCELLED"}:
                    retry_pending = task.status == "FAILED" and session.scalar(
                        select(WorkflowDispatch.dispatch_id)
                        .where(
                            WorkflowDispatch.task_id == task_id,
                            WorkflowDispatch.status.in_(["PENDING", "SENT"]),
                        )
                        .limit(1)
                    )
                    if not retry_pending:
                        run = session.scalar(
                            select(WorkflowRun).where(WorkflowRun.run_id == task.current_run_id)
                        )
                        raise RuntimeError(
                            f"repair stopped: {task.status}: {run.error_message or run.error_code}"
                        )
            assert worker.poll() is None, "worker stopped"
            time.sleep(3)
        assert fixed, "repair did not complete"
        receipt("real_ai_repair", **fixed)
        run_id = post(
            f"/api/v1/admin/tasks/{task_id}/collect",
            {
                "submission_id": fixed["submission_id"],
                "client_request_id": uuid4().hex,
                "max_pages": 10,
                "request_interval_seconds": 1,
                "timeout_seconds": 600,
            },
        )["manual_run_id"]
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            with factory() as session:
                run = session.scalar(select(ManualRun).where(ManualRun.manual_run_id == run_id))
                if run.status not in {"QUEUED", "RUNNING"}:
                    assert run.status == "WAITING_REVIEW", run.result_json
                    break
            time.sleep(2)
        rows = client.get(f"/api/v1/admin/records?manual_run_id={run_id}&page_size=100").json()[
            "records"
        ]
        assert rows and all("验收错误-" not in row["title"] for row in rows)
        for row in rows:
            assert row["title"] == row["raw_content"]["source_payload"]["detail"]["title"]
        receipt("human_recollection", manual_run_id=run_id, record_count=len(rows))
        final = post(
            f"/api/v1/admin/runs/{run_id}/finalize",
            {"review_status": "PASS", "client_request_id": uuid4().hex},
        )
        receipt("human_rereview", **final)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            with factory() as session:
                task = session.scalar(
                    select(OnboardingTask).where(OnboardingTask.task_id == task_id)
                )
                final_run = session.scalar(
                    select(WorkflowRun).where(WorkflowRun.run_id == task.current_run_id)
                )
                pending = session.scalar(
                    select(WorkflowDispatch.dispatch_id)
                    .where(
                        WorkflowDispatch.task_id == task_id,
                        WorkflowDispatch.status.in_(["PENDING", "SENT"]),
                    )
                    .limit(1)
                )
                if final_run.status == "COMPLETED" and not pending:
                    break
            time.sleep(2)
        else:
            raise TimeoutError("final review did not settle")
        with factory() as session:
            task = session.scalar(select(OnboardingTask).where(OnboardingTask.task_id == task_id))
            code = session.scalar(
                select(CodeSubmission).where(CodeSubmission.submission_id == fixed["submission_id"])
            )
            bundle = session.scalar(
                select(FailureBundle).where(
                    FailureBundle.bundle_id == feedback["failure_bundle_id"]
                )
            )
            assert task.status == "ADOPTED" and code.adoption_status == "adopted"
            assert (
                bundle.status == "REPAIRED" and task.platform_id is None and task.entity_id is None
            )
            agents = session.scalars(
                select(AgentExecution).where(AgentExecution.task_id == task_id)
            ).all()
            assert agents and all(agent.step_key != "inspect_site:0" for agent in agents)
            manifest["agent_executions"] = [
                {"conversation_id": a.conversation_id, "step": a.step_key, "status": a.status}
                for a in agents
            ]
            manifest["failure_manifest_ref"] = bundle.artifact_manifest_ref
        old = client.get(
            f"/api/v1/admin/records?manual_run_id={original_run['manual_run_id']}&page_size=100"
        ).json()["records"]
        assert all(row["title"].startswith("验收错误-") for row in old)
        manifest.update(
            status="PASS", final_commit=fixed["commit_sha"], original_records_preserved=True
        )
        receipt("completed")
    except Exception as exc:
        manifest.update(status="FAILED", error=str(exc))
        receipt("failed", error=str(exc))
        raise
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=30)
        except subprocess.TimeoutExpired:
            print(f"worker still shutting down: {worker.pid}", flush=True)
        log.close()
        app.dependency_overrides.clear()
        engine.dispose()


if __name__ == "__main__":
    main(sys.argv[1])
