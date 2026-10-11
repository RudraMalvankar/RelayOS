from enum import Enum

class TaskCategory(str, Enum):
    FACTUAL = "factual"
    MATH = "math"
    SENTIMENT = "sentiment"
    SUMMARIZATION = "summarization"
    NER = "ner"
    DEBUGGING = "debugging"
    LOGIC = "logic"
    CODE_GEN = "code_gen"
    ARCHITECTURE = "architecture"

class ComplexityLevel(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"

class ExecutionRoute(str, Enum):
    PYTHON = "python"
    LOCAL_LLM = "local_llm"
    FIREWORKS = "fireworks"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"

class ExecutionState(str, Enum):
    SUCCESS = "success"
    FALLBACK_SUCCESS = "fallback_success"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_FINAL = "failed_final"
