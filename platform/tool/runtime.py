"""ToolRuntime — the Tool Runtime core (M6.1B-1).

A `BusConsumer` that owns **registry-based execution only**. It receives a
`ToolRequest`, resolves the descriptor (explicit version, or the highest ACTIVE
version — never a scored/heuristic decision), validates the arguments against the
resolved manifest, dispatches to the injected `ToolAdapter`, normalizes the unified
`ToolResult`, emits `tool.*` events, and persists to the **temporary** repository.

What lands in M6.1B-1: **execute** (validate → dispatch → normalize, driving the
lifecycle RECEIVED→VALIDATING→EXECUTING→COMPLETED/FAILED), **get / remove /
history**, **expire** (deterministic TTL sweep), **statistics**, and read-only
**registry / catalog** exposure (no policy, no approval, no secrets, no streaming —
all explicit non-goals here; M6.1B-2+).

Literal conformance to the frozen `core.ports.tool.ToolPort` (register/list/invoke)
is deliberately **not** built in this milestone — mirroring the Provider Runtime
core precedent of not building `ProviderModelBridge` at M5.6B-1 (DL-0038). This
runtime's own `register`/`list`/`execute` already cover the same semantics; a thin
`ToolPort` conformance shim is additive, deferred work with no redesign implied.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from core.errors import ConfigError
from core.events import Event, EventSource, EventType
from core.module import BusConsumer

from ._run import run_coro_sync
from .catalog import ToolCapabilityCatalog
from .config import ToolConfiguration
from .domain import (
    Classification,
    DescriptorState,
    ExecutionState,
    ToolDescriptor,
    ToolManifest,
    ToolRequest,
    ToolResult,
    ToolStatistics,
)
from .events import ToolPhase
from .ids import request_hash_for
from .lifecycle import ExecutionTrigger, advance_execution
from .observability import Metric
from .ports import ToolAdapter
from .registry import ToolRegistry
from .repositories import ToolRepository
from .results import ResultNormalizer
from .serializer import ToolSerializer
from .validation import validate_arguments


class ToolRuntime(BusConsumer):
    def __init__(
        self,
        *,
        config: ToolConfiguration,
        repository: ToolRepository,
        registry: ToolRegistry,
        normalizer: ResultNormalizer,
        serializer: ToolSerializer | None = None,
        id_factory: Callable[[], str] | None = None,
        **kwargs: Any,
    ) -> None:
        # Core is request-driven → subscribe to nothing (no content-event ingestion).
        super().__init__(
            name=config.name, source=EventSource.TOOL, types=frozenset(),
            id_factory=id_factory, **kwargs,
        )
        self._config = config
        self._repo = repository
        self._registry = registry
        self._catalog = ToolCapabilityCatalog(registry)
        self._normalizer = normalizer
        self._serializer = serializer or ToolSerializer()

    # ── read-only discovery exposure ──────────────────────────────────────────
    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    @property
    def catalog(self) -> ToolCapabilityCatalog:
        return self._catalog

    # ── registration + descriptor lifecycle (synchronous; no store I/O) ──────
    def register(self, manifest: ToolManifest, adapter: ToolAdapter) -> ToolDescriptor:
        return self._registry.register(manifest, adapter)

    def activate(self, namespace: str, name: str, version: str) -> ToolDescriptor:
        return self._registry.activate(namespace, name, version)

    def deprecate(self, namespace: str, name: str, version: str) -> ToolDescriptor:
        return self._registry.deprecate(namespace, name, version)

    def retire(self, namespace: str, name: str, version: str) -> ToolDescriptor:
        return self._registry.retire(namespace, name, version)

    def list(self, *, include_deprecated: bool = False) -> tuple[ToolDescriptor, ...]:
        return self._registry.list(include_deprecated=include_deprecated)

    def list_scoped(
        self, *, namespace: str | None = None, capability: str | None = None,
        include_deprecated: bool = False,
    ) -> tuple[ToolDescriptor, ...]:
        return self._registry.list_scoped(
            namespace=namespace, capability=capability, include_deprecated=include_deprecated
        )

    # ── rich execute surface (drives the async core) ─────────────────────────
    def execute(self, request: ToolRequest) -> ToolResult:
        return run_coro_sync(self.aexecute(request))

    def get(self, request_id: str) -> ToolResult | None:
        return run_coro_sync(self._repo.get_result(request_id))

    def remove(self, request_id: str) -> None:
        return run_coro_sync(self._repo.remove_result(request_id))

    def history(self, session_id: str) -> Sequence[ToolResult]:
        return run_coro_sync(self._repo.results_for_session(session_id))

    # ── async core: execute ────────────────────────────────────────────────────
    async def aexecute(self, request: ToolRequest) -> ToolResult:
        now = self._clock()
        request = self._resolve(request)
        expires_at = (now + self._config.result_ttl_s) if self._config.result_ttl_s else None

        result = ToolResult(
            request_id=request.request_id, tool=request.tool, namespace=request.namespace,
            version=request.version or "", request_hash=request.request_hash,
            created_at=now, session_id=request.session_id, org_id=request.org_id,
        )
        self.metrics.incr("received")
        self.meter.incr(Metric.RECEIVED, 1)
        self._emit(result, ToolPhase.RECEIVED)

        with self.meter.timer(Metric.REQUEST_LATENCY):
            result = advance_execution(result, ExecutionTrigger.VALIDATE, clock=self._clock)
            self.meter.incr(Metric.VALIDATING, 1)
            self._emit(result, ToolPhase.VALIDATING)

            try:
                descriptor = self._registry.get_descriptor(
                    request.namespace, request.tool, request.version
                )
            except ConfigError as exc:
                return await self._fail(request, result, error=f"unknown_tool: {exc}")

            if descriptor.state not in (DescriptorState.ACTIVE, DescriptorState.DEPRECATED):
                return await self._fail(
                    request, result, error="tool_retired",
                    classification=descriptor.manifest.classification,
                )

            ok, errors = validate_arguments(descriptor.manifest, request.arguments)
            if not ok:
                return await self._fail(
                    request, result, error=f"validation_error: {'; '.join(errors)}",
                    classification=descriptor.manifest.classification,
                )

            result = advance_execution(result, ExecutionTrigger.EXECUTE, clock=self._clock)
            self.metrics.incr("executing")
            self.meter.incr(Metric.EXECUTING, 1)
            self._emit(result, ToolPhase.EXECUTING)

            try:
                adapter = self._registry.get_adapter(
                    request.namespace, descriptor.manifest.name, descriptor.version
                )
                outcome = adapter.execute(request)
            except Exception as exc:  # noqa: BLE001 — normalized to a FAILED result
                return await self._fail(
                    request, result, error=f"execution_error: {exc}",
                    classification=descriptor.manifest.classification,
                    state=ExecutionState.EXECUTING,
                )

            enriched = self._normalizer.success(
                request, descriptor, outcome, now=now, expires_at=expires_at
            )
            final = advance_execution(enriched, ExecutionTrigger.COMPLETE, clock=self._clock)

        await self._repo.add_result(final)
        self.metrics.incr("completed")
        self.meter.incr(Metric.COMPLETED, 1)
        self._emit(final, ToolPhase.COMPLETED)
        return final

    async def _fail(
        self, request: ToolRequest, result: ToolResult, *, error: str,
        classification: Classification = Classification.READ_ONLY,
        state: ExecutionState = ExecutionState.VALIDATING,
    ) -> ToolResult:
        failed_content = self._normalizer.failure(
            request, state=state, error=error, classification=classification, now=result.created_at,
        )
        failed = advance_execution(failed_content, ExecutionTrigger.FAIL, clock=self._clock)
        await self._repo.add_result(failed)
        self.metrics.incr("failed")
        self.meter.incr(Metric.FAILED, 1)
        self._emit(failed, ToolPhase.FAILED)
        return failed

    # ── maintenance: TTL expiry sweep (deterministic; caller-driven) ─────────
    async def expire(self, *, now: float | None = None) -> int:
        now = self._clock() if now is None else now
        count = 0
        terminal = (ExecutionState.COMPLETED, ExecutionState.FAILED)
        for result in await self._repo.all_results():
            if result.state in terminal and result.is_expired(now):
                await self._repo.remove_result(result.request_id)
                self.metrics.incr("expired")
                self.meter.incr(Metric.EXPIRED, 1)
                count += 1
        return count

    # ── observability ────────────────────────────────────────────────────────
    def statistics(self) -> ToolStatistics:
        return ToolStatistics(
            name=self.name, state=self.state.value, healthy=self.health().healthy,
            executions=self.metrics.handled, results=run_coro_sync(self._repo.result_count()),
            tools=self._registry.names(), metrics=self.metrics.snapshot(),
        )

    # ── no content-event ingestion in the core milestone ──────────────────────
    def should_handle(self, event: Event) -> bool:
        return False

    # ── internals ──────────────────────────────────────────────────────────────
    def _resolve(self, request: ToolRequest) -> ToolRequest:
        """Fill the request hash (deterministic) if the caller did not supply one."""
        if request.request_hash:
            return request
        from dataclasses import replace

        rhash = request_hash_for(
            request.namespace, request.tool, request.version, request.arguments
        )
        return replace(request, request_hash=rhash)

    def _emit(self, result: ToolResult, phase: ToolPhase) -> None:
        payload = self._serializer.result_event_payload(result, phase=phase.value)
        type = _PHASE_TYPE.get(phase, EventType.TOOL_REQUESTED)
        self.publish(
            type, payload, session_id=result.session_id or "", org_id=result.org_id or "",
        )


_PHASE_TYPE: dict[ToolPhase, EventType] = {
    ToolPhase.RECEIVED: EventType.TOOL_REQUESTED,
    ToolPhase.VALIDATING: EventType.TOOL_REQUESTED,
    ToolPhase.EXECUTING: EventType.TOOL_REQUESTED,
    ToolPhase.COMPLETED: EventType.TOOL_COMPLETED,
    ToolPhase.FAILED: EventType.TOOL_FAILED,
    ToolPhase.CANCELLED: EventType.TOOL_FAILED,
}
