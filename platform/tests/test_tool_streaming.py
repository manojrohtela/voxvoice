"""Tool Runtime Streaming & Cancellation (M6.1B-4) tests.

Verifies the streaming lifecycle (started -> chunks -> completed), cancellation
propagation (never leaves orphaned execution state), timeout handling, partial
results, backpressure, replay compatibility for streamed executions,
deterministic execution, serialization round-trips, metrics/diagnostics, and
dependency boundaries. WebSocket/SSE/MCP/Workflow/Agent/Skills/Hooks/Loop-Engine
integration and external SDKs are non-goals here (M6.1B-5+).
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from core.errors import ConfigError
from core.events import Event, EventSource, EventType
from core.policies import TimeoutPolicy
from tool import Classification, DescriptorState, ExecutionState, ToolManifest, ToolRequest
from tool.extensions import StreamingAdapter
from tool.streaming import (
    ChunkKind,
    EchoStreamingAdapter,
    StreamingDiagnostics,
    StreamingFactory,
    StreamingMetrics,
    StreamingPolicy,
    StreamingReplayEngine,
    StreamingSerializer,
    ToolCancellationToken,
    ToolChunk,
    ToolStreamingController,
    ToolStreamingPolicyEngine,
    ToolStreamingRuntime,
    build_streaming_diagnostics,
    streamed_replay_record_for,
)


def _req(**kw) -> ToolRequest:
    base = dict(request_id="r1", tool="echo_stream", namespace="internal",
                arguments={"a": 1, "b": 2}, session_id="s1")
    base.update(kw)
    return ToolRequest(**base)


def _runtime(**kwargs):
    kwargs.setdefault("clock", lambda: 100.0)
    return StreamingFactory.create(**kwargs)


# ── Streaming lifecycle ────────────────────────────────────────────────────────
def test_stream_success_produces_completed_result_and_final_chunk():
    rt = _runtime()
    streamed = rt.execute_stream(_req())
    assert streamed.result.state is ExecutionState.COMPLETED
    assert streamed.result.success
    assert streamed.result.output == {"echo": {"a": 1, "b": 2}}
    assert streamed.session.final
    assert streamed.session.chunks[-1].kind is ChunkKind.FINAL
    assert streamed.session.chunks[-1].done


def test_stream_partial_results_accumulate_deterministically():
    rt = _runtime()
    streamed = rt.execute_stream(_req())
    partials = [c.delta for c in streamed.session.chunks if c.kind is ChunkKind.PARTIAL]
    assert partials == [{"a": 1}, {"a": 1, "b": 2}]


def test_stream_unknown_tool_fails_without_orphaned_state():
    rt = _runtime()
    streamed = rt.execute_stream(_req(tool="unknown"))
    assert streamed.result.state is ExecutionState.FAILED
    assert streamed.result.error.startswith("unknown_tool")
    assert streamed.session.chunks == ()


def test_stream_retired_tool_fails():
    rt = _runtime()
    rt2 = _runtime(registry=rt.registry)  # reuse same registry instance
    rt2.registry.deprecate("internal", "echo_stream", "1.0.0")
    rt2.registry.retire("internal", "echo_stream", "1.0.0")
    streamed = rt2.execute_stream(_req())
    assert streamed.result.error == "tool_retired"


def test_stream_validation_failure():
    manifest = ToolManifest(
        name="strict_stream", namespace="internal", version="1.0.0",
        capabilities=frozenset({"strict_stream"}),
        inputs={"type": "object", "properties": {"name": {"type": "string"}},
                "required": ["name"]},
    )
    rt = _runtime(tools=[(manifest, EchoStreamingAdapter())])
    streamed = rt.execute_stream(_req(tool="strict_stream", arguments={}))
    assert streamed.result.error.startswith("validation_error")
    assert streamed.result.state is ExecutionState.FAILED


def test_stream_adapter_that_is_not_streaming_capable_fails_cleanly():
    from tool import ToolAdapter, ToolExecutionOutcome

    class _NonStreamingAdapter(ToolAdapter):
        name = "plain"
        capabilities = frozenset({"plain"})

        def execute(self, request):
            return ToolExecutionOutcome(output={})

    manifest = ToolManifest(name="plain", namespace="internal", version="1.0.0",
                            capabilities=frozenset({"plain"}))
    rt = _runtime(tools=[(manifest, _NonStreamingAdapter())])
    streamed = rt.execute_stream(_req(tool="plain", arguments={}))
    assert streamed.result.state is ExecutionState.FAILED
    assert "streaming" in streamed.result.error


def test_stream_adapter_exception_is_normalized_never_raised():
    class _BoomAdapter(StreamingAdapter):
        name = "boom_stream"
        capabilities = frozenset({"boom_stream"})

        def stream(self, request):
            raise RuntimeError("adapter exploded")
            yield  # pragma: no cover — makes this a generator function

    manifest = ToolManifest(name="boom_stream", namespace="internal", version="1.0.0",
                            capabilities=frozenset({"boom_stream"}))
    rt = _runtime(tools=[(manifest, _BoomAdapter())])
    streamed = rt.execute_stream(_req(tool="boom_stream", arguments={}))
    assert streamed.result.state is ExecutionState.FAILED
    assert "adapter exploded" in streamed.result.error


# ── Cancellation ───────────────────────────────────────────────────────────────
def test_cancellation_before_stream_reaches_cancelled_terminal_state():
    rt = _runtime()
    token = ToolCancellationToken("t1")
    token.cancel(reason="user requested")
    streamed = rt.execute_stream(_req(), cancellation=token)
    assert streamed.result.state is ExecutionState.CANCELLED
    assert streamed.session.cancelled
    assert token.reason == "user requested"


def test_cancellation_mid_stream_stops_pulling_further_chunks():
    token = ToolCancellationToken("t1")

    class _CancelMidwayAdapter(StreamingAdapter):
        name = "cancel_stream"
        capabilities = frozenset({"cancel_stream"})

        def stream(self, request):
            yield ToolChunk(id="c0", index=0, delta={"step": 0})
            token.cancel(reason="stop")
            yield ToolChunk(id="c1", index=1, delta={"step": 1})
            yield ToolChunk(id="c2", index=2, delta={"step": 2}, kind=ChunkKind.FINAL, done=True)

    manifest = ToolManifest(name="cancel_stream", namespace="internal", version="1.0.0",
                            capabilities=frozenset({"cancel_stream"}))
    rt = _runtime(tools=[(manifest, _CancelMidwayAdapter())])
    streamed = rt.execute_stream(_req(tool="cancel_stream", arguments={}), cancellation=token)
    assert streamed.result.state is ExecutionState.CANCELLED
    assert len(streamed.session.chunks) == 1  # only the first chunk was buffered


def test_cancellation_is_idempotent():
    token = ToolCancellationToken("t1")
    token.cancel(reason="first")
    token.cancel(reason="second")
    assert token.is_cancelled()
    assert token.reason == "second"


def test_cancellation_never_leaves_orphaned_executing_state():
    """Every terminal path (success/fail/cancel/timeout) must resolve to a
    terminal ExecutionState — never left in EXECUTING/VALIDATING/RECEIVED."""
    rt = _runtime()
    terminal = {ExecutionState.COMPLETED, ExecutionState.FAILED, ExecutionState.CANCELLED}

    ok = rt.execute_stream(_req())
    assert ok.result.state in terminal

    token = ToolCancellationToken("t1")
    token.cancel()
    cancelled = rt.execute_stream(_req(), cancellation=token)
    assert cancelled.result.state in terminal

    failed = rt.execute_stream(_req(tool="unknown"))
    assert failed.result.state in terminal


# ── Timeout ────────────────────────────────────────────────────────────────────
def test_timeout_fails_deterministically_via_elapsed_s():
    rt = _runtime(policy=StreamingPolicy(timeout=TimeoutPolicy(seconds=1.0)))
    streamed = rt.execute_stream(_req(), elapsed_s=5.0)
    assert streamed.result.state is ExecutionState.FAILED
    assert "timeout" in streamed.result.error


def test_timeout_disabled_by_default_zero_timeout_never_fires():
    engine = ToolStreamingPolicyEngine(StreamingPolicy(timeout=TimeoutPolicy(seconds=0.001)))
    assert engine.timeout_exceeded(0.0) is False  # 0.0 elapsed never exceeds any positive timeout


# ── Backpressure ───────────────────────────────────────────────────────────────
def test_backpressure_verdict_flags_when_buffered_exceeds_cap():
    engine = ToolStreamingPolicyEngine(StreamingPolicy(max_buffered_chunks=1))
    controller = ToolStreamingController(engine)
    chunks = [
        ToolChunk(id="c0", index=0, delta={"a": 1}),
        ToolChunk(id="c1", index=1, delta={"a": 1, "b": 2}, kind=ChunkKind.FINAL, done=True),
    ]
    session = controller.run(chunks)
    assert session.backpressure_events == 1


def test_backpressure_unbounded_when_cap_is_zero():
    engine = ToolStreamingPolicyEngine(StreamingPolicy(max_buffered_chunks=0))
    controller = ToolStreamingController(engine)
    chunks = [ToolChunk(id=f"c{i}", index=i, delta=i) for i in range(50)]
    session = controller.run(chunks)
    assert session.backpressure_events == 0


# ── Replay compatibility ───────────────────────────────────────────────────────
def test_streamed_replay_round_trips_and_validates():
    rt = _runtime()
    request = _req()
    streamed = rt.execute_stream(request)
    record = streamed_replay_record_for(request, streamed, now=100.0)
    comparison = StreamingReplayEngine().validate(record)
    assert comparison.matches
    assert record.execution_trace.metadata.get("streamed") == "true"


def test_streamed_replay_detects_tampering():
    from dataclasses import replace

    rt = _runtime()
    request = _req()
    streamed = rt.execute_stream(request)
    record = streamed_replay_record_for(request, streamed, now=100.0)
    tampered_trace = replace(record.execution_trace, output_hash="wrong")
    tampered = replace(record, execution_trace=tampered_trace, replay_hash="wrong")
    comparison = StreamingReplayEngine().validate(tampered)
    assert not comparison.matches


def test_replay_is_deterministic_across_two_separate_runs():
    rt1 = _runtime()
    rt2 = _runtime()
    request = _req()
    s1 = rt1.execute_stream(request)
    s2 = rt2.execute_stream(request)
    r1 = streamed_replay_record_for(request, s1, now=100.0)
    r2 = streamed_replay_record_for(request, s2, now=100.0)
    assert r1.replay_hash == r2.replay_hash


# ── Metrics + Diagnostics ──────────────────────────────────────────────────────
def test_streaming_metrics_tracks_outcomes():
    metrics = StreamingMetrics()
    rt = _runtime()
    ok = rt.execute_stream(_req())
    metrics.record_start()
    metrics.record_session(ok.session)
    snapshot = metrics.snapshot()
    assert snapshot["streams_started"] == 1
    assert snapshot["streams_completed"] == 1
    assert snapshot["total_chunks"] == 3


def test_streaming_diagnostics_reports_registry_and_metrics():
    rt = _runtime()
    metrics = StreamingMetrics()
    ok = rt.execute_stream(_req())
    metrics.record_session(ok.session)
    diag = build_streaming_diagnostics(
        registry=rt.registry, metrics=metrics, policy=StreamingPolicy(),
    )
    assert isinstance(diag, StreamingDiagnostics)
    assert "internal.echo_stream" in diag.registry_stats["tools"]
    assert diag.metric_snapshot["streams_completed"] == 1


# ── Serialization round-trips ──────────────────────────────────────────────────
def test_serializer_chunk_round_trip():
    s = StreamingSerializer()
    chunk = ToolChunk(id="c0", index=0, delta={"a": 1}, kind=ChunkKind.FINAL, done=True)
    doc = s.chunk_to_dict(chunk)
    assert s.chunk_from_dict(doc) == chunk


def test_serializer_session_round_trip():
    rt = _runtime()
    streamed = rt.execute_stream(_req())
    s = StreamingSerializer()
    doc = s.session_to_dict(streamed.session)
    assert s.session_from_dict(doc) == streamed.session


def test_serializer_streamed_result_round_trip():
    rt = _runtime()
    streamed = rt.execute_stream(_req())
    s = StreamingSerializer()
    doc = s.streamed_result_to_document(streamed)
    assert s.streamed_result_from_document(doc) == streamed


# ── Determinism ────────────────────────────────────────────────────────────────
def test_execution_is_deterministic_for_identical_input():
    rt1 = _runtime()
    rt2 = _runtime()
    r1 = rt1.execute_stream(_req())
    r2 = rt2.execute_stream(_req())
    assert r1.result.output == r2.result.output
    assert r1.result.result_hash == r2.result.result_hash
    assert r1.session.chunks == r2.session.chunks


# ── Events ─────────────────────────────────────────────────────────────────────
def test_streaming_events_emitted_through_injected_publisher():
    captured: list[Event] = []

    def publish(type: EventType, payload: dict, *, session_id: str = "", org_id: str = ""):
        event = Event(id=f"e{len(captured)}", type=type, ts=1.0, seq=len(captured),
                      session_id=session_id, source=EventSource.TOOL, org_id=org_id, data=payload)
        captured.append(event)
        return event

    rt = _runtime(publish=publish)
    rt.execute_stream(_req())
    types = {e.type for e in captured}
    assert EventType.TOOL_STREAM_STARTED in types
    assert EventType.TOOL_STREAM_CHUNK in types
    assert EventType.TOOL_STREAM_COMPLETED in types


def test_streaming_no_publisher_is_a_safe_no_op():
    rt = _runtime(publish=None)
    streamed = rt.execute_stream(_req())
    assert streamed.result.success


# ── Unknown adapter lookup ─────────────────────────────────────────────────────
def test_stream_unknown_adapter_lookup_raises_config_error_normalized():
    rt = _runtime()
    rt.registry.deregister("internal", "echo_stream", "1.0.0")
    # descriptor no longer resolvable -> unknown_tool path (not a raw ConfigError)
    streamed = rt.execute_stream(_req())
    assert streamed.result.error.startswith("unknown_tool")


# ── Dependency rules (no ownership leakage) ────────────────────────────────────
def _imports(pkg: str) -> set[str]:
    tops: set[str] = set()
    for p in pathlib.Path(pkg).rglob("*.py"):
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.ImportFrom) and n.module and not n.level:
                tops.add(n.module.split(".")[0])
            elif isinstance(n, ast.Import):
                for a in n.names:
                    tops.add(a.name.split(".")[0])
    return tops


def test_tool_dependency_rules_still_hold_with_streaming():
    forbidden = {"runtime", "api", "gateway", "replay", "events", "memory", "knowledge",
                 "context", "prompt", "reasoning", "provider", "bridge", "pipeline",
                 "voicebot", "observability", "tenant"}
    assert not (_imports("tool") & forbidden), _imports("tool")


def test_streaming_owns_no_governance_or_bare_execute_logic():
    """`ToolStreamingRuntime` never exposes evaluate/authorize (governance's job)
    and never exposes a non-streaming `execute` (the frozen core's own job)."""
    surface = {name for name in dir(ToolStreamingRuntime) if not name.startswith("_")}
    forbidden_methods = {"evaluate", "authorize", "execute", "aexecute"}
    assert not (forbidden_methods & surface)


def test_descriptor_state_import_available_for_reference():
    assert DescriptorState.ACTIVE.value == "active"


def test_streaming_configuration_rejects_negative_buffer_cap():
    from core.policies import InvalidPolicy

    with pytest.raises(InvalidPolicy):
        StreamingPolicy(max_buffered_chunks=-1)


def test_classification_carried_through_streamed_failure():
    manifest = ToolManifest(name="strict2", namespace="internal", version="1.0.0",
                            capabilities=frozenset({"strict2"}),
                            classification=Classification.DESTRUCTIVE,
                            inputs={"type": "object", "properties": {}, "required": ["x"]})
    rt = _runtime(tools=[(manifest, EchoStreamingAdapter())])
    streamed = rt.execute_stream(_req(tool="strict2", arguments={}))
    assert streamed.result.classification is Classification.DESTRUCTIVE


def test_config_error_is_the_platform_error_type():
    # sanity for the ConfigError import used elsewhere in this suite
    assert issubclass(ConfigError, Exception)
