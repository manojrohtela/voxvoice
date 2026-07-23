# Runtime Pipeline (Epic 5, M5.7) — Runtime Integration

| | |
|---|---|
| **Scope** | The official, deterministic execution path composing all six closed Epic 5 AI runtimes into one call · **Status:** 🟢 Implemented (2026-07-23) |
| **Milestone** | M5.7 — Runtime Integration (implementation only; no design sub-milestone) |
| **Basis** | The six architecturally-closed runtimes: [Memory](memory-runtime.md), [Knowledge](knowledge-runtime.md), [Context](context-runtime.md), [Prompt](prompt-runtime.md), [Reasoning](reasoning-runtime.md), [Provider](provider-runtime.md); the M5.6B-2 `ProviderModelBridge` |
| **Governs** | `platform/pipeline/` (new composition-root package); one additive attribute on `platform/bridge/provider_model_bridge.py` |

> **Integration-only milestone.** No runtime was redesigned, refactored, or bypassed. This
> package owns composition, orchestration, propagation, and validation exclusively — every
> stage is invoked through its existing, closed public surface (`ContextRuntime.assemble`,
> `PromptRuntime.build`, `ReasoningRuntime.infer`, `ProviderModelBridge`). No new business
> capability was introduced.

---

## 1. Executive Summary

The **Runtime Pipeline** (`platform/pipeline/`) is the official execution path for VoxVoice's
AI stack: **Memory → Knowledge → Context → Prompt → Reasoning → Provider → Unified
Response**, as one deterministic, offline-testable call. It is the **composition root** —
the only module in the entire platform permitted to import every one of the six Epic 5
runtimes plus the `bridge` package — and it contains **no business logic of its own**: text,
usage, finish reason, and every lineage/trace fragment are read verbatim off the artifacts
the closed runtimes already produce (`AssembledContext`, `PromptPackage`, `ResponsePackage`,
`ProviderResponse`). It propagates one `RequestContext` (request id, session id, org,
conversation, trace id, metadata) through every stage, merges every stage's already-produced
lineage into one `PipelineLineage` (never recomputing any of it), produces one ordered
`PipelineExecutionTrace`, and honours one shared `PipelineCancellation` token — checked
between stages at the pipeline's own boundary, and forwarded natively into Reasoning's own
`InferenceRequest.cancellation_token` for its existing (M5.5B-2) `CancellationManager` to act
on mid-stage. `RuntimePipelineFactory.default()` builds the fully offline, deterministic
validation configuration — in-memory stores end to end, the deterministic
`EchoProviderAdapter` behind the `ProviderModelBridge` — exactly what "Runtime Validation"
exercises.

## 2. Runtime Ownership

**This milestone owns exclusively:** runtime composition, the composition root, the runtime
pipeline, orchestration, dependency injection, request propagation, context propagation,
metadata propagation, trace propagation, error propagation, cancellation propagation,
streaming propagation, lifecycle validation.

**It owns nothing else** — no memory management, no retrieval, no context assembly logic, no
prompt construction, no inference orchestration, no provider routing. Every one of those
remains exactly where its closed runtime already put it.

## 3. Architecture Diagram

```
                         RequestContext (request_id, session_id, org_id,
                              conversation_id, user_id, query, trace_id)
                                          │
                                          ▼
┌──────────────────────────── RuntimePipeline.execute() ─────────────────────────────┐
│                                                                                     │
│  ContextRequest(session_id, org_id, conversation_id, query)                         │
│                         │                                                          │
│                         ▼                                                          │
│              ContextRuntime.assemble()  ── fans out internally to ──▶  MemoryPort   │
│                         │                                        └──▶ KnowledgePort │
│                         │                                        └──▶ PromptPort    │
│                         ▼  (frozen AssembledContext)                                │
│              PromptRuntime.build(PromptRequest, assembled)                          │
│                         │                                                          │
│                         ▼  (PromptPackage)                                         │
│      pipeline.translate.prompt_package_to_inference_request(...)                    │
│                         │                                                          │
│                         ▼  (InferenceRequest)                                       │
│              ReasoningRuntime.infer(...)  ── ModelProvider.stream ──▶ ProviderModelBridge │
│                         │                                        └──▶ ProviderRuntime.execute │
│                         ▼  (ResponsePackage; bridge.last_response == ProviderResponse) │
│                  UnifiedResponse (text, usage, lineage, trace, snapshot)             │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

Memory and Knowledge are never called directly by the pipeline — Context's own
`MemorySource`/`KnowledgeSource` (over the injected `MemoryPort`/`KnowledgePort`) already do
that inside the single `assemble()` call; calling them again at the pipeline level would
duplicate retrieval non-deterministically. Their pipeline-trace contribution is read from the
provenance Context already recorded on each `AssembledContext` item.

## 4. Dependency Diagram

```
pipeline ──▶ core, memory, knowledge, context, prompt, reasoning, provider, bridge, tenant
             (the composition root — this is its entire purpose; every other package
              in the platform is forbidden from this breadth of import)

memory, knowledge, reasoning, provider ──▶ core[, tenant]         (unchanged; closed)
context, prompt                        ──▶ core[, tenant]         (unchanged; closed)
bridge                                  ──▶ core, provider, reasoning (unchanged; closed)

None of the seven runtime/bridge packages imports `pipeline` — verified by AST scan
(`test_no_runtime_imports_the_pipeline_back`). No new cycle was introduced by wiring them
together (`test_no_runtime_owns_another_runtime_no_new_cycles`).
```

## 5. Components

```
pipeline/
  request_context.py   RequestContext — one propagated identity (no duplication)
  cancellation.py       PipelineCancellation — one token, checked between every stage
  errors.py              PipelineError / StageError / PipelineCancelled / PipelineTimeout
  trace.py                PipelineStage enum + PipelineExecutionTrace + PipelineTimings
  lineage.py               PipelineLineage + SourceProvenance — merges, never recomputes
  observability.py          Metric names + PipelineExecutionSnapshot (one per execution)
  sources.py                 UserQuerySource(ContextSource) — projects RequestContext.query
                             into a USER_INSTRUCTION item (the documented "composition root
                             injects user input" seam — no Context Runtime change)
  translate.py                prompt_package_to_inference_request() — the composition-root
                             PromptPackage → InferenceRequest bridge Reasoning's own docs
                             already anticipated
  response.py                  UnifiedResponse — self-sufficient; nothing recomputed
  pipeline.py                   RuntimePipeline — the orchestrator (stage execution,
                             ordering, cancellation, streaming, error wrapping)
  composition.py                 RuntimePipelineFactory — the composition root
```

One additive change outside `pipeline/`: `bridge.ProviderModelBridge` gained a
`last_response: ProviderResponse | None` attribute, set immediately before yielding chunks in
`stream()`. The frozen `ModelProvider.stream` contract (`Iterator[ResponseChunk]`) has no path
to return the richer `ProviderResponse` (its `ProviderMetadata` + `ProviderExecutionTrace`);
rather than re-executing the call a second time (non-deterministic duplication) or widening
the frozen port, the bridge records what it just produced so the pipeline can read it
immediately after `reasoning.infer(...)` returns. No signature change, no behaviour change.

## 6. Request Context

`RequestContext` is the single propagated identity: `request_id`, `session_id`, `trace_id`,
`org_id`, `conversation_id`, `user_id`, `query`, `metadata`. Every stage's own frozen request
shape (`ContextRequest`, `PromptRequest`, `InferenceRequest`'s `ReasoningScope`,
`ProviderRequest`'s `ProviderScope`) is derived from this one source at the point the pipeline
constructs it — never a second, competing identifier.

## 7. Runtime Lineage

`PipelineLineage` merges, without recomputing anything:
- **Memory / Knowledge** — `SourceProvenance` tuples read from the `AssembledContext` items'
  already-recorded provenance (`source_runtime`/`source_id`/`content_hash`/`original_score`).
- **Context** — the actual `ContextPackage.id` (read via `ContextRuntime.history()` after
  assembly) + `used_tokens`.
- **Prompt** — `prompt_hash` + `prompt_fingerprint`, carried verbatim from `PromptPackage`.
- **Reasoning** — the complete `ReasoningLineage` object, embedded as-is.
- **Provider** — `ProviderMetadata` + `ProviderExecutionTrace`, embedded as-is (recovered via
  `bridge.last_response`, §5).

## 8. Unified Execution Trace

`PipelineExecutionTrace` is ordered and seq-stamped, exactly mirroring the
`ReasoningExecutionTrace`/`ProviderExecutionTrace` pattern: `memory → knowledge → context →
prompt → reasoning → provider → completion`. Metadata only — it never changes execution.
Existing per-runtime traces (Reasoning's, Provider's) are never redesigned; they are carried
through unmodified inside `PipelineLineage`.

## 9. Streaming Pipeline

Reasoning's own (M5.5, closed) engine already materializes `list(adapter.stream(request))`
before returning — there is no incremental network relay to preserve in this offline,
synchronous design. `RuntimePipeline.stream()` therefore yields the **exact** `ResponsePackage
.chunks` sequence Reasoning produced — a single ordered pass, never a second
independently-computed stream (`test_streaming_yields_the_exact_reasoning_chunk_sequence`
asserts identity with `execute()`'s own chunks).

## 10. Error Propagation

Every stage call is wrapped: an exception raised inside `ContextRuntime.assemble`,
`PromptRuntime.build`, or `ReasoningRuntime.infer` is caught once and re-raised as a
`StageError(stage, cause)` — **never retried, never recovered** (retry/recovery remain each
runtime's own concern, e.g. Reasoning's `RetryController`). A cancelled token raises
`PipelineCancelled` instead, distinguishable from a genuine stage failure.

## 11. Cancellation Propagation

One `PipelineCancellation` token. The pipeline checks `is_cancelled()` **before** every stage
it directly invokes (Context, Prompt, Reasoning) since Memory/Knowledge/Context/Prompt's
frozen request shapes carry no cancellation field of their own (adding one would be a runtime
redesign — out of scope). The same token string is additionally forwarded into
`InferenceRequest.cancellation_token`, so a caller who registers it with Reasoning's own
`ReasoningRuntime.cancel(token)` (M5.5B-2, opt-in) gets genuine mid-stage cancellation inside
Reasoning too — verified end-to-end
(`test_cross_runtime_cancellation_via_reasoning_cancellation_manager`).

## 12. Dependency Validation

`test_pipeline_is_the_only_package_importing_every_runtime`,
`test_no_runtime_imports_the_pipeline_back`, and
`test_no_runtime_owns_another_runtime_no_new_cycles` (all AST-based, mirroring every closed
runtime's own dependency-rules test) certify: `pipeline` is the sole composition root; no
runtime imports it back; no sibling-runtime import was introduced anywhere as a side effect of
wiring the six runtimes together.

## 13. Observability

`PipelineExecutionSnapshot` — one per execution: per-stage `PipelineTimings`, the
`PipelineExecutionTrace`, the `PipelineLineage`, the provider/model actually used, prompt/
completion token counts, and streaming statistics (streamed flag + chunk count). No
dashboards; a pure read-side aggregate of values the stages/pipeline already computed.

## 14. Runtime Validation

`RuntimePipelineFactory.default()` — the fully offline, deterministic configuration (in-memory
stores throughout, `EchoProviderAdapter` behind the `ProviderModelBridge`, no intelligence
layers enabled anywhere) — is exercised end-to-end by
`test_end_to_end_execution_is_deterministic_offline`: two independently-constructed default
pipelines given the same `RequestContext` produce byte-identical `response_hash`es.

## 15. Tests

`tests/test_epic5_runtime_pipeline.py` (distinct from the pre-existing, unrelated legacy
M2C.2 voice-pipeline test of the same generic name under `runtime.pipeline.pipeline`) — 15
tests: deterministic end-to-end execution, no-business-logic invariant, streaming identity
(no fork), pipeline-level cancellation, cross-runtime cancellation via Reasoning's own
manager, stage-error wrapping (no retry), lineage merge completeness, memory/knowledge
provenance typing, full-trace ordering, the observability snapshot, provider neutrality
through the whole stack (a custom adapter reachable end-to-end), composition-root wiring, and
the three dependency-boundary verifications. Full repository suite: **636 passed**, ruff
clean.

## 16. Documentation

- `docs/architecture/runtime-pipeline.md` (this document, new).
- `.voxvoice/DECISION_LOG.md` — DL-0040.
- `docs/INDEX.md`, `docs/roadmap/migration-progress.md` — updated.

## 17. Boundary Verification

- **Ownership:** composition/orchestration/propagation/validation only — verified by
  inspection (no new domain logic anywhere in `pipeline/`).
- **No redesign:** `git status` confirms zero changes under `memory/`, `knowledge/`,
  `context/`, `prompt/`, `reasoning/`, `provider/`; the sole non-`pipeline/` change is the
  additive `last_response` attribute on `bridge/provider_model_bridge.py`.
- **Dependency direction:** `pipeline` is the only importer of all six runtimes + `bridge`;
  none of them import it back (AST-enforced).
- **Determinism / replay safety:** content-hash ids from every closed runtime propagate
  unchanged; the pipeline adds no randomness; two independent runs produce identical
  `response_hash`.
- **Streaming integrity:** one chunk sequence, no fork.
- **Provider neutrality:** proven end-to-end with a custom `AdapterPort` reachable through
  the full six-stage stack.

## 18. Readiness for M5.8

The Runtime Pipeline is certified as the official execution path for VoxVoice's AI stack.
Repository certified ready for **M5.8 Production Hardening**.
