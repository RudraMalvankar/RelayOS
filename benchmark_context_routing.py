"""Reproducible offline benchmark for context-routing economics.

This harness intentionally performs no model or provider inference. It compares
fixture usage under three route labels and marks correctness/usage as unknown
unless the fixture declares them.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from core.context_routing import Price, Usage, calculate_cost, recommend_switch


DEFAULT_TASKS = [
    {"task_id": "fixture-1", "prompt": "Implement input validation", "quality": 0.8, "cheap_quality": 0.82,
     "strong_usage": {"input_tokens": 300, "output_tokens": 120}, "cheap_usage": {"input_tokens": 220, "output_tokens": 90}},
    {"task_id": "fixture-2", "prompt": "Fix deterministic parsing bug", "quality": 0.9, "cheap_quality": 0.75,
     "strong_usage": {"input_tokens": 450, "output_tokens": 180}, "cheap_usage": {"input_tokens": 280, "output_tokens": 110}},
]


def _usage(raw: dict[str, Any] | None, source: str = "fixture") -> Usage:
    raw = raw or {}
    return Usage(raw.get("input_tokens"), raw.get("cached_input_tokens"), raw.get("cache_write_tokens"), raw.get("output_tokens"), source)


def run(tasks: list[dict[str, Any]], output_path: str) -> dict[str, Any]:
    rates = Price(1.0, 0.25, 1.25, 2.0, "fixture")
    rows = []
    for task in tasks:
        strong = _usage(task.get("strong_usage"))
        cheap = _usage(task.get("cheap_usage"))
        relay = recommend_switch(
            current_provider="strong-reference", candidate_provider="cheap-reference",
            current_usage=strong, candidate_usage=cheap,
            current_price=rates, candidate_price=rates,
            required_quality=float(task.get("quality", 0.0)),
            candidate_quality=float(task.get("cheap_quality", 1.0)),
            safety_margin=0.10,
        )
        rows.append({
            "task_id": task["task_id"],
            "strong_model_only": {"cost": calculate_cost(strong, rates).__dict__},
            "cheap_model_only": {"cost": calculate_cost(cheap, rates).__dict__},
            "relayos": {"recommendation": relay.recommendation, "reason": relay.reason, "recommendation_applied": False},
            "correctness": task.get("correctness", "unknown"),
        })
    report = {
        "benchmark": "context-efficient-routing-offline",
        "mode": "fixture-only; no provider inference",
        "task_count": len(tasks),
        "pricing_source": "fixture; not production pricing",
        "results": rows,
    }
    Path(output_path).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", help="JSON task fixture", default=None)
    parser.add_argument("--output", default="output/context_routing_benchmark.json")
    args = parser.parse_args()
    tasks = DEFAULT_TASKS if not args.tasks else json.loads(Path(args.tasks).read_text(encoding="utf-8"))
    report = run(tasks, args.output)
    print(json.dumps({"task_count": report["task_count"], "output": args.output}, indent=2))


if __name__ == "__main__":
    main()
