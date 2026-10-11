"""Optional Anthropic provider adapter with normalized responses."""

from typing import Optional

from .base import ProviderError, ProviderResponse


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, timeout: float = 30.0):
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:
            raise ProviderError(self.name, "provider_unavailable", "anthropic package is not installed") from exc
        try:
            self.client = AsyncAnthropic(api_key=api_key, timeout=timeout)
        except Exception as exc:
            raise ProviderError(self.name, "provider_initialization", "Anthropic client initialization failed", False) from exc

    async def complete(self, prompt: str, model: str, *, timeout: Optional[float] = None) -> ProviderResponse:
        try:
            kwargs = {"model": model, "max_tokens": 1024, "messages": [{"role": "user", "content": prompt}]}
            if timeout is not None:
                kwargs["timeout"] = timeout
            response = await self.client.messages.create(**kwargs)
            blocks = getattr(response, "content", []) or []
            text = "".join(getattr(block, "text", "") for block in blocks).strip()
            if not text:
                raise ProviderError(self.name, "empty_response", "Anthropic returned an empty response", True)
            usage = getattr(response, "usage", None)
            return ProviderResponse(
                provider=self.name,
                model=model,
                text=text,
                input_tokens=getattr(usage, "input_tokens", None),
                output_tokens=getattr(usage, "output_tokens", None),
                finish_reason=getattr(response, "stop_reason", None),
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
            raise ProviderError(self.name, error_code, "Anthropic request failed", retryable) from exc

    async def close(self) -> None:
        await self.client.close()
