# Workflow Runtime — Architecture Design (M6.4A)

**Status:** Design only — zero implementation. Supersedes Phase 0's brief §10 sketch
(`epic6-agent-platform-architecture.md`) with the complete design this milestone
commissioned. Tool Runtime, Skill Runtime, and Hook Framework are certified and
**frozen**; this document proposes zero changes to any of them, or to any Epic 5
runtime.

**Milestone:** M6.4A (design-only). Implementation is explicitly out of scope —
see §12 Bundle Breakdown.

---

## 1. What a Workflow Is (and Is Not)

A **Workflow** is a deterministic orchestration graph — nodes and edges, frozen
and content-hashed — that coordinates already-existing platform capabilities
(Skills, Tools, and cross-cutting Hooks) in a declared order, with durable
execution state, checkpointing, retry, and recovery. Workflow Runtime owns
**orchestration only**: it decides *when* and *in what order* things happen,
never *what* happens or *why*.

A Workflow is explicitly **not**:

| Not this | Why |
|---|---|
| An Agent | An Agent reasons, plans, and adapts turn-by-turn; a Workflow executes an already-declared, static graph. There is no reasoning call anywhere in this design. |
| A Skill | A Skill is a composable, planned *unit* an orchestrator can invoke; a Workflow is the orchestrator that invokes it (via a Skill Node). |
| A Tool | A Tool performs one real-world side effect; a Workflow sequences calls to Tools (via a Tool Node), never performs one itself. |
| A Hook | A Hook intercepts a lifecycle moment and returns a verdict; a Workflow is a *subject* Hooks observe (`before_workflow`/`after_node`/etc.), never a Hook implementation itself. |
| A Provider | Workflow Runtime never routes a model call — that remains Reasoning/Provider Runtime's concern, reached only indirectly through a Skill or Tool node, never directly. |

---

## 2. Responsibilities (Ownership Boundary)

**Owns:**

- Workflow registration, discovery, manifests, descriptors, lifecycle
- Workflow graph (nodes, edges, validation, hashing)
- Workflow execution planning (the deterministic order a graph resolves to)
- Workflow execution state (a second, dual-FSM lifecycle — see §6)
- Workflow checkpoints and recovery
- Workflow cancellation, retry policy, timeout policy (ownership; ties into
  `core.policies.RetryPolicy` verbatim, no new backoff math)
- Workflow observability, replay, statistics, diagnostics

**Never owns:**

- Reasoning or planning *decisions* (a Workflow graph is authored once,
  declaratively — it is never generated or altered by an inference call)
- Tool execution (Tool Runtime's own concern, reached via a Tool Node)
- Skill execution (a Skill never executes itself either — Workflow reaches a
  Skill's *plan* the same way a future Agent Runtime would, via an injected
  seam, never by importing `skill` directly)
- Provider routing
- Memory retrieval
- Hook implementation (Workflow is a Hook *subject*, never a `Hook` author)

---

## 3. Ownership Matrix

| Concept | Owner | Never |
|---|---|---|
| `WorkflowManifest`/`WorkflowDescriptor` | Workflow Runtime | Executable code — a manifest is data, exactly like `ToolManifest`/`SkillManifest`/`HookManifest` |
| `WorkflowRegistry` | Workflow Runtime | Execution, capability resolution, reasoning |
| `WorkflowGraph`/`WorkflowNode`/`WorkflowEdge` | Workflow Runtime | Arbitrary code (edges carry a small, closed condition grammar — never `eval()`) |
| `WorkflowExecution`/`WorkflowExecutionState` | Workflow Runtime | Business logic — the state machine tracks *that* a node ran, never *what* it decided |
| `WorkflowCheckpoint`/`WorkflowSnapshot` | Workflow Runtime | Ephemeral storage — this is the platform's one deliberate durable-repository exception (§7) |
| Retry/timeout/cancellation policy | Workflow Runtime (ownership); `core.policies.RetryPolicy` (mechanism, reused verbatim) | New backoff math |
| Tool execution | Tool Runtime, reached via `core.ports.tool.ToolPort` | Workflow Runtime itself |
| Skill execution | A future Agent Runtime (or whichever runtime is first granted execution authority), reached via a Workflow-owned seam interface | Workflow Runtime itself, and never Skill Runtime itself either |
| Hook evaluation | Hook Framework, reached via a Workflow-owned seam interface | Workflow Runtime itself |
| Reasoning | Reasoning Runtime (never reached by this design at all) | Workflow Runtime |

---

## 4. Workflow Graph Design

### 4.1 WorkflowNode

A single frozen, data-only shape with a `kind` discriminator — **not** a class
hierarchy. This preserves "declarative graph, not code-as-intelligence" (the
frozen `WorkflowPort`'s own docstring commitment) and mirrors how `ToolManifest`/
`SkillManifest` stay plain data rather than polymorphic objects.

```python
class WorkflowNodeKind(StrEnum):
    """Additive only (AR-32) — a new kind is a backward-compatible enum
    member, never a breaking change. `config` is the generic escape hatch
    for a kind's parameters not yet promoted to a first-class field."""
    SKILL = "skill"
    TOOL = "tool"
    CONDITIONAL = "conditional"
    PARALLEL = "parallel"
    MERGE = "merge"
    DELAY = "delay"
    EVENT = "event"


@dataclass(frozen=True, slots=True)
class WorkflowNode:
    node_id: str
    kind: WorkflowNodeKind
    # kind-specific references -- exactly one is meaningful per `kind`,
    # structurally validated at registration (§4.3), never enforced by a
    # subclass hierarchy.
    skill_ref: tuple[str, str, str] | None = None   # (namespace, id, version) for SKILL
    tool_ref: tuple[str, str, str] | None = None    # (namespace, name, version) for TOOL
    condition: WorkflowCondition | None = None       # for CONDITIONAL
    delay_ms: int | None = None                      # for DELAY
    event_name: str | None = None                    # for EVENT
    retry_policy: RetryPolicyRef | None = None        # reuses core.policies.RetryPolicy
    failure_policy: WorkflowFailurePolicy = WorkflowFailurePolicy.PROPAGATE
    config: Mapping[str, Any] = field(default_factory=dict)  # forward-compatible escape hatch
```

`PARALLEL`/`MERGE` carry no extra fields of their own — fan-out/fan-in shape is
entirely expressed through `WorkflowEdge` topology (multiple edges leaving a
`PARALLEL` node; multiple edges arriving at a `MERGE` node), never a separate
branch-list field. This keeps exactly one source of truth for graph topology
(the edge set), never two.

### 4.2 WorkflowEdge + deterministic conditions

```python
class WorkflowConditionOperator(StrEnum):
    EQ = "eq"; NEQ = "neq"; GT = "gt"; LT = "lt"; GTE = "gte"; LTE = "lte"
    IN = "in"; EXISTS = "exists"


@dataclass(frozen=True, slots=True)
class WorkflowCondition:
    """A small, closed expression grammar evaluated against the execution's
    own accumulated variables -- never arbitrary code, never `eval()`.
    Mirrors the deliberately-narrow `RoutingContext` strategy-input shape
    already established in Provider Runtime's router."""
    field: str
    operator: WorkflowConditionOperator
    value: Any = None


@dataclass(frozen=True, slots=True)
class WorkflowEdge:
    source: str  # node_id
    target: str  # node_id
    condition: WorkflowCondition | None = None  # None -> unconditional edge
```

### 4.3 WorkflowGraph

```python
@dataclass(frozen=True, slots=True)
class WorkflowGraph:
    entry_node: str
    nodes: tuple[WorkflowNode, ...]
    edges: tuple[WorkflowEdge, ...]
    graph_hash: str = ""  # content-hash over the full, sorted node+edge set
```

**Validation is a real algorithm here, not "acyclic by construction."** Unlike
Skill Runtime's dependency graph — where each new skill can only reference an
*already-registered* skill, making a true cycle structurally unreachable
through the public API — a `WorkflowGraph` arrives as **one complete manifest**
at registration time, every node and edge present at once. There is no
incremental-registration guarantee to lean on. Cycle detection is therefore
the **primary** mechanism here, not defense-in-depth: a standard DFS/Kahn's-
algorithm topological sort runs over the whole graph at registration, and an
unreachable node, a dangling edge reference, or a real cycle all fail fast
with `ConfigError`, exactly like every other structural validation in this
repository.

**Deterministic ordering** for nodes with no edge dependency between them
(e.g. two branches of a `PARALLEL` fan-out) is **edge-declaration order, not
completion order** (Phase 0's own §10 decision, retained verbatim) — so a
`MERGE` node's join order is always reproducible on replay regardless of which
branch's Tool/Skill call happened to finish first in wall-clock time.

**`graph_hash`** is a content-hash over the sorted `(node_id, kind, refs,
config)` tuples and the sorted `(source, target, condition)` tuples — two
graphs with the identical topology (regardless of node/edge declaration
order in the source manifest) hash identically, the same "declaration order
doesn't matter, content does" discipline every other manifest hash in this
repository already follows.

---

## 5. Node Model — Extensibility

`WorkflowNodeKind` is additive (AR-32): a future node type is a new enum
member plus, if it needs a new first-class reference field, an additive
`WorkflowNode` field with a `None` default (never a breaking change to an
already-serialized graph). Until a kind earns a first-class field, its
parameters live in the generic `config: Mapping[str, Any]` bag — the same
forward-compatible escape hatch `ToolManifest.metadata`/`SkillManifest.metadata`
already provide. No node type is ever expressed as Python code; every node
type this design anticipates (and every one after it) stays data-only.

---

## 6. Execution Model

### 6.1 Two lifecycles — restoring Tool Runtime's dual-FSM shape

Skill Runtime and Hook Framework each deliberately chose **one** lifecycle FSM,
because neither a Skill nor a Hook executes over time — a Skill is planned,
a Hook returns one verdict. A **Workflow does execute over time** — it is
the platform's first genuine, multi-step, potentially-paused, potentially-
recovering orchestrator since Tool Runtime's own execution FSM. This design
therefore **restores Tool Runtime's dual-FSM shape**, not Skill/Hook's
single-FSM asymmetry:

1. **Descriptor lifecycle** (is this workflow *version* usable at all):
   `DRAFT → REGISTERED → ACTIVE → DEPRECATED → RETIRED`, identical shape to
   every other closed runtime's descriptor FSM.
2. **Execution lifecycle** (is this *running instance* healthy):

```
PENDING → RUNNING → COMPLETED
             │  ↕ (PAUSE / RESUME, external signal via WorkflowPort.signal)
             │ PAUSED
             │
             ├─ NODE_FAILED ─→ RECOVERING ─→ RUNNING   (retry succeeds)
             │                      │
             │                      └─→ COMPENSATING ─→ FAILED  (compensation runs, terminal)
             │
             └─ CANCEL (from any non-terminal state) ─→ CANCELLED
```

Both FSMs reuse `core.fsm.StateMachineDefinition`/`StateMachine` unchanged —
no new state-machine framework, the same established precedent every closed
runtime in this repository already follows.

### 6.2 Checkpoint strategy

**Checkpoint after every completed node, always** — not a time-based or
sampled interval. A wall-clock-gated checkpoint cadence would make replay
non-deterministic (two runs of the identical graph could checkpoint at
different points depending on real execution speed); checkpointing on every
node boundary keeps the checkpoint sequence a pure function of the graph and
its inputs. Sparse/interval checkpointing is explicitly out of scope for this
design (a future, opt-in performance optimization, never a default).

### 6.3 Retry, timeout, cancellation ownership

- **Retry**: node-level, reusing `core.policies.RetryPolicy` verbatim (already
  present at `core/policies.py`) — no new backoff math invented, per Phase 0's
  own §10 decision, now confirmed still correct by direct inspection of the
  policy module.
- **Timeout**: ownership only in this design; a per-node timeout value gates
  when `NODE_FAILED` fires, mechanism deferred to Bundle 2.
- **Cancellation**: a `WorkflowCancellationToken`, duplicated locally (not
  imported) from `pipeline.PipelineCancellation`'s shape — the same
  "duplicate, don't cross-import" precedent every closed runtime in this
  repository already follows for this exact primitive (Tool Runtime's
  streaming bundle did the same). Checked between every node boundary;
  cancellation always reaches a terminal state (`CANCELLED`), never orphaned.

### 6.4 Failure propagation, compensation, partial completion

`WorkflowFailurePolicy` (per node): `PROPAGATE` (default — a node failure
drives the execution FSM to `RECOVERING`/`FAILED`), `COMPENSATE` (drives to
`COMPENSATING` — runs a declared compensating node, if any, before the
execution still terminates `FAILED`), `IGNORE` (the node's failure is recorded
but does not fail the execution — used for genuinely optional side-effect
nodes, e.g. a best-effort notification).

**Partial completion is a first-class, honest result**, never silently
discarded: `WorkflowExecutionResult` always reports which nodes actually
completed (from the checkpoint history) even when the overall execution
terminates `FAILED`/`CANCELLED` — mirroring Skill Runtime's own
`RequirementVerdict.UNKNOWN` philosophy of "record the honest state, never
assume completion that didn't happen."

### 6.5 Recovery and replay ownership (ownership only — no implementation)

Recovery resumes a `RECOVERING` execution from its **last checkpoint**, never
from the start of the graph — the checkpoint's `WorkflowSnapshot` is the
complete, sufficient state to resume. Replay is a pure re-verification that
an already-recorded checkpoint sequence would reproduce the same `graph_hash`
+ `checkpoint_hash` chain — mirroring the "hash-chain re-verification, never
a live re-run" discipline Tool/Skill/Hook's own replay engines already
established. Both are ownership-declared here; concrete recovery/replay
engines are Bundle 2 scope.

---

## 7. Checkpoint Model

```python
@dataclass(frozen=True, slots=True)
class WorkflowCheckpoint:
    checkpoint_id: str
    execution_id: str
    node_id: str
    sequence: int  # monotonic, per-execution
    variables_hash: str    # content-hash of the accumulated variables at this point
    checkpoint_hash: str    # content-hash(execution_id, node_id, sequence, variables_hash)
    created_at: float


@dataclass(frozen=True, slots=True)
class WorkflowSnapshot:
    """The complete, reconstructable state at a checkpoint -- the durable
    aggregate Phase 0 called `WorkflowInstance`, formalized and renamed here
    per this milestone's own naming. Sufficient, on its own, to resume a
    RECOVERING execution without replaying the graph from the start."""
    execution_id: str
    current_node: str
    variables: Mapping[str, Any]
    node_history: tuple[str, ...]  # completed node_ids, in completion order
    last_checkpoint: WorkflowCheckpoint | None = None
```

**Durability is the one deliberate, named exception** to every other closed
runtime's "temporary repository" pattern in this codebase (Phase 0's own §10
decision, retained and now formalized): a `WorkflowSnapshot` must survive a
process restart by definition, since an in-flight orchestration cannot simply
vanish. The already-existing `core.providers.stores` contracts (`DocumentStore`
+ `StoreCapabilities.durable`) are reused **unchanged** — only the configured
store's `durable` flag differs from every other closed runtime's repository
convention (which use `durable=False` in-memory adapters by default). No new
persistence contract is invented.

---

## 8. WorkflowExecutionContext

### 8.1 The extend-not-replace requirement, reconciled with zero cross-import

The milestone requires `WorkflowExecutionContext` to **extend (not replace)**
`SkillExecutionContext`, preserving backward compatibility — while also
requiring Workflow Runtime to **never import implementation packages** from
Skill Runtime. These two constraints are reconciled the same way every other
closed-runtime boundary in this repository resolves an identical tension:
**structural extension via duplication, not inheritance or import.**

`WorkflowExecutionContext` is declared as its own frozen dataclass inside
`workflow/`, carrying **every field `SkillExecutionContext` already has, by
the same name and type** (duplicated, per the established "duplicate, don't
cross-import" precedent — `skill/ids.py`/`skill/_run.py` already duplicate
`tool/`'s equivalents for the identical reason), **plus** Workflow-specific
fields appended additively. Any code written against `SkillExecutionContext`'s
field names finds the same fields present here — a genuine, backward-
compatible structural superset — while `workflow` still imports zero `skill`
package code, and `workflow → core[, tenant]` remains exactly true with no
exception carved out for this shape.

```python
@dataclass(frozen=True, slots=True)
class WorkflowExecutionContext:
    # -- fields present on skill.planning.domain.SkillExecutionContext,
    #    duplicated verbatim (same names/types) for structural, backward-
    #    compatible extension without importing skill/ --
    context_id: str
    plan_id: str            # a Skill Node's resolved plan_id, when applicable
    root: str                # "namespace.id@version" of the relevant skill, when applicable
    namespace: str
    org_id: str = ""
    tenant_id: str = ""
    requested_by: str = ""
    created_at: float = 0.0
    capability_requirements: tuple[Any, ...] = ()  # opaque at this layer -- never resolved here either
    metadata: Mapping[str, str] = field(default_factory=dict)

    # -- additive, Workflow-specific fields --
    execution_id: str = ""
    graph_hash: str = ""
    current_node_id: str = ""
    checkpoint_id: str | None = None
    retry_count: int = 0
```

### 8.2 Why this is safe

`capability_requirements` is typed `tuple[Any, ...]` at this layer rather than
importing `SkillCapabilityRequirement` — the field's *shape* (a tuple) is
preserved for structural compatibility, but its *element type* is deliberately
left opaque here, since resolving what a `SkillCapabilityRequirement` even
means is Skill Runtime's own concern, never Workflow Runtime's. A future
composition root that actually needs the typed element re-imports it at the
integration layer, never inside `workflow/` itself.

---

## 9. Hook Integration (design only — no implementation)

### 9.1 Reusing existing HookPoints, recommending three additive ones

Hook Framework already defines `HookPoint.BEFORE_WORKFLOW_STEP` /
`AFTER_WORKFLOW_STEP` and `HookRuntimeScope.WORKFLOW` — these map directly to
this milestone's requested `before_node`/`after_node` and are reused
**verbatim, with zero Hook Framework change**.

`before_workflow`, `after_workflow`, and `workflow_failure` (whole-execution
boundaries, not per-node) have **no existing `HookPoint` member** — Hook
Framework's own `HookPoint` enum only reaches down to the node/step level
today. This design **recommends**, but does not perform, an additive
extension:

```python
# core/events.py or hooks/domain.py, HookPoint -- ADDITIVE ONLY, a future
# change, never performed by this design or by Workflow Runtime itself:
BEFORE_WORKFLOW = "before_workflow"
AFTER_WORKFLOW = "after_workflow"
WORKFLOW_FAILURE = "workflow_failure"
```

This is explicitly **not** a Hook Framework redesign — it is the exact,
pre-declared additive mechanism Hook Framework's own design commits to
("new points are a backward-compatible enum addition, never a breaking
change," §4 of the M6.3A design). Adding three enum members when Bundle 2
actually wires hook integration is squarely within that commitment. This
design records the need; it does not act on it, honoring "Do NOT modify:
... Hook Framework" for *this* milestone.

### 9.2 No direct `hooks` import — a Workflow-owned seam, mirroring `CapabilityResolver`

Hook Framework, like Skill Runtime, has no `core.ports` entry (per its own
ADR-0010: "a cross-cutting library, not a `core.ports` entry"). Workflow
Runtime therefore cannot "integrate through a published interface" in the
frozen-port sense for Hooks any more than Skill Runtime could for Tool
capability resolution. The identical resolution applies: Workflow declares
its **own** extension interface —

```python
class WorkflowHookAdapter(ABC):
    """The sole seam into Hook Framework -- never implemented by Workflow
    Runtime itself. A future composition root injects a concrete adapter
    wrapping the real hooks.HookRuntime.run(...), exactly mirroring how
    skill.extensions.CapabilityResolver defers to a future caller wrapping
    Tool Runtime's own published surface."""

    @abstractmethod
    def run(self, point: str, event: WorkflowHookEvent) -> WorkflowHookVerdict: ...
```

— in its own `extensions.py`, never importing `hooks` directly. `workflow →
core[, tenant]` remains exactly true; `WorkflowHookAdapter` stays interface-
only in this design and in Bundle 1/2, wired by whichever future milestone
first has real execution authority to compose it (most likely Agent Runtime
or a Workflow-Hook integration pass, not Workflow Runtime's own bundles).

---

## 10. Policies, Observability, Replay (ownership only — no implementation)

Per this milestone's explicit "ownership only" instruction for these three
areas (concrete implementation is Bundle 2 scope, per §12):

- **`WorkflowPolicyEngine`** (interface, `extensions.py`): allow/deny/visibility
  verdicts over workflow registration and node-level failure handling —
  reuses the `PolicyVerdict`-shape precedent (ALLOW/DENY, matching Skill's own
  two-state shape; a future `PENDING`/approval state is possible but not
  designed here, deferred the same way Skill Runtime deferred it).
- **`WorkflowObservabilitySink`** (interface): execution/checkpoint/recovery
  traces — mirrors `HookObservabilitySink`'s "ownership only, Bundle 2 gives
  the first concrete default" precedent.
- **`WorkflowReplaySink`** (interface): checkpoint-chain replay records —
  mirrors `HookReplaySink`'s identical posture.

None of these three are implemented in this design or any Bundle 1 work —
concrete bodies are explicitly Bundle 2 scope, matching the milestone's own
instruction.

---

## 11. Architecture

### Package Structure

```
platform/workflow/
├── domain.py              # WorkflowManifest, WorkflowDescriptor, WorkflowLifecycleState,
│                            #   WorkflowNode, WorkflowNodeKind, WorkflowEdge, WorkflowCondition,
│                            #   WorkflowGraph, WorkflowExecutionState, WorkflowExecution,
│                            #   WorkflowExecutionResult, WorkflowExecutionContext,
│                            #   WorkflowCheckpoint, WorkflowSnapshot, WorkflowFailurePolicy
├── ids.py                   # content-hash ids (local copy of the established pattern)
├── lifecycle.py               # descriptor core.fsm (DRAFT..RETIRED)
├── execution_lifecycle.py       # execution core.fsm (PENDING..terminal) -- the dual-FSM
│                                #   restoration, §6.1
├── graph.py                      # WorkflowGraph validation: topological sort, real cycle
│                                 #   detection (primary mechanism, not defense-in-depth),
│                                 #   dangling-reference validation, graph_hash
├── registry.py                    # WorkflowRegistry -- registration/discovery/namespacing/
│                                  #   version-resolution/lifecycle; no execution
├── validation.py                   # manifest + graph structural validation
├── serializer.py                    # manifest/descriptor/graph/context <-> dict, versioned
├── config.py                         # WorkflowConfiguration (durable store config -- §7)
├── factory.py                         # WorkflowFactory.create/default (composition root)
├── extensions.py                       # WorkflowSkillPort (seam into Skill execution),
│                                       #   WorkflowHookAdapter (seam into Hook Framework),
│                                       #   WorkflowPolicyEngine, WorkflowObservabilitySink,
│                                       #   WorkflowReplaySink -- interfaces only
└── repositories/
    └── workflow_repository.py          # DURABLE (StoreCapabilities.durable=True) -- the
                                         #   one deliberate exception to every other closed
                                         #   runtime's temporary-repository convention
```

Bundle 2 adds an `execution/` subpackage (mirrors Skill's `planning/` split):
`engine.py` (the execution loop -- node-by-node advance, checkpoint-after-
every-node), `checkpoint.py`, `recovery.py`, `replay.py`/`replay_engine.py`,
`observability.py`, `diagnostics.py`, `statistics.py`. Named now so Bundle 1's
package shape does not need to be revisited when Bundle 2 lands.

### Dependency Graph

```
workflow → core[, tenant]
```

**No exception, not even for Tool/Skill/Hook "published interfaces."** Tool
Runtime's interface already lives inside `core` (`core.ports.tool.ToolPort`),
so reaching it costs nothing beyond the existing `core` dependency. Skill
Runtime and Hook Framework have no `core.ports` entry (by their own, already-
ratified ADRs), so Workflow declares its **own** extension interfaces for
both (`WorkflowSkillPort`, `WorkflowHookAdapter`) rather than importing either
package — the identical "declare the seam, defer the wiring" resolution
Skill Runtime already applied to Tool Runtime via `CapabilityResolver`, now
applied twice more, uniformly.

**Literal `WorkflowPort` conformance** (`start`/`signal`/`status`) is a design
target, not a guarantee at this stage — Tool Runtime's own certification
(M6.1B-5) already deferred literal `ToolPort` conformance as accepted,
non-blocking debt; this design expects the same honest possibility here and
records it explicitly (§13 Risks) rather than assuming conformance away.

### Ownership Matrix

See §3.

### Extension Strategy

- `WorkflowSkillPort` / `WorkflowHookAdapter` — the two sole seams into Skill
  execution and Hook evaluation, both interface-only, both deferred to a
  future composition root (most likely Agent Runtime, the first runtime with
  genuine cross-capability execution authority).
- `WorkflowNodeKind` — additive enum + generic `config` bag for forward-
  compatible new node types (§5).
- `WorkflowPolicyEngine` / `WorkflowObservabilitySink` / `WorkflowReplaySink`
  — reserved, unimplemented (§10).

### Composition Strategy

`WorkflowFactory.create(config, *, workflows=(), skill_port=None,
hook_adapter=None, ...)` — identical shape to every other closed runtime's
composition-root convention, with the two Workflow-specific seams injected
exactly like `CapabilityResolver` is injected into a future Skill consumer.

---

## 12. ADR Recommendations

1. **Workflow Runtime restores the dual-FSM shape** (descriptor lifecycle +
   execution lifecycle), diverging deliberately from Skill/Hook's single-FSM
   asymmetry — because a Workflow, unlike a Skill or a Hook, genuinely
   executes over time and needs a distinct runtime-execution lifecycle. This
   should be recorded so a future contributor does not "simplify" Workflow
   down to one FSM by analogy with the two most recent Epic 6 runtimes.
2. **Cycle detection in `WorkflowGraph` is a real, primary algorithm, not
   defense-in-depth** — unlike Skill Runtime's dependency graph, a
   `WorkflowGraph` arrives as one complete manifest, so there is no
   incremental-registration guarantee making a cycle structurally
   unreachable. This distinction should be recorded explicitly so a future
   implementer does not mistakenly copy Skill Runtime's "acyclic by
   construction, DFS guard is just defense-in-depth" reasoning here, where it
   would not hold.
3. **`WorkflowExecutionContext` extends `SkillExecutionContext` by structural
   duplication, never by inheritance or import** — reconciling "extend, don't
   replace, preserve backward compatibility" with "never import Skill
   Runtime's implementation packages." This is the same "duplicate, don't
   cross-import" precedent applied to a payload shape instead of a utility
   function, and should be recorded so the reasoning isn't reinvented (or
   worse, resolved by importing `skill/` directly) later.
4. **Workflow state is the platform's one deliberate durable-repository
   exception** — formal ratification of Phase 0's already-proposed ADR-0009,
   now backed by the complete `WorkflowCheckpoint`/`WorkflowSnapshot` design
   in this document (§7). Every other closed runtime's repository is
   temporary by convention; this one is durable by requirement, and that
   must never be "corrected" back to the temporary default by a future
   contributor pattern-matching on every other runtime in this repository.
5. **`before_workflow`/`after_workflow`/`workflow_failure` are a recommended,
   additive `HookPoint` extension, not a Hook Framework redesign** — recorded
   explicitly (§9.1) so the eventual three-member enum addition is understood
   as exercising Hook Framework's own pre-declared extension mechanism
   (AR-32), never as evidence that Hook Framework needs "completing."

---

## 13. Bundle Breakdown (Exactly Three, Per This Milestone's Instruction)

| Bundle | Scope |
|---|---|
| **M6.4B-1 — Core, Registry, Graph, Lifecycle** | `WorkflowManifest`/`WorkflowDescriptor`, `WorkflowRegistry` (registration/discovery/namespacing/version-resolution — no execution), `WorkflowGraph`/`WorkflowNode`/`WorkflowEdge`/`WorkflowCondition` + real topological/cycle validation + `graph_hash`, the descriptor `core.fsm` lifecycle, serializer/config/factory, `extensions.py` interfaces declared (zero implementation). No execution, no checkpoints, no hook wiring. |
| **M6.4B-2 — Execution, Checkpoints, Recovery, Replay, Observability** | The execution `core.fsm` (§6.1), the execution engine (node-by-node advance, deterministic parallel fan-out/fan-in per edge-declaration order), checkpoint-after-every-node (§6.2), recovery-from-last-checkpoint, `WorkflowReplayEngine` (pure hash-chain re-verification), first concrete `WorkflowObservabilitySink`, diagnostics/statistics, the durable repository. Hook integration remains interface-only unless the three additive `HookPoint` members (§9.1, ADR item 5) have separately landed by then. |
| **M6.4C — Production Certification** | Full architecture/dependency/runtime-completeness/determinism/performance/documentation audit of M6.4B-1/M6.4B-2, mirroring the M6.1B-5/M6.2B-3/M6.3C certification methodology (7-category, 100-point scoring; Blocking/High/Medium/Low technical debt; explicit PASS/FAIL closure recommendation). |

---

## 14. Risks

| Risk | Mitigation |
|---|---|
| A future implementer copies Skill Runtime's "acyclic by construction" reasoning onto `WorkflowGraph`, skipping real cycle detection | ADR item 2 (§12) records explicitly why that reasoning does not transfer here. |
| Durable workflow state is "corrected" back to a temporary-repository default by pattern-matching on every other closed runtime | ADR item 4 (§12) + §7's explicit framing as "the one deliberate exception." |
| `WorkflowExecutionContext`'s structural-duplication approach drifts from `SkillExecutionContext`'s actual shape over time (the two are edited independently, no import ties them) | A future dependency-boundary test (Bundle 1) should assert field-name/type parity between the two shapes without importing `skill/` — a structural, not import-based, compatibility check. |
| Literal `WorkflowPort` conformance is assumed complete when it is not | Recorded explicitly (§11) as a design target, not a guarantee, mirroring Tool Runtime's own honestly-deferred conformance debt. |
| The recommended additive `HookPoint` extension (§9.1) is implemented as a Hook Framework redesign instead of a pure enum addition | ADR item 5 (§12) states this explicitly; Bundle 2 must add only new enum members, touching zero existing `HookPoint`/`HookRuntimeScope` behavior. |
| `WorkflowNodeKind`'s generic `config: Mapping[str, Any]` bag becomes a dumping ground that silently grows into untyped, unvalidated business logic | Node-kind-specific first-class fields (§4.1) remain the preferred path for anything beyond simple parameters; `config` is scoped explicitly as a forward-compatibility escape hatch, not a general-purpose payload. |

---

## 15. Recommendation

Design approved for implementation. Recommend proceeding to **M6.4B-1 —
Workflow Runtime Bundle 1 (Core, Registry, Graph, Lifecycle)** on explicit
commission, following the three-bundle plan in §13. Zero changes to Tool
Runtime, Skill Runtime, Hook Framework, or any Epic 5 runtime are proposed or
required by this design.
