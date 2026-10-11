from core.config import Settings


def test_provider_status_does_not_expose_credentials():
    settings = Settings(
        fireworks_api_key="fireworks-secret",
        anthropic_api_key="anthropic-secret",
        openai_api_key="openai-secret",
    )

    status = settings.provider_status()

    assert status == {"fireworks": True, "anthropic": True, "openai": True}
    assert "secret" not in str(status)


def test_runtime_lists_are_normalized():
    settings = Settings(
        allowed_models=" model-a,model-b ",
        cors_origins=" http://localhost:5173, https://example.test ",
    )

    assert settings.allowed_models_list == ["model-a", "model-b"]
    assert settings.cors_origins_list == ["http://localhost:5173", "https://example.test"]
