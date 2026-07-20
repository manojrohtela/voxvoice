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
from .config import ReasoningConfiguration
from .domain import (
    ChunkKind,
    InferenceRequest,
    InferenceResponse,
    InferenceState,
    ReasoningScope,
    ReasoningStatistics,
    ResponsePackage,
)
from .engine import InferenceEngine
from .events import ReasoningPhase
from .ids import request_hash_for
from .lifecycle import InferenceTrigger, advance
from .observability import Metric
from .repositories import ReasoningRepository
from .serializer import ReasoningSerializer
from .session import ReasoningSessionManager


class ReasoningRuntime(BusConsumer, ReasoningPort):
    def __init__(
        self,
        *,
        config: ReasoningConfiguration,
        repository: ReasoningRepository,
        engine: InferenceEngine,
        sessions: ReasoningSessionManager,
        serializer: ReasoningSerializer | None = None,
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

    def get(self, response_id: str) -> ResponsePackage | None:
        return run_coro_sync(self._repo.get_response(response_id))

    def remove(self, response_id: str) -> None:
        return run_coro_sync(self._repo.remove_response(response_id))

    def history(self, session_id: str) -> Sequence[ResponsePackage]:
        return run_coro_sync(self._repo.responses_for_session(session_id))

    # ── async core: infer ─────────────────────────────────────────────────────
    async def ainfer(self, request: InferenceRequest) -> InferenceResponse:
        now = self._clock()
        request = self._resolve(request)
        scope = request.scope
        seq = len(await self._repo.responses_for_session(request.session_id))
        expires_at = (now + self._config.package_ttl_s) if self._config.package_ttl_s else None

        with self.meter.timer(Metric.INFER_LATENCY):
            self._emit_lite(ReasoningPhase.REQUESTED, request)
            try:
                package = await self._engine.infer(
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
                for chunk in package.chunks:
                    self.meter.incr(Metric.CHUNKS, 1)
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
