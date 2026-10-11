"""Task-local execution ledger. Network requests are nested, never double counted."""
import asyncio
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict

from core.context_routing import Usage, normalize_provider_usage

active_audit = ContextVar("relayos_audit", default=None)
active_attempt = ContextVar("relayos_attempt", default=None)


def new_audit(task_id, run_id=None):
    return {"task_id": task_id, "run_id": run_id or uuid.uuid4().hex,
            "initial_decisions": [], "attempts": [], "queue_wait_ms": 0.0}


@contextmanager
def audit_scope(audit):
    token = active_audit.set(audit)
    try:
        yield audit
    finally:
        active_audit.reset(token)


def decision(context):
    audit = active_audit.get()
    if audit is not None:
        audit["initial_decisions"].append({"subtask_id": context.request.task_id,
                                           "route": context.route.value})


async def execute_recorded(executor, context, route):
    audit = active_audit.get()
    entry = {"attempt_number": len(audit["attempts"]) + 1 if audit else 1,
             "task_id": audit["task_id"] if audit else context.request.task_id,
             "run_id": audit["run_id"] if audit else None,
             "subtask_id": context.request.task_id, "route": route,
             "model": getattr(executor, "model_path", None),
             "provider": route if route in {"fireworks", "openai", "anthropic"} else "local",
             "started_at": time.time(), "requests": [], "outcome": "running",
             "fallback": bool(audit and any(a["subtask_id"] == context.request.task_id for a in audit["attempts"]))}
    if audit is not None:
        audit["attempts"].append(entry)
    token = active_attempt.set(entry)
    start = time.perf_counter()
    try:
        result = await executor.execute(context)
        entry.update(model=result.model or entry["model"], outcome=result.state.value,
                     error_category=result.error_code, queue_wait_ms=result.queue_wait_ms)
        entry["usage"] = {key: getattr(result, key) for key in ("input_tokens", "output_tokens", "cached_input_tokens", "cache_write_tokens", "usage_source")}
        return result
    except BaseException as exc:
        entry.update(outcome="cancelled" if isinstance(exc, asyncio.CancelledError) else "error",
                     error_category=type(exc).__name__)
        raise
    finally:
        entry["elapsed_ms"] = (time.perf_counter() - start) * 1000
        entry["ended_at"] = time.time()
        entry["external_request_made"] = any(r["external_request_made"] for r in entry["requests"])
        active_attempt.reset(token)


def validation(result, context, confidence_passed=None):
    audit = active_audit.get()
    if audit:
        for entry in reversed(audit["attempts"]):
            if entry["subtask_id"] == context.request.task_id:
                entry.update(validation_passed=result.validation_passed,
                             confidence_passed=confidence_passed, outcome=result.state.value,
                             error_category=result.error_code)
                if confidence_passed is False:
                    entry["outcome"] = "confidence_rejected"
                break


@contextmanager
def provider_request(provider, model):
    """Wrap exactly one transport call; capture usage before parsing the answer."""
    attempt = active_attempt.get()
    audit = active_audit.get()
    entry = {"provider": provider, "model": model, "started_at": time.time(),
             "usage": asdict(Usage()), "outcome": "running", "external_request_made": not (audit and audit.get("inference_mode") == "mock"),
             "inference_mode": (audit or {}).get("inference_mode", "live"),
             "paid_charge_confirmed": None,
             "task_id": audit["task_id"] if audit else None,
             "run_id": audit["run_id"] if audit else None}
    if attempt is not None:
        entry["request_number"] = len(attempt["requests"]) + 1
        entry["attempt_number"] = attempt["attempt_number"]
        attempt["requests"].append(entry)
        attempt["model"] = model
    start = time.perf_counter()
    try:
        yield entry
        entry["outcome"] = "response_received"
    except BaseException as exc:
        entry.update(outcome="cancelled" if isinstance(exc, asyncio.CancelledError) else "error",
                     error_category=type(exc).__name__, status_code=getattr(exc, "status_code", None))
        raise
    finally:
        entry["elapsed_ms"] = (time.perf_counter() - start) * 1000
        entry["ended_at"] = time.time()


def capture_usage(entry, response, provider):
    usage = normalize_provider_usage(response, provider)
    if entry.get("inference_mode") == "mock":
        from dataclasses import replace
        usage = replace(usage, source="fixture")
    entry["usage"] = asdict(usage)
    return usage


async def sdk_request(provider, model, call):
    """Keep SDK's customary two transient retries visible (SDK retry disabled)."""
    for retry_number in range(3):
        try:
            with provider_request(provider, model) as entry:
                entry["retry_number"] = retry_number
                response = await call()
                capture_usage(entry, response, provider)
                return response
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            retryable = status in {408, 409, 429} or (status is not None and status >= 500)
            retryable = retryable or isinstance(exc, (TimeoutError, ConnectionError)) or type(exc).__name__ in {"APIConnectionError", "APITimeoutError"}
            if retry_number == 2 or not retryable:
                raise
            await asyncio.sleep(0.5 * 2 ** retry_number)
