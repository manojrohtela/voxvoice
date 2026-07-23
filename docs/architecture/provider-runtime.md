# Provider Runtime (Epic 5, M5.6) — Design

| | |
|---|---|
| **Scope** | The single execution boundary between the VoxVoice Runtime and every external AI provider · **Status:** 🔒 Closed (additive extensions + bug fixes only) — core M5.6B-1 + intelligence M5.6B-2 |
| **Milestone** | M5.6A — Design (approved) · M5.6B-1 — Core (2026-07-23, additive) · M5.6B-2 — Intelligence + Closure (2026-07-23, additive) |
| **Basis** | The frozen M5.0 [AI Runtime Foundation](ai-runtime-foundation.md) (`core.providers.llm.LLMProvider`, `EventType.PROVIDER_CONNECTED/DISCONNECTED`, `EventSource.PROVIDER_REGISTRY`); Constitution §A6 (Provider Architecture) + §A10 (fault tolerance); the closed [Memory](memory-runtime.md)/[Knowledge](knowledge-runtime.md)/[Context](context-runtime.md)/[Prompt](prompt-runtime.md)/[Reasoning](reasoning-runtime.md) Runtime patterns |
| **Governs** | `platform/provider/` + `platform/bridge/` (the `ProviderModelBridge` composition-root seam) |

> **Design-only milestone.** No implementation, no runtime code. This is the approved
> architecture the M5.6B milestones build to. It redesigns **nothing** already closed
> (Foundation, Memory, Knowledge, Context, Prompt, Reasoning, Ports, Repositories, Events);
> where a closed runtime seems insufficient it is classified as an *additive extension* or a
> *bug*. Reasoning's `ModelProvider` port is unchanged — Provider Runtime ships new
> implementations of it (composition-root bridges), never a modification to Reasoning.

---

## 1. Executive Summary

The **Provider Runtime** is the sole boundary through which VoxVoice talks to external AI
providers (OpenAI, Claude, Gemini, Groq, Together AI, Fireworks, Azure OpenAI, Ollama, local
models, and every future vendor). It owns **provider registration, discovery, lifecycle,
capability cataloguing, routing/selection, failover/fallback, load balancing, cost- and
latency-aware routing, region routing, health, unified streaming, unified response
normalization, usage normalization, metadata normalization, and provider observability** — and
**nothing else**. Everything above it (Memory, Knowledge, Context, Prompt, Reasoning) stays
**provider-agnostic**: none of those runtimes may ever know whether a given call executes on
OpenAI, Claude, Gemini, or a local model. Provider Runtime realizes the Constitution's §A6
**Provider Registry** vision, generalized to LLM-class capability providers, and supplies
Reasoning's already-frozen `ModelProvider` port with real, vendor-neutral implementations
without touching Reasoning. It reuses the closed-runtime shape (a `BusConsumer`, `core.fsm`
lifecycle, `core.policies`-based deterministic retry/selection, `provider.*`/`model.*`
standard envelopes) so it is provider-neutral, event-driven, replay-safe, streaming-first,
deterministic, offline-testable, and permanently extensible. After M5.6B it will be
**architecturally closed** except additive extensions and bug fixes.

## 2. Runtime Ownership

**Provider Runtime owns:**
Provider registration · provider discovery · provider lifecycle · provider capabilities ·
provider routing · provider selection · failover · fallback · load balancing · cost-aware
routing · latency-aware routing · region routing · health status · unified streaming ·
unified responses · usage normalization · metadata normalization · provider observability ·
provider statistics.

**Provider Runtime does NOT own:**
Memory · Knowledge · Context · Prompt construction · Reasoning (orchestration) · Tool
execution · Voice · Workflow · Agents · Transport · Persistence · billing calculation
(usage is normalized, never priced) · vendor SDK lifetimes beyond the adapter boundary.

Provider Runtime sits **below** Reasoning, not beside it: Reasoning still owns the
model-call *loop* (streaming session, tool-decision coordination, retry, cancellation,
response assembly — all M5.5B-2, closed). Provider Runtime owns *which provider answers the
call and how its raw protocol becomes one neutral shape*. A `ProviderModelBridge`
(composition root; implements Reasoning's frozen `ModelProvider`) is the only seam between
the two runtimes — Reasoning calls `stream(InferenceRequest)`; the bridge asks Provider
Runtime to execute it and translates the result back into `ResponseChunk`s. Neither runtime
imports the other.

## 3. Responsibilities

| Responsibility | Detail |
|---|---|
| Registration | Adapters register a `ProviderDescriptor` (name, capability flags, version, region, cost/latency class) at construction. |
| Discovery | Callers query the catalog by capability (chat/streaming/tools/vision/embeddings/…), never by vendor name when capability-driven. |
| Lifecycle | Each adapter has an independent `core.fsm` health lifecycle (HEALTHY/DEGRADED/UNAVAILABLE) driven by observed outcomes, never by polling in this design. |
| Capability catalog | A normalized, versioned `ProviderCapabilities` record per adapter — the single source of truth for what a provider can do. |
| Routing / selection | `ProviderRouter` applies a deterministic strategy (manual/auto/cheapest/fastest/quality/region/policy/A-B/canary/failover/weighted) over the catalog + health + policy verdicts to pick one adapter (or an ordered failover chain). |
| Failover / fallback | A router decision carries an **ordered candidate list**; the caller (or a retry-integration point) advances down it on failure — deterministic, not random. |
| Load balancing | Weighted / round-robin selection among equally eligible candidates — a routing strategy, not a side process. |
| Unified streaming | Every adapter's native stream (SSE, chunked JSON, gRPC, local generator) is translated into one internal `ProviderChunk` event sequence. |
| Unified response | Every adapter's final payload becomes one `ProviderResponse` — no vendor payload ever escapes the runtime. |
| Usage / metadata normalization | Vendor-specific usage/metadata shapes map onto one normalized `ProviderUsage` / `ProviderMetadata`. |
| Health | Adapter-scoped health state feeds routing exclusion and capability degradation. |
| Observability | Per-provider statistics (latency, error rate, usage) + standard envelopes for Replay/Gateway. |

## 4. Architecture Diagram

```
                 ┌────────────────────────────────────────────────────────┐
                 │                    Reasoning Runtime (closed)            │
                 │           InferenceEngine → injected ModelProvider       │
                 └───────────────────────────┬──────────────────────────────┘
                                             │ ModelProvider.stream(InferenceRequest)
                                             ▼
                 ┌────────────────────────────────────────────────────────┐
                 │        ProviderModelBridge  (composition root only)     │
                 │   implements ModelProvider · translates ⇄ Provider RT   │
                 └───────────────────────────┬──────────────────────────────┘
                                             │ ProviderRequest
                                             ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│                                PROVIDER RUNTIME                                     │
│                                                                                     │
│  ┌───────────────┐   ┌───────────────┐   ┌────────────────┐   ┌─────────────────┐  │
│  │ProviderRegistry│──▶│ ProviderRouter │──▶│ Capability     │   │ ProviderPolicy   │  │
│  │(register/      │   │ (strategy →   │   │ Catalog        │◀──│ Engine (verdicts)│  │
│  │ discover/      │   │  ordered      │   │ (normalized    │   │                  │  │
│  │ lifecycle)     │   │  candidates)  │   │  capabilities) │   └─────────────────┘  │
│  └───────┬───────┘   └───────┬───────┘   └────────────────┘                        │
│          │                   │                                                     │
│          ▼                   ▼                                                     │
│  ┌────────────────────────────────────────────────────────────────────────────┐    │
│  │                       Selected Adapter Execution                            │    │
│  │   AdapterPort.stream(ProviderRequest) → native protocol → UnifiedStreaming  │    │
│  │        ── ProviderHealth observes outcome → feeds back to Registry ──       │    │
│  └───────────────────────────┬──────────────────────────────────────────────────┘   │
│                              ▼                                                     │
│                    UnifiedResponse / UnifiedStreaming                              │
│                 (ProviderChunk* → ProviderResponse; Usage/Metadata normalized)      │
│                              │                                                     │
│                              ▼                                                     │
│              provider.* / model.* standard envelopes (Replay / Gateway)            │
└───────────────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                 ┌────────────────────────────────────────┐
                 │        External AI Providers            │
                 │  OpenAI · Claude · Gemini · Groq ·       │
                 │  Together · Fireworks · Azure · Ollama · │
                 │  Local · Future                          │
                 └────────────────────────────────────────┘
```

Layering (top → bottom, each stage provider-agnostic above the line):
Memory → Knowledge → Context → Prompt → Reasoning → **Provider Runtime** → External Providers.

## 5. Dependency Diagram

```
provider ──▶ core            (foundation: fsm, policies, events, module, errors, ports)
provider ──▶ tenant           (leaf isolation seam — added when intelligence lands, M5.6B-2,
                                mirroring Memory/Knowledge/Context/Prompt/Reasoning precedent)
provider ──X memory, knowledge, context, prompt, reasoning, tool, workflow, agent, runtime,
              api, gateway, replay, voicebot          (forbidden — acyclic, AST-enforced)

reasoning ──▶ core, tenant     (unchanged; Reasoning still imports no sibling runtime)
[composition root] ──▶ reasoning, provider   (wires ProviderModelBridge; not a package import
                                               from either runtime into the other)
```

The **only** new external dependency `provider` takes (beyond `core`, and later `tenant`) is
**vendor SDK/HTTP clients — and only inside individual adapter modules**, never in the
registry, router, catalog, policy engine, or normalization layer. Adapters are the sole
provider-specific code in the entire platform (Constitution §A6 rule 6).

## 6. Component Breakdown

Mirrors the closed-runtime shape (Memory/Knowledge/Context/Prompt/Reasoning), grouped
**core** (M5.6B-1) vs **intelligence** (M5.6B-2).

```
provider/
  domain.py            ProviderDescriptor/ProviderCapabilities/ProviderRequest/
                        ProviderChunk/ProviderResponse/ProviderUsage/ProviderMetadata/
                        ProviderHealthState/RoutingDecision (immutable, frozen+slotted)
  ids.py                content-hash ids (request hash, response hash) — replay-friendly
  lifecycle.py          core.fsm adapter health lifecycle: HEALTHY⇄DEGRADED⇄UNAVAILABLE
  ports.py              runtime-internal seam: AdapterPort (the vendor-facing contract
                        every provider adapter implements — mirrors core.providers.llm.LLMProvider)
  registry.py           ProviderRegistry — registration/discovery/lifecycle/lookup/versioning  [core]
  catalog.py            CapabilityCatalog — normalized, provider-neutral capability model      [core]
  router.py             ProviderRouter — deterministic routing strategies → RoutingDecision     [I]
  streaming.py          UnifiedStreamingController — native stream → ProviderChunk sequence     [core]
  response.py           ResponseNormalizer — native payload → ProviderResponse                  [core]
  usage.py              UsageNormalizer — native usage → ProviderUsage (no billing)              [core]
  metadata.py           MetadataNormalizer — native metadata → ProviderMetadata                 [core]
  health.py             ProviderHealthMonitor — outcome → health transitions; routing exclusion  [I]
  policies.py           ProviderPolicyEngine — allow/deny/quota/compliance/region → verdicts     [I]
  bridge.py             ProviderModelBridge — implements Reasoning's ModelProvider (composition
                        root only; not part of the provider package's own public surface)
  extensions.py         interfaces only: AsyncProvider/BatchProvider/StreamingProvider/
                        LocalProvider/GPUProvider/EnterpriseProvider/EdgeProvider/
                        ConsensusExecution/EnsembleExecution/ParallelInference/
                        SpeculativeExecution/MultimodalProvider                                 [I]
  repositories/         ProviderRepository (TEMPORARY health/statistics snapshots) — sole
                        store-toucher, mirrors the closed runtimes
  stores/               in-memory adapters + a deterministic offline EchoAdapter (dev/test
                        default — mirrors Reasoning's EchoProvider)
  observability.py      ProviderStatistics + Metric names + per-adapter stats
  events.py             ProviderPhase (phase discriminator on provider.*/model.* envelopes)
  config.py             ProviderConfiguration (registry/router/policy knobs; no vendor
                        credentials — those are adapter constructor args, injected)
  factory.py            ProviderFactory (composition root; registers adapters + the bridge)
  runtime.py            ProviderRuntime(BusConsumer) — the module surface (register/discover/
                        route/execute/health/statistics)
  _run.py               run_coro_sync (sync↔async bridge, local adapters only)
```
`[core]` = M5.6B-1. `[I]` = intelligence (M5.6B-2). The runtime depends only on `core` (+
`tenant` once intelligence tenancy lands) and injected `AdapterPort` implementations — never
on a sibling runtime.

## 7. Provider Registry Design

`ProviderRegistry` is pure bookkeeping — **no business logic** (no routing, no scoring):

- **Registration:** `register(descriptor: ProviderDescriptor, adapter: AdapterPort)` — fails
  fast (`ConfigError`) on a duplicate name or a descriptor/adapter capability mismatch.
- **Discovery:** `find(capability: Capability) -> tuple[ProviderDescriptor, ...]` — all
  registered providers declaring a capability; `get(name) -> AdapterPort` for direct lookup
  (unknown name → `ConfigError`, the established pattern from every closed `*Registry`).
- **Lifecycle:** each entry carries a `core.fsm`-driven health state (§9); the registry
  exposes `healthy()`/`degraded()`/`unavailable()` partitions for the router to consume —
  it does not decide routing itself.
- **Metadata + versioning:** `ProviderDescriptor` carries `name`, `version`, `region`,
  `cost_class`, `latency_class`, and the full `ProviderCapabilities` record. Re-registering a
  name with a new version is an explicit `update()` (audited), never a silent overwrite.
- **Determinism:** `names()`/`find()` return sorted tuples (stable iteration — replay-safe),
  matching the `ProviderRegistry.names()` convention already established in Reasoning
  (`reasoning/engine.py`).

## 8. Provider Router Design

`ProviderRouter` turns `(capability requirement, strategy, catalog, health, policy verdicts)`
into a **deterministic** `RoutingDecision` — an ordered candidate list, never a single
irreversible pick, so failover is "the next candidate," not a fresh decision:

| Strategy | Behaviour (deterministic tie-break: lowest `cost_class`, then name, unless noted) |
|---|---|
| Manual | Caller names the provider explicitly; the router validates capability + health, no scoring. |
| Auto | Health-eligible + capability-eligible candidates ordered by a composite score (policy-weighted). |
| Cheapest | Order by `cost_class` ascending. |
| Fastest | Order by observed p50 latency ascending (from `ProviderHealthMonitor` — a **stat**, not a live probe). |
| Highest Quality | Order by a configured quality rank (a static, declared ranking — no runtime scoring model). |
| Lowest Latency | Alias of Fastest at the routing layer (kept distinct as a named strategy for policy clarity). |
| Region Aware | Filter to `region` match first, then apply the secondary strategy. |
| Organization Policy | `ProviderPolicyEngine.admit_provider(org, name)` filters the candidate set first. |
| User Policy | As above, scoped to a user-level allow/deny list. |
| A/B Testing | A deterministic hash of `(session_id, experiment_id)` selects the bucket — reproducible, never `random()`. |
| Canary | A configured percentage (deterministic hash bucket, same mechanism as A/B) routes to the canary candidate; else the primary. |
| Failover | The candidate list itself *is* the failover chain — ordered by the active strategy; the caller advances on failure. |
| Backup Provider | A configured `backup_of` mapping appended to the end of any chain. |
| Weighted Routing | A deterministic hash of a monotonic counter against configured weights (stable distribution, replay-safe — never `random()`). |

**Determinism invariant:** every strategy is a pure function of `(request, catalog snapshot,
health snapshot, policy verdicts, a deterministic hash seed)` — no wall-clock randomness, no
unseeded RNG. Replaying the same inputs always yields the same `RoutingDecision`.

## 9. Capability Architecture

`ProviderCapabilities` is the normalized, provider-neutral capability record every adapter
declares at registration (Constitution §A6 rule 1 — "declared and validated... An agent
configured against an undeclared capability fails the write, loudly"):

```
ProviderCapabilities:
  chat: bool
  streaming: bool
  function_calling: bool
  tool_calling: bool
  vision: bool
  embeddings: bool
  image_generation: bool
  audio: bool
  json_mode: bool
  structured_output: bool
  thinking_models: bool          # extended/deliberate-reasoning models
  reasoning_tokens: bool         # distinct reasoning-token accounting
  context_window: int
  extra: Mapping[str, bool]      # additive escape hatch for a future capability
                                 # before it earns a first-class field (never provider-specific
                                 # business logic — just an undeclared-until-promoted flag)
```

Discovery (§7) filters on these fields only — a caller never asks "is this OpenAI," only "can
this capability serve chat + tool_calling + streaming." New capability types (speech-to-speech,
voice cloning, denoising — per Constitution §A6 rule 5) are added as new fields/contracts, a
purely additive change; existing adapters default the new field to `False` until updated.

## 10. Unified Streaming Architecture

Every adapter's native stream (OpenAI SSE, Claude's event stream, Gemini's chunked JSON,
Groq's SSE, a local generator) is translated **inside the adapter** into one internal event
sequence — no vendor shape crosses the adapter boundary:

```
ProviderChunk:
  id: str                 # content-hash id (request_hash, index, delta) — replay-friendly
  index: int
  kind: ChunkKind          # TOKEN | TOOL_CALL | FINAL  (reuses Reasoning's ChunkKind shape —
                          # same vocabulary, so the bridge is a pass-through, not a remap)
  delta: str
  done: bool
  metadata: Mapping[str, str]
```

`UnifiedStreamingController` (mirrors Reasoning's `StreamingController` — orchestration only)
pulls from the adapter's native async iterator and yields `ProviderChunk`s; it never pushes,
never blocks past a configured buffer (backpressure is a verdict, matching M5.5B-2's
pattern), and applies the same deterministic clock-based timeout hook. Because
`ProviderChunk` mirrors Reasoning's `ResponseChunk` shape 1:1, the `ProviderModelBridge`
(§14) is a structural pass-through — no field-by-field remapping logic, minimizing the seam's
surface. **Every provider produces identical internal streaming events** regardless of native
protocol. Replay-safe: the chunk sequence is exactly what gets persisted/replayed, never the
raw vendor bytes.

## 11. Unified Response Architecture

`ResponseNormalizer` assembles the pulled `ProviderChunk`s (or a single non-streaming payload)
into one `ProviderResponse` — **no provider payload ever escapes Provider Runtime**:

```
ProviderResponse:
  text: str
  finish_reason: FinishReason        # reuses Reasoning's FinishReason vocabulary
  tool_calls: tuple[ToolCallRecord, ...]
  usage: ProviderUsage               # §12
  metadata: ProviderMetadata         # §13
  safety: Mapping[str, str]          # normalized safety/moderation flags (opaque; a hook,
                                     # never a classifier in-runtime — mirrors Prompt/Reasoning's
                                     # SafetyPolicy interface-only stance)
  streaming: StreamingMetadata       # reused shape (streamed/chunk_count/first_chunk_index)
  response_hash: str                 # content hash — deterministic, replay-friendly
```
A non-streaming adapter's single payload is normalized identically to a collapsed single-chunk
stream (mirrors Reasoning's engine `_collapse` — single-shot is a mode, not a fork).

## 12. Metadata Architecture

`ProviderMetadata` — normalized, future-proofed via an `extra` escape hatch (never a reason to
touch the frozen shape for a vendor-specific field):

```
ProviderMetadata:
  provider: str
  model: str
  model_version: str
  latency_ms: float
  duration_ms: float
  request_id: str
  response_id: str
  region: str | None
  organization: str | None
  retries: int
  endpoint: str | None            # a routing-relevant label, never a raw URL with credentials
  pricing_metadata: Mapping[str, str]   # normalized cost *inputs* (rate card refs) — never a bill
  extra: Mapping[str, str]
```

## 13. Usage Architecture

`ProviderUsage` — normalized counts only; **Provider Runtime never calculates billing**
(pricing is downstream metering's job, consistent with the existing `EventType.COST_UPDATED`
living outside this runtime):

```
ProviderUsage:
  prompt_tokens: int
  completion_tokens: int
  cached_tokens: int
  reasoning_tokens: int
  image_units: int
  audio_seconds: float
  embedding_tokens: int
  total_tokens: int (derived)
```
This shape is a superset of Reasoning's `TokenUsage` (prompt/completion) — the bridge (§14)
downsamples it into Reasoning's simpler shape without loss of the fields Reasoning doesn't
need, and without Reasoning ever importing `provider`.

## 14. Health Architecture

Pure architecture — **no networking** described here; the mechanism, not a live prober:

```
ProviderHealthState:  HEALTHY | DEGRADED | UNAVAILABLE
```
- **Transitions** are driven by *observed call outcomes* fed back by whatever executes the
  adapter (success/failure/timeout counters over a rolling deterministic window) — never by a
  background poller in this design (a future additive `HealthProbe` extension may add active
  probing behind the same state machine).
- **Capability degradation:** a `DEGRADED` adapter may still serve *some* capabilities (e.g.
  chat but not tool_calling under load) — health is capability-scoped, not just adapter-scoped,
  mirroring Constitution §A6 rule 4 ("degraded modes are explicit").
- **Routing exclusion:** `UNAVAILABLE` adapters are excluded from `ProviderRouter` candidate
  lists entirely; `DEGRADED` adapters are deprioritized (strategy-dependent) but not excluded
  unless the specific capability is degraded.
- **Circuit breaker integration:** health transitions are the *input* to a circuit-breaker
  hook (an M5.6B-2 policy concern, reusing the `core.policies`-style threshold pattern already
  proven in Reasoning's `RetryController`) — breaker state and health state are related but
  distinct: a breaker trips faster and resets slower than the underlying health computation.
- **Recovery:** a `DEGRADED`/`UNAVAILABLE` adapter recovers via the same observed-outcome
  feedback (a rolling success streak), driven through `core.fsm` transitions — deterministic
  given the outcome sequence, never a fixed wall-clock cooldown alone.

## 15. Policy Architecture

`ProviderPolicyEngine` — **verdicts only, no execution** (the established Prompt/Context/
Reasoning pattern):

| Policy | Verdict produced |
|---|---|
| Allow list | `admit_provider(org, name) -> Verdict` |
| Deny list | `admit_provider` folds deny in (deny wins over allow on conflict) |
| Quotas | `quota_ok(org, resource, amount) -> Verdict` (hook; real enforcement is a `tenant.QuotaEnforcer`, same seam Reasoning uses) |
| Compliance | `compliant(region, data_class) -> Verdict` (e.g. data-residency constraints) |
| Organization restrictions | Folds into `admit_provider` scoped by `org_id` |
| Region restrictions | `admit_region(org, region) -> Verdict` |
| Provider restrictions | Same shape as Reasoning's `ProviderPolicy.allowed_providers` |

No policy performs I/O or calls a provider; `ProviderRouter` consults verdicts before scoring
candidates, and `ProviderRuntime` rejects a request outright if no candidate survives.

## 16. Extension Interfaces

Interfaces only — no implementation, no runtime dependency (mirrors Reasoning's
`extensions.py` stance exactly):

- `AsyncProvider` — the async-native `AdapterPort` variant for network-backed adapters.
- `BatchProvider` — request/response batching (non-streaming bulk inference).
- `StreamingProvider` — a marker refinement of `AdapterPort` for streaming-first vendors.
- `LocalProvider` — in-process/on-device model execution (no network).
- `GPUProvider` — a `LocalProvider` refinement declaring GPU residency requirements.
- `EnterpriseProvider` — private-endpoint / VPC / dedicated-capacity adapters.
- `EdgeProvider` — edge/CDN-local inference.
- `ConsensusExecution` — fan out to N adapters, resolve by agreement (interface only; the
  resolution policy is future work).
- `EnsembleExecution` — fan out to N adapters, combine outputs (interface only).
- `ParallelInference` — concurrent multi-candidate execution, first-good-wins (interface only).
- `SpeculativeExecution` — a fast draft adapter + a verifying adapter pairing (interface only).
- `MultimodalProvider` — non-text capability surface (image/audio in+out) built on the same
  `ProviderChunk`/`ProviderResponse` shapes with a `modality` discriminator.

## 17. Lifecycle Diagrams

**Adapter health lifecycle** (`core.fsm`):
```
HEALTHY ──(failure streak)──▶ DEGRADED ──(failure streak)──▶ UNAVAILABLE
   ▲                              │                                │
   └──────────(success streak)────┘◀───────(success streak)────────┘
```
Any state may transition directly to `UNAVAILABLE` on a hard signal (explicit deregistration,
circuit-breaker trip); `UNAVAILABLE → HEALTHY` requires passing back through `DEGRADED`
(no single-success snap-back — mirrors the conservative recovery already used in Reasoning's
retry/circuit-breaker design).

**Request execution lifecycle** (per `ProviderRequest`, a `core.fsm` mirroring Reasoning's
`InferenceState`):
```
REQUESTED → ROUTED → EXECUTING → STREAMING → COMPLETED
                          │           │
                          ▼           ▼
                       FAILED     CANCELLED
                          │
                          ▼
                    FAILED_OVER (→ ROUTED again, next candidate)
```

## 18. State Machines

| Machine | States | Triggers |
|---|---|---|
| Adapter health | HEALTHY, DEGRADED, UNAVAILABLE | OUTCOME_SUCCESS, OUTCOME_FAILURE, OUTCOME_TIMEOUT, FORCE_UNAVAILABLE, RECOVER |
| Request execution | REQUESTED, ROUTED, EXECUTING, STREAMING, COMPLETED, FAILED, CANCELLED, FAILED_OVER | ROUTE, EXECUTE, STREAM, COMPLETE, FAIL, CANCEL, FAILOVER |

Both reuse `core.fsm.StateMachineDefinition`/`StateMachine` — no hand-written transition
logic, matching every closed runtime.

## 19. Event Flow

Reuses **existing** M5.0 `EventType` members — no bus change (the established
Memory/Knowledge/Context/Prompt/Reasoning precedent), with a `phase` discriminator:

| Stage | EventType | phase values |
|---|---|---|
| Registration / discovery | `PROVIDER_CONNECTED` / `PROVIDER_DISCONNECTED` | `registered`, `deregistered` |
| Routing decision | `PROVIDER_CONNECTED` (reused; a routing decision is not a connection but rides the existing envelope with a `phase`) | `routed`, `failed_over` |
| Streaming chunk | `MODEL_TOKEN` | `chunk` |
| Completion | `MODEL_USAGE` (usage) + a provider-scoped completion phase on `PROVIDER_CONNECTED` | `completed` |
| Health transition | `PROVIDER_CONNECTED` / `PROVIDER_DISCONNECTED` | `healthy`, `degraded`, `unavailable` |

`EventSource.PROVIDER_REGISTRY` (already defined at M5.0) is the source for every event this
runtime publishes — confirming the foundation anticipated this runtime.

## 20. Boundary Verification

- **Ownership:** Provider Runtime owns registration/discovery/lifecycle/capabilities/routing/
  selection/failover/fallback/load-balancing/cost-latency-region routing/health/unified
  streaming/unified response/usage+metadata normalization/observability — nothing else.
- **Reasoning unmodified:** the `ModelProvider` port, `InferenceEngine`, `ProviderRegistry`
  (Reasoning's own, request-scoped one), controllers, and lineage are untouched; the bridge is
  a new, separate implementation of an existing frozen interface.
- **Dependency direction:** `provider → core` only in the core milestone (`provider → core,
  tenant` once M5.6B-2 intelligence lands) — AST-enforced, matching every closed runtime's
  dependency-rules test.
- **No provider-specific logic leaks:** all vendor-conditional code lives inside one adapter
  module per vendor (Constitution §A6 rule 6); the registry/router/catalog/policy/
  normalization layers are 100% vendor-neutral.
- **No billing leakage:** usage is normalized counts; `EventType.COST_UPDATED` (pricing) stays
  a downstream metering concern, never computed here.

## 21. Risk Analysis

| Risk | Mitigation |
|---|---|
| A vendor's native protocol has no clean `ProviderChunk` mapping (e.g. non-incremental function-call payloads) | The adapter degrades that capability explicitly (Constitution §A6 rule 4) rather than inventing lossy micro-chunks; `ProviderCapabilities.function_calling` reflects the true granularity. |
| Routing strategies drift into hidden non-determinism (e.g. accidental `random()` in a "weighted" implementation) | Deterministic-hash requirement is a first-class design invariant (§8) and will be a dependency-rules-style test at M5.6B (no `random` import in `router.py`). |
| Health feedback loop causes routing flapping | Conservative recovery (must pass through DEGRADED, never snap `UNAVAILABLE → HEALTHY`) + a rolling window, not single-sample transitions. |
| `ProviderChunk`/`ResponseChunk` shape drift between Reasoning and Provider Runtime over time | The bridge is the single translation point; a shape mismatch is a bridge bug, never a reason to import one package into the other. |
| Capability catalog becomes a dumping ground of vendor-specific flags | New first-class fields only for capabilities with ≥2 adapters; single-vendor quirks stay in `extra` until promoted (§9). |
| Multiple ways to add "billing-like" fields tempt scope creep into pricing | `pricing_metadata` is explicitly rate-card *references*, never a computed amount; enforced by doc + review, mirroring the existing Cost/metering boundary. |

## 22. Implementation Roadmap for M5.6B-1

Core milestone (mirrors the Memory/Knowledge/Context/Prompt/Reasoning core-first sequencing):
1. `domain.py` + `ids.py` — immutable shapes, content-hash ids.
2. `lifecycle.py` — adapter health FSM + request execution FSM over `core.fsm`.
3. `ports.py` — `AdapterPort` (the vendor-facing contract).
4. `registry.py` + `catalog.py` — registration/discovery + the capability catalog (no routing
   logic yet — a trivial "first eligible" selection stands in for the router until M5.6B-2).
5. `streaming.py` + `response.py` + `usage.py` + `metadata.py` — the normalization layer.
6. `stores/` — a deterministic offline `EchoAdapter` (mirrors Reasoning's `EchoProvider`) as
   the dev/test default `AdapterPort`.
7. `repositories/` — **temporary** health/statistics store (sole store-toucher).
8. `runtime.py` + `factory.py` + `config.py` + `observability.py` + `events.py` +
   `serializer.py` — the module surface, composition root, and standard envelopes.
9. `bridge.py` — `ProviderModelBridge` implementing Reasoning's `ModelProvider`, wired at the
   composition root only (not imported by either runtime).
10. Dependency-rules test (`provider → core` only) + full test suite.

Intelligence (M5.6B-2, after core closes): `router.py` (all strategies), `health.py`
(feedback-driven transitions + circuit-breaker hook), `policies.py` (7-policy engine), the
`tenant` dependency addition, and `extensions.py`.

## 23. Architecture Closure Certification

This design is **certified complete** for M5.6B-1 to begin implementation against. Every
future provider (OpenAI, Claude, Gemini, Groq, Together AI, Fireworks, Azure OpenAI, Ollama,
local models, and any vendor not yet named) integrates as **one `AdapterPort` implementation +
one registry entry** — no change to the registry, router, catalog, policy engine, or
normalization layer. No provider-specific logic may leak outside `provider/`'s adapter
modules. The architecture is deterministic (content-hash ids, hash-seeded routing, `core.fsm`
lifecycles), replay-safe (standard envelopes, no raw vendor payloads persisted), and
provider-neutral by construction (Reasoning and everything above it never imports `provider`
or a vendor SDK). Upon M5.6B-1 + M5.6B-2 landing per this design, the Provider Runtime will be
**architecturally closed** except for additive extensions (new adapters, new
`extensions.py` implementations) and bug fixes — the same closure standard already certified
for Memory, Knowledge, Context, Prompt, and Reasoning.

**Repository certified ready for M5.6B-1 Provider Runtime Core.**

---

## What landed in M5.6B-1 (Core — 2026-07-23, additive)

The implementation of the M5.6A design's core surface (§6 "core"-tagged components), exactly
as designed — no architecture change.

1. **Domain (`domain.py`) + ids (`ids.py`).** Immutable, frozen+slotted: `ProviderRequest`/
   `ProviderResponse`/`ProviderChunk`/`ProviderUsage`/`ProviderMetadata`/`ProviderCapabilities`/
   `ProviderDescriptor`/`ProviderSession`/`ProviderRegistryEntry`/`ProviderStatistics` +
   `ProviderRequestState`/`ProviderHealthState`/`ChunkKind`/`FinishReason`/`Capability`.
   Content-hash ids (SHA-256 hex[:16], `pr_`/`pp_`/`pc_`/`pz_` prefixes) — replay-friendly.
2. **Two `core.fsm` lifecycles (`lifecycle.py`).** The full **request execution** machine
   (REQUESTED→ROUTED→EXECUTING→STREAMING→COMPLETED, +FAILED/CANCELLED/FAILED_OVER/EXPIRED) and
   the full **adapter health** machine (HEALTHY⇄DEGRADED⇄UNAVAILABLE, conservative recovery) —
   both defined completely per the design; the core drives only the routing-free subset
   (REQUESTED→ROUTED→EXECUTING→STREAMING→COMPLETED, FAILED on an adapter exception, EXPIRED via
   the TTL sweep, and manual/caller-driven health transitions). CANCELLED/FAILED_OVER and
   observed-outcome-driven health feedback are exercised by M5.6B-2.
3. **`AdapterPort` (`ports.py`).** The vendor-neutral streaming seam (sync generator,
   mirrors Reasoning's `ModelProvider`). No SDK, no HTTP, no transport in-runtime.
4. **`ProviderRegistry` (`registry.py`).** Registration/discovery/lifecycle/lookup — **no
   routing**: `register`/`update`/`deregister`, `get`/`descriptor`/`entry`, `names`/`find`
   (capability-filtered, sorted by name — no scoring), `healthy`/`degraded`/`unavailable`
   partitions, and deterministic caller-driven health transitions
   (`mark_outcome_success/failure/timeout`, `mark_unavailable`, `recover`).
5. **`CapabilityCatalog` (`catalog.py`).** A thin read-only view: `capabilities_for`/
   `supports`/`discover`/`catalog` — no scoring, no preference ordering.
6. **Unified streaming/response/usage/metadata (`streaming.py`, `response.py`, `usage.py`,
   `metadata.py`).** `UnifiedStreamingAssembler` (chunk pull + partials + completion — no
   backpressure/timeout policy yet); `ResponseNormalizer` (chunks → `ProviderResponse`,
   deterministic `response_hash`; single-shot collapses like a streamed response, never a
   fork); `UsageNormalizer` (word-count default counter — no billing); `MetadataNormalizer`
   (provider/model/latency/duration/request-response ids — no vendor secrets).
7. **`EchoProviderAdapter` (`stores/echo_adapter.py`).** Deterministic, offline default
   `AdapterPort` — validates the whole pipeline without any vendor SDK.
8. **Session (`session.py`) + temporary repository (`repositories/`) + local adapters
   (`stores/`).** `ProviderSessionManager` over the `ProviderRepository` — the sole
   store-toucher; **temporary** response + session records over in-memory Document/Metadata
   stores.
9. **Runtime (`runtime.py`).** `ProviderRuntime(BusConsumer)` — `execute` (resolve via
   explicit name or "first capability-eligible adapter in sorted-name order" — **never a
   scored/heuristic decision**; that is the M5.6B-2 `ProviderRouter`), `get`/`remove`/
   `history`, `expire` (TTL sweep), `statistics`/`health`, read-only `registry`/`catalog`
   exposure.
10. **Serializer / config / factory / observability / events.** `ProviderSerializer`
    (round-trippable request/chunk/response/session ↔ storage + event/API views);
    `ProviderConfiguration` (validated store/TTL/default-provider/capability knobs — **no
    vendor credentials**); `ProviderFactory` (composition root — registers the
    `EchoProviderAdapter` + any injected `(descriptor, adapter)` pairs); `Metric` names +
    `ProviderPhase`.
11. **Extension hooks (`extensions.py`, M5.6B-1 scope).** Interfaces only: `AsyncAdapter`,
    `BatchAdapter`, `StreamingAdapter`, `LocalAdapter`, `EnterpriseAdapter`. The remaining
    M5.6A extension surface (GPU/Edge/Consensus/Ensemble/Parallel/Speculative/Multimodal) is
    deferred to M5.6B-2, added the same additive way.

### Event mapping (bus unmodified — the design's §19 realized)
Lifecycle phases ride the **existing** `PROVIDER_CONNECTED` (`requested`/`routed`/`executing`/
`streaming`/`completed`/`delivered`) and `PROVIDER_DISCONNECTED` (`failed`/`expired`);
streamed chunks ride `MODEL_TOKEN`; usage rides `MODEL_USAGE` — no new `EventType`, no bus
change (Memory/Knowledge/Context/Prompt/Reasoning precedent).

### Deferred to M5.6B-2 (all additive — no core shape change)
`ProviderRouter` (14 deterministic strategies) · `ProviderHealthMonitor` (observed-outcome
feedback driving the already-complete health FSM) · `ProviderPolicyEngine` (7 policies) ·
`ProviderTenancy` (adds the `tenant` foundation dep) · the remaining `extensions.py` surface
(GPU/Edge/Consensus/Ensemble/Parallel/Speculative/Multimodal) · `ProviderModelBridge`
(the composition-root seam implementing Reasoning's `ModelProvider` — not required for the
core milestone; Reasoning remains untouched and un-integrated until that bridge lands) ·
real vendor adapters (OpenAI/Claude/Gemini/Groq/Together/Fireworks/Azure/Ollama/local).

---

## What landed in M5.6B-2 (Intelligence & Closure — 2026-07-23, additive)

The intelligence layer that completes the runtime, exactly per the M5.6A design. **Fully
additive**: six new modules + guarded hooks in `runtime`/`factory` + a new, separate
composition-root package (`bridge/`); the core path is byte-for-byte unchanged when
intelligence is off (asserted by `test_core_path_unchanged_when_intelligence_off`). The only
non-`provider/` change (besides docs and the new `bridge/` package) is dropping `tenant` from
the core dependency-rules forbidden set (now a leaf foundation dep — DL-0039).

### Components
1. **`ProviderRouter` (`router.py`).** All 13 deterministic strategies from the design:
   Manual, Auto, Cheapest, Lowest Latency, Highest Quality, Region-aware, Organization
   Policy, User Policy, Weighted, Canary, A/B Testing, Failover, Backup Provider. Every
   strategy is a pure function of `(request, registry snapshot, policy verdicts, a
   deterministic hash seed)` — **no `random()` anywhere** (AST-verified). Eligibility already
   excludes `UNAVAILABLE` adapters and policy-denied names before any strategy runs.
2. **`ProviderHealthManager` (`health.py`).** The observed-outcome feedback loop the core's
   registry FSM needed to become automatic: debounces blips behind a configurable
   consecutive-outcome threshold, then drives `mark_outcome_success/failure/timeout`;
   `excluded()`/`reactivate()` for exclusion + conservative reactivation. **No networking.**
3. **`FailoverController` (`failover.py`).** Deterministic candidate-chain walk — `next()`
   returns the next untried name from a `RoutingDecision` or reports exhaustion. **No retry
   loop** against the same candidate, no sleeping; the runtime's bounded `while` loop over the
   chain is the only iteration, and it terminates because attempted names only grow.
4. **`CircuitBreaker` (`circuit_breaker.py`).** Classic CLOSED→OPEN→HALF_OPEN→CLOSED per
   adapter name. The "reset timeout" is evaluated against an **injected clock** reading —
   never a background timer/thread/sleep. A single half-open failure reopens immediately
   (conservative, mirrors the health FSM's stance).
5. **`ProviderPolicyEngine` (`policies.py`).** All 7 policies: allow list, deny list,
   organization restrictions, region restrictions, compliance (region-match), quotas (a hook),
   feature restrictions — **verdicts only**, no I/O.
6. **`ProviderTenancy` (`tenancy.py`).** Org isolation + provider allow-list + regional
   restrictions, enforced **before provider selection**; wires the M5.0 `tenant` seams — the
   same isolation seam Memory/Knowledge/Context/Prompt/Reasoning use.
7. **`ProviderModelBridge` (new `bridge/` package, separate from both `provider/` and
   `reasoning/`).** Implements Reasoning's **already-frozen** `ModelProvider` port by
   delegating to an injected `ProviderRuntime`. **Reasoning is completely untouched** — the
   bridge is simply a new implementation of an existing interface, exactly as Reasoning's own
   `EchoProvider` already is. `ProviderChunk`/`ResponseChunk` share the same `ChunkKind`
   string values, so translation is a structural pass-through. Verified end-to-end: Reasoning
   → bridge → Provider Runtime → `EchoProviderAdapter`.
8. **Remaining extension interfaces (`extensions.py`).** GPU / Edge / Consensus / Ensemble /
   Parallel / Speculative / Multimodal providers — completing all 12 from the M5.6A design.
   Interfaces only.

### Execution trace + observability
`ProviderExecutionTrace` (a new field on `ProviderResponse`, default-empty) records ordered,
seq-stamped stages — provider selection, routing decision, policy verdict, failover, health
change, execution, completion — **metadata only, never changing behaviour**; the serializer
round-trips it. `PipelineStatistics` + new `Metric` constants (routing decisions, failovers,
circuit state, health transitions, policy rejections) give a per-execution intelligence
snapshot.

### Runtime behaviour
`ProviderFactory.create(..., intelligence=False)` (the default) executes the **exact**
M5.6B-1 path. `intelligence=True` wires `ProviderRouter`/`ProviderHealthManager`/
`FailoverController`/`CircuitBreaker`/`ProviderPolicyEngine`/`ProviderTenancy` from
`config.policies`; `ProviderRuntime._resolve_and_stream` branches on whether a router is
injected — the core's `_select_core` (unchanged) or the router→failover→circuit-breaker→health
walk. `execute_scoped(request, tenant)` mirrors the tenant-aware entry point already
established by Reasoning/Prompt.

### Dependency direction
`provider → core, tenant` only (AST-enforced; `tenant` a leaf foundation dep, not a runtime).
`bridge → core, provider, reasoning` — the **only** module in the entire platform importing
both sibling runtimes; neither `provider` nor `reasoning` imports `bridge` or each other.

### Closure certification
The Provider Runtime is **architecturally closed** except for additive extensions and bug
fixes. It owns exactly what M5.6A specified — registration/discovery/lifecycle, capability
cataloguing, routing/selection, failover/fallback, health, unified streaming/response,
usage/metadata normalization, policy, tenancy, and observability. Memory, Knowledge, Context,
Prompt, Reasoning (orchestration), Tool execution, Workflow, Agents, Transport, and durable
persistence remain outside its ownership. Real vendor adapters (OpenAI/Claude/Gemini/Groq/
Together/Fireworks/Azure/Ollama/local) integrate as `AdapterPort` implementations — one
adapter + one registry entry, no redesign. Repository certified ready for **M5.7 Runtime
Integration**.
