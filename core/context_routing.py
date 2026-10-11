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
import math
import hashlib
from collections import OrderedDict
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
    version: str = "unspecified"
    currency: str = "USD"

    def __post_init__(self):
        for name in ("input_per_million", "cached_input_per_million", "cache_write_per_million", "output_per_million"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
                raise ValueError(f"Invalid price: {name}")


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
                str(values.get("version", "unspecified")), values.get("currency", "USD"),
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
    input_includes_cached: bool = True
    input_includes_writes: bool = False
    invalid_fields: tuple[str, ...] = ()
    unsupported_charge_categories: tuple[str, ...] = ()


def normalize_provider_usage(response: Any, provider: str = "unknown") -> Usage:
    """Normalize SDK or fixture usage without turning missing fields into zero."""
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
        if usage is None and any(k in response for k in ("input_tokens", "prompt_tokens", "output_tokens", "completion_tokens")):
            usage = response
    if usage is None:
        return Usage(source="unknown")

    invalid = []
    def get(obj, name):
        return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)

    def value(*names: str) -> Optional[int]:
        for name in names:
            raw = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
            if raw is not None:
                if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
                    return raw
                invalid.append(name)
                return None
        return None

    details = get(usage, "prompt_tokens_details") or get(usage, "input_tokens_details") or {}
    cached = value("cached_tokens", "cache_read_input_tokens")
    writes = value("cache_creation_input_tokens", "cache_write_tokens")
    for field in ("cached_tokens", "cache_write_tokens"):
        raw = get(details, field)
        if raw is not None:
            if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
                invalid.append(field)
            elif field == "cached_tokens":
                cached = raw
            else:
                writes = raw
    unsupported = []
    creation = get(usage, "cache_creation")
    if creation and get(creation, "ephemeral_1h_input_tokens"):
        unsupported.append("cache_write_1h_rate")
    return Usage(
        input_tokens=value("prompt_tokens", "input_tokens"),
        cached_input_tokens=cached,
        cache_write_tokens=writes,
        output_tokens=value("completion_tokens", "output_tokens"),
        source="provider",
        input_includes_cached=provider != "anthropic",
        input_includes_writes=provider == "openai",
        invalid_fields=tuple(invalid),
        unsupported_charge_categories=tuple(unsupported),
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
    cache_events: tuple[dict, ...] = ()


class ContextBuilder:
    """Bounded process-local prepared-file cache, verified against current bytes."""
    EXCLUDED_NAMES = {"credentials.json", "credentials", "id_rsa", "id_ed25519"}
    EXCLUDED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
                     "output", ".aws", ".ssh", ".codex", ".pytest_cache", "coverage", "test-results"}
    ALLOWED_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".md", ".json", ".toml", ".yaml", ".yml", ".txt", ".css", ".html"}
    _cache = OrderedDict()
    _cache_lock = threading.Lock()
    CACHE_ENTRIES = 32
    MAX_READ_BYTES = 1_000_000

    def __init__(self, repo_root: str, *, max_chars: int = 12000,
                 max_files: int = 4, max_file_chars: int = 4000):
        self.root = Path(repo_root).resolve()
        self.max_chars = min(64000, max(1, max_chars))
        self.max_files = min(32, max(1, max_files))
        self.max_file_chars = min(16000, max(1, max_file_chars))

    def build(self, prompt: str, summary: str = "", file_paths: Optional[list[str]] = None) -> ContextPackage:
        chunks = [prompt]
        included, omitted, events = [], [], []
        if file_paths is not None and (not isinstance(file_paths, (list, tuple)) or any(not isinstance(p, str) for p in file_paths)):
            omitted.append({"path": None, "reason": "invalid_file_paths"})
            file_paths = []
        used = len(chunks[0])
        for index, raw_path in enumerate(file_paths or []):
            if index >= self.max_files:
                omitted.append({"path": raw_path, "reason": "file_count_limit"})
                continue
            try:
                relative = Path(raw_path)
                candidate = (self.root / relative).resolve()
                rel = candidate.relative_to(self.root)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError()
            except (ValueError, OSError):
                omitted.append({"path": raw_path, "reason": "outside_repository_root"})
                continue
            parts = [part.lower() for part in rel.parts]
            if (any(part in self.EXCLUDED_DIRS for part in parts)
                    or candidate.name.lower().startswith(".env")
                    or candidate.name.lower() in self.EXCLUDED_NAMES
                    or any(word in candidate.stem.lower() for word in ("secret", "credential"))
                    or candidate.suffix.lower() not in self.ALLOWED_SUFFIXES):
                omitted.append({"path": raw_path, "reason": "excluded_path"})
                continue
            try:
                if not candidate.is_file():
                    omitted.append({"path": raw_path, "reason": "missing_file"})
                    continue
                with candidate.open("rb") as stream:
                    raw = stream.read(self.MAX_READ_BYTES + 1)
                if len(raw) > self.MAX_READ_BYTES:
                    omitted.append({"path": raw_path, "reason": "file_byte_limit"})
                    continue
                digest = hashlib.sha256(raw).hexdigest()
                key = (os.path.normcase(str(self.root)), rel.as_posix(), digest,
                       self.max_chars, self.max_files, self.max_file_chars)
                with self._cache_lock:
                    hit = key in self._cache
                    if hit:
                        content = self._cache.pop(key)
                    else:
                        content = raw.decode("utf-8")[:self.max_file_chars]
                    self._cache[key] = content
                    while len(self._cache) > self.CACHE_ENTRIES:
                        self._cache.popitem(last=False)
                events.append({"path": rel.as_posix(), "sha256": digest,
                               "cache": "prepared_file_memory", "hit": hit, "current_bytes_verified": True})
            except (OSError, UnicodeError):
                omitted.append({"path": raw_path, "reason": "unreadable_or_binary"})
                continue
            chunk = f"FILE {rel.as_posix()}:\n{content}"
            available = self.max_chars - used - 2
            if available <= len(chunk) - len(content):
                omitted.append({"path": raw_path, "reason": "context_size_limit"})
                continue
            if len(raw.decode("utf-8")) > len(content) or len(chunk) > available:
                omitted.append({"path": raw_path, "reason": "file_truncated"})
            chunks.append(chunk[:available])
            used += len(chunks[-1]) + 2
            included.append(rel.as_posix())
        if summary and used + 20 < self.max_chars:
            chunks.append(("SESSION SUMMARY:\n" + summary)[:self.max_chars - used - 2])
        text = "\n\n".join(chunks)
        truncated = len(text) > self.max_chars or any(x["reason"] in {"file_truncated", "context_size_limit"} for x in omitted)
        text = text[:self.max_chars]
        return ContextPackage(text, tuple(included), tuple(omitted), estimate_tokens(text), truncated,
                              cache_events=tuple(events))


def estimate_tokens(text: str) -> int:
    """Conservative estimate; it is never presented as provider usage."""
    return max(1, (len(text) + 3) // 4)


def calculate_cost(usage: Usage, price: Price, currency: str = "USD") -> CostEstimate:
    missing = ["invalid_" + field for field in usage.invalid_fields] + list(usage.unsupported_charge_categories)
    amount = 0.0
    known_components = 0
    values = {}
    for field in ("input_tokens", "cached_input_tokens", "cache_write_tokens", "output_tokens"):
        value = getattr(usage, field)
        if value is None:
            missing.append(field)
        elif isinstance(value, bool) or not isinstance(value, int) or value < 0:
            missing.append("invalid_" + field)
            value = None
        values[field] = value
    base = values["input_tokens"]
    cached, writes = values["cached_input_tokens"], values["cache_write_tokens"]
    if usage.unsupported_charge_categories:
        writes = None
    if base is not None:
        subtraction = (cached or 0) if usage.input_includes_cached else 0
        subtraction += (writes or 0) if usage.input_includes_writes else 0
        if subtraction > base:
            missing.append("invalid_cached_input_tokens")
            cached = writes = None
            base = None
        else:
            base -= subtraction
        if (usage.input_includes_cached and values["cached_input_tokens"] is None) or (usage.input_includes_writes and values["cache_write_tokens"] is None):
            base = None  # Cannot assign unknown cache categories to the ordinary rate.
    for category, count, rate in (
        ("input", base, price.input_per_million),
        ("cached_input", cached, price.cached_input_per_million),
        ("cache_write", writes, price.cache_write_per_million),
        ("output", values["output_tokens"], price.output_per_million),
    ):
        if count is None:
            continue
        if count == 0:
            known_components += 1
        elif rate is None:
            missing.append(category + "_price")
        else:
            amount += count * rate / 1_000_000
            known_components += 1
    if currency != price.currency:
        return CostEstimate(None, currency, "unknown", ("currency_mismatch",))
    if not math.isfinite(amount):
        return CostEstimate(None, currency, "unknown", ("cost_overflow",))
    if usage.source not in {"provider", "relayos", "estimated"}:
        missing.append("usage_provenance")
    status = ("estimated" if usage.source == "estimated" else "measured") if not missing else ("partial" if known_components else "unknown")
    return CostEstimate(amount if known_components else None, currency, status, tuple(sorted(set(missing))))


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
                     candidate_rebuild_cost: float = 0.0,
                     currency: str = "USD") -> RoutingRecommendation:
    current = calculate_cost(current_usage, current_price, currency)
    candidate = calculate_cost(candidate_usage, candidate_price, currency)
    numbers = (required_quality, candidate_quality, safety_margin, current_retry_probability,
               candidate_retry_probability, candidate_rebuild_cost)
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in numbers):
        return RoutingRecommendation(current_provider, candidate_provider, "inconclusive", False, current, candidate, "invalid_assumptions")
    if required_quality > 1 or candidate_quality > 1 or any(v is not None and (not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0) for v in (current_latency_ms, candidate_latency_ms, max_latency_ms)):
        return RoutingRecommendation(current_provider, candidate_provider, "inconclusive", False, current, candidate, "invalid_assumptions")
    if candidate.amount is not None and candidate_rebuild_cost:
        from dataclasses import replace
        candidate = replace(candidate, amount=candidate.amount + candidate_rebuild_cost, status="estimated")
    if candidate_quality < required_quality:
        return RoutingRecommendation(current_provider, candidate_provider, "stay", False, current, candidate, "candidate_quality_below_requirement")
    if max_latency_ms is not None and candidate_latency_ms is not None and candidate_latency_ms > max_latency_ms:
        return RoutingRecommendation(current_provider, candidate_provider, "stay", False, current, candidate, "candidate_latency_above_hard_limit")
    if current.amount is None or candidate.amount is None or current.missing or candidate.missing:
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

    def __init__(self, path: str, max_history: int = 10, max_summary_chars: int = 2000, max_sessions: int = 128):
        self.path = Path(path).resolve()
        self.max_sessions = max(1, max_sessions)
        self.max_history = min(100, max(1, max_history))
        self.max_summary_chars = min(16000, max(1, max_summary_chars))

    def _lock(self) -> threading.Lock:
        key = str(self.path)
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def _load(self) -> Dict[str, Any]:
        try:
            if self.path.stat().st_size > 8_000_000:
                return {}
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def get(self, session_id: str) -> Optional[dict]:
        if not isinstance(session_id, str) or len(session_id) > 256:
            return None
        value = self._load().get(session_id)
        return value if isinstance(value, dict) else None

    def append(self, session_id: str, task: dict, summary: str = "") -> None:
        if not isinstance(session_id, str) or len(session_id) > 256:
            return
        with self._lock():
            data = self._load()
            record = data.get(session_id) if isinstance(data.get(session_id), dict) else {"history": []}
            history = record.get("history") if isinstance(record.get("history"), list) else []
            history.append({k: str(task[k])[:256] for k in ("task_id", "provider", "model", "outcome") if k in task})
            record["history"] = history[-self.max_history:]
            record["summary"] = summary[:self.max_summary_chars]
            record["updated_at"] = time.time()
            data[session_id] = record
            # Latest-answer snapshot plus recent outcome metadata; never recursive summarization.
            record["summary_semantics"] = "latest_answer_snapshot"
            if len(data) > self.max_sessions:
                keys = sorted(data, key=lambda k: data[k].get("updated_at", 0) if isinstance(data[k], dict) and isinstance(data[k].get("updated_at", 0), (int, float)) else 0)
                for key in keys[:len(data) - self.max_sessions]:
                    del data[key]
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
