import json
import time
import asyncio
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict
from core.logger import setup_logger
from core.config import settings

logger = setup_logger("telemetry")

@dataclass
class TaskTrace:
    task_id: str
    category: str
    latency_ms: float
    route: str
    fallback_triggered: bool
    tokens: int
    state: str = "unknown"
    provider: str = "unknown"
    model: str = "unknown"
    retry_count: int = 0
    validation_passed: Optional[bool] = None
    confidence: Optional[float] = None
    queue_wait_ms: Optional[float] = None
    error_code: Optional[str] = None
    routing_metadata: Optional[dict] = None
    input_tokens: Optional[int] = None
    cached_input_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    usage_source: str = "unknown"

class TelemetryService:
    _instance = None
    _lock = asyncio.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TelemetryService, cls).__new__(cls)
            cls._instance.traces = []
            cls._instance.counters = {
                "total_tasks": 0,
                "api_fallbacks": 0,
                "total_tokens": 0,
                "routes": {}
            }
        return cls._instance

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls()
        return cls._instance

    async def record(self, trace: TaskTrace):
        async with self._lock:
            self.traces.append(trace)
            max_traces = max(1, settings.max_task_records)
            if len(self.traces) > max_traces:
                del self.traces[:-max_traces]
            self.counters["total_tasks"] += 1
            self.counters["total_tokens"] += trace.tokens
            
            if trace.fallback_triggered:
                self.counters["api_fallbacks"] += 1
                
            route = trace.route
            if route not in self.counters["routes"]:
                self.counters["routes"][route] = 0
            self.counters["routes"][route] += 1
            if trace.routing_metadata:
                self.counters.setdefault("shadow_recommendations", 0)
                self.counters["shadow_recommendations"] += 1
                self.counters.setdefault("shadow_agreement", 0)
                self.counters.setdefault("shadow_inconclusive", 0)
                recommendation = trace.routing_metadata.get("recommendation")
                if recommendation == "inconclusive":
                    self.counters["shadow_inconclusive"] += 1
                elif recommendation == "stay" and trace.routing_metadata.get("current_provider") == trace.route:
                    self.counters["shadow_agreement"] += 1
            
            # Lightweight checkpointing every 10 tasks to prevent total loss on OOM kills
            if self.counters["total_tasks"] % 10 == 0:
                self._flush_checkpoint()

    def _flush_checkpoint(self):
        try:
            self.dump_report(settings.telemetry_path + ".checkpoint")
        except Exception:
            pass

    def dump_report(self, path: str):
        report = {
            "summary": self.counters,
            "traces": [asdict(t) for t in self.traces]
        }
        try:
            import os
            os.makedirs(os.path.dirname(path), exist_ok=True)
            
            # Atomic write to prevent file corruption during container crashes
            tmp_path = path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=4)
            os.replace(tmp_path, path)
            
            logger.info(f"Telemetry report dumped to {path}")
        except Exception as e:
            logger.error(f"Failed to write telemetry: {e}")
