from core.config import Settings, settings
from .base import ProviderCapability, ProviderDescriptor


class ProviderRegistry:
    """Single source of truth for provider availability shown to clients."""

    def __init__(self, runtime: Settings = settings):
        self.runtime = runtime

    def descriptors(self) -> list[ProviderDescriptor]:
        fireworks_models = self.runtime.allowed_models_list if self.runtime.fireworks_api_key else []
        return [
            ProviderDescriptor(
                name="fireworks",
                configured=bool(fireworks_models),
                models=fireworks_models,
                capabilities=[ProviderCapability.CHAT, ProviderCapability.CODE],
                reason=None if fireworks_models else "FIREWORKS_API_KEY is not configured",
            ),
            ProviderDescriptor(
                name="anthropic",
                configured=bool(self.runtime.anthropic_api_key),
                reason=None if self.runtime.anthropic_api_key else "ANTHROPIC_API_KEY is not configured",
                capabilities=[ProviderCapability.CHAT, ProviderCapability.CODE, ProviderCapability.TOOLS],
            ),
            ProviderDescriptor(
                name="openai",
                configured=bool(self.runtime.openai_api_key),
                reason=None if self.runtime.openai_api_key else "OPENAI_API_KEY is not configured",
                capabilities=[ProviderCapability.CHAT, ProviderCapability.CODE, ProviderCapability.TOOLS],
            ),
        ]

    def public_status(self) -> list[dict]:
        return [descriptor.public_dict() for descriptor in self.descriptors()]
