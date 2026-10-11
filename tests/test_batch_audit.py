import asyncio
import csv
import json
from dataclasses import asdict
from types import SimpleNamespace

import httpx
import pytest

from config import FeatureFlags
from core.audit import active_audit, audit_scope, new_audit
from core.batch import parse_tasks, run_batch
from core.config import settings
from core.context_routing import ContextBuilder, JsonSessionStore, Price, Usage, calculate_cost, normalize_provider_usage, recommend_switch
from core.reporting import export_reports
from main import RelayOSOrchestrator
from models.schemas import TaskRequest
from models.schemas import ExecutionResult
from models.enums import ExecutionRoute, ExecutionState


@pytest.fixture
def orchestrator(monkeypatch):
    monkeypatch.setattr(FeatureFlags, "ENABLE_SPACY_NER", False)
    monkeypatch.setattr(FeatureFlags, "ENABLE_LOCAL_SUMMARIZATION_QWEN", False)
    monkeypatch.setattr(settings, "fireworks_api_key", "fixture")
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    monkeypatch.setattr(settings, "routing_prices_json", "")
    return RelayOSOrchestrator()


def test_multiline_and_generated_ids(tmp_path):
    path = tmp_path / "tasks.txt"
    path.write_text("---TASK---\ntask_id: a\nFirst\n\nsecond\n---TASK---\nThird", encoding="utf-8")
    tasks = parse_tasks(path)
    assert [t.task_id for t in tasks] == ["a", "task-0002"]
    assert tasks[0].prompt == "First\n\nsecond"
    assert parse_tasks(path) == tasks


@pytest.mark.parametrize("text", ["no boundary", "---TASK---", "---TASK---\ntask_id:\nprompt",
    "---TASK---\nprompt\n---TASK---", "---TASK---\ntask_id: same\na\n---TASK---\ntask_id: same\nb",
    "---TASK---\ntask_id: task-0002\na\n---TASK---\nb"])
def test_text_rejects_empty_malformed_and_duplicates(tmp_path, text):
    path = tmp_path / "bad.txt"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        parse_tasks(path)


def test_legacy_json_and_session_options(tmp_path):
    path = tmp_path / "tasks.json"
    data = [{"task_id": "old", "prompt": "Calculate 2+2"},
            {"task_id": "new", "prompt": "Fix code", "session_id": "s", "context_options": {"file_paths": ["a.py"]}}]
    path.write_text(json.dumps(data))
    assert parse_tasks(path)[1].context_options == data[1]["context_options"]


@pytest.mark.parametrize("field", ["cached_input_per_million", "cache_write_per_million"])
def test_nonzero_cache_without_price_is_partial_even_when_known_cost_zero(field):
    rates = dict(input_per_million=0, cached_input_per_million=0, cache_write_per_million=0, output_per_million=0)
    rates[field] = None
    cost = calculate_cost(Usage(10, 5, 5, 0, "provider"), Price(**rates))
    assert cost.status == "partial"
    assert cost.amount == 0
    assert cost.missing


@pytest.mark.parametrize("bad", [-1, float("inf"), float("nan"), "1", True])
def test_invalid_prices_rejected(bad):
    with pytest.raises(ValueError):
        Price(input_per_million=bad)


@pytest.mark.parametrize("bad", [-1, float("inf"), 1.2, "2", True])
def test_invalid_usage_reported(bad):
    usage = normalize_provider_usage({"usage": {"prompt_tokens": bad, "completion_tokens": 2}})
    assert usage.input_tokens is None
    assert "prompt_tokens" in usage.invalid_fields
    assert calculate_cost(usage, Price()).status != "measured"


def test_native_cache_schemas_and_unknowns():
    o = normalize_provider_usage({"usage": {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 40, "cache_write_tokens": 10}, "completion_tokens": 20}}, "openai")
    a = normalize_provider_usage({"usage": {"input_tokens": 50, "cache_read_input_tokens": 40, "cache_creation_input_tokens": 10, "output_tokens": 20}}, "anthropic")
    prices = Price(1, 0.1, 2, 4)
    assert calculate_cost(o, prices).amount == pytest.approx(calculate_cost(a, prices).amount)
    assert calculate_cost(o, prices).status == "measured"
    assert normalize_provider_usage({"choices": []}).source == "unknown"


def test_ast_rejects_partial_word_problem_and_nested_expansion():
    from engine.executors.python import MathSandbox
    assert MathSandbox.extract_equation("A recipe needs 3/4 cup for 12 cookies. How much for 30 cookies? If sugar costs $2.40 per cup, what is the cost?") == ""
    with pytest.raises(ValueError):
        MathSandbox.evaluate("(2 ** 10000) ** 10000")
    with pytest.raises(ValueError):
        MathSandbox.evaluate("other.sqrt(4)")


def test_partial_cost_does_not_recommend_switch():
    rec = recommend_switch(current_provider="a", candidate_provider="b",
        current_usage=Usage(100, 50, 0, 10, "provider"), candidate_usage=Usage(10, 5, 0, 1, "provider"),
        current_price=Price(1, None, None, 2), candidate_price=Price(1, None, None, 2))
    assert rec.recommendation == "inconclusive"


def test_cache_hash_invalidation_settings_limits_and_secrets(tmp_path):
    path = tmp_path / "source.py"
    path.write_text("x = 1")
    builder = ContextBuilder(str(tmp_path), max_files=2)
    first = builder.build("fix", file_paths=["source.py"])
    second = builder.build("fix", file_paths=["source.py"])
    assert not first.cache_events[0]["hit"] and second.cache_events[0]["hit"]
    path.write_text("x = 2")
    third = builder.build("fix", file_paths=["source.py"])
    assert not third.cache_events[0]["hit"] and "x = 2" in third.text
    assert not ContextBuilder(str(tmp_path), max_file_chars=2).build("fix", file_paths=["source.py"]).cache_events[0]["hit"]
    for name in [".env.production", "credentials.json", "secrets.py"]:
        (tmp_path / name).write_text("secret")
        assert not builder.build("fix", file_paths=[name]).files
    assert len(builder.build("fix", file_paths=["source.py", "missing.py", "third.py"]).omitted) == 2
    assert builder.build("fix", file_paths="source.py").omitted[0]["reason"] == "invalid_file_paths"
    for index in range(40):
        path.write_text(str(index))
        builder.build("fix", file_paths=["source.py"])
    assert len(builder._cache) <= builder.CACHE_ENTRIES


def test_corrupt_session_isolation_global_bound(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text("corrupt")
    store = JsonSessionStore(str(path), max_history=2, max_sessions=2)
    for index in range(3):
        store.append(str(index), {"task_id": str(index)}, summary=f"answer {index}")
    assert store.get("0") is None
    assert store.get("1")["summary"] == "answer 1"
    store.append("2", {"task_id": "again"}, summary="new")
    assert store.get("2")["summary"] == "new"
    assert len(json.loads(path.read_text())) == 2


@pytest.mark.asyncio
async def test_ast_run_reports_no_external_charge_and_csv(tmp_path, orchestrator):
    tasks = [TaskRequest(task_id="math", prompt="Calculate 125 * 48")]
    try:
        results, rows = await run_batch(tasks, orchestrator, "ast-run", "include")
        assert results[0].answer == "6000"
        assert rows[0]["route_counts"] == {"python": 1}
        assert rows[0]["api_cost"]["complete_actual_api_cost"] == 0
        assert rows[0]["api_cost"]["local_compute_cost"] is None
        summary = export_reports(tmp_path, "ast-run", results, rows)
        assert summary["external_requests_including_retries"] == 0
        assert json.loads((tmp_path / "results.json").read_text()) == [{"task_id": "math", "answer": "6000"}]
        assert list(csv.DictReader((tmp_path / "results.csv").open(encoding="utf-8-sig")))[0]["task_id"] == "math"
    finally:
        await orchestrator.shutdown()


@pytest.mark.asyncio
async def test_fireworks_truncation_and_retry_preserved(orchestrator, monkeypatch):
    calls = []
    async def transport(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": "fixture"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "Fixture answer"}, "finish_reason": "length" if len(calls) == 2 else "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 0}, "cache_write_tokens": 0}})
    await orchestrator.api_exec.client.aclose()
    orchestrator.api_exec.client = httpx.AsyncClient(transport=httpx.MockTransport(transport), base_url="https://fixture.test")
    monkeypatch.setattr(settings, "routing_prices_json", json.dumps({settings.fireworks_fast_model.join(["fireworks:", ""]): {"input_per_million": 1, "output_per_million": 2}}))
    try:
        _, rows = await run_batch([TaskRequest(task_id="retry", prompt="Explain APIs", provider="fireworks")], orchestrator, "retry-run")
        assert len(calls) == 3
        row = rows[0]
        assert row["external_requests"] == 3
        assert row["retries"] == 2
        assert row["tokens"]["output_tokens"]["known_total"] == 10
        assert row["tokens"]["output_tokens"]["complete"] is False
        assert row["api_cost"]["complete_actual_api_cost"] is None
        assert row["prompt"] is None and row["answer"] is None
    finally:
        await orchestrator.shutdown()


@pytest.mark.asyncio
async def test_timeout_cancels_dag_and_preserves_started_request(orchestrator):
    started = asyncio.Event()
    stopped = asyncio.Event()
    async def transport(request):
        started.set()
        try:
            await asyncio.sleep(60)
        finally:
            stopped.set()
    await orchestrator.api_exec.client.aclose()
    orchestrator.api_exec.client = httpx.AsyncClient(transport=httpx.MockTransport(transport), base_url="https://fixture.test")
    try:
        results, rows = await run_batch([TaskRequest(task_id="timeout", prompt="Explain APIs", provider="fireworks")], orchestrator, "timeout-run", timeout=0.1)
        assert started.is_set() and stopped.is_set()
        assert results[0].error_code == "task_timeout"
        assert rows[0]["external_requests"] == 1
        assert rows[0]["attempts"][0]["requests"][0]["outcome"] == "cancelled"
        assert rows[0]["api_cost"]["complete_actual_api_cost"] is None
    finally:
        await orchestrator.shutdown()


@pytest.mark.asyncio
async def test_context_does_not_mutate_request_shadow_does_not_route(tmp_path, monkeypatch, orchestrator):
    monkeypatch.setattr(settings, "session_store_path", str(tmp_path / "sessions.json"))
    monkeypatch.setattr(settings, "context_management_enabled", True)
    monkeypatch.setattr(settings, "context_routing_enabled", True)
    req = TaskRequest(task_id="math", prompt="Calculate 2 + 2", session_id="one")
    # Grounded context only requested for coding files; math should remain executable.
    try:
        _, rows = await run_batch([req], orchestrator, "context-run")
        assert req.prompt == "Calculate 2 + 2"
        assert rows[0]["initial_decisions"][0]["route"] == "python"
        assert rows[0]["routing_metadata"]["recommendation_applied"] is False
    finally:
        await orchestrator.shutdown()


@pytest.mark.asyncio
async def test_local_success_has_no_api_charge_and_fallback_is_visible(orchestrator):
    class Local:
        model_path = "fixture-qwen"
        async def execute(self, context):
            return ExecutionResult(task_id=context.request.task_id, answer="Paris", route_taken=ExecutionRoute.LOCAL_LLM, model=self.model_path)
        async def shutdown(self):
            pass
    orchestrator.local_exec = Local()
    try:
        _, rows = await run_batch([TaskRequest(task_id="local", prompt="Capital of France?", provider="local_llm")], orchestrator, "local")
        assert rows[0]["final_route"] == "local_llm"
        assert rows[0]["api_cost"]["complete_actual_api_cost"] == 0
        assert rows[0]["external_requests"] == 0
        async def failure(context):
            return ExecutionResult(task_id=context.request.task_id, answer="", route_taken=ExecutionRoute.LOCAL_LLM, state=ExecutionState.FAILED_RETRYABLE)
        orchestrator.local_exec.execute = failure
        await orchestrator.api_exec.client.aclose()
        orchestrator.api_exec.client = httpx.AsyncClient(base_url="https://fixture.test", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "Paris"}, "finish_reason": "stop"}]})))
        _, rows = await run_batch([TaskRequest(task_id="fallback", prompt="Capital of France?", provider="local_llm")], orchestrator, "fallback")
        assert [a["route"] for a in rows[0]["attempts"]] == ["local_llm", "fireworks"]
        assert rows[0]["fallbacks"] == 1
        assert rows[0]["api_cost"]["complete_actual_api_cost"] is None
    finally:
        await orchestrator.shutdown()


def test_api_legacy_contract_and_audit_endpoint():
    from fastapi.testclient import TestClient
    from api import create_app
    class Fake:
        async def process_task(self, request):
            return ExecutionResult(task_id=request.task_id, answer="ok")
    with TestClient(create_app(Fake())) as client:
        response = client.post("/api/tasks", json={"task_id": "legacy", "prompt": "hello"})
        assert response.status_code == 202
        assert client.get("/api/tasks/legacy").json()["task_id"] == "legacy"
        assert client.get("/api/tasks/legacy/audit").json()["task_id"] == "legacy"


@pytest.mark.asyncio
async def test_offline_coding_benchmark_isolated_and_labelled(tmp_path):
    from benchmark_coding import run
    report = await run(tmp_path)
    assert report["mode"] == "mock"
    assert [v["correct"] for v in report["variants"].values()] == [2, 1, 1]
    assert all(v["external_requests_including_retries"] == 0 for v in report["variants"].values())
    assert all(v["api_cost"]["complete_actual_api_cost"] is None for v in report["variants"].values())
    for task_id in ("stable_unique", "clamp"):
        assert len({t["starting_fixture_sha256"] for t in report["tasks"] if t["task_id"] == task_id}) == 1


@pytest.mark.asyncio
async def test_cli_unique_reports_and_explicit_overwrite_guard(tmp_path, monkeypatch, orchestrator):
    import main
    from core.batch import cli
    class Factory:
        def __call__(self):
            return RelayOSOrchestrator()
    monkeypatch.setattr(main, "RelayOSOrchestrator", Factory())
    tasks = tmp_path / "tasks.json"
    tasks.write_text('[{"task_id":"m","prompt":"Calculate 3 * 4"}]')
    target = tmp_path / "results.json"
    args = ["--input", str(tasks), "--output", str(target), "--output-dir", str(tmp_path / "runs")]
    try:
        assert await cli(args) == 0
        with pytest.raises(SystemExit) as error:
            await cli(args)
        assert error.value.code == 2
        assert await cli(args + ["--overwrite"]) == 0
        reports = list((tmp_path / "runs").glob("*/run_summary.json"))
        assert len(reports) == 2
        assert len({json.loads(p.read_text())["run_id"] for p in reports}) == 2
    finally:
        await orchestrator.shutdown()


def test_shadow_configured_estimates_stay_advisory(monkeypatch):
    from core.reporting import shadow_from_observations
    monkeypatch.setattr(settings, "routing_prices_json", json.dumps({"openai:a": {"input_per_million": 10, "output_per_million": 10}, "openai:b": {"input_per_million": 1, "output_per_million": 1}}))
    monkeypatch.setattr(settings, "routing_estimates_json", json.dumps({"candidate": {"provider": "openai", "model": "b", "usage": {"input_tokens": 100, "output_tokens": 10, "cached_input_tokens": 0, "cache_write_tokens": 0}, "quality": 0.9, "context_rebuild_cost": 0}}))
    audit = {"attempts": [{"route": "openai", "requests": [{"provider": "openai", "model": "a", "usage": asdict(Usage(100, 0, 0, 10, "provider")), "elapsed_ms": 1}]}]}
    rec = shadow_from_observations(audit, TaskRequest(task_id="s", prompt="code", required_quality=0.8))
    assert rec["recommendation"] == "switch"
    assert rec["recommendation_applied"] is False
    assert rec["current_cost"]["status"] == "estimated"
    assert rec["horizon"] == "one_next_task"
