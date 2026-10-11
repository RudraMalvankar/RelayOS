from models.enums import ExecutionRoute, ExecutionState
from models.schemas import ExecutionResult


def test_success_result_has_explicit_state():
    result = ExecutionResult(
        task_id="task-1",
        answer="42",
        route_taken=ExecutionRoute.PYTHON,
    )

    assert result.state == ExecutionState.SUCCESS
    assert result.error_code is None


def test_failure_result_does_not_use_error_text_as_answer():
    result = ExecutionResult(
        task_id="task-2",
        answer="",
        state=ExecutionState.FAILED_RETRYABLE,
        error_code="provider_error",
        error_message="provider unavailable",
    )

    assert result.answer == ""
    assert result.state == ExecutionState.FAILED_RETRYABLE
    assert result.error_code == "provider_error"
