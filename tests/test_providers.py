from core.config import Settings
from providers.base import ProviderCapability
from providers.registry import ProviderRegistry


def test_registry_marks_unconfigured_cloud_providers_honestly():
    registry = ProviderRegistry(Settings())
    providers = {item["name"]: item for item in registry.public_status()}

    assert providers["anthropic"]["configured"] is False
    assert providers["openai"]["configured"] is False
    assert "API_KEY" in providers["anthropic"]["reason"]


def test_registry_exposes_capabilities_without_secrets():
    registry = ProviderRegistry(Settings(fireworks_api_key="test", allowed_models="model-a"))
    fireworks = next(item for item in registry.descriptors() if item.name == "fireworks")

    assert fireworks.configured is True
    assert ProviderCapability.CODE in fireworks.capabilities
    assert "test" not in str(fireworks.public_dict())
