"""Common executor for optional OpenAI and Anthropic adapters."""

from core.config import settings
from models.enums import ExecutionRoute, ExecutionState
from models.schemas import ExecutionResult, TaskContext
from providers.anthropic import AnthropicProvider
from providers.base import ProviderError
from providers.openai import OpenAIProvider
from .base import BaseExecutor


class CloudProviderExecutor(BaseExecutor):
    def __init__(self, provider: str):
        self.provider_name = provider
        self.adapter = None
        self.initialization_error = None
        try:
            if provider == "openai" and settings.openai_api_key:
                self.adapter = OpenAIProvider(settings.openai_api_key)
            elif provider == "anthropic" and settings.anthropic_api_key:
                self.adapter = AnthropicProvider(settings.anthropic_api_key)
        except ProviderError as exc:
            self.initialization_error = exc

    async def execute(self, context: TaskContext) -> ExecutionResult:
        route = ExecutionRoute(self.provider_name)
        model = context.request.model
        models = settings.openai_models_list if self.provider_name == "openai" else settings.anthropic_models_list
        model = model or (models[0] if models else None)
        if self.initialization_error:
            return ExecutionResult(task_id=context.request.task_id, answer="", route_taken=route, state=ExecutionState.FAILED_FINAL, fallback_triggered=True, error_code=self.initialization_error.code, error_message="Cloud provider initialization failed")
        if self.adapter is None or not model:
            return ExecutionResult(task_id=context.request.task_id, answer="", route_taken=route, state=ExecutionState.FAILED_FINAL, fallback_triggered=True, error_code="provider_unavailable", error_message=f"{self.provider_name} is not configured")
        try:
            response = await self.adapter.complete(context.request.prompt, model, timeout=settings.task_timeout_seconds)
            return ExecutionResult(task_id=context.request.task_id, answer=response.text, route_taken=route, model=model, state=ExecutionState.SUCCESS, fireworks_tokens=(response.input_tokens or 0) + (response.output_tokens or 0), input_tokens=response.input_tokens, output_tokens=response.output_tokens, usage_source="provider")
        except ProviderError as exc:
            return ExecutionResult(task_id=context.request.task_id, answer="", route_taken=route, model=model, state=ExecutionState.FAILED_RETRYABLE if exc.retryable else ExecutionState.FAILED_FINAL, fallback_triggered=True, error_code=exc.code, error_message="Cloud provider request failed", fallback_reason=exc.code)

    async def shutdown(self) -> None:
        if self.adapter is not None:
            await self.adapter.close()
