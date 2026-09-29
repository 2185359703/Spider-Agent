from datetime import UTC, datetime

from fastapi.testclient import TestClient

from auto_spider.api.deps import get_session
from auto_spider.api.main import app
from auto_spider.config import get_settings
from auto_spider.db.models import (
    CodeSubmission,
    FailureBundle,
    ManualReview,
    ManualRun,
    OnboardingBatch,
    OnboardingReport,
    OnboardingTask,
    PlatformSpec,
    PolicyVersion,
    RepairRun,
    WorkflowEvent,
    WorkflowRun,
)


def test_create_task_api_uses_nullable_business_ids(db_session) -> None:
    def override_session():
        yield db_session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        response = client.post(
            "/api/v1/onboarding/batches",
            json={
                "items": [{"entry_url": "https://careers.example.com/internships"}],
                "client_request_id": "api-request-0001",
            },
        )
        assert response.status_code == 201
        task_id = response.json()["task_ids"][0]
        task_response = client.get(f"/api/v1/onboarding/tasks/{task_id}")
        assert task_response.status_code == 200
        body = task_response.json()
        assert body["platform_id"] is None
        assert body["entity_id"] is None
        task = db_session.query(OnboardingTask).filter(OnboardingTask.task_id == task_id).one()
        db_session.add(
            PlatformSpec(
                task_id=task_id,
                schema_version="1.0",
                spec_version=1,
                spec_hash="a" * 64,
                status="DRAFT",
                confidence_summary={"high": 0, "medium": 0, "low": 0},
                spec_json={"schema_name": "platform_spec"},
                evidence_manifest_ref=None,
            )
        )
        db_session.commit()
        specs_response = client.get(f"/api/v1/onboarding/tasks/{task_id}/specs")
        assert specs_response.status_code == 200
        assert specs_response.json()[0]["spec_hash"] == "a" * 64
        assert task.platform_id is None
        db_session.add(
            WorkflowRun(
                run_id="api-report-run-0001",
                task_id=task_id,
                run_type="onboarding",
                status="COMPLETED",
            )
        )
        db_session.add(
            OnboardingReport(
                report_id="api-report-0001",
                task_id=task_id,
                run_id="api-report-run-0001",
                report_version="report-v1",
                observation_code="NO_JOBS_OBSERVED",
                technical_status="PASS",
                next_action="CLOSE_WITH_REPORT",
                adoptable=False,
                report_json={"validation": {"pytest_status": "PASS"}},
            )
        )
        db_session.commit()
        report_response = client.get(f"/api/v1/onboarding/tasks/{task_id}/reports/latest")
        assert report_response.status_code == 200
        assert report_response.json()["report_id"] == "api-report-0001"
    finally:
        app.dependency_overrides.clear()


def test_task_history_endpoints_return_manual_review_and_repair_data(db_session) -> None:
    batch = OnboardingBatch(
        batch_id="batch-history-0001",
        client_request_id="history-request-0001",
        requested_count=1,
        accepted_count=1,
        created_by="tester",
    )
    task = OnboardingTask(
        task_id="task-history-0001",
        batch_id=batch.batch_id,
        entry_url="https://example.com/intern",
        normalized_url="https://example.com/intern",
        platform_key="example",
        platform_name="Example",
        created_by="tester",
    )
    run = WorkflowRun(
        run_id="run-history-0001",
        task_id=task.task_id,
        run_type="onboarding",
        status="COMPLETED",
    )
    started_at = datetime.now(UTC)
    manual = ManualRun(
        manual_run_id="manual-history-0001",
        task_id=task.task_id,
        code_revision="abc123",
        command_profile="preview",
        environment_fingerprint="test",
        started_at=started_at,
        finished_at=started_at,
        artifact_manifest_ref="manual.json",
        result_json={"sample_count": 2},
        status="WAITING_REVIEW",
    )
    review = ManualReview(
        review_id="review-history-0001",
        task_id=task.task_id,
        manual_run_id=manual.manual_run_id,
        code_revision="abc123",
        review_status="CODE_FIX_REQUIRED",
        reviewer_id="tester",
        sample_count=2,
        issue_summary="字段为空",
        evidence_refs=[],
    )
    bundle_row = FailureBundle(
        bundle_id="bundle-history-0001",
        task_id=task.task_id,
        run_id=run.run_id,
        review_id=review.review_id,
        failure_type="MANUAL_RESULT_MISMATCH",
        status="CREATED",
        bundle_json={"sanitized": True},
    )
    repair = RepairRun(
        repair_run_id="repair-history-0001",
        task_id=task.task_id,
        bundle_id=bundle_row.bundle_id,
        attempt=1,
        status="COMPLETED",
        diagnosis_json={"code_fixable": True},
        changed_files=["collectors/example.py"],
        regression_json={"pytest_status": "PASS"},
        commit_sha="def456",
    )
    event = WorkflowEvent(
        event_id="event-history-0001",
        event_type="REPORT_CREATED",
        task_id=task.task_id,
        run_id=run.run_id,
        idempotency_key="task-history-0001:report",
        payload_json={"observation_code": "INTERNSHIPS_FOUND"},
    )
    manual.result_json = {
        "sample_count": 1,
        "samples": [
            {
                "source_id": "job-1",
                "title": "实习岗位",
                "source_url": "https://example.com/jobs/1",
                "description": "岗位描述",
                "cookie": "must-not-leak",
            }
        ],
    }
    submission = CodeSubmission(
        submission_id="submission-history-0001",
        task_id=task.task_id,
        run_id=run.run_id,
        branch_name="ai/onboarding/example/task-history-0001",
        commit_sha="abc123",
        baseline_ref="base123",
        changed_files=["collectors/example.py"],
        adoption_status="candidate",
        simulated=True,
    )
    policy = PolicyVersion(
        name="report",
        version="report-v1",
        status="published",
        policy_json={"observation_codes": ["INTERNSHIPS_FOUND"]},
        created_by="tester",
    )
    db_session.add_all(
        [batch, task, run, manual, review, bundle_row, repair, event, submission, policy]
    )
    db_session.commit()

    def override_session():
        yield db_session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        assert client.get(f"/api/v1/onboarding/tasks/{task.task_id}/timeline").json()["events"]
        manual_body = client.get(f"/api/v1/onboarding/tasks/{task.task_id}/manual-runs").json()
        review_body = client.get(f"/api/v1/onboarding/tasks/{task.task_id}/reviews").json()
        repair_body = client.get(f"/api/v1/onboarding/tasks/{task.task_id}/repairs").json()
        failure_body = client.get(f"/api/v1/onboarding/tasks/{task.task_id}/failures").json()
        assert manual_body[0]["manual_run_id"] == manual.manual_run_id
        assert review_body[0]["review_id"] == review.review_id
        assert repair_body[0]["repair_run_id"] == repair.repair_run_id
        assert failure_body[0]["bundle_id"] == bundle_row.bundle_id
        samples = client.get(f"/api/v1/onboarding/tasks/{task.task_id}/samples").json()
        assert samples["count"] == 1
        assert samples["samples"][0]["title"] == "实习岗位"
        assert samples["samples"][0]["extra"]["cookie"] == "[REDACTED]"
        assert client.get("/api/v1/onboarding/samples").json()[0]["task_id"] == task.task_id
        submissions = client.get("/api/v1/onboarding/submissions").json()
        assert submissions[0]["submission_id"] == submission.submission_id
        filtered_submissions = client.get(
            "/api/v1/onboarding/submissions?adoption_status=candidate"
        ).json()
        assert filtered_submissions[0]["submission_id"] == submission.submission_id
        assert client.get("/api/v1/policies").json()[0]["version"] == "report-v1"
        created_policy = client.post(
            "/api/v1/policies",
            json={
                "name": "report",
                "version": "report-v2",
                "status": "draft",
                "policy": {"required_fields": ["title"]},
            },
        )
        assert created_policy.status_code == 201
        duplicate_policy = client.post(
            "/api/v1/policies",
            json={
                "name": "report",
                "version": "report-v2",
                "status": "draft",
                "policy": {},
            },
        )
        assert duplicate_policy.status_code == 409
        assert client.get("/api/v1/system/repositories").status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_role_permissions_protect_mutating_endpoints(db_session, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_mode", "header")

    def override_session():
        yield db_session

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app)
        payload = {
            "items": [{"entry_url": "https://example.com/internships"}],
            "client_request_id": "role-check-0001",
        }
        viewer_headers = {"X-User-Id": "viewer-1", "X-User-Role": "viewer"}
        operator_headers = {"X-User-Id": "operator-1", "X-User-Role": "operator"}
        admin_headers = {"X-User-Id": "admin-1", "X-User-Role": "admin"}
        assert client.get("/api/v1/me", headers=viewer_headers).json()["role"] == "viewer"
        health = client.get("/api/v1/system/health", headers=viewer_headers)
        assert health.status_code == 200
        assert {"database", "redis", "agent_server"} <= set(health.json()["checks"])
        assert client.post(
            "/api/v1/onboarding/batches", json=payload, headers=viewer_headers
        ).status_code == 403
        assert client.post(
            "/api/v1/onboarding/batches", json=payload, headers=operator_headers
        ).status_code == 201
        policy_payload = {
            "name": "role-policy",
            "version": "v1",
            "status": "draft",
            "policy": {},
        }
        assert client.post(
            "/api/v1/policies", json=policy_payload, headers=viewer_headers
        ).status_code == 403
        assert client.post(
            "/api/v1/policies", json=policy_payload, headers=admin_headers
        ).status_code == 201
    finally:
        app.dependency_overrides.clear()
