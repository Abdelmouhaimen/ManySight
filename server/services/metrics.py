"""Bounded in-process counters and duration samples for the realtime pipeline.

This is deliberately tiny: no external metrics dependency, no unbounded history,
no per-tick logging.  A 100 Hz scheduler cannot afford to append a row to a table
or emit a log line per tick, so everything here is an in-memory counter or a
fixed-size ring of recent durations that the metrics endpoint summarizes on read.

Metrics are process-local observability, never a source of truth: nothing in the
ingestion, fusion, or alert path reads them back to make a decision.
"""
from __future__ import annotations

import threading
import time
from collections import deque

# Enough samples for a stable p99 at 100 Hz over the last ~20 seconds without
# letting a long-running process accumulate memory.
SAMPLE_CAPACITY = 2048


class Registry:
    """Counters, gauges, and bounded duration rings behind one lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        self._durations: dict[str, deque[float]] = {}
        self._started_at = time.monotonic()

    def increment(self, name: str, amount: float = 1.0) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0.0) + amount

    def gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def observe(self, name: str, seconds: float) -> None:
        with self._lock:
            ring = self._durations.get(name)
            if ring is None:
                ring = self._durations[name] = deque(maxlen=SAMPLE_CAPACITY)
            ring.append(seconds)

    def timer(self, name: str) -> "Timer":
        return Timer(self, name)

    def counter(self, name: str) -> float:
        with self._lock:
            return self._counters.get(name, 0.0)

    def snapshot(self) -> dict:
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            durations = {name: list(ring) for name, ring in self._durations.items()}
            uptime = time.monotonic() - self._started_at
        return {
            "uptime_s": round(uptime, 3),
            "counters": {name: _clean(value) for name, value in sorted(counters.items())},
            "rates_per_s": {name: round(value / uptime, 3) if uptime > 0 else 0.0
                            for name, value in sorted(counters.items())},
            "gauges": {name: _clean(value) for name, value in sorted(gauges.items())},
            "durations_ms": {name: percentiles(values)
                             for name, values in sorted(durations.items())},
        }

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._durations.clear()
            self._started_at = time.monotonic()


class Timer:
    """Context manager recording a wall-clock duration sample."""

    __slots__ = ("_registry", "_name", "_start", "seconds")

    def __init__(self, registry: Registry, name: str) -> None:
        self._registry = registry
        self._name = name
        self._start = 0.0
        self.seconds = 0.0

    def __enter__(self) -> "Timer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *_exc) -> None:
        self.seconds = time.perf_counter() - self._start
        self._registry.observe(self._name, self.seconds)


def _clean(value: float) -> float | int:
    return int(value) if float(value).is_integer() else round(float(value), 6)


def percentiles(values: list[float]) -> dict:
    """p50/p95/p99 in milliseconds over the retained samples (nearest-rank)."""
    if not values:
        return {"count": 0}
    ordered = sorted(values)
    def at(fraction: float) -> float:
        index = min(len(ordered) - 1, max(0, round(fraction * len(ordered) + 0.5) - 1))
        return round(ordered[index] * 1000.0, 3)
    return {
        "count": len(ordered),
        "p50": at(0.50), "p95": at(0.95), "p99": at(0.99),
        "max": round(ordered[-1] * 1000.0, 3),
        "mean": round(sum(ordered) / len(ordered) * 1000.0, 3),
    }


registry = Registry()
