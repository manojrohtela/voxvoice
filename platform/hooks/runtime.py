"""HookRuntime — the Hook Framework facade (M6.3B).

Ties registration/discovery/lifecycle, deterministic ordering + filtering,
policy-governed execution, observability, replay, diagnostics, and
statistics into one object. **Never executes a Tool, never executes a
Skill, never performs planning, never mutates runtime state beyond its own
temporary repository** — every method either reads the injected
`HookRegistry` or projects an already-computed `HookRunResult`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from core.errors import ConfigError
from core.events import Event, EventType

from ._run import run_coro_sync
from .config import HookConfiguration
from .context import build_event
from .diagnostics import build_diagnostics
from .domain import (
    HookDescriptor,
    HookEvent,
    HookManifest,
    HookPoint,
    HookReplayRecord,
    HookRunResult,
    HookRuntimeDiagnostics,
    HookRuntimeScope,
    HookRuntimeStatistics,
    HookStatistics,
    ReplayComparison,
)
from .engine import HookEngine
from .events import PHASE_EVENT_TYPE, HookPhase
from .extensions import Hook
from .observability import trace_for
from .policy import HookExecutionPolicy, default_policy_for
from .registry import HookRegistry
from .replay import hook_replay_record_for
from .replay_engine import HookReplayEngine
from .repositories import HookRepository
from .serializer import HookSerializer
from .statistics import compute_statistics

Publisher = Callable[..., Event | None]


class HookRuntime:
    def __init__(
        self,
        *,
        config: HookConfiguration,
        repository: HookRepository,
        registry: HookRegistry,
        engine: HookEngine | None = None,
        replay_engine: HookReplayEngine | None = None,
        serializer: HookSerializer | None = None,
        clock: Callable[[], float] | None = None,
        publish: Publisher | None = None,
    ) -> None:
        import time

        self._config = config
        self._repo = repository
        self._registry = registry
        self._engine = engine or HookEngine()
        self._replay_engine = replay_engine or HookReplayEngine()
        self._serializer = serializer or HookSerializer()
        self._clock = clock or time.time
        self._publish = publish

    # ── read-only discovery exposure ──────────────────────────────────────────
    @property
    def registry(self) -> HookRegistry:
        return self._registry

    # ── registration + lifecycle (synchronous; registry has no store I/O) ────
    def register(self, manifest: HookManifest, hook: Hook) -> HookDescriptor:
        descriptor = self._registry.register(manifest, hook)
        run_coro_sync(self._repo.add_registration(descriptor))
        return descriptor

    def activate(self, namespace: str, id_: str, version: str) -> HookDescriptor:
        return self._registry.activate(namespace, id_, version)

    def deprecate(self, namespace: str, id_: str, version: str) -> HookDescriptor:
        return self._registry.deprecate(namespace, id_, version)

    def retire(self, namespace: str, id_: str, version: str) -> HookDescriptor:
        return self._registry.retire(namespace, id_, version)

    def get_descriptor(
        self, namespace: str, id_: str, version: str | None = None
    ) -> HookDescriptor:
        return self._registry.get_descriptor(namespace, id_, version)

    def list_for(
        self, point: HookPoint, scope: HookRuntimeScope, *, include_deprecated: bool = False,
    ) -> tuple[HookDescriptor, ...]:
        return self._registry.list_for(point, scope, include_deprecated=include_deprecated)

    def registrations(self) -> Sequence[HookDescriptor]:
        return run_coro_sync(self._repo.all_registrations())

    # ── event construction ────────────────────────────────────────────────────
    def build_event(
        self, point: HookPoint, scope: HookRuntimeScope, **kwargs: Any
    ) -> HookEvent:
        return build_event(point, scope, now=self._clock(), **kwargs)

    # ── execution (evaluates registered hooks; never the guarded action) ─────
    def run(
        self,
        point: HookPoint,
        scope: HookRuntimeScope,
        event: HookEvent,
        *,
        hooks: Mapping[str, Hook] | None = None,
        policy: HookExecutionPolicy | None = None,
    ) -> HookRunResult:
        ordered = self._registry.list_for(point, scope)
        callables = hooks if hooks is not None else self._registry.hooks_for(ordered)
        pol = policy or default_policy_for(point)

        result = self._engine.run(ordered, callables, event, pol, now=self._clock())

        trace = trace_for(result, event, now=self._clock())
        run_coro_sync(self._repo.add_trace(trace))
        self._emit(HookPhase.RUN_COMPLETED, event_id=event.event_id,
                   extra={"point": point.value, "outcome": result.aggregated_outcome.value})
        return result

    # ── replay ─────────────────────────────────────────────────────────────────
    def record_replay(self, event: HookEvent, result: HookRunResult) -> HookReplayRecord:
        record = hook_replay_record_for(
            event, result, now=self._clock(), serializer=self._serializer,
        )
        run_coro_sync(self._repo.add_replay(record))
        self._emit(HookPhase.REPLAY_RECORDED, event_id=event.event_id,
                   extra={"replay_id": record.replay_id})
        return record

    def validate_replay(self, replay_id: str) -> ReplayComparison:
        record = run_coro_sync(self._repo.get_replay(replay_id))
        if record is None:
            raise ConfigError(f"unknown hook replay {replay_id!r}")
        comparison = self._replay_engine.validate(record)
        phase = HookPhase.REPLAY_VALIDATED if comparison.matches else HookPhase.REPLAY_MISMATCH
        self._emit(phase, event_id="",
                   extra={"replay_id": replay_id, "matches": comparison.matches})
        return comparison

    def compare_replays(self, replay_id_a: str, replay_id_b: str) -> ReplayComparison:
        a = run_coro_sync(self._repo.get_replay(replay_id_a))
        b = run_coro_sync(self._repo.get_replay(replay_id_b))
        if a is None or b is None:
            raise ConfigError("unknown hook replay id(s) for comparison")
        return self._replay_engine.compare(a, b)

    # ── diagnostics + statistics ───────────────────────────────────────────────
    def diagnostics(self, *, run_count: int = 0, healthy: bool = True) -> HookRuntimeDiagnostics:
        replay_count = run_coro_sync(self._repo.replay_count())
        diag = build_diagnostics(
            config=self._config, registry=self._registry, run_count=run_count,
            replay_count=replay_count, healthy=healthy,
        )
        self._emit(HookPhase.DIAGNOSTICS_GENERATED, event_id="")
        return diag

    def statistics(self, results: Sequence[HookRunResult]) -> HookRuntimeStatistics:
        return compute_statistics(results)

    def runtime_statistics(self) -> HookStatistics:
        return HookStatistics(
            name=self._config.name, state="active", healthy=True,
            hooks=self._registry.names(),
            registrations=run_coro_sync(self._repo.registration_count()),
            metrics={},
        )

    # ── internals ────────────────────────────────────────────────────────────
    def _emit(
        self, phase: HookPhase, *, event_id: str, extra: dict[str, Any] | None = None,
    ) -> None:
        if self._publish is None:
            return
        event_type: EventType = PHASE_EVENT_TYPE[phase]
        payload = {"phase": phase.value, "event_id": event_id, **(extra or {})}
        self._publish(event_type, payload, session_id="", org_id="")
