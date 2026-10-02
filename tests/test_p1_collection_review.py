import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from auto_spider.ai.workspace_access import WorkspaceAccess
from auto_spider.config import get_settings
from auto_spider.db.models import (
    CodeSubmission,
    CollectionDispatch,
    FailureBundle,
    ManualReview,
    ManualRun,
    OnboardingReport,
    OnboardingTask,
    WorkflowRun,
)
from auto_spider.services.collection_dispatch import (
    check_collection,
    claim_collection,
    recover_collections,
)
from tests.test_admin_ui_api import admin_context as admin_fixture
from tests.test_admin_ui_api import seed_run

admin_context = admin_fixture


def test_broker_outage_and_same_request_retry_deliver_once(admin_context, monkeypatch):
    from auto_spider.services.admin_collection import collect_candidate

    client, session, _, _ = admin_context
    calls = []

    def down(**kwargs):
        calls.append(kwargs)
        raise ConnectionError("broker offline")

    monkeypatch.setattr(collect_candidate, "apply_async", down)
    body = {"submission_id": "submission", "client_request_id": "p1-broker-0001"}
    first = client.post("/api/v1/admin/tasks/task/collect", json=body)
    assert first.status_code == 202
    assert first.json()["status"] == "QUEUED"
    assert first.json()["dispatch_status"] == "PENDING"
    monkeypatch.setattr(collect_candidate, "apply_async", lambda **kwargs: calls.append(kwargs))
    second = client.post("/api/v1/admin/tasks/task/collect", json=body)
    third = client.post("/api/v1/admin/tasks/task/collect", json=body)
    assert (
        second.json()["manual_run_id"]
        == third.json()["manual_run_id"]
        == first.json()["manual_run_id"]
    )
    assert second.json()["dispatch_status"] == "SENT"
    assert len(calls) == 2
    assert session.query(ManualRun).count() == session.query(CollectionDispatch).count() == 1


def test_cancel_queued_collection_finishes_without_worker(admin_context, monkeypatch):
    from auto_spider.services.admin_collection import collect_candidate

    client, session, _, _ = admin_context
    monkeypatch.setattr(
        collect_candidate, "apply_async", lambda **kwargs: (_ for _ in ()).throw(ConnectionError())
    )
    run_id = client.post(
        "/api/v1/admin/tasks/task/collect",
        json={"submission_id": "submission", "client_request_id": "p1-cancel-0001"},
    ).json()["manual_run_id"]
    assert client.post(f"/api/v1/admin/runs/{run_id}/cancel").json()["status"] == "CANCELLED"
    published = []
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    recover_collections(factory, published.append)
    assert published == []


def test_recovery_fences_dead_collection_owner(admin_context, monkeypatch):
    from auto_spider.services.admin_collection import collect_candidate

    client, session, _, _ = admin_context
    monkeypatch.setattr(collect_candidate, "apply_async", lambda **kwargs: None)
    run_id = client.post(
        "/api/v1/admin/tasks/task/collect",
        json={"submission_id": "submission", "client_request_id": "p1-recover-0001"},
    ).json()["manual_run_id"]
    run = session.query(ManualRun).one()
    old_owner = claim_collection(session, run)
    session.commit()
    receipt = session.get(CollectionDispatch, run_id)
    receipt.heartbeat_at = receipt.next_at = datetime.now(UTC) - timedelta(minutes=5)
    session.commit()
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    published = []
    recover_collections(factory, published.append)
    assert published == [run_id]
    with pytest.raises(RuntimeError, match="LEASE_LOST"):
        check_collection(factory, run_id, old_owner)
    session.expire_all()
    assert run.status == "QUEUED"


@pytest.mark.parametrize(
    "observation", ["NO_JOBS_OBSERVED", "NO_INTERNSHIPS_OBSERVED", "INCONCLUSIVE"]
)
def test_empty_collection_can_be_reviewed_without_adopting_code(admin_context, observation):
    client, session, _, sha = admin_context
    seed_run(session, sha, [])
    body = {
        "review_status": "NO_DATA_CONFIRMED",
        "observation_code": observation,
        "comment": "已核对本次入口及最近七天范围",
        "client_request_id": "p1-empty-0001",
    }
    first = client.post("/api/v1/admin/runs/manual/finalize", json=body)
    assert first.status_code == 200, first.text
    assert client.post("/api/v1/admin/runs/manual/finalize", json=body).status_code == 200
    session.expire_all()
    assert session.query(ManualRun).one().status == "REVIEWED"
    assert session.query(ManualReview).count() == 1
    assert session.query(CodeSubmission).one().adoption_status == "candidate"
    assert session.query(OnboardingTask).one().status == "NO_DATA_CONFIRMED"
    report = session.query(OnboardingReport).one()
    assert report.observation_code == observation and not report.adoptable
    assert report.report_json["candidate_commit"] == sha
    assert client.get("/api/v1/admin/records").json()["total"] == 0


def test_failed_or_nonempty_run_cannot_be_confirmed_empty(admin_context):
    client, session, _, sha = admin_context
    manual = seed_run(session, sha, [], "failed")
    manual.status = "FAILED"
    manual.result_json = {"error_msg": "Parser failure"}
    session.commit()
    body = {
        "review_status": "NO_DATA_CONFIRMED",
        "observation_code": "NO_JOBS_OBSERVED",
        "comment": "不能掩盖错误",
        "client_request_id": "p1-failed-empty",
    }
    assert client.post("/api/v1/admin/runs/failed/finalize", json=body).status_code == 409
    seed_run(session, sha, [{"title": "实习岗位"}], "nonempty")
    assert client.post("/api/v1/admin/runs/nonempty/finalize", json=body).status_code == 409


def test_empty_collection_with_missing_jobs_can_enter_repair(admin_context, monkeypatch):
    import auto_spider.api.main as main

    client, session, _, sha = admin_context
    repairs = []
    monkeypatch.setattr(main, "enqueue_repair", lambda *args: repairs.append(args))
    seed_run(session, sha, [])
    body = {"review_status": "CODE_FIX_REQUIRED", "client_request_id": "p1-missing-records"}
    assert client.post("/api/v1/admin/runs/manual/finalize", json=body).status_code == 409
    body["comment"] = "官网列表显示软件实习生，但本轮同样筛选条件返回 0 条，请检查列表提取"
    response = client.post("/api/v1/admin/runs/manual/finalize", json=body)
    assert response.status_code == 200, response.text
    assert client.post("/api/v1/admin/runs/manual/finalize", json=body).status_code == 200
    assert len(repairs) == 1
    session.expire_all()
    review = session.query(ManualReview).one()
    bundle = session.query(FailureBundle).one()
    assert review.issue_summary == body["comment"]
    assert session.query(OnboardingTask).one().status == "REPAIRING"
    assert session.query(CodeSubmission).one().adoption_status == "candidate"
    assert session.query(ManualRun).one().status == "REVIEWED"
    manifest = json.loads(
        (get_settings().evidence_root / bundle.artifact_manifest_ref).read_text("utf-8")
    )
    assert manifest["record_count"] == 0 and manifest["affected_samples"] == []
    assert manifest["field_issues"][0]["description"] == body["comment"]
    assert manifest["field_issues"][0]["issue_type"] == "missing"
    assert manifest["diagnostics"]["evidence_ref"]
    body["comment"] = "改成另一条问题"
    assert client.post("/api/v1/admin/runs/manual/finalize", json=body).status_code == 409


def test_failure_bundle_has_scoped_samples_and_runtime_evidence(admin_context, monkeypatch):
    import auto_spider.api.main as main

    client, session, _, sha = admin_context
    monkeypatch.setattr(main, "enqueue_repair", lambda *_: None)
    row = {
        "source_id": "job-real-1",
        "source_url": "https://example.com/jobs/1",
        "raw_content": {
            "job": {"title": "开发实习生", "location": "错误城市"},
            "source_payload": {"detail": {"city": "北京", "Cookie": "must-not-leak"}},
        },
    }
    manual = seed_run(session, sha, [row])
    manual.result_json = {
        **manual.result_json,
        "options": {"max_pages": 7, "request_interval_seconds": 1},
    }
    session.commit()
    assert (
        client.put(
            "/api/v1/admin/runs/manual/records/1/review",
            json={"status": "ISSUE", "field": "location", "note": "原响应 city=北京，采集结果错误"},
        ).status_code
        == 200
    )
    response = client.post(
        "/api/v1/admin/runs/manual/finalize",
        json={"review_status": "CODE_FIX_REQUIRED", "client_request_id": "p1-bundle-0001"},
    )
    assert response.status_code == 200, response.text
    bundle = session.query(FailureBundle).one()
    assert bundle.bundle_json["manual_run_id"] == "manual"
    assert bundle.bundle_json["code_revision"] == sha
    root = get_settings().evidence_root
    manifest = json.loads((root / bundle.artifact_manifest_ref).read_text("utf-8"))
    assert manifest["collection_options"]["max_pages"] == 7
    sample_ref = manifest["affected_samples"][0]
    sample = json.loads((root / sample_ref["evidence_ref"]).read_text("utf-8"))
    assert sample["record"]["raw_content"]["source_payload"]["detail"]["city"] == "北京"
    assert sample["field_issues"][0]["actual"] == "错误城市"
    assert "must-not-leak" not in json.dumps(sample)
    reader = WorkspaceAccess(
        {
            "workspace": str(root / "workspace"),
            "evidence": str(root / "analysis"),
            "failure_evidence": str((root / bundle.artifact_manifest_ref).parent),
            "mode": "read",
            "allowed_files": [],
        }
    )
    assert "job-real-1" in reader.read("samples/1.json", area="failure")
    with pytest.raises(ValueError):
        reader.read("../../outside.json", area="failure")


def test_late_manual_edit_notification_does_not_restart_analysis(admin_context):
    from auto_spider.workflows.runner import WorkflowRunner

    client, session, _, sha = admin_context
    task = session.query(OnboardingTask).one()
    run = session.query(WorkflowRun).one()
    run.run_type, run.execution_key, run.status = "manual_edit", "human-edit", "WAITING_MANUAL_RUN"
    task.status = "ADOPTED"
    seed_run(session, sha, [{"title": "实习岗位"}])
    session.commit()

    class ForbiddenAnalyzer:
        def inspect(self, *_args, **_kwargs):
            pytest.fail("manual edits must never start site analysis")

    WorkflowRunner(analyzer=ForbiddenAnalyzer()).run_onboarding(session, task.task_id)
    session.expire_all()
    assert task.status == "ADOPTED"
