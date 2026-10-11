"""Optional OpenAI provider adapter with normalized responses."""

from typing import Optional

from .base import ProviderError, ProviderResponse
from core.audit import sdk_request
from core.context_routing import normalize_provider_usage


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, timeout: float = 30.0):
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ProviderError(self.name, "provider_unavailable", "openai package is not installed") from exc
        try:
            self.client = AsyncOpenAI(api_key=api_key, timeout=timeout, max_retries=0)
        except Exception as exc:
            raise ProviderError(self.name, "provider_initialization", "OpenAI client initialization failed", False) from exc

    async def complete(self, prompt: str, model: str, *, timeout: Optional[float] = None) -> ProviderResponse:
        try:
            kwargs = {"model": model, "messages": [{"role": "user", "content": prompt}]}
            if timeout is not None:
                kwargs["timeout"] = timeout
            response = await sdk_request(self.name, model, lambda: self.client.chat.completions.create(**kwargs))
            choice = response.choices[0]
            text = (choice.message.content or "").strip()
            if not text:
                raise ProviderError(self.name, "empty_response", "OpenAI returned an empty response", True)
            usage = normalize_provider_usage(response, self.name)
            return ProviderResponse(
                provider=self.name,
                model=model,
                text=text,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cached_input_tokens=usage.cached_input_tokens,
                cache_write_tokens=usage.cache_write_tokens,
                usage_source=usage.source,
                finish_reason=getattr(choice, "finish_reason", None),
            )
        except ProviderError:
            raise
        except Exception as exc:
            code = getattr(exc, "status_code", None)
            if code == 401:
                error_code, retryable = "provider_authentication", False
            elif code == 429:
                error_code, retryable = "provider_rate_limit", True
            elif code and code >= 500:
                error_code, retryable = "provider_unavailable", True
            else:
                error_code, retryable = "provider_error", True
            raise ProviderError(self.name, error_code, "OpenAI request failed", retryable) from exc

    async def close(self) -> None:
        await self.client.close()
