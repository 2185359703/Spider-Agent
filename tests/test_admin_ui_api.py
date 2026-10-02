import io
import subprocess
import sys
import zipfile
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from auto_spider.api.deps import get_session
from auto_spider.api.main import app
from auto_spider.config import get_settings
from auto_spider.db.models import (
    CodeSubmission,
    ManualRun,
    OnboardingBatch,
    OnboardingReport,
    OnboardingTask,
    WorkflowRun,
)


@pytest.fixture
def admin_context(db_session, tmp_path, monkeypatch):
    repo = tmp_path / "collector"
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args]).decode().strip()

    git("init")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    for path, content in {
        "collectors/example.py": "# pinned version\n",
        "config/platforms/example.toml": '[platform]\nkey="example"\n',
        "collectors/other.py": "# private other collector\n",
        ".env": "PASSWORD=never-return-this\n",
    }.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, "utf-8")
    git("add", ".")
    git("commit", "-m", "test(collectors): seed snapshot")
    commit = git("rev-parse", "HEAD")
    (repo / "collectors/example.py").write_text("# dirty workspace\n", "utf-8")
    settings = get_settings()
    monkeypatch.setattr(settings, "aicoding_repo_path", repo)
    monkeypatch.setattr(settings, "queue_enabled", True)
    monkeypatch.setattr(settings, "evidence_root", tmp_path / "evidence")
    db_session.add(
        OnboardingBatch(
            batch_id="batch",
            client_request_id="admin-seed",
            requested_count=1,
            accepted_count=1,
            created_by="tester",
        )
    )
    db_session.flush()
    db_session.add(
        OnboardingTask(
            task_id="task",
            batch_id="batch",
            entry_url="https://example.com",
            normalized_url="https://example.com",
            platform_key="example",
            platform_name="公司",
            created_by="tester",
            status="WAITING_MANUAL_RUN",
            current_run_id="workflow",
        )
    )
    db_session.add(
        WorkflowRun(run_id="workflow", task_id="task", run_type="onboarding", status="COMPLETED")
    )
    db_session.flush()
    db_session.add(
        CodeSubmission(
            submission_id="submission",
            task_id="task",
            run_id="workflow",
            branch_name="candidate",
            commit_sha=commit,
            baseline_ref=commit,
            changed_files=["collectors/example.py"],
            simulated=False,
        )
    )
    db_session.commit()
    app.dependency_overrides[get_session] = lambda: db_session
    try:
        yield TestClient(app), db_session, repo, commit
    finally:
        app.dependency_overrides.clear()


def seed_run(session, commit, records, run_id="manual"):
    now = datetime.now(UTC)
    run = ManualRun(
        manual_run_id=run_id,
        task_id="task",
        code_revision=commit,
        command_profile="test",
        environment_fingerprint="test",
        started_at=now,
        finished_at=now,
        artifact_manifest_ref="test.json",
        status="WAITING_REVIEW",
        result_json={"samples": records},
    )
    session.add(run)
    session.commit()
    return run


def test_code_reads_pinned_commit_and_blocks_other_platform_and_secrets(admin_context):
    client, _, _, commit = admin_context
    prefix = "/api/v1/admin/tasks/task/code/submission"
    response = client.get(prefix)
    assert response.status_code == 200
    assert response.json()["commit_sha"] == commit
    assert response.json()["content"].splitlines() == ["# pinned version"]
    for path in (".env", "../.env", "collectors/other.py", "/etc/passwd"):
        assert client.get(prefix, params={"path": path}).status_code == 403
    archive = client.get(prefix + "/download")
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        assert set(bundle.namelist()) == {
            "collectors/example.py",
            "config/platforms/example.toml",
        }
        assert b"pinned version" in bundle.read("collectors/example.py")


def test_full_data_projection_pagination_and_individual_review(admin_context):
    client, session, _, commit = admin_context
    records = [
        {
            "source_id": str(i),
            "raw_content": {
                "job": {
                    "title": f"真实岗位{i}",
                    "description": "官网描述",
                    "requirements": "官网要求",
                }
            },
            "title": "错误的外层标题",
            "publish_time": 0,
            "crawl_status": 1,
            "raw_html": "<h1>原始网页</h1>",
            "source_url": "https://example.com/job",
        }
        for i in range(1205)
    ]
    seed_run(session, commit, records)
    response = client.get("/api/v1/admin/records", params={"page": 61, "page_size": 20})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1205
    assert len(body["records"]) == 5
    assert body["records"][0]["title"] == "真实岗位1200"
    assert body["records"][0]["publish_time"] == 0
    assert body["records"][0]["crawl_status"] == 1
    assert body["records"][0]["requirements"] == "官网要求"
    assert body["summary"] == {"success": 1205, "failed": 0, "missing_publish_time": 1205}
    assert (
        client.put(
            "/api/v1/admin/runs/manual/records/1/review", json={"status": "ISSUE", "note": ""}
        ).status_code
        == 422
    )
    decision = client.put("/api/v1/admin/runs/manual/records/1/review", json={"status": "PASS"})
    assert decision.status_code == 200
    rows = client.get("/api/v1/admin/records", params={"page_size": 2}).json()["records"]
    assert rows[0]["review_status"] == "PASS"
    assert rows[1]["review_status"] == "UNCHECKED"
    summary = client.get("/api/v1/admin/runs/manual/review-summary").json()
    assert summary["checked"] == 1 and summary["total"] == 1205
    assert len(summary["sample_decisions"]) == 1


def test_launch_is_idempotent_pinned_and_locked(admin_context, monkeypatch):
    from auto_spider.services.admin_collection import collect_candidate

    client, session, _, commit = admin_context
    queued = []
    monkeypatch.setattr(collect_candidate, "apply_async", lambda **kwargs: queued.append(kwargs))
    payload = {"submission_id": "submission", "client_request_id": "launch-idempotent"}
    response = client.post("/api/v1/admin/tasks/task/collect", json=payload)
    assert response.status_code == 202
    run_id = response.json()["manual_run_id"]
    assert (
        client.post("/api/v1/admin/tasks/task/collect", json=payload).json()["manual_run_id"]
        == run_id
    )
    assert len(queued) == 1
    assert session.query(ManualRun).one().code_revision == commit
    assert (
        client.post(
            "/api/v1/admin/tasks/task/collect", json={**payload, "max_pages": 2}
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/v1/admin/tasks/task/collect",
            json={**payload, "client_request_id": "launch-second-id"},
        ).status_code
        == 409
    )
    assert client.post(f"/api/v1/admin/runs/{run_id}/cancel").json()["status"] == "CANCELLED"
    from auto_spider.services.admin_collection import execute_collection

    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    assert execute_collection(run_id, factory)["status"] == "CANCELLED"
    assert execute_collection(run_id, factory)["status"] == "CANCELLED"


def test_viewer_cannot_launch_or_review(admin_context):
    client, session, _, commit = admin_context
    headers = {"X-User-Role": "viewer"}
    assert (
        client.get("/api/v1/admin/tasks/task/code/submission", headers=headers).status_code == 200
    )
    assert (
        client.post(
            "/api/v1/admin/tasks/task/collect",
            headers=headers,
            json={"submission_id": "submission", "client_request_id": "viewer-launch"},
        ).status_code
        == 403
    )

    seed_run(session, commit, [{"title": "岗位"}])
    assert (
        client.put(
            "/api/v1/admin/runs/manual/records/1/review", headers=headers, json={"status": "PASS"}
        ).status_code
        == 403
    )


def test_report_validation_is_bound_to_exact_candidate_commit(admin_context):
    client, session, _, commit = admin_context
    report = OnboardingReport(
        report_id="report-version",
        task_id="task",
        run_id="workflow",
        report_version="v1",
        observation_code="INTERNSHIPS_FOUND",
        technical_status="PASS",
        next_action="WAIT_MANUAL_RUN",
        adoptable=True,
        report_json={"candidate_commit": commit, "validation": {"pytest_status": "PASS"}},
    )
    session.add(report)
    session.commit()
    path = "/api/v1/admin/tasks/task/code/submission"
    assert client.get(path).json()["validation"]["pytest_status"] == "PASS"
    report.report_json = {"candidate_commit": "f" * 40, "validation": {"pytest_status": "PASS"}}
    session.commit()
    assert client.get(path).json()["validation"] is None


def test_issue_summary_retains_original_evidence_and_does_not_adopt(admin_context):
    client, session, _, commit = admin_context
    seed_run(session, commit, [{"raw_content": {"job": {"title": "岗位"}}}])
    assert (
        client.put(
            "/api/v1/admin/runs/manual/records/1/review",
            json={
                "status": "ISSUE",
                "field": "requirements",
                "note": "缺少官网第二段",
            },
        ).status_code
        == 200
    )
    summary = client.get("/api/v1/admin/runs/manual/review-summary").json()
    assert summary["issue_count"] == 1
    assert summary["field_issues"][0]["field"] == "requirements"
    assert session.query(CodeSubmission).one().adoption_status == "candidate"


def test_final_review_allows_company_acceptance_without_individual_marks(
    admin_context, monkeypatch
):
    import auto_spider.api.main as main

    client, session, _, commit = admin_context
    monkeypatch.setattr(main, "enqueue_onboarding", lambda *_: None)
    seed_run(session, commit, [{"title": "真实岗位"}, {"title": "未核对岗位"}])
    payload = {"review_status": "PASS", "client_request_id": "final-review-pass"}
    assert client.post("/api/v1/admin/runs/manual/finalize", json=payload).status_code == 200
    assert client.post("/api/v1/admin/runs/manual/finalize", json=payload).status_code == 200
    session.expire_all()
    assert session.query(CodeSubmission).one().adoption_status == "adopted"
    assert session.query(OnboardingTask).one().status == "ADOPTED"
    rows = client.get("/api/v1/admin/records").json()["records"]
    assert rows[1]["review_status"] == "UNCHECKED"
    assert (
        client.put(
            "/api/v1/admin/runs/manual/records/1/review",
            json={"status": "ISSUE", "note": "已冻结审查"},
        ).status_code
        == 409
    )


def test_company_review_approves_without_per_record_marks_and_checks_snapshot(
    admin_context, monkeypatch
):
    import auto_spider.api.main as main
    from auto_spider.db.models import ManualReview

    client, session, _, commit = admin_context
    monkeypatch.setattr(main, "enqueue_onboarding", lambda *_: None)
    seed_run(session, commit, [{"title": "岗位一"}, {"title": "岗位二"}])
    snapshot = client.get(
        "/api/v1/admin/companies/review-summary", params={"company_name": "公司"}
    ).json()
    payload = {
        "company_name": "公司",
        "runs": snapshot["runs"],
        "client_request_id": "company-approve-0001",
    }
    stale = {**payload, "runs": [{**snapshot["runs"][0], "code_revision": "f" * 40}]}
    assert client.post("/api/v1/admin/companies/approve", json=stale).status_code == 409
    assert (
        client.post(
            "/api/v1/admin/companies/approve", json=payload, headers={"X-User-Role": "viewer"}
        ).status_code
        == 403
    )
    assert client.post("/api/v1/admin/companies/approve", json=payload).status_code == 200
    assert client.post("/api/v1/admin/companies/approve", json=payload).status_code == 200
    session.expire_all()
    assert session.query(ManualReview).count() == 1
    assert session.query(CodeSubmission).one().adoption_status == "adopted"
    assert session.query(OnboardingTask).one().status == "ADOPTED"
    assert client.get("/api/v1/admin/records").json()["records"][1]["review_status"] == "UNCHECKED"


def test_company_review_keeps_unresolved_issues_blocking(admin_context, monkeypatch):
    client, session, _, commit = admin_context
    seed_run(session, commit, [{"title": "岗位"}])
    client.put(
        "/api/v1/admin/runs/manual/records/1/review",
        json={"status": "ISSUE", "note": "岗位描述不完整"},
    )
    snapshot = client.get(
        "/api/v1/admin/companies/review-summary", params={"company_name": "公司"}
    ).json()
    response = client.post(
        "/api/v1/admin/companies/approve",
        json={
            "company_name": "公司",
            "runs": snapshot["runs"],
            "client_request_id": "company-issue-0001",
        },
    )
    assert response.status_code == 409
    session.expire_all()
    assert session.query(CodeSubmission).one().adoption_status == "candidate"


def test_code_edit_saves_new_commit_keeps_dirty_checkout_and_is_idempotent(
    admin_context, monkeypatch, tmp_path
):
    from types import SimpleNamespace

    from auto_spider.services import code_edit

    client, session, repo, commit = admin_context
    monkeypatch.setattr(get_settings(), "worktree_root", tmp_path / "edit-worktrees")
    check = SimpleNamespace(status="PASS")
    monkeypatch.setattr(
        code_edit,
        "validate_candidate",
        lambda *args, **kwargs: SimpleNamespace(
            compile=check,
            pytest=check,
            ruff=check,
            contract_status="PASS",
            business_status="PASS",
            passed=True,
            as_dict=lambda: {"passed": True},
        ),
    )
    path = "/api/v1/admin/tasks/task/code/submission"
    payload = {
        "path": "collectors/example.py",
        "content": "# human edit\n",
        "base_commit": commit,
        "client_request_id": "edit-code-0001",
    }
    assert client.put(path, json=payload, headers={"X-User-Role": "viewer"}).status_code == 403
    assert client.put(path, json={**payload, "path": "collectors/other.py"}).status_code == 403
    assert client.put(path, json={**payload, "content": "def broken("}).status_code == 409
    response = client.put(path, json=payload)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["commit_sha"] != commit
    assert client.put(path, json=payload).json() == saved
    assert (repo / "collectors/example.py").read_text() == "# dirty workspace\n"
    assert (
        subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"]).decode().strip()
        == commit
    )
    assert client.get(path).json()["content"].splitlines() == ["# pinned version"]
    new = client.get(f"/api/v1/admin/tasks/task/code/{saved['submission_id']}").json()
    assert new["content"].splitlines() == ["# human edit"] and new["validation_status"] == "PARTIAL"
    session.expire_all()
    assert (
        session.query(CodeSubmission).filter_by(submission_id="submission").one().adoption_status
        == "superseded"
    )
    assert session.query(OnboardingTask).one().platform_id is None
    assert session.query(OnboardingTask).one().entity_id is None
    import auto_spider.api.main as main
    from auto_spider.db.models import WorkflowDispatch

    queued = []
    monkeypatch.setattr(main, "enqueue_onboarding", lambda *args: queued.append(args))
    seed_run(session, saved["commit_sha"], [{"title": "人工修改后的实习岗位"}], "edited-run")
    approved = client.post(
        "/api/v1/admin/runs/edited-run/finalize",
        json={
            "review_status": "PASS",
            "client_request_id": "edited-review-0001",
        },
    )
    assert approved.status_code == 200
    assert not queued and session.query(WorkflowDispatch).count() == 0
    session.expire_all()
    task = session.query(OnboardingTask).one()
    assert task.status == "ADOPTED"
    assert (
        session.query(WorkflowRun).filter_by(run_id=task.current_run_id).one().status == "COMPLETED"
    )


def test_company_approval_rolls_back_all_entries_on_second_issue(admin_context, monkeypatch):
    from auto_spider.db.models import ManualReview

    client, session, _, commit = admin_context
    seed_run(session, commit, [{"title": "岗位一"}])
    session.add(
        OnboardingTask(
            task_id="task-two",
            batch_id="batch",
            entry_url="https://example.com/other",
            normalized_url="https://example.com/other",
            platform_key="other",
            platform_name="公司",
            created_by="tester",
            status="WAITING_MANUAL_REVIEW",
            current_run_id="workflow-two",
        )
    )
    session.flush()
    session.add(
        WorkflowRun(
            run_id="workflow-two", task_id="task-two", run_type="onboarding", status="COMPLETED"
        )
    )
    session.flush()
    session.add(
        CodeSubmission(
            submission_id="submission-two",
            task_id="task-two",
            run_id="workflow-two",
            branch_name="candidate-two",
            commit_sha=commit,
            baseline_ref=commit,
            changed_files=["collectors/other.py"],
            simulated=False,
        )
    )
    now = datetime.now(UTC)
    session.add(
        ManualRun(
            manual_run_id="manual-two",
            task_id="task-two",
            code_revision=commit,
            command_profile="test",
            environment_fingerprint="test",
            started_at=now,
            finished_at=now,
            artifact_manifest_ref="test.json",
            status="WAITING_REVIEW",
            result_json={"samples": [{"title": "岗位二"}], "error_msg": "详情字段缺失"},
        )
    )
    session.commit()
    snapshot = client.get(
        "/api/v1/admin/companies/review-summary", params={"company_name": "公司"}
    ).json()
    assert len(snapshot["runs"]) == 2
    response = client.post(
        "/api/v1/admin/companies/approve",
        json={
            "company_name": "公司",
            "runs": snapshot["runs"],
            "client_request_id": "company-atomic-0001",
        },
    )
    assert response.status_code == 409
    session.expire_all()
    assert session.query(ManualReview).count() == 0
    assert all(s.adoption_status == "candidate" for s in session.query(CodeSubmission))


def test_failed_collection_enters_diagnosis_without_fabricated_samples(admin_context, monkeypatch):
    import auto_spider.api.main as main
    from auto_spider.db.models import FailureBundle

    client, session, _, commit = admin_context
    queued = []
    monkeypatch.setattr(main, "enqueue_repair", lambda *args: queued.append(args))
    run = seed_run(session, commit, [])
    run.status = "FAILED"
    run.result_json = {"error_msg": "字段映射失败，缺少岗位标题", "samples": []}
    session.commit()
    response = client.post(
        "/api/v1/admin/runs/manual/finalize",
        json={"review_status": "CODE_FIX_REQUIRED", "client_request_id": "final-failed-run"},
    )
    assert response.status_code == 200
    assert len(queued) == 1
    assert session.query(FailureBundle).one().bundle_json["field_issues"][0]["field"] == "other"
    assert client.get("/api/v1/admin/records").json()["total"] == 0


def test_recent_range_preserves_missing_time_and_original_records():
    from auto_spider.services.admin_collection import filter_publication_range

    now = datetime(2026, 9, 30, tzinfo=UTC)
    records = [
        {"publish_time": "2026-09-29"},
        {"publish_time": "2025-01-01"},
        {"publish_time": 0},
        {"publish_time": None},
    ]
    selected = filter_publication_range(records, 7, now)
    assert len(selected) == 3
    assert records[1]["publish_time"] == "2025-01-01"


@pytest.mark.skipif(sys.platform != "linux", reason="Landlock runtime runs in Docker")
@pytest.mark.parametrize("business_id", [None, 1234])
def test_actual_collector_runtime_has_null_ids_and_no_outside_or_database_access(
    admin_context,
    tmp_path,
    business_id,
):
    from auto_spider.services.admin_collection import execute_collection

    _, session, repo, _ = admin_context
    outside = tmp_path / "outside.txt"
    outside.write_text("must-not-leak", "utf-8")
    (repo / "config/platforms/__init__.py").write_text(
        "def get_platform(key): return key\n", "utf-8"
    )
    (repo / "collectors/registry.py").write_text(
        "import os\n"
        "class Record:\n"
        "    def to_dict(self):\n"
        f"        return {{'source_platform': {business_id!r}, 'platform_id': None, "
        "'entity_id': None, 'source_id': 'intern-1', 'source_name': '公司', "
        "'source_url': 'https://example.com/job', 'crawl_status': 1, "
        "'raw_content': {'job': {'title': '实习生', 'description': '真实接口正文', "
        "'requirements': '岗位要求'}, 'authorization': 'must-be-redacted'}}\n"
        "class Collector:\n"
        "    def collect(self):\n"
        "        assert 'DATABASE_URL' not in os.environ\n"
        f"        try: open({str(outside)!r}).read()\n"
        "        except PermissionError: pass\n"
        "        else: raise RuntimeError('OUTSIDE_FILE_WAS_READABLE')\n"
        "        return [Record()]\n"
        "class CollectorRegistry:\n"
        "    @staticmethod\n"
        "    def create(*args, **kwargs): return Collector()\n",
        "utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "test(runtime): seed sandbox fixture"],
        check=True,
        capture_output=True,
    )
    commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"]).decode().strip()
    run = seed_run(session, commit, [], "sandbox-run")
    run.status = "QUEUED"
    run.result_json = {"options": {"max_pages": 1, "timeout_seconds": 30}}
    session.commit()
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    result = execute_collection(run.manual_run_id, factory)
    session.expire_all()
    if business_id is None:
        assert result["status"] == "WAITING_REVIEW"
        from auto_spider.api.admin import run_records

        records = run_records(run)
        assert records[0]["platform_id"] is None and records[0]["entity_id"] is None
        assert records[0]["raw_content"]["authorization"] == "[REDACTED]"
    else:
        assert result["status"] == "FAILED"
        assert "COLLECTION_IDS_MUST_BE_NULL" in run.result_json["error_msg"]
