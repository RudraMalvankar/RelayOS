"""Manual token benchmark script; run explicitly with ``python test_tokens.py``."""

import json
import re
import subprocess
import time


def main() -> None:
    file_path = "engine/executors/local_llm.py"
    with open(file_path, "r", encoding="utf-8") as source:
        original = source.read()
    try:
        for max_tokens in [48, 64, 80, 96]:
            print(f"\n=== Testing max_tokens={max_tokens} ===")
            updated = re.sub(
                r"max_tokens = min\(max_tokens, \d+\)",
                f"max_tokens = min(max_tokens, {max_tokens})",
                original,
            )
            with open(file_path, "w", encoding="utf-8") as target:
                target.write(updated)
            started = time.time()
            subprocess.run(["python", "run_experiments.py"], check=False)
            print(f"Total Execution Time: {time.time() - started:.2f}s")
            try:
                with open("output/telemetry.json", "r", encoding="utf-8") as report:
                    telemetry = json.load(report)
                for trace in telemetry.get("traces", []):
                    if trace.get("task_id") in ["T01", "T01b", "T01c"]:
                        print(f"{trace['task_id']} route={trace['route']} latency_ms={trace['latency_ms']}")
            except (OSError, json.JSONDecodeError) as exc:
                print(f"Could not read telemetry: {exc}")
    finally:
        with open(file_path, "w", encoding="utf-8") as target:
            target.write(original)


if __name__ == "__main__":
    main()
