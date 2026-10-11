import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["CORS_ORIGINS"] = "*"

import uvicorn

from api import create_app
from core.config import settings
from models.enums import ExecutionRoute, ExecutionState
from models.schemas import ExecutionResult


class FakeOrchestrator:
    async def process_task(self, request):
        if "slow" in request.prompt:
            await asyncio.sleep(2.5)
        if "fail" in request.prompt:
            return ExecutionResult(
                task_id=request.task_id,
                answer="",
                route_taken=ExecutionRoute.PYTHON,
                state=ExecutionState.FAILED_FINAL,
                error_code="scripted_failure",
                error_message="scripted failure",
            )
        return ExecutionResult(
            task_id=request.task_id,
            answer=f"FAKE ANSWER for: {request.prompt}",
            route_taken=ExecutionRoute.PYTHON,
            state=ExecutionState.SUCCESS,
            latency_ms=1.0,
        )


settings.cors_origins = "*"
app = create_app(FakeOrchestrator())

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8006)
