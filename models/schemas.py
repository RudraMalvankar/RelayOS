from pydantic import BaseModel, Field, model_validator
from typing import Optional, Any, List, Tuple
from .enums import TaskCategory, ComplexityLevel, ExecutionRoute, ExecutionState

class PromptProfile(BaseModel):
    word_count: int
    language: str = "en"
    complexity_score: float = 0.0
    reasoning_depth: int = 1
    requires_deterministic: bool = False
    has_code: bool = False
    has_math: bool = False
    has_ner: bool = False
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 50
    primary_category: Optional[TaskCategory] = None
    task_types: List[Tuple[str, float]] = Field(default_factory=list)
    classification_method: Any = None

class TaskRequest(BaseModel):
    task_id: str
    prompt: str
    execution_mode: str = "auto"
    provider: Optional[str] = None
    model: Optional[str] = None

class TaskContext(BaseModel):
    request: TaskRequest
    profile: Optional[Any] = None
    category: Optional[TaskCategory] = None
    complexity: Optional[ComplexityLevel] = None
    route: Optional[ExecutionRoute] = None
    failed_attempts: int = 0

class ExecutionResult(BaseModel):
    task_id: str
    answer: str
    route_taken: Optional[ExecutionRoute] = None
    model: Optional[str] = None
    state: ExecutionState = ExecutionState.SUCCESS
    fireworks_tokens: int = 0
    fallback_triggered: bool = False
    confidence: Optional[float] = None
    validation_passed: Optional[bool] = None
    retry_count: int = 0
    latency_ms: Optional[float] = None
    queue_wait_ms: Optional[float] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    fallback_reason: Optional[str] = None

    @model_validator(mode="after")
    def reject_empty_success(self):
        if not self.answer or not self.answer.strip():
            if self.state == ExecutionState.SUCCESS:
                self.state = ExecutionState.FAILED_RETRYABLE
            self.error_code = self.error_code or "empty_response"
        return self
