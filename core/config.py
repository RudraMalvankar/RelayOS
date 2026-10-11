import os
from typing import List
from pydantic import BaseModel

class Settings(BaseModel):
    fireworks_api_key: str = os.getenv("FIREWORKS_API_KEY", "")
    fireworks_base_url: str = os.getenv("FIREWORKS_BASE_URL", "https://api.fireworks.ai/inference/v1")
    allowed_models: str = os.getenv("ALLOWED_MODELS", "accounts/fireworks/models/deepseek-v4-flash,accounts/fireworks/models/deepseek-v4-pro")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    local_model_path: str = os.getenv("LOCAL_MODEL_PATH", "/model/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf")
    max_prompt_chars: int = int(os.getenv("MAX_PROMPT_CHARS", "12000"))
    task_timeout_seconds: float = float(os.getenv("TASK_TIMEOUT_SECONDS", "28"))
    max_concurrent_tasks: int = int(os.getenv("MAX_CONCURRENT_TASKS", "4"))
    cors_origins: str = os.getenv("CORS_ORIGINS", "http://localhost:5173")
    
    @property
    def fireworks_fast_model(self) -> str:
        return self.allowed_models_list[0]

    @property
    def fireworks_reasoning_model(self) -> str:
        return self.allowed_models_list[-1] if len(self.allowed_models_list) > 1 else self.allowed_models_list[0]
    @property
    def allowed_models_list(self) -> List[str]:
        return [m.strip() for m in self.allowed_models.split(",") if m.strip()]

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def provider_status(self) -> dict:
        return {
            "fireworks": bool(self.fireworks_api_key and self.allowed_models_list),
            "anthropic": bool(self.anthropic_api_key),
            "openai": bool(self.openai_api_key),
        }

settings = Settings()
