import os
import subprocess
import sys
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, inspect

from auto_spider.ai.workspace_access import WorkspaceAccess
from auto_spider.db.models import CodeSubmission, ManualRun, OnboardingTask
from auto_spider.git.publisher import publish_candidate
from auto_spider.schemas import ManualReviewRequest, ManualRunRequest
from auto_spider.services.tasks import create_manual_run, create_review
from tests.test_durable_execution import runner
from tests.test_workflow import make_task


def access(tmp_path, mode="write"):
    workspace = tmp_path / "checkout"
    evidence = tmp_path / "evidence"
    workspace.mkdir(exist_ok=True)
    evidence.mkdir(exist_ok=True)
    return WorkspaceAccess(
        {
            "workspace": str(workspace),
            "evidence": str(evidence),
            "mode": mode,
            "allowed_files": ["collectors/example.py"],
        }
    )


def test_write_scope_is_enforced_and_read_only_has_no_write(tmp_path):
    writable = access(tmp_path)
    writable.write("collectors/example.py", "x = 1\n")
    assert writable.read("collectors/example.py") == "x = 1\n"
    for path in ("../outside.py", "/tmp/outside.py", "C:/outside.py", "base.py", ".git/config"):
        with pytest.raises(ValueError):
            writable.write(path, "denied")
    with pytest.raises(ValueError, match="READ_ONLY"):
        access(tmp_path, "read").write("collectors/example.py", "denied")
    with pytest.raises(ValueError):
        writable.read(".env")


def test_symlink_cannot_escape_allowed_file(tmp_path):
    policy = access(tmp_path)
    outside = tmp_path / "private.txt"
    outside.write_text("private")
    link = policy.root / "collectors"
    try:
        link.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError):
        policy.write("collectors/example.py", "escaped")


def test_local_publication_is_idempotent_across_database_failure(tmp_path):
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "--quiet")
    (tmp_path / "README.md").write_text("baseline\n")
    git("add", "README.md")
    git(
        "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "baseline"
    )
    baseline = git("rev-parse", "HEAD")
    (tmp_path / "collectors").mkdir()
    (tmp_path / "collectors/example.py").write_text("title = 'intern'\n", newline="\n")
    arguments = dict(
        platform_key="example",
        run_id="1" * 32,
        baseline=baseline,
        commit_time="2026-09-30T08:00:00+00:00",
        repair=False,
        guard=lambda: None,
    )
    first = publish_candidate(str(tmp_path), **arguments)
    second = publish_candidate(str(tmp_path), **arguments)
    assert first == second
    assert git("rev-list", "--count", first["branch_name"]) == "2"
    assert git("rev-parse", "HEAD") == baseline
    (tmp_path / "collectors/example.py").write_text("title = 'modified'\n", newline="\n")
    with pytest.raises(RuntimeError, match="COMMIT_CONFLICT"):
        publish_candidate(str(tmp_path), **arguments)


def test_manual_run_replay_and_stale_candidate_rejection(db_session, tmp_path):
    task = make_task(db_session)
    result = runner(tmp_path).run_onboarding(db_session, task.task_id)
    request = ManualRunRequest(
        code_revision=result["candidate_commit"],
        environment_fingerprint="test",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        artifact_manifest_ref="manual/manifest.json",
        client_request_id="manual-stable-0001",
    )
    manual = create_manual_run(db_session, task, request)
    duplicate = create_manual_run(db_session, task, request)
    assert manual.manual_run_id == duplicate.manual_run_id
    assert db_session.query(ManualRun).count() == 1
    db_session.query(CodeSubmission).one().adoption_status = "superseded"
    db_session.commit()
    with pytest.raises(ValueError, match="CANDIDATE_MISMATCH"):
        create_review(
            db_session,
            task,
            ManualReviewRequest(
                review_status="PASS",
                manual_run_id=manual.manual_run_id,
                code_revision=request.code_revision,
                client_request_id="review-stale-0001",
            ),
            actor_id="reviewer",
        )
    assert db_session.get(OnboardingTask, task.id).status != "ADOPTED"


def test_review_adopts_matching_candidate_and_records_real_reviewer(db_session, tmp_path):
    task = make_task(db_session)
    result = runner(tmp_path).run_onboarding(db_session, task.task_id)
    request = ManualRunRequest(
        code_revision=result["candidate_commit"],
        environment_fingerprint="test",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        artifact_manifest_ref="manifest.json",
        client_request_id="manual-pass-0001",
    )
    manual = create_manual_run(db_session, task, request)
    review, _ = create_review(
        db_session,
        task,
        ManualReviewRequest(
            review_status="PASS",
            manual_run_id=manual.manual_run_id,
            code_revision=request.code_revision,
            client_request_id="review-pass-0001",
        ),
        actor_id="actual-reviewer",
    )
    assert review.reviewer_id == "actual-reviewer"
    assert db_session.query(CodeSubmission).one().adoption_status == "adopted"
    assert runner(tmp_path).run_onboarding(db_session, task.task_id)["status"] == "ADOPTED"


def test_empty_database_upgrade_and_repeat_upgrade(tmp_path):
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    env = {**os.environ, "DATABASE_URL": url}
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
    engine = create_engine(url)
    assert {"agent_executions", "graph_checkpoints", "workflow_dispatches"} <= set(
        inspect(engine).get_table_names()
    )
    engine.dispose()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux execution sandbox")
def test_generated_process_cannot_read_or_write_outside_checkout(tmp_path):
    from auto_spider.validators.candidate import _run_command

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = tmp_path / "private.txt"
    secret.write_text("must-not-read")
    command = [
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(secret)!r}).write_text('overwritten')",
    ]
    result = _run_command(command, workspace)
    assert result.status == "FAIL", result.output
    assert "PermissionError" in result.output
    assert secret.read_text() == "must-not-read"
    result = _run_command([sys.executable, "-c", f"print(open({str(secret)!r}).read())"], workspace)
    assert result.status == "FAIL" and "PermissionError" in result.output
    result = _run_command(
        [
            sys.executable,
            "-c",
            "import os, tempfile; "
            "assert 'OPENHANDS_LLM_API_KEY' not in os.environ; "
            "f=tempfile.NamedTemporaryFile(); f.write(b'ok'); print('scratch ok')",
        ],
        workspace,
    )
    assert result.status == "PASS", result.output
