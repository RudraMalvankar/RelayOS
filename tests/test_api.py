from fastapi.testclient import TestClient

from api import create_app
from core.config import settings
from models.enums import ExecutionState
from models.schemas import ExecutionResult


class FakeOrchestrator:
    async def process_task(self, request):
        return ExecutionResult(
            task_id=request.task_id,
            answer="ok",
            state=ExecutionState.SUCCESS,
        )


def test_health_and_provider_status_are_safe():
    client = TestClient(create_app(FakeOrchestrator()))

    assert client.get("/api/health").json()["status"] == "ok"
    providers = client.get("/api/providers").json()["providers"]
    assert {provider["name"] for provider in providers} == {"fireworks", "anthropic", "openai"}
    assert all("secret" not in str(provider) for provider in providers)


def test_task_input_limit_is_enforced():
    client = TestClient(create_app(FakeOrchestrator()))

    response = client.post("/api/tasks", json={"task_id": "too-long", "prompt": "x" * 12001})

    assert response.status_code == 413


def test_production_requires_authentication(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "api_token", "production-token")
    client = TestClient(create_app(FakeOrchestrator()))

    assert client.get("/api/health").status_code == 200
    assert client.post("/api/tasks", json={"prompt": "hello"}).status_code == 401
    authorized = client.post(
        "/api/tasks",
        headers={"Authorization": "Bearer production-token"},
        json={"prompt": "hello"},
    )
    assert authorized.status_code == 202


def test_production_startup_fails_without_token(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "api_token", "")
    import pytest

    with pytest.raises(RuntimeError, match="RELAYOS_API_TOKEN"):
        create_app(FakeOrchestrator())
