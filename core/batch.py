"""JSON/text admission and bounded execution through the existing orchestrator."""
import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path

from core.audit import audit_scope, new_audit
from core.config import settings
from core.reporting import atomic_json, export_reports, task_report
from models.enums import ExecutionState
from models.schemas import ExecutionResult, TaskRequest


def parse_tasks(path):
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".txt":
        blocks, current = [], None
        for line in text.splitlines():
            if line == "---TASK---":
                if current is not None:
                    blocks.append(current)
                current = []
            elif current is None:
                if line.strip():
                    raise ValueError("Text before first ---TASK--- delimiter")
            else:
                current.append(line)
        if current is not None:
            blocks.append(current)
        if not blocks:
            raise ValueError("No ---TASK--- boundaries found")
        raw = []
        for index, lines in enumerate(blocks, 1):
            task_id = f"task-{index:04d}"
            if lines and lines[0].startswith("task_id:"):
                task_id = lines.pop(0)[len("task_id:"):].strip()
            raw.append({"task_id": task_id, "prompt": "\n".join(lines).strip("\n")})
    else:
        raw = json.loads(text)
    if not isinstance(raw, list):
        raise ValueError("JSON input must be the existing list of task objects")
    tasks, seen = [], set()
    for index, item in enumerate(raw, 1):
        try:
            request = TaskRequest.model_validate(item)
        except Exception:
            raise ValueError(f"Invalid task object at position {index}") from None
        if not request.task_id.strip() or not request.prompt.strip():
            raise ValueError(f"Empty task ID or prompt at position {index}")
        if len(request.prompt) > settings.max_prompt_chars:
            raise ValueError(f"Prompt at position {index} exceeds MAX_PROMPT_CHARS")
        if request.task_id in seen:
            raise ValueError(f"Duplicate task ID at position {index}: {request.task_id}")
        seen.add(request.task_id)
        tasks.append(request)
    return tasks


async def run_batch(tasks, orchestrator, run_id, privacy="omit", timeout=None, inference_mode="live"):
    from config import FeatureFlags
    semaphore = asyncio.Semaphore(max(1, FeatureFlags.FIREWORKS_CONCURRENCY))
    # Preserve the existing experimental queue allowance; API has its own deadline.
    deadline = timeout if timeout is not None else (70.0 if FeatureFlags.ENABLE_LONG_LOCAL_QUEUE else 28.0)
    batch_started = time.perf_counter()
    audits = [new_audit(request.task_id, run_id) for request in tasks]

    async def run_one(request, audit):
        queued = time.perf_counter()
        audit["inference_mode"] = inference_mode
        async with semaphore:
            audit["queue_wait_ms"] = (time.perf_counter() - queued) * 1000
            started = time.perf_counter()
            with audit_scope(audit):
                try:
                    remaining = settings.batch_timeout_seconds - (time.perf_counter() - batch_started)
                    if remaining <= 0:
                        raise asyncio.TimeoutError()
                    result = await asyncio.wait_for(orchestrator.process_task(request), min(deadline, remaining))
                except Exception as exc:
                    code = "task_timeout" if isinstance(exc, asyncio.TimeoutError) else "orchestrator_error"
                    result = ExecutionResult(task_id=request.task_id, answer="", state=ExecutionState.FAILED_FINAL,
                                             error_code=code, error_message="Task deadline exceeded" if code == "task_timeout" else "Task execution failed")
                audit["latency_ms"] = (time.perf_counter() - started) * 1000
                return result, task_report(request, result, audit, privacy)

    pairs = await asyncio.gather(*(run_one(task, audit) for task, audit in zip(tasks, audits)), return_exceptions=True)
    results, rows = [], []
    for request, pair, audit in zip(tasks, pairs, audits):
        if isinstance(pair, BaseException):
            result = ExecutionResult(task_id=request.task_id, answer="", state=ExecutionState.FAILED_FINAL, error_code="reporting_error")
            pair = result, task_report(request, result, audit, privacy)
        results.append(pair[0])
        rows.append(pair[1])
    return results, rows


async def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    default_input = "/input/tasks.json" if Path("/input").is_dir() else "input/tasks.json"
    default_output = "/output/results.json" if Path("/input").is_dir() else "output/results.json"
    parser.add_argument("--input", default=default_input)
    parser.add_argument("--output", help="Explicit grader results file (refuses overwrite unless --overwrite)")
    parser.add_argument("--output-dir", help="Root for unique per-run report directories", default=str(Path(default_output).parent / "runs"))
    parser.add_argument("--privacy", choices=["omit", "include"], default=settings.report_privacy)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    run_id = uuid.uuid4().hex
    directory = Path(args.output_dir) / run_id
    target = Path(args.output or default_output)
    if args.output and target.exists() and not args.overwrite:
        parser.error("Output already exists; choose another path or pass --overwrite")
    try:
        tasks = parse_tasks(args.input)
    except (OSError, ValueError) as exc:
        export_reports(directory, run_id, [], [], [str(exc)])
        print(f"Input rejected: {exc}. Report: {directory}")
        return 2
    from main import RelayOSOrchestrator
    orchestrator = RelayOSOrchestrator()
    try:
        results, rows = await run_batch(tasks, orchestrator, run_id, args.privacy)
        summary = export_reports(directory, run_id, results, rows)
        # Preserve legacy grader path; keep a backup before refreshing the default.
        if target.exists() and not args.output:
            (directory / "previous_results.json").write_bytes(target.read_bytes())
        atomic_json(target, [{"task_id": r.task_id, "answer": r.answer} for r in results])
        from core.telemetry import TelemetryService
        TelemetryService.get_instance().dump_report(str(directory / "telemetry.json"))
        print(f"Run {run_id}: {summary['succeeded']}/{len(tasks)} succeeded; "
              f"{summary['external_requests_including_retries']} external requests; "
              f"API cost {summary['api_cost']['status']}. Reports: {directory}")
        return 0
    finally:
        await orchestrator.shutdown()
