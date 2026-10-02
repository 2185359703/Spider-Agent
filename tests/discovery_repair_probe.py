"""Opt-in real model replay of a saved discovery error; never reruns website collection."""

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from auto_spider.config import get_settings
from auto_spider.db.base import Base
from auto_spider.db.models import AgentExecution, OnboardingBatch, OnboardingTask, WorkflowRun
from auto_spider.services.execution import ExecutionContext
from auto_spider.workflows.runner import WorkflowRunner


def main(relative):
    settings = get_settings()
    source = (settings.evidence_root / relative).resolve()
    if settings.evidence_root.resolve() not in source.parents or source.name != "analysis.json":
        raise ValueError("An existing task-scoped analysis.json is required")
    analysis = json.loads(source.read_text("utf-8"))
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    task_id = Path(relative).parts[0]
    run_id = uuid4().hex
    result_dir = settings.evidence_root / "runtime-verification" / f"discovery-repair-{run_id}"
    result_dir.mkdir(parents=True)
    # Native container storage avoids SQLite locking problems on Windows bind mounts.
    database = Path("/tmp") / f"discovery-repair-{run_id}.db"
    engine = create_engine(f"sqlite:///{database}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add(
            OnboardingBatch(
                batch_id=run_id, client_request_id=run_id, requested_count=1, accepted_count=1
            )
        )
        session.flush()
        session.add(
            OnboardingTask(
                task_id=task_id,
                batch_id=run_id,
                entry_url=analysis["list_endpoint"],
                normalized_url=analysis["list_endpoint"],
                platform_key="discovery_probe",
                status="ANALYZING",
            )
        )
        session.flush()
        session.add(WorkflowRun(run_id=run_id, task_id=task_id, run_type="spec-repair-probe"))
    runtime = WorkflowRunner()
    runtime.factory, runtime.task_id, runtime.run_id = factory, task_id, run_id
    state = {
        "analysis": analysis,
        "source_name": "saved-evidence-probe",
        "platform_key": "discovery_probe",
        "entry_url": analysis["list_endpoint"],
        "manifest_ref": relative,
        "evidence_id": "original-analysis",
    }
    print(json.dumps({"probe_run_id": run_id, "status": "RUNNING"}), flush=True)
    try:
        with ExecutionContext(factory, run_id, f"probe:{run_id}") as execution:
            runtime.execution = execution
            with factory() as session:
                model, _, attempts = runtime._build_discovery_spec(state, session)
        assert model.pagination.mode == "offset" and model.pagination.page_param == "offset"
        assert model.identity.platform_id is None and model.identity.entity_id is None
        assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
        with factory() as session:
            agents = session.scalars(select(AgentExecution)).all()
            assert agents and all(a.status == "COMPLETED" and a.mode == "read" for a in agents)
            conversations = [a.conversation_id for a in agents]
        result = {
            "status": "PASS",
            "attempts": attempts,
            "source_analysis": relative,
            "source_sha256": original_hash,
            "conversation_ids": conversations,
            "pagination": model.pagination.model_dump(mode="json"),
            "original_evidence_unchanged": True,
            "production_task_modified": False,
            "schema_valid": True,
            "collection_validation": "NOT_RUN",
        }
        (result_dir / "result.json").write_text(json.dumps(result, indent=2), "utf-8")
        print(json.dumps(result), flush=True)
    finally:
        with (
            sqlite3.connect(database) as origin,
            sqlite3.connect(result_dir / "probe.db") as export,
        ):
            origin.backup(export)
        engine.dispose()


if __name__ == "__main__":
    main(sys.argv[1])
