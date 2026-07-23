"""RuntimePipeline (M5.7) — the official execution path: Memory → Knowledge → Context →
Prompt → Reasoning → Provider → Unified Response.

Pure orchestration — **no business logic**. Every stage is invoked through its existing,
closed public surface (`ContextRuntime.assemble`, `PromptRuntime.build`,
`ReasoningRuntime.infer`, with Provider reached only via the already-shipped
`ProviderModelBridge`); none of their internals are touched or bypassed. Memory and
Knowledge are not called directly by the pipeline — the Context Runtime already fans out to
them via its own `MemorySource`/`KnowledgeSource` ports in one `assemble()` call (calling
them again here would duplicate retrieval non-deterministically); their pipeline-trace
contribution is read from the provenance the Context Runtime already recorded.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from bridge import ProviderModelBridge
from context.runtime import ContextRuntime
from core.ports.context import ContextRequest
from prompt.request import PromptRequest
from prompt.runtime import PromptRuntime
from reasoning.domain import ResponseChunk
from reasoning.runtime import ReasoningRuntime

from .cancellation import PipelineCancellation
from .errors import PipelineCancelled, StageError
from .lineage import PipelineLineage, SourceProvenance
from .observability import PipelineExecutionSnapshot
from .request_context import RequestContext
from .response import UnifiedResponse
from .trace import PipelineExecutionTrace, PipelineStage, PipelineTimings
from .translate import prompt_package_to_inference_request


class RuntimePipeline:
    """Wires exactly the six closed Epic 5 runtimes into one deterministic call. Constructed
    by `RuntimePipelineFactory` (the composition root) — never build one by hand from
    partially-wired runtimes, since the stage order and the bridge identity are fixed."""

    def __init__(
        self,
        *,
        memory,
        knowledge,
        context: ContextRuntime,
        prompt: PromptRuntime,
        reasoning: ReasoningRuntime,
        bridge: ProviderModelBridge,
        clock: Callable[[], float] | None = None,
    ) -> None:
        import time

        self._memory = memory
        self._knowledge = knowledge
        self._context = context
        self._prompt = prompt
        self._reasoning = reasoning
        self._bridge = bridge
        self._clock = clock or time.time

    def execute(
        self,
        ctx: RequestContext,
        *,
        cancellation: PipelineCancellation | None = None,
        streaming: bool = True,
    ) -> UnifiedResponse:
        cancellation = cancellation or PipelineCancellation(ctx.trace_id)
        trace = PipelineExecutionTrace()
        timings = PipelineTimings()

        def guard(stage: PipelineStage) -> None:
            if cancellation.is_cancelled():
                raise PipelineCancelled(stage.value, reason=cancellation.reason)

        def run(stage: PipelineStage, fn: Callable[[], object]) -> object:
            guard(stage)
            start = self._clock()
            try:
                result = fn()
            except PipelineCancelled:
                raise
            except Exception as exc:  # noqa: BLE001 — wrapped, never retried/recovered here
                raise StageError(stage.value, exc) from exc
            nonlocal timings
            timings = timings.with_stage(stage, self._clock() - start)
            return result

        # ── CONTEXT (internally fans out to Memory/Knowledge/Prompt sources) ──────
        context_request = ContextRequest(
            session_id=ctx.session_id, org_id=ctx.org_id,
            conversation_id=ctx.conversation_id, query=ctx.query,
        )
        assembled = run(PipelineStage.CONTEXT, lambda: self._context.assemble(context_request))
        now = self._clock()

        # `AssembledContext.items` uses the frozen narrow `core.ports.context.ContextItem`
        # shape — `provenance` there is a plain dict (never the rich `context.domain.
        # Provenance`), so source-runtime filtering reads it by key.
        mem_items = [i for i in assembled.items
                    if i.provenance.get("source_runtime") == "memory"]
        know_items = [i for i in assembled.items
                     if i.provenance.get("source_runtime") == "knowledge"]
        trace = trace.with_event(PipelineStage.MEMORY, detail=f"{len(mem_items)} items", at=now)
        trace = trace.with_event(
            PipelineStage.KNOWLEDGE, detail=f"{len(know_items)} items", at=now
        )
        trace = trace.with_event(
            PipelineStage.CONTEXT,
            detail=f"{len(assembled.items)} items, {assembled.used_tokens} tokens", at=now,
        )
        context_history = self._context.history(ctx.session_id)
        context_package_id = context_history[-1].id if context_history else ""

        # ── PROMPT ────────────────────────────────────────────────────────────────
        prompt_request = PromptRequest(
            session_id=ctx.session_id, org_id=ctx.org_id,
            conversation_id=ctx.conversation_id, user_id=ctx.user_id,
        )
        package = run(PipelineStage.PROMPT, lambda: self._prompt.build(prompt_request, assembled))
        trace = trace.with_event(PipelineStage.PROMPT, detail=package.prompt_hash, at=self._clock())

        # ── REASONING (streams through Provider via the ProviderModelBridge) ──────
        inference_request = prompt_package_to_inference_request(
            package, provider=self._bridge.name, streaming=streaming,
            cancellation_token=cancellation.token,
        )
        response_package = run(
            PipelineStage.REASONING, lambda: self._reasoning.infer(inference_request)
        )
        trace = trace.with_event(
            PipelineStage.REASONING, detail=response_package.response_hash, at=self._clock()
        )

        provider_response = self._bridge.last_response
        trace = trace.with_event(
            PipelineStage.PROVIDER,
            detail=provider_response.provider if provider_response is not None else "",
            at=self._clock(),
        )
        trace = trace.with_event(
            PipelineStage.COMPLETION, detail=response_package.response_hash, at=self._clock()
        )

        lineage = PipelineLineage(
            memory_provenance=_provenance_of(mem_items),
            knowledge_provenance=_provenance_of(know_items),
            context_package_id=context_package_id,
            context_used_tokens=assembled.used_tokens,
            prompt_hash=package.prompt_hash,
            prompt_fingerprint=package.prompt_fingerprint,
            reasoning_lineage=response_package.lineage,
            provider_metadata=provider_response.metadata if provider_response else None,
            provider_trace=provider_response.trace if provider_response else None,
        )
        snapshot = PipelineExecutionSnapshot(
            request_id=ctx.request_id, trace_id=ctx.trace_id, timings=timings, trace=trace,
            lineage=lineage, provider=response_package.provider, model=response_package.model,
            prompt_tokens=response_package.usage.prompt_tokens,
            completion_tokens=response_package.usage.completion_tokens,
            streamed=response_package.streaming.streamed,
            chunk_count=len(response_package.chunks),
        )
        return UnifiedResponse(
            request_id=ctx.request_id, trace_id=ctx.trace_id, text=response_package.text,
            chunks=response_package.chunks, usage=response_package.usage,
            finish_reason=response_package.finish_reason, provider=response_package.provider,
            model=response_package.model, response_hash=response_package.response_hash,
            lineage=lineage, trace=trace, snapshot=snapshot,
        )

    def stream(
        self,
        ctx: RequestContext,
        *,
        cancellation: PipelineCancellation | None = None,
    ) -> Iterator[ResponseChunk]:
        """Yield the exact chunk sequence Reasoning produced — **no fork**: `execute` already
        materializes the full `ResponsePackage` (Reasoning's own closed engine collects the
        provider's stream before returning, a M5.5 design decision this milestone does not
        redesign), so this is a single, ordered pass over that one sequence, never a second
        independently-computed stream."""
        result = self.execute(ctx, cancellation=cancellation, streaming=True)
        yield from result.chunks


def _provenance_of(items) -> tuple[SourceProvenance, ...]:
    return tuple(
        SourceProvenance(
            source_runtime=str(i.provenance.get("source_runtime", "")),
            source_id=str(i.provenance.get("source_id", "")),
            content_hash=str(i.provenance.get("content_hash", "")),
            score=float(i.provenance.get("original_score", i.score)),
        )
        for i in items
    )
