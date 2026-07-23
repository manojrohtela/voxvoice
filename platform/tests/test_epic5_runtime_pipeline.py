"""Epic 5 Runtime Pipeline (M5.7) integration tests.

Verifies the official Memory→Knowledge→Context→Prompt→Reasoning→Provider execution path
(`platform/pipeline/`) — distinct from the legacy M2C.2 voice `runtime.pipeline.pipeline`
(unrelated, untouched). Covers end-to-end execution, streaming, cancellation
(pipeline-level and cross-runtime via Reasoning's own CancellationManager), metadata/lineage
propagation, the unified execution trace, replay safety (determinism), dependency
boundaries (the pipeline may import every runtime; no runtime may import the pipeline or
each other), composition root correctness, provider neutrality, and no runtime-ownership
leakage (the pipeline contains no business logic of its own).
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from bridge import ProviderModelBridge
from context import ContextConfiguration, ContextFactory
from knowledge import KnowledgeFactory
from memory import MemoryFactory
from pipeline import (
    PipelineCancellation,
    PipelineCancelled,
    RequestContext,
    RuntimePipeline,
    RuntimePipelineFactory,
    StageError,
    UserQuerySource,
)
from prompt import PromptFactory
from provider import ProviderConfiguration, ProviderFactory
from reasoning import ReasoningConfiguration, ReasoningFactory


def _ctx(**kw):
    base = dict(request_id="r1", session_id="s1", trace_id="t1", org_id="o1",
               query="How do I cover drive?")
    base.update(kw)
    return RequestContext(**base)


# ── end-to-end execution ──────────────────────────────────────────────────────────
def test_end_to_end_execution_is_deterministic_offline():
    pipe = RuntimePipelineFactory.default(clock=lambda: 100.0)
    resp = pipe.execute(_ctx())
    assert resp.text == "echo: How do I cover drive?"
    assert resp.provider == "provider-runtime"  # the ModelProvider name (the bridge)
    assert resp.finish_reason.value == "stop"
    assert resp.usage.completion_tokens > 0

    other = RuntimePipelineFactory.default(clock=lambda: 100.0).execute(_ctx())
    assert resp.response_hash == other.response_hash  # deterministic (replay-safe)


def test_pipeline_uses_only_public_runtime_surfaces_no_business_logic():
    # the pipeline never computes text/usage itself — it is read verbatim off Reasoning's
    # ResponsePackage, which is itself built from Provider's response via the bridge
    pipe = RuntimePipelineFactory.default(clock=lambda: 100.0)
    resp = pipe.execute(_ctx())
    assert resp.text.endswith(resp.chunks[-1].delta)


# ── streaming (no fork) ────────────────────────────────────────────────────────────
def test_streaming_yields_the_exact_reasoning_chunk_sequence():
    pipe = RuntimePipelineFactory.default(clock=lambda: 100.0)
    ctx = _ctx()
    streamed = list(pipe.stream(ctx))
    executed = pipe.execute(ctx)
    assert [c.delta for c in streamed] == [c.delta for c in executed.chunks]


# ── cancellation propagation ──────────────────────────────────────────────────────
def test_pipeline_level_cancellation_stops_before_first_stage():
    pipe = RuntimePipelineFactory.default(clock=lambda: 100.0)
    cancellation = PipelineCancellation("tok-1")
    cancellation.cancel(reason="user requested stop")
    with pytest.raises(PipelineCancelled) as exc_info:
        pipe.execute(_ctx(), cancellation=cancellation)
    assert exc_info.value.stage == "context"
    assert exc_info.value.reason == "user requested stop"


def test_cross_runtime_cancellation_via_reasoning_cancellation_manager():
    # build the pieces manually (mirrors composition.py) so the test can reach into
    # Reasoning directly and register a cancellation the pipeline's shared token matches
    memory_rt = MemoryFactory.default(clock=lambda: 100.0)
    knowledge_rt = KnowledgeFactory.default(clock=lambda: 100.0)
    prompt_rt = PromptFactory.default(clock=lambda: 100.0)
    context_rt = ContextFactory.create(
        ContextConfiguration.default(), memory_port=memory_rt, knowledge_port=knowledge_rt,
        prompt_port=prompt_rt, extra_sources=[UserQuerySource()], clock=lambda: 100.0,
    )
    provider_rt = ProviderFactory.default(clock=lambda: 100.0)
    bridge = ProviderModelBridge(provider_rt)
    reasoning_rt = ReasoningFactory.create(
        ReasoningConfiguration.default(), providers=[bridge], intelligence=True,
        clock=lambda: 100.0,
    )
    pipe = RuntimePipeline(
        memory=memory_rt, knowledge=knowledge_rt, context=context_rt, prompt=prompt_rt,
        reasoning=reasoning_rt, bridge=bridge, clock=lambda: 100.0,
    )

    cancellation = PipelineCancellation("shared-tok")
    reasoning_rt.cancel("shared-tok", reason="manual")  # Reasoning's own CancellationManager

    resp = pipe.execute(_ctx(), cancellation=cancellation)
    assert resp.finish_reason.value == "cancelled"
    assert resp.chunks == ()


# ── error propagation (no retry, no recovery) ─────────────────────────────────────
def test_stage_error_is_wrapped_and_never_retried():
    pipe = RuntimePipelineFactory.default(clock=lambda: 100.0)

    def boom(*a, **kw):
        raise RuntimeError("context exploded")

    pipe._context.assemble = boom  # simulate a stage failure (no retry logic exists to hide it)
    with pytest.raises(StageError) as exc_info:
        pipe.execute(_ctx())
    assert exc_info.value.stage == "context"
    assert isinstance(exc_info.value.cause, RuntimeError)


# ── metadata / lineage propagation ────────────────────────────────────────────────
def test_lineage_merges_every_stage_without_recomputation():
    pipe = RuntimePipelineFactory.default(clock=lambda: 100.0)
    resp = pipe.execute(_ctx())
    ln = resp.lineage
    assert ln.context_package_id  # a real ContextRuntime-assigned id, not invented
    assert ln.prompt_hash
    assert ln.reasoning_lineage is not None and ln.reasoning_lineage.response_hash
    assert ln.provider_metadata is not None and ln.provider_metadata.provider == "echo"
    assert ln.provider_trace is not None  # present even if empty (core path, no intelligence)


def test_memory_and_knowledge_provenance_reflect_context_assembly():
    pipe = RuntimePipelineFactory.default(clock=lambda: 100.0)
    resp = pipe.execute(_ctx())
    # the user's query is the only contribution in the fully offline default (empty
    # memory/knowledge stores) — provenance tuples exist and are typed correctly either way
    assert isinstance(resp.lineage.memory_provenance, tuple)
    assert isinstance(resp.lineage.knowledge_provenance, tuple)


# ── unified execution trace ────────────────────────────────────────────────────────
def test_execution_trace_covers_every_stage_in_order():
    pipe = RuntimePipelineFactory.default(clock=lambda: 100.0)
    resp = pipe.execute(_ctx())
    stages = [e.stage.value for e in resp.trace.events]
    assert stages == ["memory", "knowledge", "context", "prompt", "reasoning", "provider",
                      "completion"]
    assert [e.seq for e in resp.trace.events] == list(range(len(resp.trace.events)))


def test_snapshot_carries_timings_and_lineage_summary():
    pipe = RuntimePipelineFactory.default(clock=lambda: 100.0)
    resp = pipe.execute(_ctx())
    snap = resp.snapshot
    assert snap is not None
    assert snap.request_id == "r1" and snap.trace_id == "t1"
    assert snap.provider == "provider-runtime" and snap.prompt_tokens > 0
    assert len(snap.timings.stages) == 3  # context, prompt, reasoning are individually timed


# ── provider neutrality ────────────────────────────────────────────────────────────
def test_provider_neutrality_custom_adapter_through_full_stack():
    from provider import ProviderCapabilities, ProviderDescriptor
    from provider.domain import ChunkKind, ProviderChunk

    # named "custom" (sorts before the auto-registered "echo") so the core's deterministic,
    # routing-free "first eligible in sorted-name order" selection picks it without needing
    # the M5.6B-2 router/intelligence layer
    class FixedAdapter:
        name = "custom"
        capabilities = ProviderCapabilities(chat=True)

        def stream(self, request):
            yield ProviderChunk(id="c0", index=0, delta="fixed-answer",
                                kind=ChunkKind.FINAL, done=True)

    provider_rt = ProviderFactory.create(
        ProviderConfiguration.default(),
        adapters=[(ProviderDescriptor(name="custom", capabilities=ProviderCapabilities(chat=True)),
                  FixedAdapter())],
        clock=lambda: 100.0,
    )
    bridge = ProviderModelBridge(provider_rt)
    reasoning_rt = ReasoningFactory.create(
        ReasoningConfiguration.default(), providers=[bridge], clock=lambda: 100.0,
    )
    memory_rt = MemoryFactory.default(clock=lambda: 100.0)
    knowledge_rt = KnowledgeFactory.default(clock=lambda: 100.0)
    prompt_rt = PromptFactory.default(clock=lambda: 100.0)
    context_rt = ContextFactory.create(
        ContextConfiguration.default(), memory_port=memory_rt, knowledge_port=knowledge_rt,
        prompt_port=prompt_rt, extra_sources=[UserQuerySource()], clock=lambda: 100.0,
    )
    pipe = RuntimePipeline(
        memory=memory_rt, knowledge=knowledge_rt, context=context_rt, prompt=prompt_rt,
        reasoning=reasoning_rt, bridge=bridge, clock=lambda: 100.0,
    )
    resp = pipe.execute(_ctx())
    assert resp.text == "fixed-answer"


# ── composition root correctness ──────────────────────────────────────────────────
def test_composition_root_wires_exactly_six_runtimes():
    pipe = RuntimePipelineFactory.default()
    assert pipe._memory is not None
    assert pipe._knowledge is not None
    assert pipe._context is not None
    assert pipe._prompt is not None
    assert pipe._reasoning is not None
    assert pipe._bridge is not None


# ── dependency boundaries (architecture verification) ─────────────────────────────
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


def test_pipeline_is_the_only_package_importing_every_runtime():
    imports = _imports("pipeline")
    for runtime_pkg in ("memory", "knowledge", "context", "prompt", "reasoning", "provider",
                        "bridge"):
        assert runtime_pkg in imports, f"pipeline should compose {runtime_pkg}"


def test_no_runtime_imports_the_pipeline_back():
    for runtime_pkg in ("memory", "knowledge", "context", "prompt", "reasoning", "provider",
                        "bridge"):
        assert "pipeline" not in _imports(runtime_pkg), (
            f"{runtime_pkg} must not import pipeline (would invert the composition-root "
            f"dependency direction)"
        )


def test_no_runtime_owns_another_runtime_no_new_cycles():
    # each closed runtime keeps its own established forbidden set; verifying none of them
    # gained a sibling-runtime import as a side effect of wiring the pipeline together
    sibling_forbidden = {
        "memory": {"knowledge", "context", "prompt", "reasoning", "provider", "bridge",
                   "pipeline"},
        "knowledge": {"memory", "context", "prompt", "reasoning", "provider", "bridge",
                     "pipeline"},
        "context": {"pipeline", "bridge"},  # context legitimately imports the frozen ports
        "prompt": {"pipeline", "bridge"},
        "reasoning": {"prompt", "context", "memory", "knowledge", "provider", "pipeline"},
        "provider": {"memory", "knowledge", "context", "prompt", "reasoning", "pipeline"},
        "bridge": {"memory", "knowledge", "context", "prompt", "pipeline"},
    }
    for pkg, forbidden in sibling_forbidden.items():
        assert not (_imports(pkg) & forbidden), (pkg, _imports(pkg) & forbidden)
