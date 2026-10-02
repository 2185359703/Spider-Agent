"""Opt-in nine-entry real onboarding acceptance in an isolated database, repository and broker."""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4


def main():
    from urllib.parse import urlsplit, urlunsplit

    from redis import Redis

    from auto_spider.config import get_settings

    settings = get_settings()
    run_id = uuid4().hex
    root = settings.evidence_root / "batch-acceptance" / run_id
    root.mkdir(parents=True)
    sqlite_root = Path("/tmp") / f"auto-spider-batch-{run_id}"
    sqlite_root.mkdir()
    db_url = f"sqlite:///{sqlite_root / 'acceptance.db'}?timeout=30"
    broker = Redis.from_url(settings.redis_url)
    occupied = broker.info("keyspace")
    database = next(n for n in range(15, 0, -1) if f"db{n}" not in occupied)
    parts = urlsplit(settings.redis_url)
    broker_url = urlunsplit((parts.scheme, parts.netloc, f"/{database}", "", ""))
    repo = root / "collector-repo"
    baseline = settings.aicoding_baseline_ref
    subprocess.run(
        ["git", "clone", "--shared", "--no-checkout", str(settings.aicoding_repo_path), str(repo)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "--detach", baseline], check=True, capture_output=True
    )
    os.environ.update(
        DATABASE_URL=db_url,
        REDIS_URL=broker_url,
        AICODING_REPO_PATH=str(repo),
        QUEUE_ENABLED="true",
        AGENT_MAX_ITERATIONS="100",
    )
    get_settings.cache_clear()
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from auto_spider.api.deps import get_session
    from auto_spider.api.main import app
    from auto_spider.db.base import Base
    from auto_spider.db.models import AgentExecution, CodeSubmission, OnboardingTask
    from auto_spider.services.batch_report import build_batch_report
    from auto_spider.services.resource_cleanup import cleanup_resources

    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def dependency():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = dependency
    log = (root / "worker.log").open("w", encoding="utf-8")
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
            f"batch-{run_id[:8]}",
        ],
        env=os.environ.copy(),
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    manifest = {
        "status": "RUNNING",
        "real_agent": True,
        "real_celery": True,
        "production_tasks_modified": False,
        "database_url": db_url,
        "redis_database": database,
        "entries": [],
        "baseline": baseline,
    }

    def save():
        (root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8"
        )

    save()
    try:
        client = TestClient(app, headers={"X-User-Role": "admin", "X-User-Id": "batch-acceptance"})
        text = (Path(__file__).parent / "fixtures/company_matrix.txt").read_text("utf-8")
        preview = client.post("/api/v1/admin/company-import/preview", json={"text": text})
        assert preview.status_code == 200 and preview.json()["valid"] == 9, preview.text
        response = client.post(
            "/api/v1/admin/company-import", json={"text": text, "client_request_id": run_id}
        )
        assert response.status_code == 201 and response.json()["accepted_count"] == 9, response.text
        manifest["batch_id"] = response.json()["batch_id"]
        print(json.dumps({"batch_id": manifest["batch_id"], "accepted": 9}), flush=True)
        save()
        terminal = {
            "WAITING_MANUAL_RUN",
            "WAITING_MANUAL_REVIEW",
            "ADOPTED",
            "NO_DATA_CONFIRMED",
            "BLOCKED",
            "FAILED",
            "REJECTED",
            "CANCELLED",
            "TIMED_OUT",
        }
        previous = None
        deadline = time.monotonic() + 14400
        while time.monotonic() < deadline:
            if worker.poll() is not None:
                raise RuntimeError("BATCH_WORKER_STOPPED")
            with factory() as session:
                report = build_batch_report(session, manifest["batch_id"])
                manifest["entries"] = report["entries"]
                states = [(e["company_name"], e["status"]) for e in report["entries"]]
                if states != previous:
                    print(json.dumps({"states": states}, ensure_ascii=False), flush=True)
                    previous = states
                save()
                if len(states) == 9 and all(state in terminal for _, state in states):
                    break
            time.sleep(5)
        else:
            raise TimeoutError("BATCH_ACCEPTANCE_TIME_LIMIT")
        with factory() as session:
            tasks = list(session.scalars(select(OnboardingTask)))
            assert len(tasks) == 9 and all(
                t.platform_id is None and t.entity_id is None for t in tasks
            )
            executions = list(session.scalars(select(AgentExecution)))
            assert len({e.task_id for e in executions}) == 9
            candidates = list(
                session.scalars(
                    select(CodeSubmission).where(CodeSubmission.adoption_status == "candidate")
                )
            )
            manifest["candidate_count"] = len(candidates)
            manifest["candidate_commits"] = [c.commit_sha for c in candidates]
        head = subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
        ).strip()
        assert head == baseline
        manifest["cleanup"] = cleanup_resources(factory)
        with (
            sqlite3.connect(sqlite_root / "acceptance.db") as source,
            sqlite3.connect(root / "acceptance-export.db") as destination,
        ):
            source.backup(destination)
        manifest["database_export_sha256"] = hashlib.sha256(
            (root / "acceptance-export.db").read_bytes()
        ).hexdigest()
        # A terminal state alone is not acceptance; failure reports need inspection.
        manifest["status"] = "REQUIRES_AUDIT"
        save()
        print(
            json.dumps({"status": manifest["status"], "manifest": str(root / "manifest.json")}),
            flush=True,
        )
    except Exception as exc:
        manifest.update(status="FAILED", error=str(exc))
        save()
        raise
    finally:
        worker.terminate()
        worker.wait(timeout=30)
        log.close()
        app.dependency_overrides.clear()
        engine.dispose()


if __name__ == "__main__":
    main()
