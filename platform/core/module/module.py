"""RuntimeModule — the shared base every AI runtime module composes (M5.0).

Gives each future module (Memory, Prompt, Knowledge, Context, Tool, Reasoning, …) a
deterministic lifecycle, health, metrics, configuration, dependency injection,
structured logging, error isolation, and graceful shutdown — **for free** — so a
module author writes only their domain logic (the M5.0 success criterion).

The lifecycle is a `core.fsm.StateMachine` (dogfooding the FSM framework): every
transition is validated + recorded, so an illegal lifecycle move raises rather than
corrupting state. Owns module mechanics only — no domain logic, no bus, no AI.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeVar

from core.errors import PlatformError
from core.fsm import StateMachine, StateMachineDefinition

from .metrics import HealthStatus, Meter, ModuleMetrics, NullMeter


class ModuleError(PlatformError):
    """A RuntimeModule lifecycle failure."""


class ModuleState(StrEnum):
    CREATED = "created"
    INITIALIZING = "initializing"
    READY = "ready"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class _T(StrEnum):  # lifecycle triggers
    INITIALIZE = "initialize"
    READY = "ready"
    START = "start"
    STOP = "stop"
    STOPPED = "stopped"
    SHUTDOWN = "shutdown"
    FAIL = "fail"


def _lifecycle_def() -> StateMachineDefinition[ModuleState, _T]:
    d: StateMachineDefinition[ModuleState, _T] = StateMachineDefinition()
    d.add(ModuleState.CREATED, _T.INITIALIZE, ModuleState.INITIALIZING)
    d.add(ModuleState.INITIALIZING, _T.READY, ModuleState.READY)
    d.add(ModuleState.READY, _T.START, ModuleState.RUNNING)
    d.add(ModuleState.RUNNING, _T.STOP, ModuleState.STOPPING)
    d.add(ModuleState.STOPPING, _T.STOPPED, ModuleState.STOPPED)
    # graceful shutdown from any resting state
    d.add(ModuleState.READY, _T.SHUTDOWN, ModuleState.STOPPED)
    d.add(ModuleState.RUNNING, _T.SHUTDOWN, ModuleState.STOPPED)
    # failure from any non-terminal working state
    for s in (ModuleState.CREATED, ModuleState.INITIALIZING, ModuleState.READY,
              ModuleState.RUNNING, ModuleState.STOPPING):
        d.add(s, _T.FAIL, ModuleState.FAILED)
    return d


_LIFECYCLE = _lifecycle_def()


@dataclass(frozen=True, slots=True)
class ModuleConfig:
    """Base module configuration. Modules subclass to add typed options; the generic
    `options` map keeps unknown keys without a schema change."""

    name: str = "module"
    options: Mapping[str, Any] = field(default_factory=dict)


D = TypeVar("D")


class RuntimeModule:
    """Composable base: lifecycle + health + metrics + DI + logging + isolation."""

    def __init__(
        self,
        *,
        name: str,
        config: ModuleConfig | None = None,
        deps: Mapping[Any, Any] | None = None,
        meter: Meter | None = None,
        clock: Callable[[], float] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.name = name
        self.config = config or ModuleConfig(name=name)
        self._deps: dict[Any, Any] = dict(deps or {})
        self.metrics = ModuleMetrics()
        self.meter: Meter = meter or NullMeter()
        self._clock = clock or time.time
        self.log = logger or logging.getLogger(f"voxvoice.module.{name}")
        self._fsm: StateMachine[ModuleState, _T] = StateMachine(
            _LIFECYCLE, ModuleState.CREATED, machine_id=name
        )

    # ── read-only exposure ──────────────────────────────────────────────────
    @property
    def state(self) -> ModuleState:
        return self._fsm.state

    def health(self) -> HealthStatus:
        st = self.state
        healthy = st in (ModuleState.READY, ModuleState.RUNNING)
        return HealthStatus(self.name, st.value, healthy)

    # ── dependency injection (ports/adapters wired at the composition root) ──
    def add_dependency(self, key: Any, value: Any) -> None:
        self._deps[key] = value

    def dependency(self, key: Any, default: D | None = None) -> D | None:
        return self._deps.get(key, default)

    def require(self, key: Any) -> Any:
        if key not in self._deps:
            raise ModuleError(f"{self.name}: missing required dependency {key!r}")
        return self._deps[key]

    # ── lifecycle (deterministic; FSM-validated) ────────────────────────────
    async def initialize(self) -> None:
        self._fire(_T.INITIALIZE)
        try:
            await self.on_initialize()
            self._fire(_T.READY)
        except Exception as exc:  # noqa: BLE001 — routed to FAILED + typed
            self._fail(exc)
            raise ModuleError(f"{self.name}: initialize failed: {exc}") from exc

    async def start(self) -> None:
        try:
            self._fire(_T.START)
            await self.on_start()
            self.metrics.started += 1
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)
            raise ModuleError(f"{self.name}: start failed: {exc}") from exc

    async def stop(self) -> None:
        if self.state is not ModuleState.RUNNING:
            return
        self._fire(_T.STOP)
        try:
            await self.on_stop()
        finally:
            self._fire(_T.STOPPED)
            self.metrics.stopped += 1

    async def shutdown(self) -> None:
        """Graceful, idempotent teardown from any state (never raises)."""
        try:
            if self.state is ModuleState.RUNNING:
                await self.stop()
            elif self.state is ModuleState.READY:
                self._fire(_T.SHUTDOWN)
            await self.on_shutdown()
        except Exception as exc:  # noqa: BLE001 — shutdown must not raise
            self.log.warning("%s: shutdown error (ignored): %s", self.name, exc)

    # ── overridable domain hooks (no-ops by default) ───────────────────────
    async def on_initialize(self) -> None: ...
    async def on_start(self) -> None: ...
    async def on_stop(self) -> None: ...
    async def on_shutdown(self) -> None: ...

    # ── error isolation (a work item never corrupts the module) ─────────────
    def isolate(self, fn: Callable[[], Any], *, what: str = "handler") -> Any:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — isolated + counted
            self.metrics.handler_errors += 1
            self.meter.incr("module.handler_errors", 1, module=self.name)
            self.log.warning("%s: isolated %s error: %s", self.name, what, exc)
            return None

    # ── internals ────────────────────────────────────────────────────────────
    def _fire(self, trigger: _T) -> None:
        self._fsm.fire(trigger)

    def _fail(self, exc: BaseException) -> None:
        if self._fsm.can(_T.FAIL):
            self._fsm.fire(_T.FAIL)
        self.metrics.failures += 1
        self.log.error("%s: entered FAILED: %s", self.name, exc)
