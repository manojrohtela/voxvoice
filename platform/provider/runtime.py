"""ProviderRuntime — the Provider Runtime core (M5.6B-1) + intelligence (M5.6B-2).

A `BusConsumer` that owns **registry-based execution** (core) plus, when intelligence is
enabled, deterministic routing/failover/health/circuit-breaking/policy/tenancy. It receives a
`ProviderRequest`, resolves the adapter — the M5.6B-1 core path (explicit name, or the first
capability-eligible adapter in deterministic sorted-name order — **never a scored/heuristic
decision**) is byte-for-byte unchanged when intelligence is off — executes it through the
injected `AdapterPort`, assembles the unified `ProviderResponse`, emits `provider.*`/`model.*`
events, and persists to the **temporary** repository.

What lands in M5.6B-1 (core):
- **execute** — resolve → stream → normalize, drive the lifecycle
  REQUESTED→ROUTED→EXECUTING→STREAMING→COMPLETED, persist, emit events.
- **get / remove / history** — read/delete a temporary response; list a session's responses.
- **expire** — deterministic TTL sweep (caller-driven). **statistics / health**.
- **registry / catalog** — read-only exposure for discovery (no routing intelligence).

What lands in M5.6B-2 (intelligence, opt-in via injected members — `None` → core path):
- **Routing** — `ProviderRouter` produces a deterministic ordered candidate chain.
- **Failover** — `FailoverController` walks the chain on a candidate's failure/exclusion; no
  retry loop against the same candidate, no sleeping.
- **Health** — `ProviderHealthManager` records outcomes, driving the registry's health FSM.
- **Circuit breaking** — `CircuitBreaker` gates a candidate before it is tried.
- **Tenancy** — isolation before provider selection (mirrors Memory/Knowledge/Context/Prompt/
  Reasoning's `tenant`-seam pattern).
- **Execution trace** — metadata-only, attached to the response; never changes behaviour.

The pipeline is async-first; the synchronous `execute`/`get`/`remove`/`history` drive it via
the non-blocking sync bridge (local adapters only).
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
from .circuit_breaker import CircuitBreaker
from .config import ProviderConfiguration
from .domain import (
    ProviderExecutionResult,
    ProviderExecutionTrace,
    ProviderRequest,
    ProviderRequestState,
    ProviderResponse,
    ProviderScope,
    ProviderStatistics,
)
from .events import ProviderPhase
from .failover import FailoverController, FailoverReason
from .health import ProviderHealthManager
from .ids import request_hash_for
from .lifecycle import ProviderTrigger, advance
from .observability import Metric, PipelineStatistics
from .policies import ProviderPolicyEngine
from .registry import ProviderRegistry
from .repositories import ProviderRepository
from .response import ResponseNormalizer
from .router import ProviderRouter
from .serializer import ProviderSerializer
from .session import ProviderSessionManager
from .tenancy import ProviderTenancy

try:  # tenant is a foundation package (added at M5.6B-2 for tenancy); optional at import
    from tenant import TenantContext
except Exception:  # pragma: no cover
    TenantContext = Any  # type: ignore[assignment,misc]


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
        router: ProviderRouter | None = None,
        health: ProviderHealthManager | None = None,
        failover: FailoverController | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        policy: ProviderPolicyEngine | None = None,
        tenancy: ProviderTenancy | None = None,
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
        # ── M5.6B-2 intelligence (opt-in; None → the core path is unchanged) ────
        self._router = router
        self._health = health
        self._failover = failover
        self._circuit_breaker = circuit_breaker
        self._policy = policy
        self._tenancy = tenancy

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

    def execute_scoped(
        self, request: ProviderRequest, tenant: TenantContext | None
    ) -> ProviderExecutionResult:
        """Tenant-aware execute (isolation enforced before provider selection)."""
        return run_coro_sync(self.aexecute(request, tenant=tenant))

    def get(self, response_id: str) -> ProviderResponse | None:
        return run_coro_sync(self._repo.get_response(response_id))

    def remove(self, response_id: str) -> None:
        return run_coro_sync(self._repo.remove_response(response_id))

    def history(self, session_id: str) -> Sequence[ProviderResponse]:
        return run_coro_sync(self._repo.responses_for_session(session_id))

    # ── async core: execute ────────────────────────────────────────────────────
    async def aexecute(
        self, request: ProviderRequest, *, tenant: TenantContext | None = None
    ) -> ProviderExecutionResult:
        now = self._clock()
        request = self._resolve(request)
        scope = request.scope

        # 0. isolation FIRST — before provider selection (M5.6B-2; guarded)
        if self._tenancy is not None:
            scope = self._tenancy.guard(scope, tenant, provider=request.provider).scope
            request = replace(request, scope=scope)

        seq = len(await self._repo.responses_for_session(request.session_id))
        expires_at = (now + self._config.response_ttl_s) if self._config.response_ttl_s else None

        trace = ProviderExecutionTrace() if self._is_intelligent else None
        with self.meter.timer(Metric.REQUEST_LATENCY):
            self._emit_lite(ProviderPhase.REQUESTED, request)
            try:
                name, chunks, request, trace = await self._resolve_and_stream(request, now, trace)
                self.meter.incr(Metric.PROVIDER_SELECTED, 1)
                request_id = f"{name}:{request.request_hash or seq}"
                streamed = request.streaming
                if not streamed:
                    chunks = [self._normalizer.collapse(request, chunks)]
                response = self._normalizer.normalize(
                    request, chunks, session_id=request.session_id, scope=scope, now=now,
                    seq=seq, streamed=streamed, expires_at=expires_at, request_id=request_id,
                )
                response = response.with_state(ProviderRequestState.REQUESTED)
                if trace is not None:
                    trace = trace.with_event("completion", detail=name, at=now)
                    response = replace(response, trace=trace)
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

    def pipeline_statistics(self, response: ProviderResponse) -> PipelineStatistics:
        """A per-execution intelligence snapshot (tracing/replay) for a finished response."""
        circuit_state = (
            self._circuit_breaker.state_for(response.provider).value
            if self._circuit_breaker is not None else "closed"
        )
        return PipelineStatistics(
            provider=response.provider, model=response.model, chunks=len(response.chunks),
            used_tokens=response.usage.total_tokens,
            failovers=self._failover.statistics["failovers"] if self._failover else 0,
            circuit_state=circuit_state,
            trace_events=len(response.trace.events),
        )

    # ── no content-event ingestion in the core milestone ──────────────────────
    def should_handle(self, event: Event) -> bool:
        return False

    # ── internals ──────────────────────────────────────────────────────────────
    @property
    def _is_intelligent(self) -> bool:
        return any((self._router, self._health, self._failover,
                   self._circuit_breaker, self._tenancy))

    async def _resolve_and_stream(
        self, request: ProviderRequest, now: float, trace: ProviderExecutionTrace | None,
    ) -> tuple[str, list, ProviderRequest, ProviderExecutionTrace | None]:
        """Resolve a provider and pull its chunk stream. The M5.6B-1 core path (no router) is
        unchanged: explicit name, else the first capability-eligible adapter in sorted-name
        order — never scored. The M5.6B-2 path routes, then walks the deterministic failover
        chain, consulting the circuit breaker and recording health outcomes; no retry loop
        against the same candidate, no sleeping."""
        if self._router is None:
            name = self._select_core(request)
            adapter = self._registry.get(name)
            req = replace(request, provider=name)
            return name, list(adapter.stream(req)), req, trace

        decision = self._router.route(request)
        if trace is not None:
            trace = trace.with_event(
                "routing_decision",
                detail=f"{decision.strategy.value}:{','.join(decision.candidates)}", at=now,
            )
        if not decision.candidates:
            raise ConfigError(f"no provider available for capability {request.capability!r}")

        attempted: tuple[str, ...] = ()
        last_exc: Exception | None = None
        while True:
            outcome = (self._failover.next(decision, attempted=attempted,
                                           reason=FailoverReason.UNAVAILABLE)
                      if self._failover is not None
                      else _default_next(decision, attempted))
            if outcome.exhausted:
                if trace is not None:
                    trace = trace.with_event("failover", detail="exhausted", at=now)
                if last_exc is not None:
                    raise last_exc
                raise ConfigError(f"no available provider for capability {request.capability!r}")
            name = outcome.candidate
            attempted = outcome.attempted

            if self._policy is not None and trace is not None:
                verdict = self._policy.admit_provider(name)
                trace = trace.with_event("policy_verdict", detail=f"{name}:{verdict.ok}", at=now)
            if self._circuit_breaker is not None and not self._circuit_breaker.allow(name, now=now):
                if trace is not None:
                    trace = trace.with_event("failover", detail=f"circuit_open:{name}", at=now)
                continue
            try:
                adapter = self._registry.get(name)
                req = replace(request, provider=name)
                chunks = list(adapter.stream(req))
            except Exception as exc:  # noqa: BLE001 — recorded, then failed over
                last_exc = exc
                if self._health is not None:
                    self._health.record_failure(name)
                if self._circuit_breaker is not None:
                    self._circuit_breaker.record_failure(name, now=now)
                if trace is not None:
                    trace = trace.with_event("health_change", detail=f"failure:{name}", at=now)
                continue

            if self._health is not None:
                self._health.record_success(name)
            if self._circuit_breaker is not None:
                self._circuit_breaker.record_success(name)
            if trace is not None:
                trace = trace.with_event("provider_selection", detail=name, at=now)
                trace = trace.with_event("execution", detail=name, at=now)
            return name, chunks, req, trace

    def _select_core(self, request: ProviderRequest) -> str:
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


def _default_next(decision, attempted: tuple[str, ...]):
    """A stateless fallback identical to `FailoverController.next` for the (uncommon) case a
    router is injected without an explicit `FailoverController` — factory-built runtimes
    always wire both together."""
    from .failover import FailoverOutcome

    tried = set(attempted)
    remaining = [c for c in decision.candidates if c not in tried]
    if not remaining:
        return FailoverOutcome(candidate=None, exhausted=True, attempted=attempted)
    return FailoverOutcome(candidate=remaining[0], exhausted=False,
                           attempted=(*attempted, remaining[0]))


_PHASE_TYPE: dict[ProviderPhase, EventType] = {
    ProviderPhase.ROUTED: EventType.PROVIDER_CONNECTED,
    ProviderPhase.EXECUTING: EventType.PROVIDER_CONNECTED,
    ProviderPhase.STREAMING: EventType.PROVIDER_CONNECTED,
    ProviderPhase.COMPLETED: EventType.PROVIDER_CONNECTED,
    ProviderPhase.DELIVERED: EventType.PROVIDER_CONNECTED,
    ProviderPhase.EXPIRED: EventType.PROVIDER_DISCONNECTED,
    ProviderPhase.FAILED: EventType.PROVIDER_DISCONNECTED,
}
