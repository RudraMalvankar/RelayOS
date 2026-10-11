"""Fixed coding acceptance benchmark. Default uses authored mock responses, not models.

Live mode requires explicit model names and Docker for isolated acceptance tests.
Every variant uses the normal orchestrator and a fresh copy of each fixture.
"""
import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import uuid
from pathlib import Path

import httpx

from core.batch import run_batch
from core.config import settings
from core.reporting import atomic_json, export_reports
from models.schemas import TaskRequest

FIXTURES = [
    {"id": "stable_unique", "instruction": "Implement stable_unique(values), preserving first occurrence order; values are hashable.",
     "source": "def stable_unique(values):\n    raise NotImplementedError\n",
     "test": "from solution import stable_unique\nassert stable_unique([3, 1, 3, 2, 1]) == [3, 1, 2]\nassert stable_unique([]) == []\n",
     "strong": "def stable_unique(values):\n    return list(dict.fromkeys(values))\n",
     "cheap": "def stable_unique(values):\n    return sorted(set(values))\n"},
    {"id": "clamp", "instruction": "Implement clamp(value, low, high), inclusive; raise ValueError when low exceeds high.",
     "source": "def clamp(value, low, high):\n    raise NotImplementedError\n",
     "test": "from solution import clamp\nassert clamp(8, 0, 5) == 5\nassert clamp(-1, 0, 5) == 0\nassert clamp(3, 0, 5) == 3\ntry:\n    clamp(3, 5, 1)\nexcept ValueError:\n    pass\nelse:\n    raise AssertionError('invalid range accepted')\n",
     "strong": "def clamp(value, low, high):\n    if low > high:\n        raise ValueError('invalid range')\n    return min(high, max(low, value))\n",
     "cheap": "def clamp(value, low, high):\n    if low > high:\n        raise ValueError('invalid range')\n    return min(high, max(low, value))\n"},
]


def acceptance(workspace, mode):
    if mode == "mock":
        # Only repository-authored fixtures are executed on the host.
        command = [sys.executable, "-I", "-c", "import sys; sys.path.insert(0, '.'); exec(open('check.py').read())"]
    else:
        command = ["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
                   "--security-opt", "no-new-privileges", "--memory", "128m", "--cpus", "1", "--pids-limit", "32",
                   "-v", f"{workspace.resolve()}:/work:ro", "-w", "/work", "python:3.11-slim",
                   "python", "-I", "-c", "import sys; sys.path.insert(0, '/work'); exec(open('/work/check.py').read())"]
    try:
        result = subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=20)
        return {"passed": result.returncode == 0, "exit_code": result.returncode,
                "error": result.stderr[-2000:] if result.returncode else None}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"passed": False, "status": "blocked", "error": type(exc).__name__}


async def run(output_root, mode="mock", strong_model=None, cheap_model=None):
    from main import RelayOSOrchestrator
    from config import FeatureFlags
    run_id = uuid.uuid4().hex
    root = Path(output_root) / run_id
    root.mkdir(parents=True)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    fixture_hash = hashlib.sha256(json.dumps(FIXTURES, sort_keys=True).encode()).hexdigest()
    strong_model = strong_model or "fixture-strong"
    cheap_model = cheap_model or "fixture-cheap"
    saved = {name: getattr(settings, name) for name in ("fireworks_api_key", "allowed_models", "repository_root", "session_store_path", "context_management_enabled")}
    saved_spacy = FeatureFlags.ENABLE_SPACY_NER
    rows, variants = [], {}
    try:
        settings.context_management_enabled = True
        if mode == "mock":
            settings.fireworks_api_key = "fixture-not-a-credential"
            settings.allowed_models = f"{cheap_model},{strong_model}"
            FeatureFlags.ENABLE_SPACY_NER = False
        for variant in ("strong-model-only", "cheap-model-only", "relayos"):
            variant_rows, variant_results = [], []
            for fixture in FIXTURES:
                workspace = root / variant / fixture["id"]
                workspace.mkdir(parents=True)
                (workspace / "solution.py").write_text(fixture["source"], encoding="utf-8")
                (workspace / "check.py").write_text(fixture["test"], encoding="utf-8")
                baseline = hashlib.sha256((fixture["source"] + fixture["test"]).encode()).hexdigest()
                settings.repository_root = str(workspace.resolve())
                settings.session_store_path = str(workspace / "session.json")
                orchestrator = RelayOSOrchestrator()
                try:
                    if mode == "mock":
                        async def respond(request):
                            model = json.loads(request.content)["model"]
                            answer = fixture["strong"] if model == strong_model else fixture["cheap"]
                            # Omit usage: mock text is not measured inference.
                            return httpx.Response(200, json={"choices": [{"message": {"content": answer}, "finish_reason": "stop"}]})
                        await orchestrator.api_exec.client.aclose()
                        orchestrator.api_exec.client = httpx.AsyncClient(transport=httpx.MockTransport(respond), base_url="https://fixture.invalid")
                    request = TaskRequest(task_id=fixture["id"], prompt="Write Python code only. " + fixture["instruction"],
                        provider="fireworks" if variant != "relayos" else None,
                        model=(strong_model if variant == "strong-model-only" else cheap_model) if variant != "relayos" else None,
                        session_id=f"{variant}-{fixture['id']}", context_options={"file_paths": ["solution.py", "check.py"]})
                    results, reports = await run_batch([request], orchestrator, run_id, "include", inference_mode=mode)
                    (workspace / "solution.py").write_text(results[0].answer, encoding="utf-8")
                    check = await asyncio.to_thread(acceptance, workspace, mode)
                    reports[0].update(variant=variant, acceptance=check, starting_fixture_sha256=baseline,
                                      starting_revision=revision, inference_mode=mode)
                    variant_rows.extend(reports)
                    variant_results.extend(results)
                finally:
                    await orchestrator.shutdown()
            summary = export_reports(root / variant, run_id, variant_results, variant_rows)
            correct = sum(row["acceptance"]["passed"] for row in variant_rows)
            total_cost = summary["api_cost"]["complete_actual_api_cost"]
            summary.update(correct=correct, total=len(FIXTURES),
                           cost_per_task=total_cost / len(FIXTURES) if total_cost is not None else None,
                           cost_per_success=total_cost / correct if total_cost is not None and correct else None)
            variants[variant] = summary
            rows.extend(variant_rows)
        report = {"mode": mode, "inference": "authored mock HTTP responses; no model inference" if mode == "mock" else "live configured providers/local inference",
                  "run_id": run_id, "starting_revision": revision, "fixture_sha256": fixture_hash,
                  "variants": variants, "tasks": rows,
                  "limitations": ["Two fixed tasks; no statistical confidence or demonstrated savings", "Mock timing measures harness overhead only"] if mode == "mock" else ["Two fixed tasks; no statistical confidence"]}
        atomic_json(root / "benchmark.json", report)
        print(json.dumps({"output": str(root), "mode": mode, "correct": {k: v["correct"] for k, v in variants.items()}}))
        return report
    finally:
        for name, value in saved.items():
            setattr(settings, name, value)
        FeatureFlags.ENABLE_SPACY_NER = saved_spacy


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", default="output/coding-benchmark")
    parser.add_argument("--mode", choices=["mock", "live"], default="mock")
    parser.add_argument("--strong-model")
    parser.add_argument("--cheap-model")
    args = parser.parse_args()
    if args.mode == "live" and not (args.strong_model and args.cheap_model):
        parser.error("Live mode requires --strong-model and --cheap-model")
    asyncio.run(run(args.output_root, args.mode, args.strong_model, args.cheap_model))
