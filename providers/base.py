from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Protocol


class ProviderCapability(str, Enum):
    CHAT = "chat"
    CODE = "code"
    TOOLS = "tools"


@dataclass(frozen=True)
class ProviderDescriptor:
    name: str
    configured: bool
    status: str = "unavailable"
    models: list[str] = field(default_factory=list)
    capabilities: list[ProviderCapability] = field(default_factory=lambda: [ProviderCapability.CHAT])
    reason: Optional[str] = None

    def public_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "configured": self.configured,
            "status": self.status,
            "models": self.models,
            "capabilities": [capability.value for capability in self.capabilities],
            "reason": self.reason,
        }


@dataclass
class ProviderResponse:
    provider: str
    model: str
    text: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    finish_reason: Optional[str] = None
    cached_input_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    usage_source: str = "unknown"


class ProviderError(RuntimeError):
    def __init__(self, provider: str, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.provider = provider
        self.code = code
        self.retryable = retryable


class ProviderAdapter(Protocol):
    """Common async contract implemented by optional cloud providers."""

    name: str

    async def complete(self, prompt: str, model: str, *, timeout: Optional[float] = None) -> ProviderResponse:
        ...

    async def close(self) -> None:
        ...

