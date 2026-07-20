"""ReasoningRuntime — the Reasoning Runtime core (M5.5B-1).

A `BusConsumer` (hence a `RuntimeModule`) implementing `core.ports.ReasoningPort`. It owns
inference orchestration and nothing else: it never stores memory, owns knowledge, assembles
context, constructs prompts, talks to a provider SDK, opens a socket, executes a tool, or
persists durably. The model is the injected `ModelProvider` (the deterministic `EchoProvider`
in the core); Reasoning coordinates.

What lands in M5.5B-1 (core):
- **infer** — run the deterministic model-call loop (provider select → stream → assemble),
  drive the lifecycle REQUESTED→STARTED→STREAMING→COMPLETED, persist to the temporary
  repository, and emit `reasoning.*`/`model.*` events.
- **run** — the frozen `ReasoningPort`: stream `Decision`s (token/final) for a `ReasoningRequest`.
- **get / remove / history** — read/delete a temporary response; list a session's responses.
- **expire** — deterministic TTL sweep (caller-driven). **statistics / health**.

The pipeline is async-first; the synchronous `ReasoningPort.run` iterator + `infer`/`get`/
`remove`/`history` drive it via the non-blocking sync bridge (local adapters only). Retry,
cancellation, tool coordination, scheduling, policies, and tenancy are M5.5B-2.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace
from typing import Any

from core.events import Event, EventSource, EventType
from core.module import BusConsumer
from core.ports.reasoning import Decision, DecisionKind, ReasoningPort, ReasoningRequest

from ._run import run_coro_sync
from .cancellation import CancellationManager, CancelReason
from .config import ReasoningConfiguration
from .domain import (
    ChunkKind,
    InferenceRequest,
    InferenceResponse,
    InferenceState,
    ReasoningExecutionTrace,
    ReasoningScope,
    ReasoningStatistics,
    ResponsePackage,
)
from .engine import InferenceEngine
from .events import ReasoningPhase
from .ids import request_hash_for
from .lifecycle import InferenceTrigger, advance
from .observability import Metric, PipelineStatistics
from .repositories import ReasoningRepository
from .retry import RetryController
from .serializer import ReasoningSerializer
from .session import ReasoningSessionManager
from .streaming import StreamingController
from .tenancy import ReasoningTenancy
from .tools import ToolDecisionCoordinator

try:  # tenant is a foundation package (added at M5.5B-2 for tenancy); optional at import
    from tenant import TenantContext
except Exception:  # pragma: no cover
    TenantContext = Any  # type: ignore[assignment,misc]


class ReasoningRuntime(BusConsumer, ReasoningPort):
    def __init__(
        self,
        *,
        config: ReasoningConfiguration,
        repository: ReasoningRepository,
        engine: InferenceEngine,
        sessions: ReasoningSessionManager,
        serializer: ReasoningSerializer | None = None,
        streaming: StreamingController | None = None,
        retry: RetryController | None = None,
        cancellation: CancellationManager | None = None,
        tools: ToolDecisionCoordinator | None = None,
        tenancy: ReasoningTenancy | None = None,
        id_factory: Callable[[], str] | None = None,
        **kwargs: Any,
    ) -> None:
        # Core is request-driven → subscribe to nothing (no content-event ingestion).
        super().__init__(
            name=config.name,
            source=EventSource.REASONING,
            types=frozenset(),
            id_factory=id_factory,
            **kwargs,
        )
        self._config = config
        self._repo = repository
        self._engine = engine
        self._sessions = sessions
        self._serializer = serializer or ReasoningSerializer()
        # ── M5.5B-2 intelligence (opt-in; None → the core path is unchanged) ────
        self._streaming = streaming
        self._retry = retry
        self._cancellation = cancellation
        self._tools = tools
        self._tenancy = tenancy

    # ── ReasoningPort (frozen) — stream decisions for a minimal request ───────
    def run(self, request: ReasoningRequest) -> Iterator[Decision]:
        """Stream reasoning `Decision`s (token / final). The rich path is `infer` with a full
        `InferenceRequest`; `run` builds a minimal one from the `ReasoningRequest` (the
        opaque `context_ref` carries no messages in the core) and yields its decisions."""
        inference = InferenceRequest(
            session_id=request.session_id,
            messages=(),
            provider=self._config.default_provider,
            model=request.model or self._config.default_model,
            scope=ReasoningScope(org_id=request.org_id, session_id=request.session_id),
        )
        package = self.infer(inference)
        for chunk in package.chunks:
            kind = DecisionKind.FINAL if chunk.kind is ChunkKind.FINAL else DecisionKind.TOKEN
            yield Decision(kind=kind, payload={"delta": chunk.delta, "index": chunk.index})
        if not package.chunks or package.chunks[-1].kind is not ChunkKind.FINAL:
            yield Decision(kind=DecisionKind.FINAL, payload={"text": package.text})

    # ── rich infer surface (drives the async core) ───────────────────────────
    def infer(self, request: InferenceRequest) -> ResponsePackage:
        return run_coro_sync(self.ainfer(request)).package

    def infer_scoped(
        self, request: InferenceRequest, tenant: TenantContext | None
    ) -> InferenceResponse:
        """Tenant-aware infer (isolation enforced before provider selection)."""
        return run_coro_sync(self.ainfer(request, tenant=tenant))

    def get(self, response_id: str) -> ResponsePackage | None:
        return run_coro_sync(self._repo.get_response(response_id))

    def remove(self, response_id: str) -> None:
        return run_coro_sync(self._repo.remove_response(response_id))

    def history(self, session_id: str) -> Sequence[ResponsePackage]:
        return run_coro_sync(self._repo.responses_for_session(session_id))

    # ── async core: infer ─────────────────────────────────────────────────────
    async def ainfer(
        self, request: InferenceRequest, *, tenant: TenantContext | None = None
    ) -> InferenceResponse:
        now = self._clock()
        request = self._resolve(request)
        scope = request.scope

        # 0. isolation FIRST — before provider selection (M5.5B-2; guarded)
        if self._tenancy is not None:
            scope = self._tenancy.guard(scope, tenant, provider=request.provider).scope
            request = replace(request, scope=scope)

        seq = len(await self._repo.responses_for_session(request.session_id))
        expires_at = (now + self._config.package_ttl_s) if self._config.package_ttl_s else None

        # 0b. cancellation short-circuit — never even select a provider (M5.5B-2)
        if self._cancellation is not None and self._cancellation.is_cancelled(
            request.cancellation_token, session_id=request.session_id
        ):
            return await self._cancel_before_start(request, scope, now)

        trace = ReasoningExecutionTrace() if self._is_intelligent else None
        with self.meter.timer(Metric.INFER_LATENCY):
            self._emit_lite(ReasoningPhase.REQUESTED, request)
            try:
                if trace is not None:
                    trace = trace.with_event("provider_selection", detail=request.provider, at=now)
                    self.meter.incr(Metric.PROVIDER_SELECTED, 1)
                package = await self._run_engine(
                    request, session_id=request.session_id, scope=scope, now=now,
                    seq=seq, expires_at=expires_at,
                )
            except Exception as exc:  # noqa: BLE001 — surface as a FAILED event, then re-raise
                self.metrics.incr("failed")
                self.meter.incr(Metric.FAILED, 1)
                self._publish(EventType.REASONING_FAILED,
                              {"phase": ReasoningPhase.FAILED.value,
                               "session_id": request.session_id, "error": str(exc)},
                              scope=scope)
                raise

            # REQUESTED → STARTED
            package = advance(package, InferenceTrigger.START, clock=self._clock)
            self.metrics.incr("started")
            self.meter.incr(Metric.STARTED, 1)
            self._emit(package, ReasoningPhase.STARTED)

            if request.streaming:
                # STARTED → STREAMING (+ per-chunk model.token)
                package = advance(package, InferenceTrigger.STREAM, clock=self._clock)
                self.meter.incr(Metric.STREAMING, 1)
                self._emit(package, ReasoningPhase.STREAMING)
                # backpressure orchestration (M5.5B-2; guarded, deterministic)
                if self._streaming is not None:
                    session = self._streaming.run(package.chunks)
                    if session.backpressure_events:
                        self.meter.incr(Metric.BACKPRESSURE, session.backpressure_events)
                    if session.timed_out:
                        self.meter.incr(Metric.STREAM_TIMEOUT, 1)
                    if trace is not None:
                        trace = trace.with_event("streaming",
                                                 detail=f"chunks={session.metadata.chunk_count}",
                                                 at=now)
                for chunk in package.chunks:
                    self.meter.incr(Metric.CHUNKS, 1)
                    if trace is not None:
                        trace = trace.with_event("chunk", detail=str(chunk.index), at=now)
                    self._publish(
                        EventType.MODEL_TOKEN,
                        {"phase": ReasoningPhase.CHUNK.value, "id": package.id,
                         "index": chunk.index, "delta": chunk.delta, "done": chunk.done},
                        scope=scope,
                    )
                package = advance(package, InferenceTrigger.COMPLETE, clock=self._clock)
            else:
                # single-shot: STARTED → COMPLETED
                package = advance(package, InferenceTrigger.COMPLETE, clock=self._clock)

            # tool coordination (M5.5B-2; only if a coordinator + tool-call chunks present)
            if self._tools is not None:
                calls = self._tools.parse_calls(package.chunks)
                if calls:
                    coordination = await self._tools.coordinate(calls, now=now)
                    self.meter.incr(Metric.TOOL_CALLS, len(calls))
                    self.meter.incr(Metric.TOOL_REJECTED, coordination.rejected)
                    package = self._record_tools(package, coordination)
                    if trace is not None:
                        trace = trace.with_event("tool_decision",
                                                 detail=f"calls={len(calls)}", at=now)

            if trace is not None:
                trace = trace.with_event("completion", detail=package.response_hash, at=now)
                package = self._attach_trace(package, trace)

        await self._repo.add_response(package)
        await self._sessions.record_response(request.session_id, package.id, scope=scope)

        self.metrics.incr("completed")
        self.meter.incr(Metric.COMPLETED, 1)
        self.meter.incr(Metric.PROMPT_TOKENS, package.usage.prompt_tokens)
        self.meter.incr(Metric.COMPLETION_TOKENS, package.usage.completion_tokens)
        self._publish(EventType.MODEL_USAGE, self._serializer.usage_event_payload(package),
                      scope=scope)
        self._emit(package, ReasoningPhase.COMPLETED)
        self._emit(package, ReasoningPhase.DELIVERED)
        return InferenceResponse(package=package, delivered=True)

    # ── maintenance: TTL expiry sweep (deterministic; caller-driven) ─────────
    async def expire(self, *, now: float | None = None) -> int:
        now = self._clock() if now is None else now
        count = 0
        for pkg in await self._repo.all_responses():
            if pkg.state is InferenceState.COMPLETED and pkg.is_expired(now):
                expired = advance(pkg, InferenceTrigger.EXPIRE, clock=self._clock)
                await self._repo.remove_response(pkg.id)
                self.metrics.incr("expired")
                self.meter.incr(Metric.EXPIRED, 1)
                self._emit(expired, ReasoningPhase.EXPIRED)
                count += 1
        return count

    # ── cancellation (M5.5B-2; public signalling) ────────────────────────────
    def cancel(self, token: str, *, reason: str = "manual", now: float | None = None) -> None:
        """Signal cancellation for an in-flight inference token (always terminal for that
        token). No-op if no cancellation manager is wired."""
        if self._cancellation is None:
            return
        self._cancellation.cancel(token, CancelReason(reason), now=now or self._clock())

    def cancel_session(self, session_id: str, *, now: float | None = None) -> None:
        if self._cancellation is None:
            return
        self._cancellation.cancel_session(session_id, now=now or self._clock())

    def pipeline_statistics(self, package: ResponsePackage) -> PipelineStatistics:
        """A per-inference intelligence snapshot (tracing/replay) for a finished package."""
        ln = package.lineage
        return PipelineStatistics(
            provider=package.provider, model=package.model, chunks=len(package.chunks),
            used_tokens=package.usage.total_tokens, retries=len(ln.retry_history),
            tool_calls=len(ln.tool_history), cancelled=package.cancellation is not None,
            backpressure_events=0, trace_events=len(ln.execution_trace.events),
        )

    # ── observability ────────────────────────────────────────────────────────
    def statistics(self) -> ReasoningStatistics:
        return ReasoningStatistics(
            name=self.name,
            state=self.state.value,
            healthy=self.health().healthy,
            responses=run_coro_sync(self._repo.response_count()),
            sessions=run_coro_sync(self._repo.session_count()),
            provider=self._config.default_provider,
            providers=self._engine.providers,
            metrics=self.metrics.snapshot(),
        )

    # ── no content-event ingestion in the core milestone ──────────────────────
    def should_handle(self, event: Event) -> bool:
        return False

    # ── internals ──────────────────────────────────────────────────────────────
    @property
    def _is_intelligent(self) -> bool:
        return any((self._streaming, self._retry, self._cancellation,
                    self._tools, self._tenancy))

    async def _run_engine(self, request: InferenceRequest, **kw: Any) -> ResponsePackage:
        """Run the engine, wrapped in the retry controller when one is injected (deterministic
        backoff; the controller never networks). Core path (no retry) calls the engine once."""
        if self._retry is None:
            return await self._engine.infer(request, **kw)

        async def _attempt(attempt: int) -> ResponsePackage:
            return await self._engine.infer(request, **kw)

        package, outcome = await self._retry.run(_attempt, kind="provider", now=kw.get("now", 0.0))
        if outcome.history:
            self.meter.incr(Metric.RETRIES, len(outcome.history))
            package = replace(package,
                              lineage=replace(package.lineage, retry_history=outcome.history))
        return package

    async def _cancel_before_start(
        self, request: InferenceRequest, scope: ReasoningScope, now: float
    ) -> InferenceResponse:
        """A token already cancelled before the loop started → build a terminal CANCELLED
        package deterministically (provider never selected)."""
        seq = len(await self._repo.responses_for_session(request.session_id))
        package = await self._engine.empty(request, session_id=request.session_id,
                                           scope=scope, now=now, seq=seq)
        signal = self._cancellation.signal_for(request.cancellation_token) or \
            self._cancellation.cancel(request.cancellation_token or f"session:{request.session_id}",
                                      CancelReason.SESSION, now=now)
        package = self._cancellation.apply(package, signal, clock=self._clock)
        await self._repo.add_response(package)
        self.metrics.incr("cancelled")
        self.meter.incr(Metric.CANCELLED, 1)
        self.meter.incr(Metric.CANCELLATIONS, 1)
        self._emit(package, ReasoningPhase.CANCELLED)
        return InferenceResponse(package=package, delivered=False, reason=signal.reason.value)

    def _record_tools(self, package: ResponsePackage, coordination: Any) -> ResponsePackage:
        history = tuple({"call_id": r.call_id, "name": r.name, "decision": r.decision,
                         "ok": str(r.ok), "detail": r.detail} for r in coordination.history)
        decisions = tuple({"call_id": r.call_id, "name": r.name, "decision": r.decision}
                          for r in coordination.history)
        return replace(package, tool_decisions=decisions,
                       lineage=replace(package.lineage, tool_history=history))

    def _attach_trace(
        self, package: ResponsePackage, trace: ReasoningExecutionTrace
    ) -> ResponsePackage:
        return replace(package, lineage=replace(package.lineage, execution_trace=trace))

    def _resolve(self, request: InferenceRequest) -> InferenceRequest:
        """Fill the request hash (deterministic) if the caller did not supply one."""
        if request.request_hash:
            return request
        rhash = request_hash_for(
            request.provider, request.model, request.prompt_hash, request.generation
        )
        return replace(request, request_hash=rhash)

    # ── event emission (Replay/Gateway observe these for free) ───────────────
    def _emit(self, package: ResponsePackage, phase: ReasoningPhase) -> None:
        payload = self._serializer.package_event_payload(package, phase=phase.value)
        type = _PHASE_TYPE.get(phase, EventType.REASONING_COMPLETED)
        self._publish(type, payload, scope=package.scope)

    def _emit_lite(self, phase: ReasoningPhase, request: InferenceRequest) -> None:
        self._publish(
            EventType.REASONING_STARTED,
            {"phase": phase.value, "session_id": request.session_id,
             "provider": request.provider, "model": request.model},
            scope=request.scope,
        )

    def _publish(
        self, type: EventType, data: dict[str, Any], *, scope: ReasoningScope
    ) -> Event | None:
        return self.publish(
            type, data, session_id=scope.session_id or "", org_id=scope.org_id or "",
            conversation_id=scope.conversation_id,
        )


_PHASE_TYPE: dict[ReasoningPhase, EventType] = {
    ReasoningPhase.STARTED: EventType.REASONING_STARTED,
    ReasoningPhase.STREAMING: EventType.REASONING_STARTED,
    ReasoningPhase.COMPLETED: EventType.REASONING_COMPLETED,
    ReasoningPhase.DELIVERED: EventType.REASONING_COMPLETED,
    ReasoningPhase.EXPIRED: EventType.REASONING_COMPLETED,
    ReasoningPhase.FAILED: EventType.REASONING_FAILED,
    ReasoningPhase.CANCELLED: EventType.REASONING_FAILED,
}
