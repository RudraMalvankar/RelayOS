"""HTTP API for RelayOS.

The existing ``main.py`` batch entry point remains supported. This module adds
an optional service boundary with bounded task admission and redacted provider
status information.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from core.config import settings
from core.telemetry import TelemetryService
from main import RelayOSOrchestrator
from models.enums import ExecutionState
from models.schemas import ExecutionResult, TaskRequest


class TaskCreate(BaseModel):
    prompt: str = Field(min_length=1)
    task_id: Optional[str] = None


class TaskSummary(BaseModel):
    task_id: str
    status: str
    result: Optional[ExecutionResult] = None
    error: Optional[str] = None
    created_at: float
    updated_at: float


@dataclass
class _TaskRecord:
    task_id: str
    status: str = "queued"
    result: Optional[ExecutionResult] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    job: Optional[asyncio.Task] = None

    def summary(self) -> TaskSummary:
        return TaskSummary(
            task_id=self.task_id,
            status=self.status,
            result=self.result,
            error=self.error,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


def create_app(orchestrator: Any = None) -> FastAPI:
    app = FastAPI(title="RelayOS API", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    app.state.orchestrator = orchestrator
    app.state.tasks: Dict[str, _TaskRecord] = {}
    app.state.task_lock = asyncio.Lock()
    app.state.semaphore = asyncio.Semaphore(max(1, settings.max_concurrent_tasks))

    async def get_orchestrator():
        if app.state.orchestrator is None:
            app.state.orchestrator = RelayOSOrchestrator()
        return app.state.orchestrator

    async def run_task(record: _TaskRecord, request: TaskRequest):
        async with app.state.semaphore:
            record.status = "running"
            record.updated_at = time.time()
            try:
                result = await asyncio.wait_for(
                    (await get_orchestrator()).process_task(request),
                    timeout=settings.task_timeout_seconds,
                )
                record.result = result
                record.status = result.state.value
            except asyncio.CancelledError:
                record.status = "cancelled"
                raise
            except asyncio.TimeoutError:
                record.status = "failed_final"
                record.error = "Task deadline exceeded"
            except Exception as exc:
                record.status = "failed_final"
                record.error = str(exc)
            finally:
                record.updated_at = time.time()

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok", "service": "relayos"}

    @app.get("/api/providers")
    async def providers() -> dict:
        return {"providers": settings.provider_status()}

    @app.get("/api/models")
    async def models() -> dict:
        return {
            "fireworks": settings.allowed_models_list,
            "anthropic": [],
            "openai": [],
            "local": {"configured": bool(settings.local_model_path), "path": settings.local_model_path},
        }

    @app.post("/api/tasks", response_model=TaskSummary, status_code=202)
    async def create_task(payload: TaskCreate):
        if len(payload.prompt) > settings.max_prompt_chars:
            raise HTTPException(status_code=413, detail="Prompt exceeds configured size limit")
        task_id = payload.task_id or str(uuid.uuid4())
        async with app.state.task_lock:
            if task_id in app.state.tasks:
                raise HTTPException(status_code=409, detail="Task ID already exists")
            record = _TaskRecord(task_id=task_id)
            app.state.tasks[task_id] = record
            record.job = asyncio.create_task(
                run_task(record, TaskRequest(task_id=task_id, prompt=payload.prompt))
            )
        return record.summary()

    @app.get("/api/tasks/{task_id}", response_model=TaskSummary)
    async def get_task(task_id: str):
        record = app.state.tasks.get(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return record.summary()

    @app.post("/api/tasks/{task_id}/cancel", response_model=TaskSummary)
    async def cancel_task(task_id: str):
        record = app.state.tasks.get(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Task not found")
        if record.job and not record.job.done():
            record.job.cancel()
            record.status = "cancelled"
            record.updated_at = time.time()
        return record.summary()

    @app.get("/api/metrics")
    async def metrics() -> dict:
        return TelemetryService.get_instance().counters

    return app


app = create_app()
