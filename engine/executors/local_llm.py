"""Supervised local GGUF inference with hard timeout isolation."""

import asyncio
import multiprocessing as mp
import os
import time
from typing import Optional

from core.config import settings
from core.logger import setup_logger
from models.enums import ExecutionRoute, ExecutionState
from models.schemas import ExecutionResult, TaskContext
from .base import BaseExecutor

logger = setup_logger("local_llm_executor")

try:
    from llama_cpp import Llama, LlamaGrammar
    HAS_LLAMA = True
except ImportError:
    Llama = None
    LlamaGrammar = None
    HAS_LLAMA = False

NER_GRAMMAR = r'''root ::= "[" entity ("," entity)* "]"
entity ::= "{\"entity\": \"" [^\"]+ "\", \"label\": \"" label "\"}"
label ::= "PERSON" | "ORGANIZATION" | "LOCATION" | "DATE"'''


def _prompt_for(category: str, prompt: str) -> list[dict[str, str]]:
    if category == "sentiment":
        return [{"role": "system", "content": "Classify sentiment as Positive, Negative, Neutral, or Mixed."}, {"role": "user", "content": prompt}]
    if category == "ner":
        return [{"role": "system", "content": "Extract entities into JSON list format."}, {"role": "user", "content": prompt}]
    return [{"role": "system", "content": "Answer concisely."}, {"role": "user", "content": prompt}]


def _worker_main(conn, model_path: str, n_threads: int) -> None:
    model = None
    try:
        if not HAS_LLAMA:
            conn.send({"ok": False, "error_code": "provider_unavailable", "error": "llama-cpp-python is not installed"})
            return
        model = Llama(model_path=model_path, n_ctx=512, n_batch=512, n_threads=n_threads, verbose=False)
        conn.send({"ok": True})
        while True:
            request = conn.recv()
            if request is None:
                return
            started = time.perf_counter()
            grammar = None
            if request["category"] == "ner" and LlamaGrammar is not None:
                grammar = LlamaGrammar.from_string(NER_GRAMMAR)
            response = model.create_chat_completion(
                messages=_prompt_for(request["category"], request["prompt"]),
                max_tokens=request["max_tokens"], temperature=0.0, grammar=grammar,
                stop=["User:", "user:", "<|im_end|>"],
            )
            answer = response["choices"][0]["message"]["content"].strip()
            conn.send({"ok": bool(answer), "answer": answer, "usage": response.get("usage"), "latency_ms": (time.perf_counter() - started) * 1000, "error_code": None if answer else "empty_response"})
    except Exception as exc:
        try:
            conn.send({"ok": False, "error_code": "provider_error", "error": str(exc)})
        except Exception:
            pass
    finally:
        conn.close()


def _controlled_sleep_worker():
    """Test/support target used to validate hard process termination."""
    time.sleep(300)


class WorkerState:
    STARTING = "starting"
    HEALTHY = "healthy"
    BUSY = "busy"
    COOLDOWN = "cooldown"
    DEAD = "dead"
    STOPPING = "stopping"


class LocalLLMWorker:
    def __init__(self, model_path: str):
        self.model_path = model_path
        self.state = WorkerState.DEAD
        self.process: Optional[mp.Process] = None
        self.parent_conn = None
        self.lock = asyncio.Lock()
        self.restart_count = 0

    def start(self) -> bool:
        if self.process and self.process.is_alive():
            return self.state == WorkerState.HEALTHY
        if not HAS_LLAMA or not os.path.exists(self.model_path):
            self.state = WorkerState.DEAD
            return False
        self.state = WorkerState.STARTING
        ctx = mp.get_context("spawn")
        self.parent_conn, child_conn = ctx.Pipe()
        self.process = ctx.Process(target=_worker_main, args=(child_conn, self.model_path, 1), daemon=True)
        self.process.start()
        if self.parent_conn.poll(settings.local_startup_timeout_seconds):
            message = self.parent_conn.recv()
            if message.get("ok"):
                self.state = WorkerState.HEALTHY
                return True
        self.terminate()
        return False

    def terminate(self) -> bool:
        self.state = WorkerState.STOPPING
        process = self.process
        if process:
            if process.is_alive():
                process.terminate()
            process.join(timeout=2)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(timeout=2)
            if process.is_alive():
                self.state = WorkerState.DEAD
                return False
        self.process = None
        self.parent_conn = None
        self.state = WorkerState.DEAD
        return True


class LocalLLMExecutor(BaseExecutor):
    def __init__(self, model_path: Optional[str] = None):
        self.model_path = model_path or settings.local_model_path
        self.worker = LocalLLMWorker(self.model_path)
        self.queue = asyncio.Queue(maxsize=max(1, settings.local_queue_size))
        self.circuit_breaker_tripped = False
        self._shutdown = False
        self._startup_lock = asyncio.Lock()

    def is_available(self) -> bool:
        return HAS_LLAMA and not self._shutdown and not self.circuit_breaker_tripped

    async def _ensure_worker(self) -> bool:
        async with self._startup_lock:
            if self.worker.state == WorkerState.HEALTHY and self.worker.process and self.worker.process.is_alive():
                return True
            if self.worker.restart_count >= settings.local_restart_limit:
                self.circuit_breaker_tripped = True
                return False
            self.worker.restart_count += 1
            try:
                return await asyncio.to_thread(self.worker.start)
            except asyncio.CancelledError:
                await asyncio.to_thread(self.worker.terminate)
                raise

    async def _receive_response(self, timeout: float):
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            if self.worker.parent_conn.poll(0.05):
                return self.worker.parent_conn.recv()
            await asyncio.sleep(0)
        raise asyncio.TimeoutError

    async def execute(self, context: TaskContext) -> ExecutionResult:
        if not self.is_available() or not await self._ensure_worker():
            return self._failure(context, "provider_unavailable", "Local worker is unavailable")
        queued_at = time.perf_counter()
        try:
            self.queue.put_nowait(context.request.task_id)
        except asyncio.QueueFull:
            return self._failure(context, "queue_full", "Local inference queue is full")
        async with self.worker.lock:
            await self.queue.get()
            queue_wait_ms = (time.perf_counter() - queued_at) * 1000
            self.worker.state = WorkerState.BUSY
            try:
                max_tokens = 256
                if context.profile and context.profile.estimated_output_tokens:
                    max_tokens = min(max(int(context.profile.estimated_output_tokens * 1.2), 256), 512)
                self.worker.parent_conn.send({"task_id": context.request.task_id, "prompt": context.request.prompt, "category": context.category.value if context.category else "general", "max_tokens": max_tokens})
                response = await self._receive_response(settings.local_inference_timeout_seconds)
                self.worker.state = WorkerState.HEALTHY
                if not response.get("ok"):
                    return self._failure(context, response.get("error_code", "provider_error"), response.get("error", "Local inference failed"), queue_wait_ms)
                from core.context_routing import normalize_provider_usage
                usage = normalize_provider_usage(response)
                return ExecutionResult(task_id=context.request.task_id, answer=response["answer"], route_taken=ExecutionRoute.LOCAL_LLM, state=ExecutionState.SUCCESS, queue_wait_ms=queue_wait_ms, latency_ms=response.get("latency_ms"), model=self.model_path,
                    input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                    usage_source="relayos" if usage.source == "provider" else "unknown")
            except asyncio.TimeoutError:
                self.worker.state = WorkerState.COOLDOWN
                self.worker.terminate()
                return self._failure(context, "timeout", "Local inference deadline exceeded", queue_wait_ms)
            except asyncio.CancelledError:
                self.worker.state = WorkerState.COOLDOWN
                await asyncio.to_thread(self.worker.terminate)
                raise
            except Exception as exc:
                self.worker.terminate()
                return self._failure(context, "provider_error", "Local inference failed", queue_wait_ms, str(exc))
            finally:
                self.queue.task_done()

    def _failure(self, context: TaskContext, code: str, message: str, queue_wait_ms: Optional[float] = None, detail: Optional[str] = None) -> ExecutionResult:
        return ExecutionResult(task_id=context.request.task_id, answer="", route_taken=ExecutionRoute.LOCAL_LLM, state=ExecutionState.FAILED_RETRYABLE, fallback_triggered=True, queue_wait_ms=queue_wait_ms, error_code=code, error_message=detail or message, fallback_reason=code)

    async def shutdown(self) -> None:
        self._shutdown = True
        await asyncio.to_thread(self.worker.terminate)

    async def math_extraction_fallback(self, prompt: str) -> Optional[str]:
        return None
