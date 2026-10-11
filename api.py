"""HTTP API for RelayOS.

The existing ``main.py`` batch entry point remains supported. This module adds
an optional service boundary with bounded task admission and redacted provider
status information.
"""

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from collections import defaultdict, deque
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from core.audit import audit_scope, new_audit
from core.config import settings
from core.telemetry import TelemetryService
from main import RelayOSOrchestrator
from models.enums import ExecutionState
from models.schemas import ExecutionResult, TaskRequest
from providers.registry import ProviderRegistry


class TaskCreate(BaseModel):
    prompt: str = Field(min_length=1)
    task_id: Optional[str] = None
    execution_mode: str = Field(default="auto", pattern="^(auto|local|cloud)$")
    provider: Optional[str] = Field(default=None, pattern="^(fireworks|openai|anthropic|local_llm)$")
    model: Optional[str] = None
    session_id: Optional[str] = None
    required_quality: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    context_options: Optional[dict] = None


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
    audit: Optional[dict] = None

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
    if settings.environment.lower() in {"production", "prod"} and not settings.api_token:
        raise RuntimeError("RELAYOS_API_TOKEN must be configured when RELAYOS_ENV=production")

    @asynccontextmanager
    async def lifespan(app_instance: FastAPI):
        yield
        active_orchestrator = app_instance.state.orchestrator
        if active_orchestrator is not None and hasattr(active_orchestrator, "shutdown"):
            await active_orchestrator.shutdown()

    app = FastAPI(title="RelayOS API", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
    )

    app.state.orchestrator = orchestrator
    app.state.tasks: Dict[str, _TaskRecord] = {}
    app.state.task_lock = asyncio.Lock()
    app.state.orchestrator_lock = asyncio.Lock()
    app.state.semaphore = asyncio.Semaphore(max(1, settings.max_concurrent_tasks))
    app.state.rate_hits = defaultdict(deque)

    async def get_orchestrator():
        if app.state.orchestrator is None:
            async with app.state.orchestrator_lock:
                if app.state.orchestrator is None:
                    app.state.orchestrator = RelayOSOrchestrator()
        return app.state.orchestrator

    async def authorize(request: Request, authorization: Optional[str] = Header(default=None)):
        authentication_required = settings.environment.lower() in {"production", "prod"} or bool(settings.api_token)
        if authentication_required:
            expected = f"Bearer {settings.api_token}"
            if authorization != expected:
                raise HTTPException(status_code=401, detail="Authentication required")
        now = time.time()
        key = request.client.host if request.client else "unknown"
        hits = app.state.rate_hits[key]
        while hits and hits[0] <= now - 60:
            hits.popleft()
        if len(hits) >= settings.rate_limit_per_minute:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        hits.append(now)

    async def prune_tasks():
        cutoff = time.time() - settings.task_retention_seconds
        async with app.state.task_lock:
            expired = [task_id for task_id, record in app.state.tasks.items() if record.updated_at < cutoff and (not record.job or record.job.done())]
            for task_id in expired:
                app.state.tasks.pop(task_id, None)
            if len(app.state.tasks) > settings.max_task_records:
                records = sorted(app.state.tasks.values(), key=lambda item: item.updated_at)
                for record in records[:len(app.state.tasks) - settings.max_task_records]:
                    if not record.job or record.job.done():
                        app.state.tasks.pop(record.task_id, None)

    async def run_task(record: _TaskRecord, request: TaskRequest):
        queued = time.perf_counter()
        record.audit = new_audit(record.task_id)
        async with app.state.semaphore:
            record.audit["queue_wait_ms"] = (time.perf_counter() - queued) * 1000
            record.status = "running"
            record.updated_at = time.time()
            with audit_scope(record.audit):
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
                    record.error = "Task execution failed"
                finally:
                    record.updated_at = time.time()
                    record.audit["state"] = record.status

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok", "service": "relayos"}

    @app.get("/api/providers")
    async def providers(_: None = Depends(authorize)) -> dict:
        orchestrator = app.state.orchestrator
        errors = getattr(orchestrator, "provider_initialization_errors", {}) if orchestrator else {}
        return {"providers": ProviderRegistry(initialization_errors=errors).public_status()}

    @app.get("/api/models")
    async def models(_: None = Depends(authorize)) -> dict:
        return {
            "fireworks": settings.allowed_models_list,
            "anthropic": settings.anthropic_models_list,
            "openai": settings.openai_models_list,
            "local": {"configured": bool(settings.local_model_path), "path": settings.local_model_path},
        }

    @app.post("/api/tasks", response_model=TaskSummary, status_code=202)
    async def create_task(payload: TaskCreate, _: None = Depends(authorize)):
        if len(payload.prompt) > settings.max_prompt_chars:
            raise HTTPException(status_code=413, detail="Prompt exceeds configured size limit")
        if payload.provider == "local_llm" and payload.execution_mode == "cloud":
            raise HTTPException(status_code=400, detail="local_llm cannot be used with cloud execution mode")
        if payload.provider in {"fireworks", "openai", "anthropic"} and payload.execution_mode == "local":
            raise HTTPException(status_code=400, detail="Cloud providers cannot be used with local execution mode")
        if payload.model:
            available = {
                "fireworks": settings.allowed_models_list,
                "openai": settings.openai_models_list,
                "anthropic": settings.anthropic_models_list,
            }
            if payload.provider in available and payload.model not in available[payload.provider]:
                raise HTTPException(status_code=400, detail="Requested model is not configured for this provider")
        await prune_tasks()
        task_id = payload.task_id or str(uuid.uuid4())
        async with app.state.task_lock:
            if task_id in app.state.tasks:
                raise HTTPException(status_code=409, detail="Task ID already exists")
            record = _TaskRecord(task_id=task_id)
            app.state.tasks[task_id] = record
            record.job = asyncio.create_task(run_task(record, TaskRequest(task_id=task_id, prompt=payload.prompt, execution_mode=payload.execution_mode, provider=payload.provider, model=payload.model, session_id=payload.session_id, required_quality=payload.required_quality, context_options=payload.context_options)))
        return record.summary()

    @app.get("/api/tasks/{task_id}", response_model=TaskSummary)
    async def get_task(task_id: str, _: None = Depends(authorize)):
        record = app.state.tasks.get(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return record.summary()

    @app.get("/api/tasks/{task_id}/audit")
    async def get_task_audit(task_id: str, _: None = Depends(authorize)):
        record = app.state.tasks.get(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Task not found")
        from core.reporting import priced_requests
        from copy import deepcopy
        audit = deepcopy(record.audit) if record.audit else {"task_id": task_id, "attempts": [], "state": record.status}
        priced_requests(audit)
        return audit

    @app.post("/api/tasks/{task_id}/cancel", response_model=TaskSummary)
    async def cancel_task(task_id: str, _: None = Depends(authorize)):
        record = app.state.tasks.get(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Task not found")
        if record.job and not record.job.done():
            record.job.cancel()
            record.status = "cancelled"
            record.updated_at = time.time()
        return record.summary()

    @app.get("/api/metrics")
    async def metrics(_: None = Depends(authorize)) -> dict:
        return TelemetryService.get_instance().counters

    return app


app = create_app()
