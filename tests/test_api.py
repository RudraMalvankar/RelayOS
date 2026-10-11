from fastapi.testclient import TestClient

from api import create_app
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
