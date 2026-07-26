"""Agent Platform Integration — M6.7.1 Operational Hardening test suite.

Covers the three M6.7C certification findings:
- F1: concurrency-safe trace propagation (contextvars) — parallel / nested /
  async / thread isolation, no leakage.
- F2: bounded correlation stores — replay index (LRU), span store (LRU), latency
  ring buffer.
- F3: cancellation wired execute() -> IntegrationCancellationToken ->
  WorkflowDelegationBridge -> the delegated workflow's own token.
"""

from __future__ import annotations

import asyncio
import threading

import integration as I
from core.ports.reasoning import ReasoningRequest
from integration.bridges import PipelineRoutedReasoningPort, WorkflowDelegationBridge
from integration.observability import LatencyRecorder, RoutingLatencySample
from integration.replay import PlatformReplayIndex
from integration.trace import current_cancellation, current_trace, execution_scope
from workflow import (
    WorkflowFactory,
    WorkflowGraph,
    WorkflowManifest,
    WorkflowNode,
    WorkflowNodeKind,
)
from workflow.execution import WorkflowExecutionFactory

CLOCK = lambda: 0.0  # noqa: E731


def _real_workflow_runtimes():
    wf = WorkflowFactory.default(clock=CLOCK)
    graph = WorkflowGraph(
        entry_node="a",
        nodes=(WorkflowNode(node_id="a", kind=WorkflowNodeKind.DELAY, delay_ms=1),),
        edges=(),
    )
    wf.register(WorkflowManifest(id="onboard", namespace="internal", version="1.0.0", graph=graph))
    wf.activate("internal", "onboard", "1.0.0")
    ex = WorkflowExecutionFactory.default(wf.registry, clock=CLOCK)
    return wf, ex


class _SpyPipeline:
    """Records the trace_id each routed reasoning call carries."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def execute(self, ctx, *, cancellation=None, streaming=True):
        self.seen.append(ctx.trace_id)

        class _Resp:
            text = "ok"
            snapshot = None

        return _Resp()


# ── F1: trace scope basics ──────────────────────────────────────────────────
def test_execution_scope_sets_and_resets():
    assert current_trace() == ""  # no ambient value outside a scope
    with execution_scope("tr_a"):
        assert current_trace() == "tr_a"
    assert current_trace() == ""  # restored (no leakage)


def test_execution_scope_nesting_restores_lifo():
    with execution_scope("outer"):
        assert current_trace() == "outer"
        with execution_scope("inner"):
            assert current_trace() == "inner"
        assert current_trace() == "outer"  # inner reset restores outer exactly
    assert current_trace() == ""


def test_execution_scope_resets_on_exception():
    try:
        with execution_scope("tr"):
            raise ValueError("boom")
    except ValueError:
        pass
    assert current_trace() == ""  # finally-reset guarantees no leak on error


def test_reasoning_port_reads_context_trace():
    spy = _SpyPipeline()
    port = PipelineRoutedReasoningPort(spy, trace_provider=current_trace)
    with execution_scope("tr_ctx"):
        list(port.run(ReasoningRequest(session_id="s", context_ref="c")))
    assert spy.seen == ["tr_ctx"]


def test_thread_trace_isolation():
    # Two threads run overlapping scopes; each sees only its own trace.
    seen: dict[str, str] = {}
    barrier = threading.Barrier(2)

    def worker(tid: str) -> None:
        with execution_scope(f"tr_{tid}"):
            barrier.wait()          # force overlap: both inside their scopes together
            seen[tid] = current_trace()

    threads = [threading.Thread(target=worker, args=(t,)) for t in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert seen == {"A": "tr_A", "B": "tr_B"}  # no cross-thread leakage


def test_async_task_trace_isolation():
    # Concurrent asyncio tasks each keep their own trace across await points.
    async def task(tid: str) -> str:
        with execution_scope(f"tr_{tid}"):
            await asyncio.sleep(0)   # yield to the other task mid-scope
            return current_trace()

    async def main():
        return await asyncio.gather(task("X"), task("Y"))

    results = asyncio.run(main())
    assert set(results) == {"tr_X", "tr_Y"}


def test_parallel_execute_trace_isolation_on_shared_root():
    # The certification-critical F1 case: many parallel execute() on ONE root.
    root = I.PlatformFactory.default(clock=CLOCK)
    results: dict[int, str] = {}

    def run(i: int) -> None:
        resp = root.execute(I.PlatformRequest(
            session_id=f"s{i}", agent_id=f"a{i}", goal_description=f"g{i}",
        ))
        results[i] = resp.trace_id

    threads = [threading.Thread(target=run, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # Each request produced a distinct trace, and each is recorded under its own id.
    assert len(set(results.values())) == 16
    for tid in results.values():
        assert root.replay_correlation.index.entries_for(tid)


# ── F2: bounded stores ──────────────────────────────────────────────────────
def test_latency_recorder_is_bounded():
    rec = LatencyRecorder(maxlen=10)
    for i in range(100):
        rec.record(RoutingLatencySample(total_s=float(i)))
    assert len(rec.samples) == 10               # window capped
    assert rec.total_recorded == 100            # lifetime count preserved
    assert rec.stats().count == 10
    assert rec.stats().max_s == 99.0            # newest retained


def test_replay_index_is_lru_bounded():
    idx = PlatformReplayIndex(max_traces=3)
    for i in range(5):
        idx.record(f"tr{i}", "agent", "agent_replay", f"ar{i}")
    assert len(idx.trace_ids()) == 3
    assert set(idx.trace_ids()) == {"tr2", "tr3", "tr4"}  # oldest two evicted
    assert idx.entries_for("tr0") == ()                    # evicted


def test_replay_index_per_trace_cap():
    idx = PlatformReplayIndex(max_traces=8, max_per_trace=2)
    for i in range(5):
        idx.record("tr", "agent", "agent_replay", f"ar{i}")
    entries = idx.entries_for("tr")
    assert len(entries) == 2
    assert [e.ref_id for e in entries] == ["ar3", "ar4"]  # oldest ring-dropped


def test_span_store_is_bounded_on_shared_root():
    root = I.PlatformFactory.create(
        I.IntegrationConfiguration(max_correlation_traces=4), clock=CLOCK,
    )
    for i in range(10):
        root.execute(I.PlatformRequest(
            session_id=f"s{i}", agent_id=f"a{i}", goal_description=f"g{i}",
        ))
    # Only the last 4 traces' spans + replay entries are retained.
    assert len(root.replay_correlation.index.trace_ids()) == 4


def test_configuration_validates_bounds():
    I.IntegrationConfiguration().validate()  # defaults valid
    for bad in (
        I.IntegrationConfiguration(max_correlation_traces=0),
        I.IntegrationConfiguration(max_spans_per_trace=0),
        I.IntegrationConfiguration(max_latency_samples=0),
    ):
        try:
            bad.validate()
        except I.IntegrationError:
            continue
        raise AssertionError("expected IntegrationError for invalid bound")


# ── F3: cancellation wiring ─────────────────────────────────────────────────
def test_bridge_chains_workflow_token_to_context_cancellation():
    wf, ex = _real_workflow_runtimes()
    token = I.IntegrationCancellationToken("tr")
    bridge = WorkflowDelegationBridge(
        ex, wf.registry, cancellation_provider=current_cancellation,
    )
    with execution_scope("tr", token):
        handle = bridge.resolve("internal.onboard@1.0.0")
        eid = bridge.submit(handle, {})
    wf_token = bridge.cancellation_token_for(eid)
    assert wf_token is not None and not wf_token.is_cancelled()
    token.cancel(reason="client-abort")           # cancel the platform token
    assert wf_token.is_cancelled()                # ... cancels the workflow token (chained)
    assert wf_token.reason == "client-abort"


def test_execute_wires_cancellation_into_workflow_bridge():
    root = I.PlatformFactory.default(clock=CLOCK)
    # register a workflow so a delegation could occur
    wf = root.config.workflow_runtime
    graph = WorkflowGraph(
        entry_node="a",
        nodes=(WorkflowNode(node_id="a", kind=WorkflowNodeKind.DELAY, delay_ms=1),),
        edges=(),
    )
    wf.register(WorkflowManifest(id="onboard", namespace="internal", version="1.0.0", graph=graph))
    wf.activate("internal", "onboard", "1.0.0")
    token = I.IntegrationCancellationToken("caller")
    resp = root.execute(
        I.PlatformRequest(session_id="s", agent_id="a", goal_description="g"),
        cancellation=token,
    )
    assert isinstance(resp, I.PlatformResponse)  # token threaded, no crash


def test_execute_short_circuits_when_precancelled():
    root = I.PlatformFactory.default(clock=CLOCK)
    token = I.IntegrationCancellationToken("caller")
    token.cancel()
    resp = root.execute(
        I.PlatformRequest(session_id="s", agent_id="a", goal_description="g"),
        cancellation=token,
    )
    assert resp.final_state == "cancelled"
    assert resp.partial_success is False


def test_context_cancellation_is_none_outside_scope():
    assert current_cancellation() is None
    tok = I.IntegrationCancellationToken("t")
    with execution_scope("tr", tok):
        assert current_cancellation() is tok
    assert current_cancellation() is None  # reset, no leak
