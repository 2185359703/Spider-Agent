from fastapi.testclient import TestClient

from auto_spider.ai.gateway import resolve_gateway_config
from auto_spider.api.deps import get_session
from auto_spider.api.main import app
from auto_spider.config import get_settings
from auto_spider.db.models import RuntimeSetting, WorkflowRun
from tests.test_workflow import make_task


def test_opencode_profile_resolves_without_exposing_key(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_gateway_profile", "opencode_zen")
    monkeypatch.setattr(settings, "opencode_api_key", "test-secret")
    config = resolve_gateway_config(settings)
    assert config.profile == "opencode_zen"
    assert config.model == "deepseek-v4.1-flash"
    assert config.api_mode == "chat"
    assert config.base_url == "https://opencode.ai/zen/v1"
    assert config.api_key == "test-secret"


def test_gateway_snapshot_keeps_old_model_after_profile_switch(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "opencode_api_key", "new-secret")
    old = resolve_gateway_config(settings, profile_override="opencode_zen")
    monkeypatch.setattr(settings, "opencode_model", "new-model")
    resumed = resolve_gateway_config(settings, snapshot=old.snapshot())
    assert resumed.model == old.model
    assert resumed.base_url == old.base_url
    assert resumed.api_key == "new-secret"


def test_admin_can_switch_gateway_when_idle(db_session, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "opencode_api_key", "test-secret")

    def override_session():
        yield db_session

    app.dependency_overrides[get_session] = override_session
    try:
        response = TestClient(app).post(
            "/api/v1/system/ai-gateway/select", json={"profile": "opencode_zen"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["active"]["profile"] == "opencode_zen"
        assert db_session.get(RuntimeSetting, "ai_gateway_profile").value_json == {
            "profile": "opencode_zen"
        }
    finally:
        app.dependency_overrides.clear()


def test_gateway_switch_is_blocked_while_workflow_runs(db_session, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "opencode_api_key", "test-secret")
    task = make_task(db_session)
    db_session.add(
        WorkflowRun(
            run_id="gateway-active-run",
            task_id=task.task_id,
            run_type="onboarding",
            status="RUNNING",
        )
    )
    db_session.commit()

    def override_session():
        yield db_session

    app.dependency_overrides[get_session] = override_session
    try:
        response = TestClient(app).post(
            "/api/v1/system/ai-gateway/select", json={"profile": "opencode_zen"}
        )
        assert response.status_code == 409
    finally:
        app.dependency_overrides.clear()
