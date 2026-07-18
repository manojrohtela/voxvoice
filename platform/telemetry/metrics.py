"""In-process metrics — counters, gauges, histograms, timers (M5.0).

Deterministic, dependency-free (stdlib only), and structurally compatible with the
`core.module.Meter` protocol (`incr` / `observe`) so a RuntimeModule can take a
`Telemetry` as its meter without `core` importing `telemetry`. No exporters here —
`exporters.py` defines the OpenTelemetry / Prometheus extension points.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field


def _key(name: str, labels: Mapping[str, str]) -> str:
    if not labels:
        return name
    tags = ",".join(f"{k}={labels[k]}" for k in sorted(labels))
    return f"{name}{{{tags}}}"


@dataclass(slots=True)
class Histogram:
    """A fixed-bucket histogram (deterministic; sums + count + buckets)."""

    buckets: tuple[float, ...] = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)
    counts: list[int] = field(default_factory=list)
    total: float = 0.0
    n: int = 0

    def __post_init__(self) -> None:
        if not self.counts:
            self.counts = [0] * (len(self.buckets) + 1)

    def observe(self, value: float) -> None:
        self.total += value
        self.n += 1
        for i, edge in enumerate(self.buckets):
            if value <= edge:
                self.counts[i] += 1
                return
        self.counts[-1] += 1

    @property
    def mean(self) -> float:
        return self.total / self.n if self.n else 0.0


class Telemetry:
    """A metrics registry + meter. Satisfies `core.module.Meter` (incr/observe)."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.perf_counter
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, Histogram] = {}

    # ── Meter protocol ───────────────────────────────────────────────────────
    def incr(self, name: str, n: float = 1, **labels: str) -> None:
        k = _key(name, labels)
        self._counters[k] = self._counters.get(k, 0.0) + n

    def observe(self, name: str, value: float, **labels: str) -> None:
        self._histograms.setdefault(_key(name, labels), Histogram()).observe(value)

    # ── gauges + timers ──────────────────────────────────────────────────────
    def gauge(self, name: str, value: float, **labels: str) -> None:
        self._gauges[_key(name, labels)] = value

    @contextmanager
    def timer(self, name: str, **labels: str):
        """Time a block; records seconds into a histogram. Deterministic under an
        injected clock (tests pass a fake clock)."""
        start = self._clock()
        try:
            yield
        finally:
            self.observe(name, self._clock() - start, **labels)

    # ── read-only snapshot ───────────────────────────────────────────────────
    def counter(self, name: str, **labels: str) -> float:
        return self._counters.get(_key(name, labels), 0.0)

    def histogram(self, name: str, **labels: str) -> Histogram | None:
        return self._histograms.get(_key(name, labels))

    def snapshot(self) -> dict[str, dict[str, float]]:
        return {
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "histograms": {
                k: {"count": h.n, "sum": h.total, "mean": h.mean}
                for k, h in self._histograms.items()
            },
        }
