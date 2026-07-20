"""ContextRuntime — the Context Runtime core (M5.3B-1).

A `BusConsumer` (hence a `RuntimeModule`) implementing `core.ports.ContextPort`. It owns
context assembly and nothing else: it never stores memory, owns knowledge, renders prompts,
reasons, calls tools, or talks to an LLM. It consumes every source through an injected port
(wrapped as a `ContextSource`), so it imports no sibling runtime.

What lands in M5.3B-1 (core):
- **assemble** — run the deterministic pipeline (select → fetch → merge → order → naive
  budget), drive the package lifecycle REQUESTED→ASSEMBLED→VALIDATED→DELIVERED, persist to
  the temporary repository, update the session window/history, and emit `context.*` events.
- **get / remove / history** — read/delete a temporary package; list a session's packages.
- **expire** — deterministic TTL sweep (caller-driven).
- **statistics / health**.

The pipeline is async-first (design decision); the synchronous `ContextPort` +
`get/remove/history` drive it via the non-blocking sync bridge (local adapters only).
Publishing standard envelopes means Replay records and the Gateway streams them for free.
Budget/ranking/compression/validation/policy/tenancy **intelligence is M5.3B-2** — the core
validation step is a trivial always-pass so the lifecycle + events are correct now.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from core.events import Event, EventSource, EventType
from core.module import BusConsumer
from core.ports.context import AssembledContext, ContextPort, ContextRequest
from core.ports.context import ContextItem as PortItem

from ._run import run_coro_sync
from .assembly import ContextAssembler
from .config import ContextConfiguration
from .domain import ContextKind, ContextPackage, ContextScope, PackageState
from .events import ContextPhase
from .ids import package_id_for, request_hash_for
from .lifecycle import PackageTrigger, advance_package
from .observability import ContextStatistics, Metric
from .ports import AssemblyRequest
from .repositories import ContextRepository
from .serializer import ContextSerializer
from .session import ContextSessionManager


class ContextRuntime(BusConsumer, ContextPort):
    def __init__(
        self,
        *,
        config: ContextConfiguration,
        repository: ContextRepository,
        assembler: ContextAssembler,
        sessions: ContextSessionManager,
        serializer: ContextSerializer | None = None,
        id_factory: Callable[[], str] | None = None,
        **kwargs: Any,
    ) -> None:
        # Core is request-driven → subscribe to nothing (optional ingestion is M5.3B-2).
        super().__init__(
            name=config.name,
            source=EventSource.CONTEXT,
            types=frozenset(),
            id_factory=id_factory,
            **kwargs,
        )
        self._config = config
        self._repo = repository
        self._assembler = assembler
        self._sessions = sessions
        self._serializer = serializer or ContextSerializer()

    # ── ContextPort (synchronous) — drive the async core deterministically ────
    def assemble(self, request: ContextRequest) -> AssembledContext:
        response = run_coro_sync(self.aassemble(request))
        return _to_assembled(request, response.package)

    def get(self, package_id: str) -> ContextPackage | None:
        return run_coro_sync(self._repo.get_package(package_id))

    def remove(self, package_id: str) -> None:
        return run_coro_sync(self._repo.remove_package(package_id))

    def history(self, session_id: str) -> Sequence[ContextPackage]:
        return run_coro_sync(self._repo.packages_for_session(session_id))

    # ── async core: assemble ──────────────────────────────────────────────────
    async def aassemble(self, request: ContextRequest):
        from .domain import ContextResponse

        now = self._clock()
        scope = ContextScope(
            org_id=request.org_id,
            conversation_id=request.conversation_id,
            session_id=request.session_id,
        )
        budget = request.budget_tokens or self._config.default_budget_tokens
        rhash = request_hash_for(
            request.session_id, request.org_id, request.conversation_id, request.query,
            budget, request.include_memory, request.include_knowledge,
        )
        seq = len(await self._repo.packages_for_session(request.session_id))
        package_id = package_id_for(request.session_id, rhash, seq)
        expires_at = (now + self._config.package_ttl_s) if self._config.package_ttl_s else None

        areq = AssemblyRequest(
            session_id=request.session_id,
            scope=scope,
            query=request.query,
            budget_tokens=budget,
            include_memory=request.include_memory,
            include_knowledge=request.include_knowledge,
            limit=self._config.per_source_limit,
        )

        with self.meter.timer(Metric.ASSEMBLY_LATENCY):
            # REQUESTED
            self._emit_phase_lite(ContextPhase.REQUESTED, package_id, request, len_items=0)
            package = await self._assembler.assemble(
                areq, package_id=package_id, scope=scope, now=now,
                budget_tokens=budget, expires_at=expires_at,
            )
            # ASSEMBLED
            package = advance_package(package, PackageTrigger.ASSEMBLE, clock=self._clock)
            self.metrics.incr("assembled")
            self.meter.incr(Metric.ASSEMBLED, 1)
            self.meter.incr(Metric.ITEMS, len(package.items))
            self._emit_package(package, ContextPhase.BUILT)
            # VALIDATED (trivial pass in the core; the ValidationEngine is M5.3B-2)
            package = advance_package(package, PackageTrigger.VALIDATE, clock=self._clock)
            self._emit_package(package, ContextPhase.VALIDATED)
            # DELIVERED
            package = advance_package(package, PackageTrigger.DELIVER, clock=self._clock)

        await self._repo.add_package(package)
        await self._sessions.record_package(package)
        turn_items = package.section(ContextKind.USER_INSTRUCTION)
        if turn_items:
            await self._sessions.push_to_window(request.session_id, turn_items, scope=scope)

        self.metrics.incr("delivered")
        self.meter.incr(Metric.DELIVERED, 1)
        self.meter.incr(Metric.USED_TOKENS, package.used_tokens)
        self._emit_package(package, ContextPhase.DELIVERED)
        if package.truncated:
            self.meter.incr(Metric.TRUNCATED, 1)
            self._emit_truncated(package, ContextPhase.COMPRESSED)
        return ContextResponse(package=package, delivered=True)

    # ── maintenance: TTL expiry sweep (deterministic; caller-driven) ─────────
    async def expire(self, *, now: float | None = None) -> int:
        """Expire TTL-elapsed delivered packages. Deterministic — no background timer;
        the caller (or a scheduler) invokes it. Returns the count expired."""
        now = self._clock() if now is None else now
        count = 0
        for pkg in await self._repo.all_packages():
            if pkg.state is PackageState.DELIVERED and pkg.is_expired(now):
                expired = advance_package(pkg, PackageTrigger.EXPIRE, clock=self._clock)
                await self._repo.remove_package(pkg.id)
                self.metrics.incr("expired")
                self.meter.incr(Metric.EXPIRED, 1)
                self._emit_truncated(expired, ContextPhase.EXPIRED)
                count += 1
        return count

    # ── observability ────────────────────────────────────────────────────────
    def statistics(self) -> ContextStatistics:
        return ContextStatistics(
            name=self.name,
            state=self.state.value,
            healthy=self.health().healthy,
            packages=run_coro_sync(self._repo.package_count()),
            sessions=run_coro_sync(self._repo.session_count()),
            sources=tuple(s.name for s in self._assembler.sources),
            metrics=self.metrics.snapshot(),
        )

    # ── no content-event ingestion in the core milestone ──────────────────────
    def should_handle(self, event: Event) -> bool:
        return False

    # ── event emission (Replay/Gateway observe these for free) ───────────────
    def _emit_package(self, package: ContextPackage, phase: ContextPhase) -> None:
        payload = self._serializer.package_event_payload(package, phase=phase.value)
        self._publish(EventType.CONTEXT_ASSEMBLED, payload, package=package)

    def _emit_truncated(self, package: ContextPackage, phase: ContextPhase) -> None:
        payload = self._serializer.package_event_payload(package, phase=phase.value)
        self._publish(EventType.CONTEXT_TRUNCATED, payload, package=package)

    def _emit_phase_lite(
        self, phase: ContextPhase, package_id: str, request: ContextRequest, *, len_items: int
    ) -> None:
        self.publish(
            EventType.CONTEXT_ASSEMBLED,
            {"phase": phase.value, "id": package_id, "session_id": request.session_id,
             "items": len_items},
            session_id=request.session_id,
            org_id=request.org_id or "",
            conversation_id=request.conversation_id,
        )

    def _publish(
        self, type: EventType, data: dict[str, Any], *, package: ContextPackage
    ) -> Event | None:
        return self.publish(
            type, data,
            session_id=package.session_id,
            org_id=package.org_id or "",
            conversation_id=package.conversation_id,
        )


def _to_assembled(request: ContextRequest, package: ContextPackage) -> AssembledContext:
    """Narrow the rich internal `ContextPackage` to the frozen `core.ports.AssembledContext`
    (the port contract). Full provenance rides along in each item's `provenance`."""
    items = tuple(
        PortItem(
            kind=i.kind.value,
            text=i.text,
            score=i.confidence,
            provenance={
                "source_runtime": i.provenance.source_runtime.value,
                "source_id": i.provenance.source_id,
                "original_score": i.provenance.original_score,
                "content_hash": i.provenance.content_hash,
                "confidence_source": i.confidence_source,
                "order_index": i.order_index,
                "token_estimate": i.token_estimate,
            },
        )
        for i in package.items
    )
    return AssembledContext(
        request=request, items=items, truncated=package.truncated, used_tokens=package.used_tokens
    )
