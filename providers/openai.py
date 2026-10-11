"""Optional OpenAI provider adapter with normalized responses."""

from typing import Optional

from .base import ProviderError, ProviderResponse


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, timeout: float = 30.0):
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ProviderError(self.name, "provider_unavailable", "openai package is not installed") from exc
        try:
            self.client = AsyncOpenAI(api_key=api_key, timeout=timeout)
        except Exception as exc:
            raise ProviderError(self.name, "provider_initialization", "OpenAI client initialization failed", False) from exc

    async def complete(self, prompt: str, model: str, *, timeout: Optional[float] = None) -> ProviderResponse:
        try:
            kwargs = {"model": model, "messages": [{"role": "user", "content": prompt}]}
            if timeout is not None:
                kwargs["timeout"] = timeout
            response = await self.client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            text = (choice.message.content or "").strip()
            if not text:
                raise ProviderError(self.name, "empty_response", "OpenAI returned an empty response", True)
            usage = response.usage
            return ProviderResponse(
                provider=self.name,
                model=model,
                text=text,
                input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                output_tokens=getattr(usage, "completion_tokens", 0) or 0,
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
