from pathlib import Path
import pytest

from core.context_routing import ContextBuilder, JsonSessionStore, Price, Usage, calculate_cost, load_prices, normalize_provider_usage, recommend_switch


def test_unknown_usage_is_not_fabricated_as_zero():
    result = calculate_cost(Usage(input_tokens=None, output_tokens=None), Price())
    assert result.amount is None
    assert result.status == "unknown"
    assert "input_tokens" in result.missing


def test_provider_usage_normalization_preserves_missing_fields():
    usage = normalize_provider_usage({"usage": {"prompt_tokens": 12, "completion_tokens": 4}})
    assert usage.input_tokens == 12
    assert usage.output_tokens == 4
    assert usage.cached_input_tokens is None
    assert usage.source == "provider"


def test_operator_price_configuration_is_explicit_and_model_scoped():
    prices = load_prices('{"fireworks:model-a":{"input_per_million":1,"output_per_million":2}}')
    assert prices["fireworks:model-a"].source == "operator"
    assert prices.get("fireworks:model-b") is None
    assert load_prices("not-json") == {}


def test_cached_tokens_are_charged_separately_without_double_counting():
    result = calculate_cost(
        Usage(input_tokens=1000, cached_input_tokens=400, output_tokens=100),
        Price(input_per_million=10, cached_input_per_million=1, output_per_million=20, source="operator"),
    )
    assert result.amount == 0.006 + 0.0004 + 0.002
    assert result.status == "partial"


def test_invalid_cached_tokens_do_not_inflate_cost():
    result = calculate_cost(
        Usage(input_tokens=100, cached_input_tokens=1000, output_tokens=10),
        Price(input_per_million=10, cached_input_per_million=1, output_per_million=20, source="operator"),
    )
    assert result.amount == pytest.approx(0.0012)
    assert "invalid_cached_input_tokens" in result.missing


def test_quality_gate_and_unknown_comparison_are_explicit():
    recommendation = recommend_switch(
        current_provider="fireworks", candidate_provider="local_llm",
        current_usage=Usage(1000, 0, 0, 100, "provider"),
        candidate_usage=Usage(100, 0, 0, 50, "estimated"),
        current_price=Price(10, 1, 1, 20, "operator"),
        candidate_price=Price(), required_quality=0.9, candidate_quality=0.8,
    )
    assert recommendation.recommendation == "stay"
    assert recommendation.reason == "candidate_quality_below_requirement"


def test_safety_margin_can_reject_small_saving():
    recommendation = recommend_switch(
        current_provider="a", candidate_provider="b",
        current_usage=Usage(1000, 0, 0, 100, "provider"),
        candidate_usage=Usage(900, 0, 0, 100, "provider"),
        current_price=Price(10, 1, 1, 20, "operator"),
        candidate_price=Price(10, 1, 1, 20, "operator"),
        safety_margin=0.5,
    )
    assert recommendation.recommendation == "stay"


def test_retry_overhead_and_latency_limit_affect_recommendation():
    kwargs = dict(
        current_provider="a", candidate_provider="b",
        current_usage=Usage(1000, 0, 0, 100, "provider"),
        candidate_usage=Usage(500, 0, 0, 50, "provider"),
        current_price=Price(10, 1, 1, 20, "operator"),
        candidate_price=Price(10, 1, 1, 20, "operator"),
    )
    retry = recommend_switch(**kwargs, candidate_retry_probability=2.0)
    assert retry.recommendation == "stay"
    latency = recommend_switch(**kwargs, candidate_latency_ms=1001, max_latency_ms=1000)
    assert latency.reason == "candidate_latency_above_hard_limit"


def test_session_store_is_bounded_and_recovers_missing_state(tmp_path: Path):
    store = JsonSessionStore(str(tmp_path / "sessions.json"), max_history=2, max_summary_chars=5)
    assert store.get("missing") is None
    for i in range(3):
        store.append("s", {"task_id": str(i), "provider": "local", "outcome": "success"}, "abcdef")
    record = store.get("s")
    assert [item["task_id"] for item in record["history"]] == ["1", "2"]
    assert record["summary"] == "abcde"


def test_context_builder_rejects_traversal_and_excludes_secrets(tmp_path: Path):
    (tmp_path / "safe.py").write_text("print('ok')", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=hidden", encoding="utf-8")
    package = ContextBuilder(str(tmp_path), max_chars=1000, max_files=3).build(
        "do task", file_paths=["safe.py", ".env", "../outside.py"]
    )
    assert package.files == ("safe.py",)
    assert "SECRET" not in package.text
    assert {item["reason"] for item in package.omitted} == {"excluded_path", "outside_repository_root"}
