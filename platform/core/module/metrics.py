"""Module-level metrics + health + the injectable Meter contract (M5.0).

`ModuleMetrics` are the always-present, dependency-free counters every RuntimeModule
carries. `Meter` is a *structural* protocol for richer telemetry (counters/timers/
histograms) — the `telemetry/` package satisfies it without `core` importing
`telemetry` (dependency inversion keeps the graph acyclic). `NullMeter` is the
no-cost default.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class ModuleMetrics:
    """Generic lifecycle + isolation counters (no domain metrics)."""

    started: int = 0
    stopped: int = 0
    failures: int = 0
    handled: int = 0  # events/work items handled
    handler_errors: int = 0  # isolated handler failures
    published: int = 0
    extra: dict[str, int] = field(default_factory=dict)

    def incr(self, name: str, n: int = 1) -> None:
        if hasattr(self, name) and name != "extra":
            setattr(self, name, getattr(self, name) + n)
        else:
            self.extra[name] = self.extra.get(name, 0) + n

    def snapshot(self) -> dict[str, int | dict[str, int]]:
        return asdict(self)


@runtime_checkable
class Meter(Protocol):
    """Structural telemetry sink. `telemetry.Telemetry` implements this; modules type
    their injected meter as `Meter` so `core` never imports `telemetry`."""

    def incr(self, name: str, n: int = 1, **labels: str) -> None: ...
    def observe(self, name: str, value: float, **labels: str) -> None: ...


class NullMeter:
    """A no-op Meter (default). Deterministic, zero-cost."""

    def incr(self, name: str, n: int = 1, **labels: str) -> None:  # noqa: D401
        return None

    def observe(self, name: str, value: float, **labels: str) -> None:
        return None

    @contextmanager
    def timer(self, name: str, **labels: str):
        yield


@dataclass(frozen=True, slots=True)
class HealthStatus:
    """A module's health snapshot."""

    name: str
    state: str
    healthy: bool
    detail: str | None = None
