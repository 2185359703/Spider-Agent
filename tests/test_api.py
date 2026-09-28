from fastapi.testclient import TestClient

from auto_spider.api.deps import get_session
from auto_spider.api.main import app


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
    finally:
        app.dependency_overrides.clear()
