from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class ProviderCapability(str, Enum):
    CHAT = "chat"
    CODE = "code"
    TOOLS = "tools"


@dataclass(frozen=True)
class ProviderDescriptor:
    name: str
    configured: bool
    models: list[str] = field(default_factory=list)
    capabilities: list[ProviderCapability] = field(default_factory=lambda: [ProviderCapability.CHAT])
    reason: Optional[str] = None

    def public_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "configured": self.configured,
            "models": self.models,
            "capabilities": [capability.value for capability in self.capabilities],
            "reason": self.reason,
        }


@dataclass
class ProviderResponse:
    provider: str
    model: str
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: Optional[str] = None


class ProviderError(RuntimeError):
    def __init__(self, provider: str, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.provider = provider
        self.code = code
        self.retryable = retryable

