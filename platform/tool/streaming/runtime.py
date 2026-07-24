"""ToolStreamingRuntime (M6.1B-4) — deterministic streaming + cancellation
execution. A new, additive facade — `tool/runtime.py` (frozen since M6.1B-1) is
never modified. Reuses the existing execution FSM (`tool.lifecycle`), argument
validation (`tool.validation`), result normalization (`tool.results`), and
request hashing (`tool.ids`) verbatim — no duplicated business logic, only a
new orchestration path for adapters that stream instead of returning once.

**Cancellation never leaves orphaned execution state**: every path below —
success, validation failure, execution failure, timeout, or cancellation —
ends by firing the matching `ExecutionTrigger` (`COMPLETE`/`FAIL`/`CANCEL`) on
the same FSM `tool.runtime.ToolRuntime.aexecute` uses, so a streamed request
always reaches a terminal `ExecutionState`, never stalls in `EXECUTING`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.errors import ConfigError
from core.events import Event, EventType

from .._run import run_coro_sync
from ..domain import Classification, DescriptorState, ExecutionState, ToolRequest, ToolResult
from ..extensions import StreamingAdapter
from ..ids import request_hash_for
from ..lifecycle import ExecutionTrigger, advance_execution
from ..ports import ToolExecutionOutcome
from ..registry import ToolRegistry
from ..results import ResultNormalizer
from ..validation import validate_arguments
from .cancellation import ToolCancellationToken
from .controller import ToolStreamingController
from .domain import StreamedToolResult, StreamingSession
from .events import PHASE_EVENT_TYPE, StreamingPhase

Publisher = Callable[..., Event | None]


class ToolStreamingRuntime:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        controller: ToolStreamingController | None = None,
        normalizer: ResultNormalizer | None = None,
        clock: Callable[[], float] | None = None,
        publish: Publisher | None = None,
    ) -> None:
        import time

        self._registry = registry
        self._controller = controller or ToolStreamingController()
        self._normalizer = normalizer or ResultNormalizer()
        self._clock = clock or time.time
        self._publish = publish

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    # ── sync surface ──────────────────────────────────────────────────────────
    def execute_stream(
        self, request: ToolRequest, *,
        cancellation: ToolCancellationToken | None = None, elapsed_s: float = 0.0,
    ) -> StreamedToolResult:
        return run_coro_sync(self.aexecute_stream(
            request, cancellation=cancellation, elapsed_s=elapsed_s
        ))

    # ── async core ────────────────────────────────────────────────────────────
    async def aexecute_stream(
        self, request: ToolRequest, *,
        cancellation: ToolCancellationToken | None = None, elapsed_s: float = 0.0,
    ) -> StreamedToolResult:
        now = self._clock()
        request = self._resolve(request)

        result = ToolResult(
            request_id=request.request_id, tool=request.tool, namespace=request.namespace,
            version=request.version or "", request_hash=request.request_hash,
            created_at=now, session_id=request.session_id, org_id=request.org_id,
        )
        self._emit(StreamingPhase.STREAM_STARTED, request.request_id)
        result = advance_execution(result, ExecutionTrigger.VALIDATE, clock=self._clock)

        try:
            descriptor = self._registry.get_descriptor(
                request.namespace, request.tool, request.version
            )
        except ConfigError as exc:
            return self._fail(request, result, error=f"unknown_tool: {exc}")

        if descriptor.state not in (DescriptorState.ACTIVE, DescriptorState.DEPRECATED):
            return self._fail(
                request, result, error="tool_retired",
                classification=descriptor.manifest.classification,
            )

        ok, errors = validate_arguments(descriptor.manifest, request.arguments)
        if not ok:
            return self._fail(
                request, result, error=f"validation_error: {'; '.join(errors)}",
                classification=descriptor.manifest.classification,
            )

        result = advance_execution(result, ExecutionTrigger.EXECUTE, clock=self._clock)

        try:
            adapter = self._registry.get_adapter(
                request.namespace, descriptor.manifest.name, descriptor.version
            )
        except ConfigError as exc:
            return self._fail(
                request, result, error=f"execution_error: {exc}",
                classification=descriptor.manifest.classification, state=ExecutionState.EXECUTING,
            )
        if not isinstance(adapter, StreamingAdapter):
            return self._fail(
                request, result, error="execution_error: adapter does not support streaming",
                classification=descriptor.manifest.classification, state=ExecutionState.EXECUTING,
            )

        try:
            chunks = adapter.stream(request)
            session = self._controller.run(chunks, cancellation=cancellation, elapsed_s=elapsed_s)
        except Exception as exc:  # noqa: BLE001 — normalized to a FAILED result
            return self._fail(
                request, result, error=f"execution_error: {exc}",
                classification=descriptor.manifest.classification, state=ExecutionState.EXECUTING,
            )

        if session.cancelled:
            cancelled_content = self._normalizer.failure(
                request, state=ExecutionState.EXECUTING, error="cancelled",
                classification=descriptor.manifest.classification, now=now,
            )
            cancelled = advance_execution(
                cancelled_content, ExecutionTrigger.CANCEL, clock=self._clock
            )
            self._emit(StreamingPhase.STREAM_CANCELLED, request.request_id)
            return StreamedToolResult(result=cancelled, session=session)

        if session.timed_out:
            self._emit(StreamingPhase.STREAM_TIMED_OUT, request.request_id)
            return self._fail(
                request, result, error="execution_error: streaming timeout",
                classification=descriptor.manifest.classification, state=ExecutionState.EXECUTING,
            )

        for chunk in session.chunks:
            self._emit(StreamingPhase.STREAM_CHUNK, request.request_id,
                       extra={"index": chunk.index})

        outcome = ToolExecutionOutcome(output=session.partial_output)
        enriched = self._normalizer.success(request, descriptor, outcome, now=now, expires_at=None)
        final = advance_execution(enriched, ExecutionTrigger.COMPLETE, clock=self._clock)
        self._emit(StreamingPhase.STREAM_COMPLETED, request.request_id)
        return StreamedToolResult(result=final, session=session)

    # ── internals ──────────────────────────────────────────────────────────────
    def _fail(
        self, request: ToolRequest, result: ToolResult, *, error: str,
        classification: Classification = Classification.READ_ONLY,
        state: ExecutionState = ExecutionState.VALIDATING,
    ) -> StreamedToolResult:
        failed_content = self._normalizer.failure(
            request, state=state, error=error, classification=classification, now=result.created_at,
        )
        failed = advance_execution(failed_content, ExecutionTrigger.FAIL, clock=self._clock)
        return StreamedToolResult(result=failed, session=StreamingSession())

    def _resolve(self, request: ToolRequest) -> ToolRequest:
        if request.request_hash:
            return request
        from dataclasses import replace

        rhash = request_hash_for(
            request.namespace, request.tool, request.version, request.arguments
        )
        return replace(request, request_hash=rhash)

    def _emit(
        self, phase: StreamingPhase, request_id: str, *, extra: dict[str, Any] | None = None
    ) -> None:
        if self._publish is None:
            return
        event_type: EventType = PHASE_EVENT_TYPE[phase]
        payload = {"phase": phase.value, "request_id": request_id, **(extra or {})}
        self._publish(event_type, payload, session_id="", org_id="")


def _empty_session() -> Any:
    from .domain import StreamingSession

    return StreamingSession()
