"""PipelineRoutedReasoningPort (M6.7B-1) — Agent/Loop reasoning routed
through the already-composed Runtime Pipeline.

Per the M6.7A design §11 and ADR rec 3: Agent Runtime and Loop Engine both
already call `core.ports.reasoning.ReasoningPort.run()` directly per their own
certified designs. This adapter is the concrete `ReasoningPort` instance they
receive at composition time — it satisfies the **already-frozen** port by
internally invoking `pipeline.RuntimePipeline.execute()` and adapting the
resulting `UnifiedResponse` back into a `Decision` stream, so every planning /
refinement call benefits from the full Memory→Knowledge→Context→Prompt→
Reasoning→Provider assembly rather than a bare `ReasoningRuntime.run()` that
would bypass Context/Prompt entirely.

This is composition, not a redesign — exactly the shape `bridge.
ProviderModelBridge` already has satisfying Reasoning's `ModelProvider`
(design §11). `ReasoningRuntime.run()` itself is untouched.

**Scope note (design §11, §17 risk 2):** the pipeline emits a single
provider-neutral response `text`; this adapter yields it as one
`Decision(kind=FINAL, payload={"content": text})`. That is exactly what Loop
Engine's propose/critique/repair read (`payload["content"]`,
`loop.core.pipeline._parse_final_content`). Agent decomposition additionally
reads `payload["steps"]` — structured plan steps are only present when a
structured-output provider supplies them; the deterministic offline
`EchoProvider` default does not, so an agent goal decomposed through the
offline pipeline yields a zero-step plan. Emitting fabricated steps here would
be planning logic, which this layer never owns (DL-0072). Trace is threaded by
value via `with_trace` (immutable rebind — no shared mutable state).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator

from core.ports.reasoning import Decision, DecisionKind, ReasoningPort, ReasoningRequest
from pipeline import RequestContext, RuntimePipeline

from ..context import default_trace_id
from ..observability.latency import LatencyRecorder, RoutingLatencySample


class PipelineRoutedReasoningPort(ReasoningPort):
    """A `ReasoningPort` backed by the Runtime Pipeline. Synchronous, mirroring
    the port's own shape (the deterministic local pipeline never suspends).

    Trace threading (M6.7B-2): the one platform `trace_id` reaches every routed
    reasoning call via one of three, in precedence order — an explicit
    `with_trace` binding, an injected `trace_provider` (the composition root's
    "current execution trace"), or a deterministic content-hash fallback. All
    three carry the *same one* id into the pipeline's `RequestContext.trace_id`;
    none is a new propagation mechanism (design §14).

    Latency (M6.7B-2): each routed call's wall time is recorded into the shared
    `LatencyRecorder` (design §17 risk 2). Wall time uses `perf_counter`,
    independent of the deterministic domain clock — measuring never alters a
    domain result."""

    def __init__(
        self,
        pipeline: RuntimePipeline,
        *,
        trace_id: str = "",
        trace_provider: Callable[[], str] | None = None,
        latency: LatencyRecorder | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._trace_id = trace_id
        self._trace_provider = trace_provider
        self._latency = latency
        self._clock = clock

    @property
    def latency(self) -> LatencyRecorder | None:
        return self._latency

    def with_trace(self, trace_id: str) -> PipelineRoutedReasoningPort:
        """Return a new port bound to `trace_id` — an immutable rebind so the
        composition root can thread the platform trace per execution without
        sharing mutable state (design §14, §17). The latency recorder is shared
        (a telemetry sink, not domain state) so samples still accumulate."""
        return PipelineRoutedReasoningPort(
            self._pipeline, trace_id=trace_id, trace_provider=self._trace_provider,
            latency=self._latency, clock=self._clock,
        )

    def _resolve_trace(self, request: ReasoningRequest) -> str:
        if self._trace_id:
            return self._trace_id
        if self._trace_provider is not None:
            provided = self._trace_provider()
            if provided:
                return provided
        return default_trace_id("reasoning", request.session_id, request.context_ref)

    def run(self, request: ReasoningRequest) -> Iterator[Decision]:
        ctx = RequestContext(
            request_id=default_trace_id("req", request.session_id, request.context_ref),
            session_id=request.session_id,
            trace_id=self._resolve_trace(request),
            org_id=request.org_id,
        )
        started = time.perf_counter()
        response = self._pipeline.execute(ctx, streaming=False)
        elapsed = time.perf_counter() - started
        if self._latency is not None:
            self._latency.record(RoutingLatencySample(
                total_s=elapsed, context_assembly_s=_context_stage_s(response),
            ))
        yield Decision(kind=DecisionKind.FINAL, payload={"content": response.text})


def _context_stage_s(response) -> float:
    """The pipeline's own CONTEXT-stage duration, read from its already-computed
    snapshot — never recomputed here. Zero under a deterministic domain clock."""
    snapshot = getattr(response, "snapshot", None)
    if snapshot is None:
        return 0.0
    for stage in snapshot.timings.stages:
        if stage.stage.value == "context":
            return stage.duration_s
    return 0.0
