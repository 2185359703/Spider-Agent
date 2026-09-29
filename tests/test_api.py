from fastapi.testclient import TestClient

from auto_spider.api.deps import get_session
from auto_spider.api.main import app
from auto_spider.db.models import OnboardingTask, PlatformSpec


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
    finally:
        app.dependency_overrides.clear()
