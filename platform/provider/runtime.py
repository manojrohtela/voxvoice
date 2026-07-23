"""ProviderRuntime — the Provider Runtime core (M5.6B-1).

A `BusConsumer` that owns **registry-based execution only**: it receives a `ProviderRequest`,
resolves the adapter (explicit name, or the first capability-eligible adapter in
deterministic sorted-name order — **never a scored/heuristic routing decision**, that is the
`ProviderRouter`'s job in M5.6B-2), executes it through the injected `AdapterPort`, assembles
the unified `ProviderResponse`, emits `provider.*`/`model.*` events, and persists to the
**temporary** repository.

What lands in M5.6B-1 (core):
- **execute** — resolve → stream → normalize, drive the lifecycle
  REQUESTED→ROUTED→EXECUTING→STREAMING→COMPLETED, persist, emit events.
- **get / remove / history** — read/delete a temporary response; list a session's responses.
- **expire** — deterministic TTL sweep (caller-driven). **statistics / health**.
- **registry / catalog** — read-only exposure for discovery (no routing intelligence).

The pipeline is async-first; the synchronous `execute`/`get`/`remove`/`history` drive it via
the non-blocking sync bridge (local adapters only). Routing strategies, health-outcome
feedback, policies, and tenancy are M5.6B-2.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

from core.errors import ConfigError
from core.events import Event, EventSource, EventType
from core.module import BusConsumer

from ._run import run_coro_sync
from .catalog import CapabilityCatalog
from .config import ProviderConfiguration
from .domain import (
    ProviderExecutionResult,
    ProviderRequest,
    ProviderRequestState,
    ProviderResponse,
    ProviderScope,
    ProviderStatistics,
)
from .events import ProviderPhase
from .ids import request_hash_for
from .lifecycle import ProviderTrigger, advance
from .observability import Metric
from .registry import ProviderRegistry
from .repositories import ProviderRepository
from .response import ResponseNormalizer
from .serializer import ProviderSerializer
from .session import ProviderSessionManager


class ProviderRuntime(BusConsumer):
    def __init__(
        self,
        *,
        config: ProviderConfiguration,
        repository: ProviderRepository,
        registry: ProviderRegistry,
        normalizer: ResponseNormalizer,
        sessions: ProviderSessionManager,
        serializer: ProviderSerializer | None = None,
        id_factory: Callable[[], str] | None = None,
        **kwargs: Any,
    ) -> None:
        # Core is request-driven → subscribe to nothing (no content-event ingestion).
        super().__init__(
            name=config.name,
            source=EventSource.PROVIDER_REGISTRY,
            types=frozenset(),
            id_factory=id_factory,
            **kwargs,
        )
        self._config = config
        self._repo = repository
        self._registry = registry
        self._catalog = CapabilityCatalog(registry)
        self._normalizer = normalizer
        self._sessions = sessions
        self._serializer = serializer or ProviderSerializer()

    # ── read-only discovery exposure (no routing intelligence) ────────────────
    @property
    def registry(self) -> ProviderRegistry:
        return self._registry

    @property
    def catalog(self) -> CapabilityCatalog:
        return self._catalog

    # ── rich execute surface (drives the async core) ─────────────────────────
    def execute(self, request: ProviderRequest) -> ProviderResponse:
        return run_coro_sync(self.aexecute(request)).response

    def get(self, response_id: str) -> ProviderResponse | None:
        return run_coro_sync(self._repo.get_response(response_id))

    def remove(self, response_id: str) -> None:
        return run_coro_sync(self._repo.remove_response(response_id))

    def history(self, session_id: str) -> Sequence[ProviderResponse]:
        return run_coro_sync(self._repo.responses_for_session(session_id))

    # ── async core: execute ────────────────────────────────────────────────────
    async def aexecute(self, request: ProviderRequest) -> ProviderExecutionResult:
        now = self._clock()
        request = self._resolve(request)
        scope = request.scope
        seq = len(await self._repo.responses_for_session(request.session_id))
        expires_at = (now + self._config.response_ttl_s) if self._config.response_ttl_s else None

        with self.meter.timer(Metric.REQUEST_LATENCY):
            self._emit_lite(ProviderPhase.REQUESTED, request)
            try:
                name = self._select(request)
                self.meter.incr(Metric.PROVIDER_SELECTED, 1)
                adapter = self._registry.get(name)
                request = replace(request, provider=name)
                request_id = f"{name}:{request.request_hash or seq}"
                chunks = list(adapter.stream(request))
                streamed = request.streaming
                if not streamed:
                    chunks = [self._normalizer.collapse(request, chunks)]
                response = self._normalizer.normalize(
                    request, chunks, session_id=request.session_id, scope=scope, now=now,
                    seq=seq, streamed=streamed, expires_at=expires_at, request_id=request_id,
                )
                response = response.with_state(ProviderRequestState.REQUESTED)
            except Exception as exc:  # noqa: BLE001 — surface as a FAILED event, then re-raise
                self.metrics.incr("failed")
                self.meter.incr(Metric.FAILED, 1)
                self._publish(EventType.PROVIDER_DISCONNECTED,
                              {"phase": ProviderPhase.FAILED.value,
                               "session_id": request.session_id, "error": str(exc)},
                              scope=scope)
                raise

            # REQUESTED → ROUTED (registry selection only — no routing strategy)
            response = advance(response, ProviderTrigger.ROUTE, clock=self._clock)
            self.metrics.incr("routed")
            self.meter.incr(Metric.ROUTED, 1)
            self._emit(response, ProviderPhase.ROUTED)

            # ROUTED → EXECUTING
            response = advance(response, ProviderTrigger.EXECUTE, clock=self._clock)
            self.metrics.incr("executing")
            self.meter.incr(Metric.EXECUTING, 1)
            self._emit(response, ProviderPhase.EXECUTING)

            if request.streaming:
                # EXECUTING → STREAMING (+ per-chunk model.token)
                response = advance(response, ProviderTrigger.STREAM, clock=self._clock)
                self.meter.incr(Metric.STREAMING, 1)
                self._emit(response, ProviderPhase.STREAMING)
                for chunk in response.chunks:
                    self.meter.incr(Metric.CHUNKS, 1)
                    self._publish(
                        EventType.MODEL_TOKEN,
                        {"phase": ProviderPhase.CHUNK.value, "id": response.id,
                         "index": chunk.index, "delta": chunk.delta, "done": chunk.done},
                        scope=scope,
                    )
                response = advance(response, ProviderTrigger.COMPLETE, clock=self._clock)
            else:
                # single-shot: EXECUTING → COMPLETED
                response = advance(response, ProviderTrigger.COMPLETE, clock=self._clock)

        await self._repo.add_response(response)
        await self._sessions.record_response(request.session_id, response.id, scope=scope)

        self.metrics.incr("completed")
        self.meter.incr(Metric.COMPLETED, 1)
        self.meter.incr(Metric.PROMPT_TOKENS, response.usage.prompt_tokens)
        self.meter.incr(Metric.COMPLETION_TOKENS, response.usage.completion_tokens)
        self._publish(EventType.MODEL_USAGE, self._serializer.usage_event_payload(response),
                      scope=scope)
        self._emit(response, ProviderPhase.COMPLETED)
        self._emit(response, ProviderPhase.DELIVERED)
        return ProviderExecutionResult(response=response, delivered=True)

    # ── maintenance: TTL expiry sweep (deterministic; caller-driven) ─────────
    async def expire(self, *, now: float | None = None) -> int:
        now = self._clock() if now is None else now
        count = 0
        for resp in await self._repo.all_responses():
            if resp.state is ProviderRequestState.COMPLETED and resp.is_expired(now):
                expired = advance(resp, ProviderTrigger.EXPIRE, clock=self._clock)
                await self._repo.remove_response(resp.id)
                self.metrics.incr("expired")
                self.meter.incr(Metric.EXPIRED, 1)
                self._emit(expired, ProviderPhase.EXPIRED)
                count += 1
        return count

    # ── observability ────────────────────────────────────────────────────────
    def statistics(self) -> ProviderStatistics:
        return ProviderStatistics(
            name=self.name,
            state=self.state.value,
            healthy=self.health().healthy,
            requests=run_coro_sync(self._repo.response_count()),
            sessions=run_coro_sync(self._repo.session_count()),
            provider=self._config.default_provider,
            providers=self._registry.names(),
            metrics=self.metrics.snapshot(),
        )

    # ── no content-event ingestion in the core milestone ──────────────────────
    def should_handle(self, event: Event) -> bool:
        return False

    # ── internals ──────────────────────────────────────────────────────────────
    def _select(self, request: ProviderRequest) -> str:
        """Deterministic, routing-free selection: explicit name, else the first
        capability-eligible adapter in sorted-name order. No scoring, no strategy."""
        if request.provider:
            return request.provider
        candidates = self._registry.find(request.capability)
        if not candidates:
            raise ConfigError(
                f"no provider registered for capability {request.capability!r}"
            )
        return candidates[0].name

    def _resolve(self, request: ProviderRequest) -> ProviderRequest:
        """Fill the request hash (deterministic) if the caller did not supply one."""
        if request.request_hash:
            return request
        rhash = request_hash_for(
            request.provider or "auto", request.model, request.capability, request.generation
        )
        return replace(request, request_hash=rhash)

    # ── event emission (Replay/Gateway observe these for free) ───────────────
    def _emit(self, response: ProviderResponse, phase: ProviderPhase) -> None:
        payload = self._serializer.response_event_payload(response, phase=phase.value)
        type = _PHASE_TYPE.get(phase, EventType.PROVIDER_CONNECTED)
        self._publish(type, payload, scope=response.scope)

    def _emit_lite(self, phase: ProviderPhase, request: ProviderRequest) -> None:
        self._publish(
            EventType.PROVIDER_CONNECTED,
            {"phase": phase.value, "session_id": request.session_id,
             "provider": request.provider, "capability": request.capability},
            scope=request.scope,
        )

    def _publish(
        self, type: EventType, data: dict[str, Any], *, scope: ProviderScope
    ) -> Event | None:
        return self.publish(
            type, data, session_id=scope.session_id or "", org_id=scope.org_id or "",
            conversation_id=scope.conversation_id,
        )


_PHASE_TYPE: dict[ProviderPhase, EventType] = {
    ProviderPhase.ROUTED: EventType.PROVIDER_CONNECTED,
    ProviderPhase.EXECUTING: EventType.PROVIDER_CONNECTED,
    ProviderPhase.STREAMING: EventType.PROVIDER_CONNECTED,
    ProviderPhase.COMPLETED: EventType.PROVIDER_CONNECTED,
    ProviderPhase.DELIVERED: EventType.PROVIDER_CONNECTED,
    ProviderPhase.EXPIRED: EventType.PROVIDER_DISCONNECTED,
    ProviderPhase.FAILED: EventType.PROVIDER_DISCONNECTED,
}
