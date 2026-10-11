import os
from typing import List
from pydantic import BaseModel

class Settings(BaseModel):
    environment: str = os.getenv("RELAYOS_ENV", "development")
    fireworks_api_key: str = os.getenv("FIREWORKS_API_KEY", "")
    fireworks_base_url: str = os.getenv("FIREWORKS_BASE_URL", "https://api.fireworks.ai/inference/v1")
    allowed_models: str = os.getenv("ALLOWED_MODELS", "accounts/fireworks/models/deepseek-v4-flash,accounts/fireworks/models/deepseek-v4-pro")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_models: str = os.getenv("OPENAI_MODELS", "")
    anthropic_models: str = os.getenv("ANTHROPIC_MODELS", "")
    local_model_path: str = os.getenv("LOCAL_MODEL_PATH", "/model/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf")
    max_prompt_chars: int = int(os.getenv("MAX_PROMPT_CHARS", "12000"))
    task_timeout_seconds: float = float(os.getenv("TASK_TIMEOUT_SECONDS", "28"))
    max_concurrent_tasks: int = int(os.getenv("MAX_CONCURRENT_TASKS", "4"))
    max_task_records: int = int(os.getenv("MAX_TASK_RECORDS", "256"))
    task_retention_seconds: float = float(os.getenv("TASK_RETENTION_SECONDS", "3600"))
    local_queue_size: int = int(os.getenv("LOCAL_QUEUE_SIZE", "2"))
    local_inference_timeout_seconds: float = float(os.getenv("LOCAL_INFERENCE_TIMEOUT_SECONDS", "18"))
    local_startup_timeout_seconds: float = float(os.getenv("LOCAL_STARTUP_TIMEOUT_SECONDS", "60"))
    local_restart_limit: int = int(os.getenv("LOCAL_RESTART_LIMIT", "2"))
    api_token: str = os.getenv("RELAYOS_API_TOKEN", "")
    rate_limit_per_minute: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
    telemetry_path: str = os.getenv("TELEMETRY_PATH", "./output/telemetry.json")
    math_max_expression_chars: int = int(os.getenv("MATH_MAX_EXPRESSION_CHARS", "1000"))
    math_max_ast_nodes: int = int(os.getenv("MATH_MAX_AST_NODES", "100"))
    math_max_ast_depth: int = int(os.getenv("MATH_MAX_AST_DEPTH", "20"))
    math_max_integer_digits: int = int(os.getenv("MATH_MAX_INTEGER_DIGITS", "100"))
    math_max_exponent: int = int(os.getenv("MATH_MAX_EXPONENT", "10000"))
    math_max_result_magnitude: float = float(os.getenv("MATH_MAX_RESULT_MAGNITUDE", "1e100"))
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

    @property
    def openai_models_list(self) -> List[str]:
        return [m.strip() for m in self.openai_models.split(",") if m.strip()]

    @property
    def anthropic_models_list(self) -> List[str]:
        return [m.strip() for m in self.anthropic_models.split(",") if m.strip()]

    def provider_status(self) -> dict:
        return {
            "fireworks": bool(self.fireworks_api_key and self.allowed_models_list),
            "anthropic": bool(self.anthropic_api_key),
            "openai": bool(self.openai_api_key),
        }

settings = Settings()
