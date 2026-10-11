import importlib.util

from core.config import Settings, settings
from .base import ProviderCapability, ProviderDescriptor


class ProviderRegistry:
    """Single source of truth for provider availability shown to clients."""

    def __init__(self, runtime: Settings = settings, initialization_errors: dict[str, str] | None = None):
        self.runtime = runtime
        self.initialization_errors = initialization_errors or {}

    def descriptors(self) -> list[ProviderDescriptor]:
        fireworks_models = self.runtime.allowed_models_list if self.runtime.fireworks_api_key else []
        anthropic_status = self.initialization_errors.get("anthropic") or self._status(self.runtime.anthropic_api_key, self.runtime.anthropic_models_list, "anthropic")
        openai_status = self.initialization_errors.get("openai") or self._status(self.runtime.openai_api_key, self.runtime.openai_models_list, "openai")
        return [
            ProviderDescriptor(
                name="fireworks",
                configured=bool(fireworks_models),
                status="configured" if fireworks_models else "missing_credentials",
                models=fireworks_models,
                capabilities=[ProviderCapability.CHAT, ProviderCapability.CODE],
                reason=None if fireworks_models else "FIREWORKS_API_KEY is not configured",
            ),
            ProviderDescriptor(
                name="anthropic",
                configured=anthropic_status == "configured",
                status=anthropic_status,
                models=self.runtime.anthropic_models_list,
                reason=None if anthropic_status == "configured" else self._reason("ANTHROPIC", anthropic_status),
                capabilities=[ProviderCapability.CHAT, ProviderCapability.CODE, ProviderCapability.TOOLS],
            ),
            ProviderDescriptor(
                name="openai",
                configured=openai_status == "configured",
                status=openai_status,
                models=self.runtime.openai_models_list,
                reason=None if openai_status == "configured" else self._reason("OPENAI", openai_status),
                capabilities=[ProviderCapability.CHAT, ProviderCapability.CODE, ProviderCapability.TOOLS],
            ),
        ]

    @staticmethod
    def _status(api_key: str, models: list[str], package: str) -> str:
        if not api_key:
            return "missing_credentials"
        if not models:
            return "missing_model_configuration"
        try:
            package_available = importlib.util.find_spec(package) is not None
        except (ImportError, ValueError):
            package_available = False
        if not package_available:
            return "missing_sdk"
        return "configured"

    @staticmethod
    def _reason(prefix: str, status: str) -> str:
        return {
            "missing_credentials": f"{prefix}_API_KEY is not configured",
            "missing_model_configuration": f"{prefix}_MODELS is not configured",
            "missing_sdk": f"{prefix.lower()} SDK is not installed",
            "initialization_failed": f"{prefix.lower()} provider initialization failed",
        }.get(status, "provider is unavailable")

    def public_status(self) -> list[dict]:
        return [descriptor.public_dict() for descriptor in self.descriptors()]
