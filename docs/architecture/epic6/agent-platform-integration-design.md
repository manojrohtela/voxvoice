# Agent Platform Integration — Design (M6.7A)

Composes all thirteen certified/frozen capabilities (Tool, Skill, Hook, Workflow,
Agent, Loop, Provider, Memory, Knowledge, Prompt, Context, Pipeline, Reasoning
Runtimes) into one production execution path. **This layer owns composition
only** — request entry, wiring, context/trace/cancellation propagation,
cross-capability observability correlation, and platform-wide error typing. It
never re-owns a decision any certified capability already owns, and it modifies
no certified capability's source.

---

## 1. What Agent Platform Integration Is (and Is Not)

**It is** the platform's second composition root, mirroring `pipeline/`'s own
role for the six Epic 5 runtimes (Memory→Knowledge→Context→Prompt→Reasoning→
Provider) — but one layer up, wiring Agent Runtime's own delegation lineage
(Agent→Loop→Workflow→Skill→Tool) together with the already-composed
`pipeline.RuntimePipeline` as Agent's own Reasoning path.

**It is not**:
- **A fourteenth capability.** It owns no domain model, no lifecycle, no
  registry — exactly like `pipeline/` and `bridge/`, its only artifacts are
  composition-root wiring code and request/response shapes that carry data
  between already-certified capabilities' own public surfaces.
- **A redesign of any certified capability.** Every integration point below
  is expressed as *composition* (bridge adapters implementing an already-
  frozen port by delegating to an already-certified concrete runtime) —
  never a new field, method, or behavior inside `agent/`, `workflow/`,
  `loop/`, `tool/`, `skill/`, `hooks/`, or any Epic 5 runtime.
- **A second event bus, replay engine, or observability system.** It reuses
  `core.events.EventBus`, the top-level `replay/` Conversation Replay &
  Observability Platform, and `pipeline`'s own `RequestContext`/
  `PipelineCancellation`/`PipelineLineage` shapes wherever their existing
  contract already covers the need — new shapes are introduced only where a
  genuine gap exists (see §4, §7, §8).

**The single governing constraint**: every ownership boundary already
certified stays exactly where it is. This design's only job is to specify
*what calls what, in what order, carrying what data* — never *what any
capability decides*.

---

## 2. Platform Architecture

```
Client
  │
  ▼
API Layer (api/, gateway/ — existing, Epic 1-4 control plane)
  │  translates a control-plane request (org-scoped HTTP/WS) into an
  │  IntegrationRequest — the one new request shape this layer owns
  ▼
Agent Platform Integration (new: integration/)
  │  composition root — see §3 Ownership Matrix for exactly what it wires
  ▼
Agent Runtime (frozen)
  │  AgentExecutionEngine.submit_goal() drives the goal-scoped FSM
  ├──▶ Reasoning Runtime (frozen, via pipeline.RuntimePipeline)   [planning/decomposition]
  ├──▶ Loop Engine (frozen, pure library)                        [plan revision only — §5]
  │        └──▶ Reasoning Runtime (via pipeline.RuntimePipeline)  [propose/critique/repair]
  └──▶ Workflow Runtime (frozen, via AgentWorkflowPort)           [delegated execution — §6]
           │
           ├──▶ Skill Runtime (frozen, via WorkflowSkillPort)     [already-certified contract — §7]
           │        └──▶ Tool Runtime (frozen, via ToolPort)      [already-certified contract — §8]
           └──▶ Tool Runtime (frozen, directly for Tool-kind nodes)
                    │
                    ▼
              Provider Runtime (frozen, via ProviderModelBridge — already shipped)
                    │
                    ▼
              External Systems (model vendors, adapters)
```

Reasoning Runtime is reached exactly once as a *shape*, from two different
callers (Agent's own planning, and Loop's own propose/critique/repair) — both
already go through `core.ports.reasoning.ReasoningPort` directly per their own
certified designs (ADR-0014). **This design's own contribution is routing
both call sites through the existing `pipeline.RuntimePipeline` composition**
so every Reasoning call an Agent or a Loop invocation makes also benefits
from Memory/Knowledge/Context/Prompt's already-certified assembly — not a new
capability, just correct wiring of an existing one (see §9).

Memory and Knowledge are **never** called directly by Agent Runtime, Loop
Engine, or this integration layer — exactly as `pipeline.RuntimePipeline`
already established for Epic 5 (Context's own `MemorySource`/`KnowledgeSource`
fan out to them once per `assemble()` call); calling them a second time here
would reintroduce the exact non-deterministic double-retrieval `pipeline/`'s
own design already forecloses.

---

## 3. Ownership Matrix

| Concern | Owner |
|---|---|
| Request entry (HTTP/WS, org-scoping, auth) | **API Layer** (`api/`, `gateway/` — unchanged, existing) |
| Translating a control-plane request into an `IntegrationRequest` | **Agent Platform Integration** (new, thin translation only) |
| Session creation / identity | **API Layer's existing `Session`/`Org` models** for the control-plane record; **Agent Runtime's own `AgentGoal.conversation_id`** for the orchestration-side session — Integration maps one to the other, owns neither |
| Goal decomposition, capability resolution, delegation | **Agent Runtime** (unchanged) |
| Plan revision iteration/stopping/validation orchestration | **Loop Engine** (unchanged) — Agent Runtime supplies the `LoopValidator`/`LoopDefinition` (§5) |
| Workflow graph execution, checkpoints, recovery | **Workflow Runtime** (unchanged) |
| Skill composition/planning | **Skill Runtime** (unchanged) |
| Tool execution, governance, streaming | **Tool Runtime** (unchanged) |
| Provider routing, failover, health | **Provider Runtime** (unchanged, via the existing `ProviderModelBridge`) |
| Reasoning/inference orchestration | **Reasoning Runtime** (unchanged) |
| Memory/Knowledge/Context/Prompt assembly | **Their own runtimes** (unchanged), reached only via `pipeline.RuntimePipeline` (unchanged) |
| Context propagation (session/trace/org ids across capability calls) | **Agent Platform Integration** — a new `IntegrationExecutionContext`, propagated by value into each capability's *own* already-certified context shape (`AgentExecutionContext`/`WorkflowExecutionContext`/`LoopExecutionContext`/`pipeline.RequestContext`); never a shared mutable object |
| Trace propagation | **Agent Platform Integration** assigns one `trace_id` at request entry; every capability already threads its own `trace_id` field through its own context/decision/event shapes (established platform-wide since M5.0) — Integration's only job is generating the one root value and passing it down, never re-deriving it per stage |
| Cancellation propagation | **Agent Platform Integration**, reusing `pipeline.PipelineCancellation`'s exact shape (a second, Integration-level cancellation token is unnecessary — see §3.1) |
| Execution identifiers | **Each capability mints its own** (`AgentGoalExecution.execution_id`, `WorkflowExecutionState`'s own instance id, `LoopResult.loop_id`, `ToolResult`'s own id) — Integration only correlates them (§7), never mints a replacement id |
| Replay boundaries | **Each capability replays only its own state** (§7) — Integration never replays anything itself, only correlates already-independent replay records by shared `trace_id` |
| Diagnostics | **Each capability's own `build_diagnostics()`** — Integration's own diagnostics (§8) aggregates *references* to each capability's diagnostics object, never recomputes their contents |
| Observability / structured events | **Each capability emits its own `EventType`** onto the existing `core.events.EventBus`; the pre-existing top-level `replay/` package (Conversation Replay & Observability Platform, M4.3) already subscribes read-only and needs no change — Integration's only job is ensuring `trace_id`/`correlation_id` are set consistently so `EventCorrelator` groups a whole platform-wide execution correctly (§8) |
| Error propagation | **Each capability raises its own typed error** (subclassing `core.errors.PlatformError`); Integration defines exactly one new *boundary* translation (§9), never a duplicate error hierarchy |
| Configuration flow | **Each capability's own `*Configuration` dataclass**, constructed by its own `*Factory` — Integration's own `IntegrationConfiguration` (§3.2) only holds the *set of already-built runtime instances* plus which optional stages are enabled (e.g. whether plan revision is on), never per-capability tuning knobs a capability's own config already owns |

### 3.1 Why cancellation needs no new shape

`pipeline.PipelineCancellation` is already a single, checked-between-stages
token forwarded into Reasoning's own native `InferenceRequest.cancellation_
token`. Agent Runtime's own `AgentCancellationToken` and Workflow's own
`WorkflowCancellationToken` are each local, capability-owned duplicates of
the *same* shape (the established "duplicate, don't cross-import" precedent,
ADR-0017's own sibling convention). Agent Platform Integration's job is
narrow: hold **one** `IntegrationCancellationToken` at the top (request
scope) and, at each delegation boundary, call the *next* capability's own
local cancellation primitive when the top-level token cancels — never
propagate a raw shared object across a capability boundary, since that would
violate the same "no capability imports another's implementation package"
rule every dependency-boundary test in the repository already enforces.

### 3.2 `IntegrationConfiguration` (illustrative — no code in this milestone)

```python
@dataclass(frozen=True, slots=True)
class IntegrationConfiguration:
    enable_plan_revision: bool = True          # whether Agent invokes Loop Engine at all
    max_plan_revisions: int = 1                # forwarded to AgentExecutionEngine's own existing field
    max_loop_iterations: int = 5               # forwarded to LoopStoppingPolicy — never re-implemented here
    trace_id_factory: Callable[[], str] | None = None   # defaults to a content-hash, never random()
```

---

## 4. Integration Boundaries

### 4.1 `IntegrationRequestContext` (new, illustrative shape)

```python
@dataclass(frozen=True, slots=True)
class IntegrationExecutionContext:
    trace_id: str                 # minted once, at request entry
    session_id: str               # the control-plane session (api/gateway's own identity)
    org_id: str | None = None
    goal_id: str | None = None    # populated once Agent Runtime accepts the goal
```

This is **not** a fifth entry in the Skill→Workflow→Agent→Loop structural-
duplication lineage (ADR-0017) — it sits *above* that lineage, at the
composition root, the same relationship `pipeline.RequestContext` already has
to `ContextPort`/`PromptRequest`/`InferenceRequest`'s own inline fields. Each
capability downstream still constructs its *own* context shape (an
`AgentExecutionContext`, a `LoopExecutionContext`) from the relevant fields
here — Integration never passes this object into a capability's internals
directly, only extracts the fields that shape's own constructor accepts.

### 4.2 Boundary-by-boundary responsibility

| Boundary | What crosses it | What does not |
|---|---|---|
| API → Integration | An org-scoped goal request (persona/task description, capabilities) | Any Epic 6 domain object — the API layer's own `Agent` DB model (a persona/config record, Epic 1-4) is translated, never passed through as-is; it is a different concept from Epic 6's `AgentManifest`/`AgentIdentity` and must not be conflated (see §12 Risks) |
| Integration → Agent Runtime | `AgentGoal`, `capabilities`, a `ContextRequest` | Integration never constructs an `AgentPlan` or drives the goal FSM itself — `AgentExecutionEngine.submit_goal()` owns that entirely |
| Agent Runtime → Loop Engine | A `LoopDefinition` (built from the failing `AgentPlan`'s own revision context) + an injected `LoopValidator` (§5) | Agent Runtime never inspects `LoopIteration` internals — only reads the final `LoopResult` |
| Agent Runtime → Workflow Runtime | `resolve`/`submit`/`status`/`signal` via `AgentWorkflowPort` (§6) | Agent Runtime never touches a `WorkflowGraph` (AST-verified already, unchanged) |
| Workflow Runtime → Skill Runtime | Already-certified `WorkflowSkillPort` contract (§7) — verified, not redesigned | Workflow never imports `skill/` directly (unchanged) |
| Skill Runtime → Tool Runtime | Already-certified `CapabilityResolver`-mediated contract (§8) — verified, not redesigned | Skill never imports `tool/` directly (unchanged) |
| Any Reasoning call site → Reasoning Runtime | A `ReasoningRequest` (session_id, context_ref, model, org_id) | Routed through `pipeline.RuntimePipeline` where a full Memory/Knowledge/Context/Prompt assembly is warranted (§9); Agent/Loop's own direct `ReasoningPort` usage is preserved where their own certified design already calls for it |

---

## 5. Agent ↔ Loop Integration

### 5.1 When Loop Engine is invoked

Only from inside `AgentExecutionEngine.poll()`'s existing revise-or-fail
branch (already implemented, Bundle 3) — when a delegated workflow's status
comes back `"failed"` and `plan.revision < max_revisions`. This is **not a
new call site**; it is the existing `revise_plan()` call in
`agent/planning/revision.py`, now optionally routed through Loop Engine
instead of (or in addition to) a single-shot `ReasoningPort.run` call.

### 5.2 When it is skipped

- `IntegrationConfiguration.enable_plan_revision = False` (an operator can
  disable iterative refinement entirely; Agent Runtime falls back to its
  own existing single-shot `revise_plan()`, unchanged, zero behavior
  regression for callers who never opt in).
- The revision budget is already exhausted (`plan.revision >= max_revisions`)
  — Agent Runtime's own existing fail path fires; Loop Engine is never
  reached, exactly as today.
- A goal's plan has no `delegate_workflow` step needing revision (e.g. a
  goal that only decomposes into non-workflow steps) — Loop Engine has
  nothing to refine.

### 5.3 Plan revision lifecycle

```
AgentExecutionEngine.poll() [delegated workflow status == "failed"]
        │
        ▼
plan.revision < max_revisions?
        │ yes
        ▼
Build LoopDefinition(loop_id=execution_id, task_description=<plan summary>,
                      stopping_policy=LoopStoppingPolicy(max_iterations=
                      integration_config.max_loop_iterations))
        │
        ▼
run_loop(definition, reasoning_port=<pipeline-routed ReasoningPort adapter>,
         validator=AgentPlanValidator(...), session_id=..., clock=...)
        │
        ▼
LoopResult (final_proposal_id, converged, stop_reason)
        │
        ▼
Agent Runtime parses the final proposal's content_ref back into a revised
AgentPlan (the same deterministic-parse discipline `parse_plan_steps` already
applies) → advances AgentGoalTrigger.REVISE → AgentGoalTrigger.DELEGATE
```

### 5.4 Iteration / stopping / validation ownership

Unchanged from Loop Engine's own certification (ADR-0016, ADR-0014): Loop
Engine owns iteration sequencing and mechanical stopping policy; correctness
is entirely the injected `AgentPlanValidator`'s call. Agent Runtime supplies
that validator — **a new, small composition-root class**, not a Loop Engine
change:

```python
class AgentPlanValidator(LoopValidator):
    """Validates a revised plan proposal by checking it against the
    already-resolved AgentCapability set and the AgentWorkflowPort's own
    `resolve()` — never re-implementing Loop's own correctness delegation,
    only supplying Agent-specific domain logic behind the seam Loop Engine
    already declared."""
    def validate(self, proposal: LoopProposal) -> LoopValidation:
        ...  # parses proposal.content_ref back into a candidate AgentPlan,
             # checks step kinds resolve via AgentWorkflowPort.resolve()
```

### 5.5 Replay ownership

Loop Engine's own `LoopReplayRecord` (hash-chain over its iteration
sequence) stays exactly as certified — it replays only the propose→critique→
repair→validate sequence Loop Engine itself ran. Agent Runtime's own
`AgentReplayRecord` gains **no new field** for this; a plan revision that
went through Loop Engine is already represented as an `AgentDecision` in
Agent's own decision-hash chain (the `revise_plan`/`REVISE` trigger records a
decision today) — Integration's only requirement is that the `AgentDecision`
recorded for a Loop-Engine-mediated revision include `LoopResult.loop_id` in
its payload, so a future cross-capability inspection can look up the
corresponding `LoopReplayRecord` by id. This is a **data-inclusion detail for
Bundle implementation**, not a schema change to any certified shape.

---

## 6. Agent ↔ Workflow Integration

Unchanged from Agent Runtime's own certified design (ADR-0015) — this
section verifies and documents the existing contract, not a redesign.

| Concern | Contract |
|---|---|
| Workflow submission | `AgentWorkflowPort.submit(step) -> execution_id` — already implemented in `agent/planning/delegation.py`'s `delegate_step`, called from `delegate_plan` |
| Workflow monitoring | `AgentWorkflowPort.status(execution_id) -> str` — polled from `AgentExecutionEngine.poll()`, already implemented |
| Workflow completion | `poll()` reads `status == "completed"`, fires `AgentGoalTrigger.COMPLETE` — already implemented |
| Workflow failure | `poll()` reads `status == "failed"`, either revises (§5) or fires `AgentGoalTrigger.FAIL` — already implemented |
| Workflow cancellation | `AgentExecutionEngine.cancel()` fires `AgentGoalTrigger.CANCEL`; **Integration's own responsibility is wiring `AgentWorkflowPort`'s concrete adapter to also forward a cancellation to `WorkflowExecutionRuntime`'s own cancellation primitive** — the one genuinely new piece of wiring in this section, since Bundle 3 never had a concrete `AgentWorkflowPort` implementation to wire (only the interface) |
| Workflow replay boundaries | `WorkflowReplayRecord` stays exactly as certified — Agent Runtime never inspects it; if a future diagnostic needs to correlate an Agent goal with its delegated Workflow's own replay record, that correlation goes through the shared `trace_id` (§8), never through Agent Runtime reading Workflow's internal state |

**The one concrete new artifact this section requires**: a real
`AgentWorkflowPort` implementation (`WorkflowDelegationBridge`, in the new
`integration/` package) delegating to `WorkflowExecutionRuntime`'s own
already-certified `submit_execution`/`status`/`signal`/`cancel_execution`
methods — Bundle 1-3 of Agent Runtime declared and tested the seam
interface but never built a concrete adapter (this was explicit, correct
scoping at the time — Workflow Runtime didn't exist yet when Agent Bundle 1
shipped).

---

## 7. Workflow ↔ Skill Integration (verification only)

Ownership verified against the certified Workflow Runtime design
(`workflow-runtime-design.md` §9, ADR-0009 sibling decisions): Workflow
Runtime declares its own `WorkflowSkillPort` seam (since Skill Runtime has no
`core.ports` entry of its own, ADR-0008's own consequence) — a Skill-kind
`WorkflowNode` resolves and invokes a Skill's own `SkillExecutionPlanner`
output through this seam. **No redesign performed.** The interaction
contract, as already certified:

```
WorkflowNode(kind="skill", skill_ref="internal.onboard@1.0.0", inputs={...})
        │
        ▼  (via WorkflowSkillPort, Workflow-owned seam)
SkillRuntime.plan(skill_ref, inputs) -> SkillPlan   # planning only, per Skill's own certified scope
        │
        ▼
Workflow Runtime's own execution engine drives the resulting plan's steps
node-by-node, exactly as any other node kind
```

Skill Runtime never executes anything itself (certified, ADR-0008) — the
actual Tool/Workflow steps a Skill's plan resolves to are executed by
whichever runtime Workflow's own execution engine already dispatches to.
Integration adds no new code here; this section exists per the milestone's
own "verify ownership only, document the interaction contract" instruction.

---

## 8. Skill ↔ Tool Integration (verification only)

Ownership verified against the certified Skill Runtime design
(`skill-runtime-design.md` §6): Skill Runtime declares capability
requirements (`SkillCapabilityRequirement(kind="tool", tag=...)`) but never
resolves them itself — resolution is entirely the injected
`CapabilityResolver`'s job (the same seam Agent Runtime's own
`AgentCapabilityResolver` mirrors, per ADR-0013/0014's shared "structure vs.
resolution" discipline). **No redesign performed.** The interaction
contract, as already certified:

```
SkillCapabilityRequirement(kind="tool", tag="crm.lookup")
        │
        ▼  (via the injected CapabilityResolver — Skill Runtime's own seam)
CapabilityResolver.resolve(requirement) -> ResolvedCapability(available: bool, resolved_ref: str)
        │
        ▼  (only if the resolved plan is later executed by Workflow, per §7)
ToolRuntime.invoke(resolved_ref, ...) -> ToolResult   # via the frozen core.ports.tool.ToolPort shape
```

Skill Runtime imports no `tool` package directly (certified, AST-verified).
The concrete `CapabilityResolver` implementation that actually calls
`ToolRegistry.list`/`ToolRuntime.invoke` is composition-root code — the same
category of new artifact as `AgentCapabilityResolver`'s own concrete
implementation (§6), built in `integration/`, never inside `skill/`.

---

## 9. Provider Integration

**No redesign of Provider Runtime.** Provider Runtime is already reached
exclusively through the shipped `bridge.ProviderModelBridge`, which
implements Reasoning's frozen `ModelProvider` port — this remains completely
unchanged, verified end-to-end already at M5.6B-2/M5.7.

| Concern | Ownership (unchanged) |
|---|---|
| Provider Registry | `ProviderRegistry` (Provider Runtime, certified) |
| Capability resolution | `CapabilityCatalog` (Provider Runtime, certified) |
| Execution flow | `ProviderRouter`'s 13 deterministic strategies (Provider Runtime, certified) |
| Provider failures | `ProviderHealthManager` + `FailoverController` (Provider Runtime, certified) |
| Retries | `ReasoningRuntime`'s own `RetryController` (Reasoning Runtime, certified) — **not** Provider's; Provider's own `CircuitBreaker` is a distinct, lower-level concern (per-adapter health), already certified as non-overlapping |
| Fallback ownership | `FailoverController`'s deterministic chain walk (Provider Runtime, certified) |

Agent Platform Integration's only involvement: every Reasoning call this
layer routes through `pipeline.RuntimePipeline` (§2, §10) automatically
reaches Provider Runtime via the pipeline's own already-wired `bridge`
reference — zero new code, zero new configuration surface.

---

## 10. Memory / Knowledge / Prompt / Context

**No redesign.** These four remain interface-driven exactly as Epic 5
certified them (`ContextPort`/`PromptPort` frozen at M5.0; concrete
`ContextRuntime`/`PromptRuntime` implementing them directly, verified).

- **Assembly lifecycle**: unchanged — `ContextRuntime.assemble()` fans out to
  Memory/Knowledge via its own already-certified `MemorySource`/
  `KnowledgeSource` ports, once per call; `PromptRuntime.build()` renders the
  resulting `AssembledContext` into a provider-neutral `PromptPackage`.
- **Ownership**: each runtime owns exactly what it owned at Epic 5 closure
  (M5.8) — Integration adds no new responsibility to any of the four.
- **Propagation**: `pipeline.RuntimePipeline` already threads `RequestContext`
  through all four stages; Agent Platform Integration's own
  `IntegrationExecutionContext` (§4.1) supplies that `RequestContext`'s
  `session_id`/`org_id`/`trace_id` fields at the point Agent Runtime's own
  planning/revision needs a full pipeline call (see §9's routing decision).
- **Isolation**: unchanged — each runtime's own tenancy checks (`Context
  Tenancy`, `Prompt Tenancy`) already run before assembly/rendering; no new
  isolation boundary is introduced or bypassed.
- **Replay boundaries**: unchanged — each of the four's own provenance/lineage
  fields (already embedded in `PipelineLineage`) remain the sole source of
  truth for what happened during assembly; Agent/Loop never re-derive this.

**Never duplicate state**: Agent Runtime's own `AgentMemoryContext`/
`AgentKnowledgeContext`/`AgentPromptContext` (declared in the M6.5A design)
are provenance/replay *records* only — pointers into what Memory/Knowledge/
Prompt already produced, never a second copy of the underlying content. This
was already a certified Agent Runtime constraint (M6.5A §8); Integration
changes nothing about it.

---

## 11. Reasoning Integration

**No redesign of Reasoning Runtime.** Planning (Agent's `decompose_goal`/
`revise_plan`) and proposal/critique/repair (Loop's `run_loop` internals)
both already call `core.ports.reasoning.ReasoningPort.run()` directly, per
their own certified designs (ADR-0014). Reasoning Runtime remains the sole
owner of intelligence: structured outputs are the same `Decision(kind=FINAL)`
payload-parsing discipline every one of these call sites already applies
(`parse_plan_steps`, `_parse_final_content`) — Integration introduces no new
parsing convention.

**This design's one concrete decision**: which concrete `ReasoningPort`
instance Agent Runtime and Loop Engine receive at composition time.
Two options were available (see §12 Risks for the rejected one) — the
selected option:

> Agent Runtime and Loop Engine are constructed with a **`PipelineRoutedReasoningPort`**
> adapter (new, in `integration/`) that implements `core.ports.reasoning.
> ReasoningPort.run()` by internally invoking `pipeline.RuntimePipeline.
> execute()` and adapting its `UnifiedResponse` back into a `Decision`
> stream — so every Agent/Loop reasoning call benefits from the full
> Memory→Knowledge→Context→Prompt→Reasoning→Provider path, not a bare
> `ReasoningRuntime.run()` call bypassing Context/Prompt assembly entirely.

This is composition, not a redesign: `ReasoningRuntime.run()` itself is
untouched; `PipelineRoutedReasoningPort` is a new adapter class satisfying an
already-frozen port, exactly the same shape as `ProviderModelBridge`
satisfying `ModelProvider`.

---

## 12. Error Model

**No duplicate error hierarchy.** Every capability already raises typed
errors under `core.errors.PlatformError` (Constitution §A2.5). Agent
Platform Integration adds exactly **one** new boundary rule:

```python
class IntegrationError(PlatformError):
    """Raised only at a composition-root boundary itself (e.g. a required
    capability instance was never wired into the IntegrationFactory) —
    never wraps or re-raises a capability's own typed error, which
    propagates verbatim to the caller."""
```

| Concern | Ownership |
|---|---|
| Typed errors | Each capability's own hierarchy (`ConfigError`, `RuntimeConflict`, capability-specific subclasses) — propagate verbatim through Integration, never caught-and-rewrapped |
| Retries | Reasoning's `RetryController` / Provider's `FailoverController` (unchanged) — Integration never adds a second retry loop at the composition-root level |
| Cancellations | Each capability's own local cancellation token, chained from the one top-level `IntegrationCancellationToken` (§3.1) |
| Validation failures | Each capability's own validation (`validate_manifest`, `LoopValidation`, Tool's policy engine's `PENDING`/deny verdicts) — Integration never re-validates already-validated input |
| Workflow failures | `AgentExecutionEngine`'s own existing revise-or-fail policy (§5, §6) — unchanged |
| Provider failures | Provider Runtime's own `FailoverController`/`CircuitBreaker` (§9) — unchanged |

**Never duplicate error handling**: Integration's only new behavior is at the
API boundary — translating a capability's own typed error into an HTTP status
the existing `api/` layer already knows how to emit (mirroring how `api/`
already translates other Epic 5/1-4 errors today) — never a new retry,
never a new validation pass.

---

## 13. Replay Model

Two genuinely different "replay" concepts already coexist in this
repository, and this design does not conflate them:

1. **Each Epic 6 capability's own hash-chain replay** (`AgentReplayRecord`,
   `WorkflowReplayRecord`, `LoopReplayRecord`, plus Skill's/Tool's own
   replay-metadata) — pure recomputation over that capability's *own*
   recorded decision/iteration/checkpoint sequence, never a live re-run,
   never inspecting another capability's state. **Unchanged by this design.**
2. **The pre-existing top-level `replay/` package** (Conversation Replay &
   Observability Platform, M4.3) — a read-only subscriber to
   `core.events.EventBus`, providing a cross-cutting timeline/index/
   correlation/inspector view over *every* event any runtime emits. This
   already works for any new `EventType` a capability emits (additive by
   construction, AR-32) — **it needs zero code change** for Agent/Loop/
   Workflow/Skill/Tool's own already-shipped `EventType` members to appear
   in it.

Ownership, per capability (unchanged from each one's own certification):

| Capability | Replays | Never replays |
|---|---|---|
| Agent | Its own `AgentDecision` payload-hash chain | A delegated Workflow's own checkpoint chain |
| Loop | Its own `LoopIteration` hash chain | Agent's decision chain, or any Reasoning/Provider internal state |
| Workflow | Its own `WorkflowCheckpoint`/`WorkflowSnapshot` chain | A Skill's or Tool's own execution record |
| Skill | Its own plan/replay metadata (ownership declared, M6.2A) | A Tool's own `ToolResult`/replay record |
| Tool | Its own `ReplayRecord` (deterministic replay vs. `EchoToolAdapter` only, certified) | Anything upstream of it |

**Agent Platform Integration's only replay-related responsibility**:
ensure every capability's own replay record, once produced, carries the
shared `trace_id` (§4.1) in whatever metadata field it already has room for
(e.g. `AgentReplayRecord`'s own metadata, `WorkflowReplayRecord`'s own) so a
future cross-capability inspection tool can correlate "everything that
happened for this one client request" without any capability replaying
another's state. **No capability may replay another capability's state —
verified as already true today (each replay engine's own dependency test
imports nothing outside its own package), and this design adds no exception.**

---

## 14. Observability Model

| Concern | Ownership |
|---|---|
| Trace IDs | Integration mints exactly one `trace_id` at request entry (§4.1); every capability already threads its own `trace_id` field through its own context/event/decision shapes (established platform-wide, additive since M5.0) |
| Span ownership | Each capability owns its own execution trace shape (`AgentExecutionTrace`, `WorkflowExecutionTrace`, `LoopExecutionTrace`, `ToolExecutionTrace`) — Integration never constructs a span itself, only ensures each capability's trace-building call receives the shared `trace_id` |
| Metrics ownership | Each capability's own `*Statistics`/`compute_statistics()` (unchanged) — Integration's own diagnostics (below) references these, never recomputes them |
| Structured events | Each capability's own `EventType` emissions onto the existing, unmodified `core.events.EventBus` |
| Diagnostics ownership | A new, thin `IntegrationDiagnostics` (illustrative): |

```python
@dataclass(frozen=True, slots=True)
class IntegrationDiagnostics:
    trace_id: str
    agent_diagnostics: AgentRuntimeDiagnostics | None = None
    workflow_diagnostics: object | None = None      # WorkflowRuntimeDiagnostics, unchanged shape
    loop_diagnostics: LoopDiagnostics | None = None
    tool_diagnostics: object | None = None
    skill_diagnostics: object | None = None
    healthy: bool = True
```

`IntegrationDiagnostics` is a pure aggregation of *references* to each
capability's own already-certified diagnostics builder output — never a
recomputation, never a new metric. **Avoiding duplicate telemetry**: no new
counter/gauge is introduced anywhere in this design; every number already
exists in some capability's own `*Statistics` object.

---

## 15. Dependency Graph

```
integration  →  core [, tenant]                          (composition root, like pipeline/bridge)
integration  →  agent, workflow, loop, tool, skill, hooks (all six, by their own public facades only)
integration  →  pipeline, bridge                          (reused, unchanged, for Reasoning/Provider routing)
integration  →  reasoning, provider, context, prompt, memory, knowledge
                                                            (only via pipeline/bridge's own existing
                                                             composition — never a direct import
                                                             bypassing them)

agent        →  core [, tenant]                            (unchanged — zero new import)
loop         →  core                                       (unchanged — zero new import)
workflow     →  core [, tenant]                             (unchanged — zero new import)
skill        →  core [, tenant]                              (unchanged — zero new import)
tool         →  core [, tenant]                               (unchanged — zero new import)
hooks        →  core [, tenant]                                (unchanged — zero new import)

agent   ⇥  workflow, skill, tool, hooks, loop, reasoning(-package)   (never — seams only, unchanged)
workflow ⇥  skill, tool, hooks                                        (never — seams only, unchanged)
skill    ⇥  tool                                                      (never — CapabilityResolver seam only)
loop     ⇥  agent, workflow, tool, skill, hooks, reasoning(-package)   (never — unchanged)
```

`integration` is the only package in the platform importing across this many
capability boundaries — the same role `pipeline` already plays for the six
Epic 5 runtimes, one level up. No certified capability imports `integration`
back (it is a pure composition root, never a dependency of anything it
composes) — mirroring `pipeline`'s and `bridge`'s own existing, verified
acyclic relationship to the runtimes they wire.

---

## 16. Sequence Diagrams

### 16.1 Successful execution

```
Client → API → Integration: submit goal
Integration → Agent Runtime: submit_goal(goal, capabilities, context_request)
Agent Runtime → Reasoning (via pipeline): decompose_goal → AgentPlan
Agent Runtime → Workflow (via AgentWorkflowPort): submit(step) → execution_id
Agent Runtime → Workflow (via AgentWorkflowPort): poll status → "running" → ... → "completed"
Workflow → Skill (via WorkflowSkillPort): plan(skill_ref) → SkillPlan   [if a skill-kind node]
Skill → Tool (via CapabilityResolver + ToolPort): invoke(...) → ToolResult
Workflow → Provider (via ProviderModelBridge, if a reasoning-bearing node exists): inference
Agent Runtime → Integration: AgentGoalResult (partial_success=True)
Integration → API → Client: response + trace_id
```

### 16.2 Plan revision

```
Agent Runtime → Workflow: poll → status "failed"
Agent Runtime: plan.revision < max_revisions? yes
Agent Runtime → Loop Engine: run_loop(LoopDefinition, reasoning_port=<pipeline-routed>,
                                       validator=AgentPlanValidator)
Loop Engine → Reasoning (via pipeline): propose → LoopProposal
Loop Engine → AgentPlanValidator: validate(proposal) → LoopValidation(passed=False)
Loop Engine → Reasoning (via pipeline): critique → LoopCritique
Loop Engine → Reasoning (via pipeline): repair → LoopRepair
Loop Engine → AgentPlanValidator: validate(repaired) → LoopValidation(passed=True)
Loop Engine → Agent Runtime: LoopResult(stop_reason="validation_success", converged=True)
Agent Runtime: parse final proposal → revised AgentPlan
Agent Runtime → Workflow: submit(revised step) → new execution_id
```

### 16.3 Workflow failure (revision budget exhausted)

```
Agent Runtime → Workflow: poll → status "failed"
Agent Runtime: plan.revision >= max_revisions
Agent Runtime: AgentGoalTrigger.FAIL → AgentGoalState.FAILED
Agent Runtime → Integration: AgentGoalResult(partial_success=False, error=...)
Integration → API → Client: typed error response (§12), trace_id preserved
```

### 16.4 Cancellation

```
Client → API → Integration: cancel(trace_id)
Integration: IntegrationCancellationToken.cancel()
Integration → Agent Runtime: cancel(execution, snapshot)
Agent Runtime: AgentGoalTrigger.CANCEL → AgentGoalState.ABANDONED
Agent Runtime → Workflow (via AgentWorkflowPort's concrete adapter, §6): forward cancellation
                                                                          to WorkflowExecutionRuntime
Workflow Runtime: its own existing cancellation path (unchanged)
Integration → API → Client: AgentGoalResult(partial_success=False, error="cancelled")
```

### 16.5 Replay

```
Operator → replay/ (existing, read-only): query events by trace_id
replay/EventCorrelator: groups every EventType emitted by Agent/Loop/Workflow/
                        Skill/Tool/Reasoning/Provider that shares this trace_id
                        — pure read over already-recorded core.events.Event objects,
                        zero live capability calls
Operator → AgentReplayEngine.validate(AgentReplayRecord): independently,
           per-capability, if a specific capability's own hash-chain needs
           re-verification (never triggered by the trace_id query itself)
```

---

## 17. Architectural Verification

- **Ownership**: every concern in §3's matrix traces to exactly one owner;
  no concern appears twice.
- **Dependency boundaries**: `integration` is additive-only and acyclic
  (§15); no certified capability's own dependency test needs updating,
  since none of them gain a new import.
- **Determinism**: `PipelineRoutedReasoningPort`, `WorkflowDelegationBridge`,
  and `AgentPlanValidator` (the three genuinely new composition-root
  classes) are all pure delegation/adaptation — no new randomness, no new
  unguarded wall-clock read introduced anywhere in this design.
- **Replay boundaries**: verified in §13 — no capability replays another's
  state; the only cross-capability artifact is a shared `trace_id` value,
  never a shared replay algorithm.
- **Context boundaries**: verified in §4, §10 — `IntegrationExecutionContext`
  never replaces or is consumed directly by any capability's own context
  shape; each capability's own constructor still receives only the fields
  it already accepts.
- **Cancellation boundaries**: verified in §3.1, §16.4 — one top-level
  token, chained (never shared as a raw object) into each capability's own
  local cancellation primitive.
- **Observability boundaries**: verified in §14 — zero duplicate telemetry;
  every metric traces to exactly one capability's own `*Statistics` object.

### Integration Risks

- **Naming collision**: the existing control-plane `api.models.Agent` (a
  persona/config DB row, Epic 1-4) and Epic 6's `agent.domain.AgentManifest`/
  `AgentIdentity` are different concepts that happen to share a name. The
  API→Integration translation (§4.2) must be unambiguous about this or a
  future implementer may conflate them.
- **`PipelineRoutedReasoningPort` adds one extra hop** (Context/Prompt
  assembly) to every Agent/Loop reasoning call, where their own certified
  designs only required a bare `ReasoningRequest`. This is a deliberate
  quality trade-off (richer assembled context per call) but changes latency
  characteristics neither Agent Runtime's nor Loop Engine's own performance
  audits measured against — should be benchmarked in M6.7B-1, not assumed
  free.
- **`AgentWorkflowPort`'s concrete adapter is the first place Workflow's own
  unverified `WorkflowPort` conformance gap (Deferred Decisions item 10)
  becomes operationally load-bearing** — today it is a documented, inert
  gap; once `WorkflowDelegationBridge` is built and actually calls into
  `WorkflowExecutionRuntime`'s concrete methods, any latent conformance
  mismatch stops being theoretical. M6.7B-1 must smoke-test this path
  explicitly, not merely reuse Bundle 3's own fakes.
- **`AgentPlanValidator`'s own correctness logic is new, untested domain
  logic** — Loop Engine's own certification (M6.6C) flagged "an
  unimplemented `LoopValidator` is a silent no-op risk" as a named risk;
  `AgentPlanValidator` is the first real implementation, and its own
  correctness (not Loop Engine's) becomes the thing that could silently
  accept a bad revised plan.

---

## 18. ADR Recommendations (not written — recommended only)

1. **Agent Platform Integration is a composition root, not a fourteenth
   capability** — no registry, no lifecycle, no domain model of its own;
   mirrors `pipeline`'s/`bridge`'s own precedent, extended one layer up.
2. **`AgentPort`/`WorkflowPort` conformance is resolved via bridge adapters
   at the composition root, never inside `agent/`/`workflow/`** — the same
   pattern `ProviderModelBridge` already established for `ModelProvider`,
   finally closing Deferred Decisions items 1 and 10 as a natural
   consequence of integration, not a capability redesign.
3. **Agent/Loop reasoning calls are routed through `pipeline.RuntimePipeline`
   via a new `PipelineRoutedReasoningPort` adapter**, rather than a bare
   `ReasoningRuntime.run()` call — trading one extra hop for full Memory/
   Knowledge/Context/Prompt assembly on every planning/refinement call.
4. **One top-level `IntegrationCancellationToken`, chained (never shared) into
   each capability's own local cancellation primitive** — extending the
   "duplicate, don't cross-import" cancellation-token precedent one level up.
5. **`trace_id` is minted once at the composition root and threaded by value
   into every capability's own already-existing `trace_id` field** — no new
   propagation mechanism, no shared mutable context object.
6. **Cross-capability replay correlation is a `trace_id`-keyed read over the
   existing top-level `replay/` package's `EventCorrelator`, never a new
   replay engine** — formalizing that Agent Platform Integration adds zero
   replay logic of its own.

---

## 19. Bundle Breakdown (Exactly Three, Per This Milestone's Instruction)

| Bundle | Scope |
|---|---|
| **M6.7B-1 — Core Integration, Execution Wiring, Context Propagation** | New `integration/` package: `IntegrationExecutionContext`, `IntegrationCancellationToken`, `IntegrationConfiguration`, `WorkflowDelegationBridge` (concrete `AgentWorkflowPort`), `AgentPortBridge`/`WorkflowPortBridge` (resolving Deferred Decisions items 1/10), `PipelineRoutedReasoningPort`, `AgentPlanValidator`, the API→Integration translation layer, `IntegrationFactory` (composition root). Smoke-test the full successful-execution path (§16.1) end-to-end against real (not faked) Agent/Workflow/Reasoning/Provider instances. |
| **M6.7B-2 — Replay, Observability, Diagnostics, Failure Handling** | `IntegrationDiagnostics`, `trace_id` propagation verification across every capability's own event emissions, the plan-revision path (§5, §16.2) wired end-to-end with a real `AgentPlanValidator`, workflow-failure and cancellation paths (§16.3, §16.4) smoke-tested, `IntegrationError` boundary translation into `api/`'s existing HTTP error handling. |
| **M6.7C — Production Certification** | Full architecture/dependency/runtime-completeness/determinism/performance/documentation audit mirroring every prior Epic 6 certification's own methodology — with particular attention to the three Integration Risks named in §17 (the `PipelineRoutedReasoningPort` latency trade-off, the newly-load-bearing `WorkflowPort` conformance gap, and `AgentPlanValidator`'s own correctness logic). |

---

## 20. Risks

(Consolidated from §17's Integration Risks — repeated here per this
milestone's own deliverable structure.)

- Naming collision between control-plane `Agent` (Epic 1-4 DB model) and
  Epic 6 `AgentManifest`/`AgentIdentity`.
- `PipelineRoutedReasoningPort`'s added latency, unmeasured against any
  existing performance baseline.
- `WorkflowDelegationBridge` making Workflow's own unverified `WorkflowPort`
  conformance gap operationally load-bearing for the first time.
- `AgentPlanValidator`'s own correctness logic being new, untested domain
  logic sitting behind Loop Engine's own "no-op validator" risk.

---

## 21. Recommendation

Design approved for **M6.7B-1 — Core Integration, Execution Wiring, Context
Propagation** next, per this document's own three-bundle breakdown (§19).
All thirteen certified capabilities remain untouched and frozen; this design
introduces exactly one new package (`integration/`) whose only artifacts are
composition-root wiring and request/response shapes.
