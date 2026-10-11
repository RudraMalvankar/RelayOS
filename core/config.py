import os
from typing import List
from pydantic import BaseModel
from dotenv import load_dotenv

# Load local development secrets and settings when a .env file is present.
# The file is ignored by Git and environment variables already set by the
# shell/container continue to take precedence.
load_dotenv()

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
    context_management_enabled: bool = os.getenv("CONTEXT_MANAGEMENT_ENABLED", "false").lower() in {"1", "true", "yes"}
    context_routing_enabled: bool = os.getenv("CONTEXT_ROUTING_ENABLED", "false").lower() in {"1", "true", "yes"}
    context_routing_shadow: bool = os.getenv("CONTEXT_ROUTING_SHADOW", "true").lower() in {"1", "true", "yes"}
    session_store_path: str = os.getenv("SESSION_STORE_PATH", "./output/sessions.json")
    session_max_history: int = int(os.getenv("SESSION_MAX_HISTORY", "10"))
    session_summary_max_chars: int = int(os.getenv("SESSION_SUMMARY_MAX_CHARS", "2000"))
    routing_currency: str = os.getenv("ROUTING_CURRENCY", "USD")
    routing_safety_margin: float = float(os.getenv("ROUTING_SAFETY_MARGIN", "0.10"))
    routing_prices_json: str = os.getenv("ROUTING_PRICES_JSON", "")
    repository_root: str = os.getenv("REPOSITORY_ROOT", ".")
    context_max_chars: int = int(os.getenv("CONTEXT_MAX_CHARS", "12000"))
    context_max_files: int = int(os.getenv("CONTEXT_MAX_FILES", "4"))
    context_max_file_chars: int = int(os.getenv("CONTEXT_MAX_FILE_CHARS", "4000"))
    
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
