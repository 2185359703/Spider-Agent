"""Opt-in real Agent Server/model check; stores results outside the business DB.

Run `python -m tests.agent_server_probe create`, restart the Agent Server, then
run `python -m tests.agent_server_probe resume` in the API/worker container.
"""

import json
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from auto_spider.ai.openhands_gateway import OpenHandsGateway
from auto_spider.config import get_settings
from auto_spider.db.base import Base
from auto_spider.db.models import AgentExecution, OnboardingBatch, OnboardingTask, WorkflowRun
from auto_spider.services.execution import ExecutionContext

PROMPT = (
    "Bounded integration check of the collector file tools. "
    "Write exactly 'def answer():\\n    return 42\\n' (with actual newlines) "
    "to collectors/runtime_probe.py using the write tool. "
    "Read that file back with the read tool. Then finish. Do not do any other work."
)


def main(mode):
    settings = get_settings()
    root = settings.evidence_root / "runtime-verification"
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "agent-probe.json"
    engine = create_engine(
        f"sqlite:///{root / 'agent-probe.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    if mode == "create":
        task_id, run_id = uuid4().hex, uuid4().hex
        workspace = settings.worktree_root / task_id / run_id
        workspace.mkdir(parents=True, exist_ok=True)
        with factory.begin() as session:
            session.add(
                OnboardingBatch(
                    batch_id=task_id, client_request_id=task_id, requested_count=1, accepted_count=1
                )
            )
            session.flush()
            session.add(
                OnboardingTask(
                    task_id=task_id,
                    batch_id=task_id,
                    entry_url="https://example.com/",
                    normalized_url="https://example.com/",
                    platform_key="runtime_probe",
                )
            )
            session.flush()
            session.add(WorkflowRun(run_id=run_id, task_id=task_id, run_type="runtime-probe"))
        manifest = {"task_id": task_id, "run_id": run_id, "workspace": str(workspace)}
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    else:
        manifest = json.loads(manifest_path.read_text())
        task_id, run_id, workspace = (
            manifest["task_id"],
            manifest["run_id"],
            Path(manifest["workspace"]),
        )
    spec = {"generation": {"allowed_files": ["collectors/runtime_probe.py"]}, "evidence": {}}
    prompt = (
        "Read collectors/runtime_probe.py with the read tool and report the return value. "
        "This is a read-only review; finish after reading."
        if mode == "read-only"
        else PROMPT
    )
    with ExecutionContext(factory, run_id, "integration:runtime-probe") as context:
        result = OpenHandsGateway().run(
            prompt,
            workspace,
            execution=context,
            task_id=task_id,
            step_key="review-probe" if mode == "read-only" else "probe",
            spec=spec,
            review_only=mode == "read-only",
            event_callback=lambda e: print(type(e).__name__, flush=True),
        )
    assert (
        workspace / "collectors/runtime_probe.py"
    ).read_text() == "def answer():\n    return 42\n"
    with factory.begin() as session:
        execution = session.scalar(
            select(AgentExecution).where(
                AgentExecution.run_id == run_id,
                AgentExecution.step_key == ("review-probe" if mode == "read-only" else "probe"),
            )
        )
        assert execution.status == "COMPLETED"
        if mode == "create":
            manifest["conversation_id"] = execution.conversation_id
            # Simulate a worker disconnect before it recorded graph-node completion.
            execution.status = "DISCONNECTED"
        elif mode == "resume":
            assert execution.conversation_id == manifest["conversation_id"]
            assert (
                session.scalar(
                    select(AgentExecution.execution_id).where(AgentExecution.run_id == run_id)
                )
                is not None
            )
            manifest["server_restart_resume"] = "PASS"
        else:
            assert execution.mode == "read"
            policy = json.loads(
                (settings.agent_policy_root / f"{execution.execution_id}.json").read_text()
            )
            assert policy["mode"] == "read"
    manifest[mode] = "PASS"
    manifest["response_present"] = bool(result.final_response)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest), flush=True)
    engine.dispose()


if __name__ == "__main__":
    main(sys.argv[1])
