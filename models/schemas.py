from pydantic import BaseModel
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
    task_types: List[Tuple[str, float]] = []
    classification_method: Any = None

class TaskRequest(BaseModel):
    task_id: str
    prompt: str

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
