"""Reports derive API totals only from transport records, never final routes."""
import csv
import hashlib
import json
import os
import statistics
import tempfile
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path

from core.config import settings
from core.context_routing import Price, Usage, calculate_cost, estimate_tokens, load_prices, recommend_switch

TOKEN_FIELDS = ("input_tokens", "output_tokens", "cached_input_tokens", "cache_write_tokens")


def requests(audit):
    return [r for a in audit.get("attempts", []) for r in a["requests"]]


def token_totals(records):
    totals = {}
    for key in TOKEN_FIELDS:
        values = [r["usage"].get(key) if r["usage"]["source"] == "provider" else None for r in records]
        known = [v for v in values if v is not None]
        totals[key] = {"known_total": sum(known) if known else None,
                       "reported_requests": len(known), "total_requests": len(values),
                       "complete": len(known) == len(values),
                       "applicable": bool(values)}
    totals["input_semantics"] = "provider_reported; Anthropic excludes cache reads/writes; others include reads (see per-request flags)"
    return totals


def priced_requests(audit):
    prices = load_prices(settings.routing_prices_json)
    for request in requests(audit):
        if "cost" in request:
            continue  # Preserve the rates/version used when the task finished.
        key = f"{request['provider']}:{request['model']}"
        price = prices.get(key, Price())  # Never share rates across models implicitly.
        cost = calculate_cost(Usage(**request["usage"]), price, settings.routing_currency)
        request["cost"] = {**asdict(cost), "price_key": key, "price_source": price.source,
                           "price_version": price.version, "units": "currency_per_million_tokens"}


def cost_totals(records):
    complete = all(r["cost"]["status"] == "measured" for r in records)
    measured = [r["cost"]["amount"] for r in records if r["cost"]["status"] == "measured"]
    partial = [r["cost"]["amount"] for r in records if r["cost"]["status"] == "partial" and r["usage"]["source"] == "provider"]
    estimated = [r["cost"]["amount"] for r in records if r["usage"]["source"] == "estimated" and r["cost"]["amount"] is not None]
    return {"complete_actual_api_cost": sum(measured) if complete else None,
            "known_complete_request_cost": sum(measured) if measured else None,
            "partial_api_cost": sum(partial) if partial else None,
            "estimated_api_cost": sum(estimated) if estimated else None,
            "status": "measured" if complete else "partial" if measured or partial else "estimated" if estimated else "unknown",
            "currency": settings.routing_currency, "complete_requests": len(measured),
            "total_requests": len(records), "coverage_complete": complete,
            "local_compute_cost": None, "local_compute_cost_status": "not_measured"}


def task_report(request, result, audit, privacy="omit"):
    priced_requests(audit)
    remote = requests(audit)
    attempts = audit["attempts"]
    counts = Counter(a["route"] for a in attempts)
    missing = sorted({m for r in remote for m in r["cost"]["missing"]})
    return {**audit, "prompt": request.prompt if privacy == "include" else None,
            "prompt_sha256": hashlib.sha256(request.prompt.encode()).hexdigest(),
            "privacy": privacy, "answer": result.answer if privacy == "include" else None,
            "error": result.error_code, "state": result.state.value,
            "final_route": audit.get("final_route") if result.answer else None,
            "validation_passed": result.validation_passed,
            "route_counts": dict(counts), "external_requests": sum(r["external_request_made"] for r in remote),
            "provider_request_attempts": len(remote),
            "providers_models": sorted({f"{r['provider']}:{r['model']}" for r in remote}),
            "tokens": token_totals(remote),
            "estimated_tokens": {"input_tokens": estimate_tokens(request.prompt), "source": "estimated", "scope": "original_prompt_only"},
            "usage_provenance": sorted({r["usage"]["source"] for r in remote}),
            "api_cost": cost_totals(remote), "missing_measurements": missing,
            "retries": sum(max(0, len(a["requests"]) - 1) for a in attempts),
            "fallbacks": sum(a["fallback"] for a in attempts),
            "routing_metadata": result.routing_metadata}


def summarize(rows):
    remote = [r for row in rows for r in requests(row)]
    attempts = [a for row in rows for a in row["attempts"]]
    successes = {"success", "fallback_success"}
    latencies = [row["latency_ms"] for row in rows if row.get("latency_ms") is not None]
    routes = {}
    for route in {a["route"] for a in attempts}:
        selected = [a for a in attempts if a["route"] == route]
        passed = sum(a["outcome"] in successes and a.get("validation_passed") is not False for a in selected)
        routes[route] = {"attempts": len(selected), "successes": passed, "failures": len(selected) - passed}
    validation_count = sum(row["validation_passed"] is not None for row in rows)
    return {"total_tasks": len(rows), "succeeded": sum(r["state"] in successes for r in rows),
            "failed": sum(r["state"] not in successes for r in rows),
            "executor_routes": routes,
            "provider_model_requests": dict(Counter(f"{r['provider']}:{r['model']}" for r in remote)),
            "external_requests_including_retries": sum(r["external_request_made"] for r in remote),
            "provider_request_attempts": len(remote),
            "retries": sum(r["retries"] for r in rows), "fallbacks": sum(r["fallbacks"] for r in rows),
            "tokens": token_totals(remote),
            "local_tokens": {key: {
                "known_total": sum(a.get("usage", {}).get(key) or 0 for a in attempts if a["route"] == "local_llm" and a.get("usage", {}).get("usage_source") == "relayos") if any(a["route"] == "local_llm" and a.get("usage", {}).get("usage_source") == "relayos" and a.get("usage", {}).get(key) is not None for a in attempts) else None,
                "measured_attempts": sum(a["route"] == "local_llm" and a.get("usage", {}).get("usage_source") == "relayos" and a.get("usage", {}).get(key) is not None for a in attempts),
                "total_attempts": sum(a["route"] == "local_llm" for a in attempts),
                "source": "relayos"} for key in ("input_tokens", "output_tokens")},
            "estimated_input_tokens": sum(r["estimated_tokens"]["input_tokens"] for r in rows),
            "estimated_token_scope": "original_prompts_only", "api_cost": cost_totals(remote),
            "tasks_missing_usage_or_rates": sum(bool(r["missing_measurements"]) for r in rows),
            "latency_ms": {"sample_count": len(latencies), "mean": statistics.mean(latencies) if latencies else None,
                           "median": statistics.median(latencies) if latencies else None,
                           "max": max(latencies) if latencies else None},
            "validation": {"sample_count": validation_count,
                           "pass_rate": sum(r["validation_passed"] is True for r in rows) / validation_count if validation_count else None},
            "final_route_distribution": dict(Counter(r["final_route"] or "none" for r in rows)),
            "shadow_recommendations": dict(Counter((r.get("routing_metadata") or {}).get("recommendation", "disabled") for r in rows))}


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, allow_nan=False)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def export_reports(directory, run_id, results, rows, input_errors=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    summary = {"run_id": run_id, **summarize(rows), "input_errors": input_errors or []}
    atomic_json(directory / "results.json", [{"task_id": r.task_id, "answer": r.answer} for r in results])
    atomic_json(directory / "run_audit.json", {"schema_version": 1, "run_id": run_id, "tasks": rows, "input_errors": input_errors or []})
    atomic_json(directory / "run_summary.json", summary)
    columns = ["task_id", "prompt", "answer", "state", "final_route", "validation_passed", "latency_ms", "queue_wait_ms",
               "external_requests", "providers_models", "route_counts", "retries", "fallbacks", "tokens", "estimated_tokens",
               "usage_provenance", "api_cost", "missing_measurements", "routing_metadata", "error"]
    with (directory / "results.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            cells = {}
            for key in columns:
                value = row.get(key)
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)
                if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                    value = "'" + value
                cells[key] = value
            writer.writerow(cells)
    return summary


def shadow_from_observations(audit, request):
    remote = requests(audit or {})
    base = {"mode": "shadow", "recommendation_applied": False, "recommendation": "inconclusive",
            "reason": "insufficient_pricing_or_usage_data", "horizon": "one_next_task",
            "actual_routes": [a["route"] for a in (audit or {}).get("attempts", [])]}
    if not remote:
        return {**base, "reason": "no_observed_provider_usage"}
    last = remote[-1]
    base["observed_provider_model"] = f"{last['provider']}:{last['model']}"
    try:
        config = json.loads(settings.routing_estimates_json or "{}")
        candidate = config["candidate"]
        prices = load_prices(settings.routing_prices_json)
        current_key = base["observed_provider_model"]
        candidate_key = f"{candidate['provider']}:{candidate['model']}"
        current_usage = replace(Usage(**last["usage"]), source="estimated")
        candidate_usage = Usage(**{**candidate["usage"], "source": "estimated"})
        # Extra retrieval/rebuild work must be an explicit monetary assumption, not a score.
        overhead = float(candidate.get("context_rebuild_cost", 0))
        if overhead < 0 or not __import__("math").isfinite(overhead):
            raise ValueError("invalid overhead")
        recommendation = recommend_switch(
            current_provider=current_key, candidate_provider=candidate_key,
            current_usage=current_usage, candidate_usage=candidate_usage,
            current_price=prices.get(current_key, Price()), candidate_price=prices.get(candidate_key, Price()),
            required_quality=request.required_quality or 0, candidate_quality=float(candidate["quality"]),
            safety_margin=settings.routing_safety_margin,
            current_retry_probability=float(config.get("current_retry_probability", 0)),
            candidate_retry_probability=float(candidate.get("retry_probability", 0)),
            current_latency_ms=last["elapsed_ms"], candidate_latency_ms=candidate.get("latency_ms"),
            max_latency_ms=config.get("max_latency_ms"), currency=settings.routing_currency,
            candidate_rebuild_cost=overhead)
        return {**base, **asdict(recommendation), "current_model": last["model"], "candidate_model": candidate["model"],
                "assumptions": {"current_usage": "last_request_as_next_task_estimate_not_guaranteed", "candidate": candidate,
                                "current_latency_ms": last["elapsed_ms"], "quality_evidence": candidate.get("quality_evidence", "operator_estimate")}}
    except (KeyError, TypeError, ValueError):
        return base
