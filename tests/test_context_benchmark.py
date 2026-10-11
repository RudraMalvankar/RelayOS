import json

from benchmark_context_routing import run


def test_offline_benchmark_marks_fixture_mode_and_writes_report(tmp_path):
    output = tmp_path / "benchmark.json"
    report = run([{
        "task_id": "t1", "quality": 0.5, "cheap_quality": 0.8,
        "strong_usage": {"input_tokens": 100, "output_tokens": 20},
        "cheap_usage": {"input_tokens": 50, "output_tokens": 10},
    }], str(output))
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert report["mode"].startswith("fixture-only")
    assert saved["results"][0]["relayos"]["recommendation_applied"] is False
    assert saved["results"][0]["correctness"] == "unknown"
