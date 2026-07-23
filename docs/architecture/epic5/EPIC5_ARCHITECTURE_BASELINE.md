# Epic 5 — AI Runtime Platform: Architecture Baseline (v1.0)

| | |
|---|---|
| **Purpose** | The frozen, point-in-time reference snapshot of Epic 5's architecture at official closure |
| **Status** | 🔒 **FROZEN** — Epic 5 architecture baseline, tagged `epic5-runtime-platform-v1.0` |
| **Basis** | M5.0–M5.8 (Foundation through Production Hardening & Closure); see [epic5-closure-certification.md](../epic5-closure-certification.md) for the full audit this baseline follows |
| **Date** | 2026-07-24 |

> This document is a **snapshot**, not a design doc — it records what exists, verbatim,
> at the moment Epic 5 closed. Each runtime's own architecture doc
> (`docs/architecture/{runtime}-runtime.md`) remains the authoritative source for design
> rationale; this baseline exists so a future reader (or Epic 6) never has to reconstruct
> "what shipped" from git history.

---

## 1. Final Package Structure

```
platform/
├── core/                    Foundation (M5.0) — 33 files
│   ├── errors.py              Typed platform error hierarchy (PlatformError root)
│   ├── events.py               EventType/EventSource/Event — the bus vocabulary
│   ├── fsm/                     StateMachine/StateMachineDefinition (shared FSM framework)
│   ├── module/                   BusConsumer/RuntimeModule/ModuleState
│   ├── policies.py                Shared policy value objects (RetryPolicy, BudgetPolicy, ...)
│   ├── ports/                      Frozen interfaces — see §5
│   └── providers/                   Store/LLM capability contracts (DocumentStore, MetadataStore, LLMProvider)
│
├── tenant/                   Foundation isolation seam (leaf dep) — 3 files
│   └── TenantContext / OrganizationContext / Authorizer / QuotaEnforcer / AuditSink
│
├── memory/                   M5.1 Memory Runtime — 24 files — CLOSED
├── knowledge/                M5.2 Knowledge Runtime — 39 files — CLOSED
├── context/                  M5.3 Context Runtime — 31 files — CLOSED
├── prompt/                   M5.4 Prompt Runtime — 31 files — CLOSED
├── reasoning/                M5.5 Reasoning Runtime — 29 files — CLOSED
├── provider/                 M5.6 Provider Runtime — 32 files — CLOSED
├── bridge/                   M5.6B-2 composition-root seam — 2 files — CLOSED
│   └── provider_model_bridge.py   ProviderModelBridge(ModelProvider)
└── pipeline/                 M5.7 Runtime Pipeline (composition root) — 12 files — CLOSED
    ├── request_context.py         RequestContext
    ├── cancellation.py             PipelineCancellation
    ├── errors.py                    PipelineError / StageError / PipelineCancelled / PipelineTimeout
    ├── trace.py                      PipelineStage / PipelineExecutionTrace / PipelineTimings
    ├── lineage.py                     PipelineLineage / SourceProvenance
    ├── observability.py                PipelineExecutionSnapshot / Metric
    ├── sources.py                       UserQuerySource(ContextSource)
    ├── translate.py                      prompt_package_to_inference_request
    ├── response.py                        UnifiedResponse
    ├── pipeline.py                          RuntimePipeline
    └── composition.py                       RuntimePipelineFactory
```

Every one of memory/knowledge/context/prompt/reasoning/provider independently mirrors the
same internal shape (established at M5.1, held through M5.6):
`domain.py · ids.py · lifecycle.py · ports.py · <core-engine>.py · repositories/ · stores/ ·
serializer.py · config.py · factory.py · runtime.py · observability.py · events.py`, plus an
intelligence layer (`policies.py`, `tenancy.py`, and runtime-specific engines) added in each
package's second milestone. This repetition is deliberate (DL-0024 through DL-0039): each
runtime must remain independently acyclic, so no shared base class crosses runtime
boundaries.

## 2. Runtime Ownership Matrix

| Runtime | Owns | Never owns |
|---|---|---|
| **Memory** | Memory storage, tiers, retrieval ranking, memory lifecycle | Context assembly, reasoning, prompts, tools |
| **Knowledge** | Document/chunk lifecycle, knowledge retrieval, embeddings | Memory, context, prompts, reasoning |
| **Context** | Assembling Memory + Knowledge + Prompt + input into one budgeted package | Storage, retrieval logic itself, prompt construction, reasoning |
| **Prompt** | Template resolution, section rendering, budget, the constructed message set | Context assembly, model calls, tool execution |
| **Reasoning** | Inference orchestration, streaming/retry/tool-coordination/cancellation, response assembly | Memory/Knowledge/Context/Prompt, the model itself, tool execution, provider selection |
| **Provider** | Provider registration/discovery/routing/failover/health/policy/tenancy, unified streaming/response/usage/metadata normalization | Reasoning orchestration, tool execution, transport, billing |
| **bridge** (composition seam) | Translating between Reasoning's `ModelProvider` port and Provider's `ProviderRuntime.execute` | Any business logic — pure translation |
| **pipeline** (composition root) | Stage ordering, request/context/metadata/trace/error/cancellation/streaming propagation, lifecycle validation | Any business logic — reads every value verbatim off the runtimes it composes |

No runtime owns another runtime. No runtime imports another runtime's package (only frozen
`core.ports.*` interfaces cross runtime boundaries, plus the two composition-layer exceptions
above, which exist *for* that purpose).

## 3. Dependency Graph

```
core, tenant                                    (foundation — no dependencies within Epic 5)
  ▲
  │  (each of the six runtimes imports ONLY core[, tenant])
  │
memory ──┐
knowledge ┤
context ──┤──▶ core[, tenant]     (context additionally consumes MemoryPort/KnowledgePort/
prompt ───┤                        PromptPort — the frozen ports, never the packages)
reasoning ┤
provider ─┘

bridge ──▶ core, provider, reasoning      (the ONE package importing two sibling runtimes)

pipeline ──▶ core, tenant,
             memory, knowledge, context, prompt, reasoning, provider, bridge
             (the composition root — the ONLY package with this import breadth)
```

**Acyclicity is AST-enforced**, not just documented: each runtime carries its own
`test_<runtime>_dependency_rules` test; `bridge` and `pipeline` carry their own equivalents.
Zero cyclic imports exist (re-verified at M5.8 closure).

## 4. Runtime Interaction Graph (request-time call sequence)

```
Caller
  │  RequestContext(request_id, session_id, org_id, conversation_id, user_id, query, trace_id)
  ▼
RuntimePipeline.execute()
  │
  ├─▶ ContextRuntime.assemble(ContextRequest)
  │     ├─▶ MemoryPort.retrieve(MemoryQuery)         [MemoryRuntime, injected as the port]
  │     ├─▶ KnowledgePort.retrieve(KnowledgeQuery)    [KnowledgeRuntime, injected as the port]
  │     ├─▶ PromptPort.render(...)                    [PromptRuntime, injected as the port]
  │     └─▶ UserQuerySource.fetch(...)                 [pipeline-injected, projects the query]
  │     ◀── AssembledContext
  │
  ├─▶ PromptRuntime.build(PromptRequest, AssembledContext)
  │     ◀── PromptPackage
  │
  ├─▶ prompt_package_to_inference_request(PromptPackage)   [pipeline.translate — pure mapping]
  │     ◀── InferenceRequest
  │
  ├─▶ ReasoningRuntime.infer(InferenceRequest)
  │     └─▶ ModelProvider.stream(InferenceRequest)     [ProviderModelBridge]
  │           └─▶ ProviderRuntime.execute(ProviderRequest)
  │                 └─▶ AdapterPort.stream(ProviderRequest)   [EchoProviderAdapter, or any
  │                                                             registered AdapterPort]
  │                 ◀── ProviderResponse  (bridge.last_response records it)
  │           ◀── Iterator[ResponseChunk]
  │     ◀── ResponsePackage
  │
  ▼
UnifiedResponse (text, usage, finish_reason, lineage, trace, snapshot)
```

Memory and Knowledge are **never** called directly by the pipeline — only through Context's
own internal fan-out, over the frozen ports.

## 5. Public Ports (frozen, `core.ports.*`)

| Port | File | Implemented by |
|---|---|---|
| `MemoryPort` | `core/ports/memory.py` | `MemoryRuntime` |
| `KnowledgePort` | `core/ports/knowledge.py` | `KnowledgeRuntime` |
| `ContextPort` | `core/ports/context.py` | `ContextRuntime` |
| `PromptPort` | `core/ports/prompt.py` | `PromptRuntime` |
| `ReasoningPort` | `core/ports/reasoning.py` | `ReasoningRuntime` |
| `ToolPort` | `core/ports/tool.py` | *(not yet implemented — Epic 6 seam)* |
| `WorkflowPort`-equivalent (`workflow.py`) | `core/ports/workflow.py` | *(not yet implemented — Epic 6 seam)* |
| `AgentPort`-equivalent (`agent.py`) | `core/ports/agent.py` | *(not yet implemented — Epic 6 seam)* |

Runtime-internal (non-`core`) seams, also frozen at closure:

| Seam | File | Default implementation |
|---|---|---|
| `ModelProvider` | `reasoning/ports.py` | `EchoProvider` (dev/test); `ProviderModelBridge` (production seam) |
| `AdapterPort` | `provider/ports.py` | `EchoProviderAdapter` |
| `ContextSource` | `context/ports.py` | `MemorySource` / `KnowledgeSource` / `PromptSource` / `StaticContextSource`; `pipeline.UserQuerySource` |

## 6. Bridges

**`ProviderModelBridge`** (`platform/bridge/provider_model_bridge.py`) — the single
cross-runtime bridge in the entire platform. Implements Reasoning's frozen `ModelProvider`
by delegating to an injected `ProviderRuntime`. Neither `reasoning` nor `provider` imports
the other; only `bridge` imports both. Carries one additive inspection attribute,
`last_response: ProviderResponse | None`, set immediately before yielding chunks in
`stream()` (added at M5.7 so the Runtime Pipeline can recover Provider-level lineage without
re-executing the call).

No other bridge exists. Future bridges (e.g. a Tool↔Reasoning bridge, once a Tool Runtime
exists) belong in this same package, following the identical pattern.

## 7. Composition Root

**`RuntimePipelineFactory`** (`platform/pipeline/composition.py`) is the sole composition
root for the AI Runtime Platform — the only code in the repository permitted to construct
and wire all six runtimes plus the bridge together. `RuntimePipelineFactory.default()`
produces the fully offline, deterministic reference configuration (in-memory stores
throughout, `EchoProviderAdapter` behind `ProviderModelBridge`, no intelligence layers
enabled). `RuntimePipelineFactory.create(...)` accepts per-runtime configuration/intelligence
overrides for callers who need a non-default wiring, without ever bypassing a runtime
boundary.

## 8. Extension Points (frozen interfaces, proven live at closure)

| Extension point | Where | Proven by |
|---|---|---|
| `ContextSource` | `context/ports.py` | `pipeline.UserQuerySource` — a new source registered from outside `context/` with zero `context` changes |
| `ModelProvider` | `reasoning/ports.py` | `ProviderModelBridge` — a new implementation with zero `reasoning` changes |
| `AdapterPort` | `provider/ports.py` | Ad hoc test adapters (`FixedAdapter`, `BoomAdapter`) — zero `provider` changes |
| `AsyncModelProvider`, `ReasoningConsumer`, `AgentBridge`, `WorkflowBridge`, `ToolProvider`, `ResponseOptimizer`, `ReasoningOptimizer`, `MultiModalResponder` | `reasoning/extensions.py` | Interfaces only — not yet exercised (Epic 6 seams) |
| `AsyncAdapter`, `BatchAdapter`, `StreamingAdapter`, `LocalAdapter`, `EnterpriseAdapter`, `GPUAdapter`, `EdgeAdapter`, `ConsensusProvider`, `EnsembleProvider`, `ParallelProvider`, `SpeculativeProvider`, `MultimodalProvider` | `provider/extensions.py` | Interfaces only — not yet exercised (Epic 6 seams; real vendor adapters land here) |
| `core.ports.tool.ToolPort` | `core/ports/tool.py` | Consumed by Reasoning's `ToolDecisionCoordinator` (M5.5B-2); no concrete Tool Runtime yet |

## 9. ADR References

| ADR | Title | Relevance to Epic 5 |
|---|---|---|
| ADR-0001 | First-party runtime | Established the substrate Epic 5's runtimes are built on |
| ADR-0002 | Substrate behind contracts | The `core.ports.*` frozen-interface pattern every Epic 5 runtime follows |
| ADR-0003 | Two-plane architecture | Control plane / runtime plane split; Epic 5 lives in the runtime plane |
| ADR-0004 | Event-driven core | The `core.events`/bus foundation every Epic 5 runtime publishes standard envelopes to |
| ADR-0005 | Streaming-first Turn Intelligence | Predates Epic 5; the streaming-first principle Epic 5's `ResponseChunk`/`ProviderChunk` shapes continue |
| ADR-0006 | Single system of record | Informs the "temporary vs. durable" repository distinction every Epic 5 runtime observes |
| ADR-0007 | Keep control plane, delete adapters | Legacy-pipeline scoping decision; not directly load-bearing for Epic 5 but preserves the surrounding architecture Epic 5 was built inside |
| P-ADR-0001 | Implementation profile v1 | The vendor/framework profile Epic 5's future real adapters (Epic 6) will need to honour |

Constitution §A6 (Provider Architecture) and §A12 (additive-only evolution) are the two
sections most directly realized by Epic 5 (Provider Runtime; the entire "additive extension,
never redesign" discipline across all eight milestone pairs).

## 10. Frozen APIs

The following are **frozen** as of `epic5-runtime-platform-v1.0` — changes require a new ADR
or an explicit additive-extension milestone, never a silent edit:

- All eight `core.ports.*` interfaces (§5).
- Every runtime's public `__init__.py` export surface (`memory`, `knowledge`, `context`,
  `prompt`, `reasoning`, `provider`).
- `bridge.ProviderModelBridge`'s public shape (`__init__(runtime, *, name)`, `stream()`,
  `last_response`).
- `pipeline`'s public shape: `RuntimePipeline.{execute,stream}`,
  `RuntimePipelineFactory.{create,default}`, `RequestContext`, `UnifiedResponse`,
  `PipelineCancellation`, `PipelineLineage`, `PipelineExecutionTrace`,
  `PipelineExecutionSnapshot`.
- Every runtime's `*Configuration` frozen dataclass shape (fields may gain additive,
  defaulted entries in a future intelligence milestone; existing fields never change type or
  meaning).
- Every domain artifact's content-hash id scheme (SHA-256-derived, prefixed per runtime:
  `mem_`/`kn_`/`cp_`/`pp_`/`ir_`/`rp_`/`pr_`/`pp_` families — see each runtime's `ids.py`).

## 11. Repository Metrics (at closure)

| Metric | Value |
|---|---|
| Epic 5 packages | 8 (`memory`, `knowledge`, `context`, `prompt`, `reasoning`, `provider`, `bridge`, `pipeline`) |
| Foundation packages | 2 (`core` — 33 files; `tenant` — 3 files) |
| Files per package | memory 24 · knowledge 39 · context 31 · prompt 31 · reasoning 29 · provider 32 · bridge 2 · pipeline 12 |
| Total tests (repository-wide) | 636, all passing |
| Epic-5-specific tests | 365 |
| Dependency/boundary tests | 12, all passing |
| Ruff errors (Epic 5 scope) | 0 |
| Decision Log entries | 41 (DL-0001–DL-0041) |
| Offline full-pipeline execution cost | ~0.93 ms/execution |
| Milestone commits (linear, M5.0→M5.8) | 16 |

## 12. Baseline Statement

This document, together with `docs/architecture/epic5-closure-certification.md` and the 41
entries in `.voxvoice/DECISION_LOG.md`, constitutes the complete, frozen record of Epic 5 at
the moment of the `epic5-runtime-platform-v1.0` tag. Any future change to a file under
`platform/{memory,knowledge,context,prompt,reasoning,provider,bridge,pipeline,core,tenant}/`
must be either (a) a genuine bug fix, or (b) an explicitly-scoped additive extension
documented in a new Decision Log entry — never a silent architectural change.
