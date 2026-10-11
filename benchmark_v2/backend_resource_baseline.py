"""Small reproducible backend baseline; no external provider calls or model download."""

import json
import os
import platform
import subprocess
import sys
import time
from typing import Optional
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.executors.python import MathSandbox


def process_memory_mb() -> Optional[float]:
    try:
        import psutil
        return round(psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024, 2)
    except ImportError:
        return None


def main() -> None:
    before = process_memory_mb()
    started = time.perf_counter()
    values = [MathSandbox.evaluate(f"{index} * 3 + 1") for index in range(1000)]
    elapsed_ms = (time.perf_counter() - started) * 1000
    result = {
        "python": sys.version,
        "platform": platform.platform(),
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "memory_before_mb": before,
        "memory_after_mb": process_memory_mb(),
        "math_operations": len(values),
        "math_elapsed_ms": round(elapsed_ms, 2),
        "local_model_loaded": False,
        "note": "Local model measurements require an explicitly configured GGUF and are not performed by this baseline.",
    }
    output = Path("output")
    output.mkdir(exist_ok=True)
    (output / "backend_resource_baseline.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
