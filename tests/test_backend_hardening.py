import asyncio
import multiprocessing as mp
import sys
import types
from types import SimpleNamespace

import pytest

from engine.executors.python import MathSandbox, PythonExecutor
import engine.executors.local_llm as local_llm
from engine.executors.local_llm import LocalLLMExecutor, LocalLLMWorker, WorkerState
from engine.executors.cloud import CloudProviderExecutor
from models.enums import ExecutionRoute, ExecutionState, TaskCategory
from models.schemas import TaskContext, TaskRequest
from providers.anthropic import AnthropicProvider
from providers.base import ProviderError
from providers.openai import OpenAIProvider
from providers.registry import ProviderRegistry
from core.config import Settings


def test_math_limits_reject_expensive_and_non_finite_results():
    with pytest.raises(ValueError, match="exponent"):
        MathSandbox.evaluate("2 ** 10001")
    with pytest.raises(ValueError, match="Math Error"):
        MathSandbox.evaluate("sqrt(-1)")
    with pytest.raises(ValueError, match="Division by zero"):
        MathSandbox.evaluate("1 / 0")


@pytest.mark.asyncio
async def test_empty_deterministic_result_is_retryable():
    context = TaskContext(request=TaskRequest(task_id="empty", prompt="Explain the universe"), category=TaskCategory.MATH)
    result = await PythonExecutor().execute(context)
    assert result.state == ExecutionState.FAILED_RETRYABLE
    assert result.error_code == "unsupported_expression"
    assert result.answer == ""


@pytest.mark.asyncio
async def test_openai_response_is_normalized_without_network():
    class Completions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=" answer "), finish_reason="stop")],
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4),
            )

    provider = OpenAIProvider.__new__(OpenAIProvider)
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    response = await provider.complete("hello", "test-model")
    assert response.text == "answer"
    assert response.input_tokens == 3
    assert response.output_tokens == 4


@pytest.mark.asyncio
async def test_anthropic_content_blocks_are_normalized_without_network():
    class Messages:
        async def create(self, **kwargs):
            return SimpleNamespace(
                content=[SimpleNamespace(text="first "), SimpleNamespace(text="second")],
                usage=SimpleNamespace(input_tokens=2, output_tokens=5),
                stop_reason="end_turn",
            )

    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider.client = SimpleNamespace(messages=Messages())
    response = await provider.complete("hello", "test-model")
    assert response.text == "first second"
    assert response.input_tokens == 2
    assert response.output_tokens == 5


@pytest.mark.asyncio
async def test_empty_provider_response_is_rejected():
    class EmptyCompletions:
        async def create(self, **kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=""), finish_reason="stop")], usage=None)

    provider = OpenAIProvider.__new__(OpenAIProvider)
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=EmptyCompletions()))
    with pytest.raises(ProviderError) as error:
        await provider.complete("hello", "test-model")
    assert error.value.code == "empty_response"
    assert error.value.retryable is True


@pytest.mark.asyncio
async def test_provider_authentication_and_rate_limit_errors_are_classified():
    class ErrorCompletions:
        def __init__(self, status_code):
            self.status_code = status_code

        async def create(self, **kwargs):
            error = RuntimeError("provider failure")
            error.status_code = self.status_code
            raise error

    for status_code, expected_code, retryable in [(401, "provider_authentication", False), (429, "provider_rate_limit", True)]:
        provider = OpenAIProvider.__new__(OpenAIProvider)
        provider.client = SimpleNamespace(chat=SimpleNamespace(completions=ErrorCompletions(status_code)))
        with pytest.raises(ProviderError) as error:
            await provider.complete("hello", "test-model")
        assert error.value.code == expected_code
        assert error.value.retryable is retryable


@pytest.mark.asyncio
async def test_provider_timeout_and_malformed_response_are_retryable_errors():
    class TimeoutCompletions:
        async def create(self, **kwargs):
            raise TimeoutError("timeout")

    provider = OpenAIProvider.__new__(OpenAIProvider)
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=TimeoutCompletions()))
    with pytest.raises(ProviderError) as timeout_error:
        await provider.complete("hello", "test-model")
    assert timeout_error.value.code == "provider_error"
    assert timeout_error.value.retryable is True

    class MalformedCompletions:
        async def create(self, **kwargs):
            return SimpleNamespace()

    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=MalformedCompletions()))
    with pytest.raises(ProviderError) as malformed_error:
        await provider.complete("hello", "test-model")
    assert malformed_error.value.code == "provider_error"


def test_provider_initialization_failure_is_structured(monkeypatch):
    fake_openai = types.ModuleType("openai")

    class FailingClient:
        def __init__(self, **kwargs):
            raise RuntimeError("initialization failed")

    fake_openai.AsyncOpenAI = FailingClient
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    with pytest.raises(ProviderError) as error:
        OpenAIProvider("configured")
    assert error.value.code == "provider_initialization"


@pytest.mark.asyncio
async def test_missing_cloud_credentials_fail_without_network():
    executor = CloudProviderExecutor("openai")
    context = TaskContext(request=TaskRequest(task_id="missing", prompt="hello", provider="openai"), category=TaskCategory.FACTUAL)
    result = await executor.execute(context)
    assert result.state == ExecutionState.FAILED_FINAL
    assert result.error_code == "provider_unavailable"


def test_provider_registry_reports_missing_model_configuration():
    registry = ProviderRegistry(Settings(openai_api_key="configured", openai_models=""))
    openai = next(item for item in registry.descriptors() if item.name == "openai")
    assert openai.configured is False
    assert openai.status == "missing_model_configuration"


def test_provider_constructor_reports_missing_sdk(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", None)
    with pytest.raises(ProviderError) as error:
        OpenAIProvider("configured")
    assert error.value.code == "provider_unavailable"


def test_provider_adapter_names_are_stable():
    assert OpenAIProvider.name == "openai"
    assert AnthropicProvider.name == "anthropic"
    assert ExecutionRoute.OPENAI.value == "openai"


def test_controlled_worker_is_terminated():
    context = mp.get_context("spawn")
    process = context.Process(target=local_llm._controlled_sleep_worker)
    process.start()
    worker = LocalLLMWorker("unused")
    worker.process = process
    worker.state = WorkerState.HEALTHY
    worker.terminate()
    assert not process.is_alive()


def test_live_worker_is_not_replaced_before_exit():
    context = mp.get_context("spawn")
    process = context.Process(target=local_llm._controlled_sleep_worker)
    process.start()
    worker = LocalLLMWorker("unused")
    worker.process = process
    worker.state = WorkerState.DEAD
    original_process = worker.process
    assert worker.start() is False
    assert worker.process is original_process
    worker.terminate()


@pytest.mark.asyncio
async def test_local_cancellation_terminates_active_worker(monkeypatch):
    monkeypatch.setattr(local_llm, "HAS_LLAMA", True)

    class FakeConnection:
        def send(self, message):
            return None

    context = mp.get_context("spawn")
    process = context.Process(target=local_llm._controlled_sleep_worker)
    process.start()
    worker = LocalLLMWorker("unused")
    worker.process = process
    worker.parent_conn = FakeConnection()
    worker.state = WorkerState.HEALTHY

    executor = LocalLLMExecutor("unused")
    executor.worker = worker

    async def already_started():
        return True

    async def never_returns(timeout):
        await asyncio.sleep(300)

    executor._ensure_worker = already_started
    executor._receive_response = never_returns
    task_context = TaskContext(request=TaskRequest(task_id="cancel", prompt="hello"), category=TaskCategory.FACTUAL)
    task = asyncio.create_task(executor.execute(task_context))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not process.is_alive()
