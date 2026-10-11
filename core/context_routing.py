"""Bounded, provider-neutral context and switching-cost policy.

The policy is deliberately advisory: callers may record its recommendation in
shadow mode without changing the execution route.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class Price:
    input_per_million: Optional[float] = None
    cached_input_per_million: Optional[float] = None
    cache_write_per_million: Optional[float] = None
    output_per_million: Optional[float] = None
    source: str = "unknown"


def load_prices(raw: str) -> dict[str, Price]:
    """Parse operator-provided rates; malformed or absent rates stay absent."""
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    prices = {}
    for key, values in payload.items():
        if not isinstance(key, str) or not isinstance(values, dict):
            continue
        try:
            prices[key] = Price(
                values.get("input_per_million"), values.get("cached_input_per_million"),
                values.get("cache_write_per_million"), values.get("output_per_million"),
                values.get("source", "operator"),
            )
        except (TypeError, ValueError):
            continue
    return prices


@dataclass(frozen=True)
class Usage:
    input_tokens: Optional[int] = None
    cached_input_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    source: str = "unknown"


def normalize_provider_usage(response: Any) -> Usage:
    """Normalize SDK or fixture usage without turning missing fields into zero."""
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage", response)
    if usage is None:
        return Usage(source="unknown")

    def value(*names: str) -> Optional[int]:
        for name in names:
            raw = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
            if raw is not None:
                try:
                    return int(raw)
                except (TypeError, ValueError):
                    return None
        return None

    return Usage(
        input_tokens=value("prompt_tokens", "input_tokens"),
        cached_input_tokens=value("cached_tokens", "cache_read_input_tokens"),
        cache_write_tokens=value("cache_creation_input_tokens", "cache_write_tokens"),
        output_tokens=value("completion_tokens", "output_tokens"),
        source="provider",
    )


@dataclass(frozen=True)
class CostEstimate:
    amount: Optional[float]
    currency: str
    status: str
    missing: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoutingRecommendation:
    current_provider: str
    candidate_provider: str
    recommendation: str
    recommendation_applied: bool
    current_cost: CostEstimate
    candidate_cost: CostEstimate
    reason: str
    confidence: str = "estimated"


@dataclass(frozen=True)
class ContextPackage:
    text: str
    files: tuple[str, ...]
    omitted: tuple[dict, ...]
    estimated_tokens: int
    truncated: bool
    token_source: str = "estimated"


class ContextBuilder:
    """Build a small, grounded package from an explicitly configured root."""
    EXCLUDED_NAMES = {".env", ".env.example", "credentials.json"}
    EXCLUDED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist", "build"}

    def __init__(self, repo_root: str, *, max_chars: int = 12000,
                 max_files: int = 4, max_file_chars: int = 4000):
        self.root = Path(repo_root).resolve()
        self.max_chars = max(1, max_chars)
        self.max_files = max(1, max_files)
        self.max_file_chars = max(1, max_file_chars)

    def build(self, prompt: str, summary: str = "", file_paths: Optional[list[str]] = None) -> ContextPackage:
        chunks = ["TASK:\n" + prompt]
        if summary:
            chunks.append("SESSION SUMMARY:\n" + summary)
        included = []
        omitted = []
        for raw_path in (file_paths or [])[:self.max_files]:
            candidate = (self.root / raw_path).resolve()
            try:
                candidate.relative_to(self.root)
            except ValueError:
                omitted.append({"path": raw_path, "reason": "outside_repository_root"})
                continue
            if candidate.name in self.EXCLUDED_NAMES or any(part in self.EXCLUDED_DIRS for part in candidate.parts):
                omitted.append({"path": raw_path, "reason": "excluded_path"})
                continue
            try:
                if not candidate.is_file():
                    omitted.append({"path": raw_path, "reason": "missing_file"})
                    continue
                content = candidate.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                omitted.append({"path": raw_path, "reason": "unreadable_or_binary"})
                continue
            if len(content) > self.max_file_chars:
                content = content[:self.max_file_chars]
                omitted.append({"path": raw_path, "reason": "file_truncated"})
            chunks.append(f"FILE {candidate.relative_to(self.root)}:\n{content}")
            included.append(str(candidate.relative_to(self.root)))
        text = "\n\n".join(chunks)
        truncated = len(text) > self.max_chars
        if truncated:
            text = text[:self.max_chars]
        return ContextPackage(text, tuple(included), tuple(omitted), estimate_tokens(text), truncated)


def estimate_tokens(text: str) -> int:
    """Conservative estimate; it is never presented as provider usage."""
    return max(1, (len(text) + 3) // 4)


def calculate_cost(usage: Usage, price: Price, currency: str = "USD") -> CostEstimate:
    missing = []
    amount = 0.0
    if usage.input_tokens is None:
        missing.append("input_tokens")
    elif usage.input_tokens < 0:
        missing.append("invalid_input_tokens")
    elif price.input_per_million is None:
        missing.append("input_price")
    else:
        cached = usage.cached_input_tokens or 0
        if cached < 0 or cached > usage.input_tokens:
            missing.append("invalid_cached_input_tokens")
            cached = 0
        uncached = max(0, usage.input_tokens - cached)
        amount += uncached * price.input_per_million / 1_000_000
        if cached:
            if price.cached_input_per_million is None:
                missing.append("cached_input_price")
            else:
                amount += cached * price.cached_input_per_million / 1_000_000
    if usage.cache_write_tokens:
        if price.cache_write_per_million is None:
            missing.append("cache_write_price")
        else:
            amount += usage.cache_write_tokens * price.cache_write_per_million / 1_000_000
    if usage.output_tokens is None:
        missing.append("output_tokens")
    elif price.output_per_million is None:
        missing.append("output_price")
    else:
        amount += usage.output_tokens * price.output_per_million / 1_000_000
    status = "measured" if usage.source == "provider" and not missing else ("unknown" if not amount else "partial")
    return CostEstimate(amount if amount else (0.0 if not missing else None), currency, status, tuple(sorted(set(missing))))


def recommend_switch(*, current_provider: str, candidate_provider: str,
                     current_usage: Usage, candidate_usage: Usage,
                     current_price: Price, candidate_price: Price,
                     required_quality: float = 0.0, candidate_quality: float = 1.0,
                     safety_margin: float = 0.0,
                     current_retry_probability: float = 0.0,
                     candidate_retry_probability: float = 0.0,
                     current_latency_ms: Optional[float] = None,
                     candidate_latency_ms: Optional[float] = None,
                     max_latency_ms: Optional[float] = None,
                     currency: str = "USD") -> RoutingRecommendation:
    current = calculate_cost(current_usage, current_price, currency)
    candidate = calculate_cost(candidate_usage, candidate_price, currency)
    if candidate_quality < required_quality:
        return RoutingRecommendation(current_provider, candidate_provider, "stay", False, current, candidate, "candidate_quality_below_requirement")
    if max_latency_ms is not None and candidate_latency_ms is not None and candidate_latency_ms > max_latency_ms:
        return RoutingRecommendation(current_provider, candidate_provider, "stay", False, current, candidate, "candidate_latency_above_hard_limit")
    if current.amount is None or candidate.amount is None:
        return RoutingRecommendation(current_provider, candidate_provider, "inconclusive", False, current, candidate, "insufficient_pricing_or_usage_data")
    # Expected retry overhead is modeled as an expected additional request
    # fraction; it is not presented as measured retry activity.
    saving = current.amount * (1.0 + max(0.0, current_retry_probability)) - candidate.amount * (1.0 + max(0.0, candidate_retry_probability))
    if saving > max(0.0, current.amount * safety_margin):
        return RoutingRecommendation(current_provider, candidate_provider, "switch", False, current, candidate, "estimated_saving_exceeds_safety_margin")
    return RoutingRecommendation(current_provider, candidate_provider, "stay", False, current, candidate, "estimated_saving_below_safety_margin")


class JsonSessionStore:
    """Small bounded store; failures are safe and never affect inference."""
    _locks: dict[str, threading.Lock] = {}
    _locks_guard = threading.Lock()

    def __init__(self, path: str, max_history: int = 10, max_summary_chars: int = 2000):
        self.path = Path(path)
        self.max_history = max(1, max_history)
        self.max_summary_chars = max(1, max_summary_chars)

    def _lock(self) -> threading.Lock:
        key = str(self.path)
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def _load(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def get(self, session_id: str) -> Optional[dict]:
        value = self._load().get(session_id)
        return value if isinstance(value, dict) else None

    def append(self, session_id: str, task: dict, summary: str = "") -> None:
        with self._lock():
            data = self._load()
            record = data.get(session_id) if isinstance(data.get(session_id), dict) else {"history": []}
            history = record.get("history") if isinstance(record.get("history"), list) else []
            history.append({k: task[k] for k in ("task_id", "provider", "model", "outcome") if k in task})
            record["history"] = history[-self.max_history:]
            record["summary"] = summary[:self.max_summary_chars]
            record["updated_at"] = time.time()
            data[session_id] = record
            temp = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd, temp = tempfile.mkstemp(prefix="relayos-session-", suffix=".tmp", dir=str(self.path.parent))
                os.close(fd)
                Path(temp).write_text(json.dumps(data), encoding="utf-8")
                os.replace(temp, self.path)
            except OSError:
                if temp:
                    try:
                        Path(temp).unlink(missing_ok=True)
                    except OSError:
                        pass
