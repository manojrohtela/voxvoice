# Agent Platform Integration (`platform/integration/`)

**Status:** M6.7B-1 + M6.7B-2 shipped; **M6.7C certified** (92/100, *Certified
With Conditions*); **M6.7.1 operational hardening** resolved all three
conditions (F1/F2/F3). **Design authority:**
[`epic6/agent-platform-integration-design.md`](epic6/agent-platform-integration-design.md)
(M6.7A, frozen). This document is the component spec (Constitution §A11.3) for
the *implemented* layer — it never supersedes or contradicts the design.

The platform's **second composition root** — mirroring `pipeline/`'s own role
for the six Epic 5 runtimes, one layer up. It wires Agent Runtime's delegation
lineage (Agent→Workflow, Agent/Loop→Reasoning) together with the already-
composed `pipeline.RuntimePipeline`. **It owns composition only** (design §1):
request entry, wiring, and context/trace/cancellation propagation. It is not a
fourteenth capability — no domain model, no lifecycle, no registry — and it
modifies no certified capability's source.

## What Bundle 1 delivers

| Area | Artifacts | Notes |
|---|---|---|
| Platform bootstrap | `PlatformBuilder`, `PlatformCompositionRoot`, `PlatformBootstrap`, `PlatformFactory` | Deterministic construction order (design §15) + startup/shutdown ordering (shutdown = reverse). `PlatformConfiguration.validate()` fails fast (raising `IntegrationError`) on a missing capability wire. |
| Configuration | `IntegrationConfiguration` (tunable knobs, design §3.2), `PlatformConfiguration` (the set of built runtime instances) | Budgets are forwarded verbatim to a capability's own field, never re-implemented. |
| Request entry | `PlatformRequest`, `PlatformExecution`, `PlatformExecutionContext`, `PlatformResponse` | Request translation + id/trace/cancellation minting only. **No orchestration** — the goal FSM stays owned by `AgentExecutionEngine`. |
| Trace propagation | `IntegrationExecutionContext`, `default_trace_id` (deterministic content-hash) | One `trace_id` minted at entry, threaded by value into each capability's own `trace_id` field (design §14). Never a new propagation mechanism. |
| Cancellation | `IntegrationCancellationToken` | One top-level token, **chained** (never shared as a raw object) into each capability's own local token (design §3.1). |
| Bridge adapters | `PipelineRoutedReasoningPort`, `WorkflowDelegationBridge`, `WorkflowPortBridge`, `AgentPortBridge`, `AgentPlanValidator` | Each implements an already-frozen port by delegating to an already-certified concrete runtime — the same category as `bridge.ProviderModelBridge`. |
| Diagnostics | `IntegrationDiagnostics`, `build_diagnostics` | Aggregates *references* to each capability's own diagnostics — never a new metric (design §14). |
| Error model | `IntegrationError` (subclass of `core.errors.PlatformError`) | The one new boundary error; never wraps a capability's own typed error (design §12). |

## Deferred Decisions closed

- **Item 1 (`AgentPort` conformance):** `AgentPortBridge` implements the frozen
  `core.ports.agent.AgentPort` by delegating to `agent.AgentRegistry`.
- **Item 10 (`WorkflowPort` conformance):** `WorkflowPortBridge` implements the
  frozen `core.ports.workflow.WorkflowPort` by delegating to
  `workflow.execution.WorkflowExecutionRuntime`.

Both resolved at the composition root — never by retrofitting conformance onto
a certified runtime (design §18 ADR rec 2).

## What Bundle 2 adds (M6.7B-2)

| Area | Artifacts | Notes |
|---|---|---|
| Replay correlation | `integration/replay/`: `PlatformReplayCorrelation`, `PlatformReplayIndex`, `PlatformReplayQuery`, `ReplayArtifact` | Correlates capability replay artifacts by `trace_id`; `validate()` delegates to each artifact's **owning** engine (`AgentExecutionRuntime.validate_replay`, …). Zero new replay engine; never replays another capability's state (design §13). |
| Observability | `integration/observability/`: `PlatformTrace`, `PlatformSpan`, `PlatformMetrics`, `PlatformCorrelation`, `LatencyRecorder` | Reference aggregation + trace_id correlation. Does **not** import the standalone `replay/`/`events/` platforms (not allowed deps). |
| Diagnostics | Extended `IntegrationDiagnostics` | Adds Loop; references `statistics()` where a capability has no `diagnostics()`. Reference aggregation only. |
| Failure handling | `integration/errors/`: `IntegrationFailurePolicy`, `FailureTranslator`, `PlatformFailureSummary` | Capability errors propagate verbatim; the original is kept, never rewrapped (design §12). No retry/recovery. |
| HTTP translation | `integration/http/`: `translate_error` → `HttpErrorResponse` | Deterministic, typed, framework-neutral (no fastapi import); `api/` consumes the result. |
| Trace propagation | `_CurrentTrace` holder + `PipelineRoutedReasoningPort` trace threading | One `trace_id` reaches the reasoning sub-call; capabilities keep their own internal ids and are correlated by the platform id (DL-0077). |
| Plan revision | `PlanRevisionCoordinator` | Real Agent↓Loop↓`PipelineRoutedReasoningPort`↓Reasoning wiring via Loop's own `run_loop` + real `AgentPlanValidator`. Runs with **no** reasoning stub (DL-0076). |
| Capability wiring | `PlatformCapabilityResolver` | Concrete `AgentCapabilityResolver` over the certified tool/skill/workflow discovery surfaces (design §8). Now wired into `agent_execution`. |
| Latency | `PipelineRoutedReasoningPort` instrumentation | Measures added routing overhead + context-assembly time (design §17 risk 2). |

**Measured latency** (200 samples, real clock): avg total routed reasoning call
≈ 0.68 ms; context-assembly hop ≈ 0.14 ms; routing+reasoning+provider ≈ 0.54 ms.
The extra Context/Prompt hop the §17 risk flagged costs ≈0.14 ms/call offline.

## Operational hardening (M6.7.1)

The three M6.7C conditions are resolved, integration-only, no architecture change:

- **F1 — trace concurrency:** `integration/trace.py` uses `contextvars`
  (`current_trace`/`current_cancellation`/`execution_scope`) — async-, thread-,
  and nested-safe; leak-free via `finally` reset. Context-local, not global
  mutable state. Still one platform `trace_id`, read by the same two adapters
  (reasoning port, workflow bridge). Verified by parallel/nested/async
  isolation tests.
- **F2 — bounded stores:** `LatencyRecorder` is a ring buffer (deque `maxlen`);
  `PlatformReplayIndex` and the span store are LRU-bounded by trace with a
  per-trace cap; the workflow bridge's tracking maps are LRU-capped. Sizes are
  typed `IntegrationConfiguration` knobs (`max_correlation_traces`,
  `max_spans_per_trace`, `max_latency_samples`), validated. Eviction is
  deterministic.
- **F3 — cancellation:** `execute()` binds the request's
  `IntegrationCancellationToken` via `execution_scope`; `WorkflowDelegationBridge`
  reads it (context provider) and chains each delegated workflow's own token to
  it. Cancelling the platform token cancels the workflow token through its own
  primitive. `execute(request, *, cancellation=…)` accepts a caller token and
  short-circuits when pre-cancelled. No shared mutable token.

Latency after F1: ≈ 0.80 ms/routed reasoning call (context-assembly ≈ 0.17 ms) —
no meaningful regression vs the M6.7C ≈ 0.76 ms.

## Dependency graph (verified acyclic)

```
integration → core, agent, workflow, loop, tool, skill, hooks, pipeline, bridge, context
```

`context` is a direct edge (refining design §15) so the Agent's planning path
has a real `ContextPort` — reasoning is still routed exclusively through
`pipeline.RuntimePipeline`. See DL-0075. No certified capability imports
`integration` back (verified by AST boundary test in
`tests/test_integration_platform.py`).

## Known limitations (documented)

- **Offline provider yields zero-step plans (DL-0072):** the deterministic
  `EchoProvider` cannot emit structured plan steps, so agent goal *decomposition*
  through `PipelineRoutedReasoningPort` yields a zero-step plan. The delegation
  path is tested with a structured reasoning stub over real Agent/Workflow
  runtimes (the certified Agent Runtime's own test pattern). The Loop *revision*
  path (which reads `content`, not `steps`) runs with **no** stub (DL-0076).
- **Trace into capabilities (DL-0077):** the certified Agent/Workflow runtimes
  mint their own internal trace_ids and accept no external one, so the one
  platform `trace_id` is threaded into the reasoning sub-call (Integration-owned)
  and used to *correlate* capability artifacts — not written into each
  capability's own trace field.
- **Plan revision (DL-0076):** runs as an Integration coordinator around the
  frozen engine (which has no Loop injection point), not inside `revise_plan`.

## Scope boundary (owned by M6.7C / capabilities)

Production certification (performance/security/determinism audit), and any
retry/recovery/planning/reasoning/validation logic — the latter remain owned by
the certified capabilities, never by this layer.

## Verification

- **Tests:** `tests/test_integration_platform.py` (27, Bundle 1) +
  `tests/test_integration_platform_bundle2.py` (22, Bundle 2) — bootstrap/DI/
  ordering, all bridges against *real* certified runtimes, replay correlation +
  owning-engine validation, observability aggregation, diagnostics aggregation,
  HTTP translation, failure propagation, end-to-end trace, plan revision,
  capability-resolver wiring, latency, dependency boundaries. Full repository
  suite: **1318 passed**.
- **Ruff:** clean across all source packages.
- **Dependency validation:** AST boundary tests confirm `integration` imports
  only the sanctioned set (no `replay`/`events`/`api`/`fastapi`) and no
  capability imports it back.
- **Latency:** context-assembly hop ≈ 0.14 ms/reasoning call (200 samples).
