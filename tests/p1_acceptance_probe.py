"""Opt-in real edit/collect/review/OpenHands repair acceptance in an isolated DB/repo.

Uses the already-authorized Zulong collector and real HTTP/model calls. It does
not adopt or modify production tasks. A private Redis DB carries real Celery jobs.
Run inside the API container: python -m tests.p1_acceptance_probe
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from uuid import uuid4

BASELINE = "38947448dfabd00d0a94b40d8a826dba98411d84"
KEY = "campus_zulong_com"
SOURCE_TASK = "6899917fb7d2438092eae1b79a77fc90"
ENTRY = "https://campus.zulong.com/campus_apply/zulong/25158/#/jobs"


def main():
    from auto_spider.config import get_settings

    settings = get_settings()
    from redis import Redis

    broker = Redis.from_url(settings.redis_url)
    occupied = broker.info("keyspace")
    database = next(n for n in range(15, 0, -1) if f"db{n}" not in occupied)
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(settings.redis_url)
    private_broker = urlunsplit((parts.scheme, parts.netloc, f"/{database}", "", ""))
    task_id, initial_run, batch_id = uuid4().hex, uuid4().hex, uuid4().hex
    root = settings.evidence_root / "p1-acceptance" / batch_id
    root.mkdir(parents=True)
    manifest_path = root / "manifest.json"
    manifest = {
        "status": "RUNNING",
        "task_id": task_id,
        "batch_id": batch_id,
        "production_task_modified": False,
        "real_collector": True,
        "real_agent": True,
        "real_celery": True,
        "steps": [],
        "redis_database": database,
    }

    def receipt(name, **data):
        manifest["steps"].append({"step": name, "at": datetime.now(UTC).isoformat(), **data})
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
        print(json.dumps({"step": name, **data}, ensure_ascii=False), flush=True)

    def git(path, *args):
        return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()

    source_repo = settings.aicoding_repo_path
    repo = root / "collector-repo"
    subprocess.run(
        ["git", "clone", "--shared", "--no-checkout", str(source_repo), str(repo)],
        check=True,
        capture_output=True,
    )
    git(repo, "checkout", "--detach", BASELINE)
    original_code = (repo / f"collectors/{KEY}.py").read_text("utf-8")
    with urllib.request.urlopen(
        f"http://api:8000/api/v1/onboarding/tasks/{SOURCE_TASK}/specs", timeout=15
    ) as response:
        original_spec = json.load(response)[0]["spec"]
    old_manifest = settings.evidence_root / original_spec["evidence"]["manifest_ref"]
    saved_analysis = json.loads(old_manifest.read_text("utf-8")) if old_manifest.is_file() else {}
    old_prefix = original_spec["task_id"]
    original_spec = json.loads(json.dumps(original_spec).replace(old_prefix, task_id))
    original_spec.update(task_id=task_id, spec_revision=1, spec_id=uuid4().hex, spec_hash=None)
    original_spec["generation"]["baseline_ref"] = BASELINE
    original_spec["evidence"]["manifest_ref"] = f"{task_id}/{initial_run}/analysis.json"
    db_url = f"sqlite:///{root / 'acceptance.db'}?timeout=30"
    os.environ.update(
        DATABASE_URL=db_url,
        REDIS_URL=private_broker,
        AICODING_REPO_PATH=str(repo),
        AICODING_BASELINE_REF=BASELINE,
        QUEUE_ENABLED="true",
        AGENT_MAX_ITERATIONS="60",
    )
    get_settings.cache_clear()
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from auto_spider.api.deps import get_session
    from auto_spider.api.main import app
    from auto_spider.db.base import Base
    from auto_spider.db.models import (
        AgentExecution,
        CodeSubmission,
        FailureBundle,
        ManualReview,
        ManualRun,
        OnboardingBatch,
        OnboardingReport,
        OnboardingTask,
        PlatformSpec,
        WorkflowRun,
    )
    from auto_spider.schemas import PlatformSpec as Spec
    from auto_spider.services.evidence import EvidenceStore

    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    spec = Spec.model_validate(original_spec).with_hash()
    with factory.begin() as session:
        session.add(
            OnboardingBatch(
                batch_id=batch_id, client_request_id=batch_id, requested_count=1, accepted_count=1
            )
        )
        session.flush()
        session.add(
            OnboardingTask(
                task_id=task_id,
                batch_id=batch_id,
                entry_url=ENTRY,
                normalized_url=ENTRY,
                platform_key=KEY,
                platform_name="祖龙 P1 隔离验收",
                status="WAITING_MANUAL_RUN",
                current_run_id=initial_run,
            )
        )
        session.flush()
        session.add(
            WorkflowRun(
                run_id=initial_run,
                task_id=task_id,
                run_type="onboarding",
                status="COMPLETED",
                context_json={"spec_binding": {"spec_revision": 1, "spec_hash": spec.spec_hash}},
            )
        )
        session.flush()
        session.add(
            PlatformSpec(
                task_id=task_id,
                spec_version=1,
                schema_version="1.0",
                spec_hash=spec.spec_hash,
                status="NEEDS_REVIEW",
                spec_json=spec.model_dump(mode="json"),
                evidence_manifest_ref=spec.evidence.manifest_ref,
            )
        )
        session.add(
            CodeSubmission(
                submission_id="baseline",
                task_id=task_id,
                run_id=initial_run,
                commit_sha=BASELINE,
                baseline_ref=BASELINE,
                branch_name="probe-baseline",
                changed_files=[],
            )
        )
        EvidenceStore().write_json(
            session,
            task_id=task_id,
            run_id=initial_run,
            name="analysis.json",
            payload=saved_analysis,
            file_type="analysis",
        )
        session.add(
            OnboardingReport(
                report_id=uuid4().hex,
                task_id=task_id,
                run_id=initial_run,
                observation_code="INTERNSHIPS_FOUND",
                technical_status="PARTIAL",
                next_action="WAIT_MANUAL_RUN",
                report_json={
                    "candidate_commit": BASELINE,
                    "spec_revision": 1,
                    "spec_hash": spec.spec_hash,
                    "observation_code": "INTERNSHIPS_FOUND",
                    "entry_url": ENTRY,
                },
            )
        )

    def dependency():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = dependency
    client = TestClient(
        app, headers={"X-User-Id": "p1-acceptance-reviewer", "X-User-Role": "admin"}
    )
    worker_log = (root / "worker.log").open("w", encoding="utf-8")
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
            "-Q",
            "analysis,coding,validation,reporting,maintenance",
            "-n",
            f"p1-{batch_id[:8]}",
        ],
        stdout=worker_log,
        stderr=subprocess.STDOUT,
        env=os.environ.copy(),
    )

    def post(path, data):
        response = client.post(path, json=data)
        assert response.status_code < 300, response.text
        return response.json()

    def wait_run(run_id):
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            with factory() as session:
                run = session.scalar(select(ManualRun).where(ManualRun.manual_run_id == run_id))
                if run.status not in {"QUEUED", "RUNNING"}:
                    assert run.status == "WAITING_REVIEW", run.result_json
                    return
            assert worker.poll() is None, "worker stopped"
            time.sleep(2)
        raise TimeoutError("manual collection timed out")

    try:
        faulty_code = original_code + (
            "\n    def collect(self) -> list[Any]:\n"
            "        records = super().collect()\n"
            "        for record in records:\n"
            "            job = record.raw_content['job']\n"
            "            job['title'] = '验收错误-' + job['title']\n"
            "        return records\n"
        )
        response = client.put(
            f"/api/v1/admin/tasks/{task_id}/code/baseline",
            json={
                "path": f"collectors/{KEY}.py",
                "content": faulty_code,
                "base_commit": BASELINE,
                "client_request_id": uuid4().hex,
            },
        )
        assert response.status_code == 200, response.text
        edited = response.json()
        assert edited["status"] == "candidate", edited
        receipt("edit", **edited)
        first_run = post(
            f"/api/v1/admin/tasks/{task_id}/collect",
            {
                "submission_id": edited["submission_id"],
                "client_request_id": uuid4().hex,
                "max_pages": 10,
                "request_interval_seconds": 1,
                "timeout_seconds": 600,
            },
        )["manual_run_id"]
        wait_run(first_run)
        rows = client.get(f"/api/v1/admin/records?manual_run_id={first_run}&page_size=100").json()[
            "records"
        ]
        assert rows and all(row["title"].startswith("验收错误-") for row in rows)
        receipt(
            "human_collection",
            manual_run_id=first_run,
            record_count=len(rows),
            code_revision=edited["commit_sha"],
        )
        issue = client.put(
            f"/api/v1/admin/runs/{first_run}/records/1/review",
            json={
                "status": "ISSUE",
                "field": "title",
                "note": (
                    "岗位标题被追加了‘验收错误-’。"
                    "请依据本条 raw_content.source_payload.detail.title "
                    "恢复官网标题，移除错误覆盖，并新增回归断言。不要改写保存的原始数据。"
                ),
            },
        )
        assert issue.status_code == 200, issue.text
        review = post(
            f"/api/v1/admin/runs/{first_run}/finalize",
            {"review_status": "CODE_FIX_REQUIRED", "client_request_id": uuid4().hex},
        )
        receipt("human_feedback", **review)
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
                    run = session.scalar(
                        select(WorkflowRun).where(WorkflowRun.run_id == task.current_run_id)
                    )
                    raise RuntimeError(
                        f"repair stopped: {task.status}: {run.error_message or run.error_code}"
                    )
            assert worker.poll() is None, "worker stopped"
            time.sleep(3)
        assert fixed, "AI repair did not produce a candidate"
        receipt("real_ai_repair", **fixed)
        second_run = post(
            f"/api/v1/admin/tasks/{task_id}/collect",
            {
                "submission_id": fixed["submission_id"],
                "client_request_id": uuid4().hex,
                "max_pages": 10,
                "request_interval_seconds": 1,
                "timeout_seconds": 600,
            },
        )["manual_run_id"]
        wait_run(second_run)
        fixed_rows = client.get(
            f"/api/v1/admin/records?manual_run_id={second_run}&page_size=100"
        ).json()["records"]
        assert fixed_rows
        for row in fixed_rows:
            assert "验收错误-" not in row["title"]
            assert row["title"] == row["raw_content"]["source_payload"]["detail"]["title"]
        receipt("human_recollection", manual_run_id=second_run, record_count=len(fixed_rows))
        final_review = post(
            f"/api/v1/admin/runs/{second_run}/finalize",
            {"review_status": "PASS", "client_request_id": uuid4().hex},
        )
        receipt("human_rereview", **final_review)
        with factory() as session:
            task = session.scalar(select(OnboardingTask).where(OnboardingTask.task_id == task_id))
            final = session.scalar(
                select(CodeSubmission).where(CodeSubmission.submission_id == fixed["submission_id"])
            )
            assert task.status == "ADOPTED" and final.adoption_status == "adopted"
            assert task.platform_id is None and task.entity_id is None
            bundle = session.scalar(
                select(FailureBundle).where(FailureBundle.bundle_id == review["failure_bundle_id"])
            )
            assert bundle.status == "REPAIRED" and bundle.artifact_manifest_ref
            manifest["failure_manifest_ref"] = bundle.artifact_manifest_ref
            agents = session.scalars(
                select(AgentExecution).where(AgentExecution.task_id == task_id)
            ).all()
            assert agents and all(agent.step_key != "inspect_site:0" for agent in agents)
            manifest["agent_executions"] = [
                {"conversation_id": a.conversation_id, "step": a.step_key, "status": a.status}
                for a in agents
            ]
            assert (
                len(
                    session.scalars(
                        select(ManualReview).where(ManualReview.task_id == task_id)
                    ).all()
                )
                == 2
            )
        old_rows = client.get(
            f"/api/v1/admin/records?manual_run_id={first_run}&page_size=100"
        ).json()["records"]
        assert all(row["title"].startswith("验收错误-") for row in old_rows)
        assert git(repo, "rev-parse", "HEAD") == BASELINE
        manifest["status"] = "PASS"
        manifest["final_commit"] = fixed["commit_sha"]
        manifest["original_records_preserved"] = True
        receipt("completed")
    except Exception as exc:
        manifest["status"], manifest["error"] = "FAILED", str(exc)
        receipt("failed", error=str(exc))
        raise
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=30)
        except subprocess.TimeoutExpired:
            # Preserve the handle and state; do not kill an Agent conversation.
            print(f"acceptance worker still shutting down: {worker.pid}", flush=True)
        worker_log.close()
        app.dependency_overrides.clear()
        engine.dispose()


if __name__ == "__main__":
    main()
