# Agent Runtime — Design (M6.5A)

Supersedes Phase 0's brief sketch of Agent Runtime (`epic6-agent-platform-architecture.md`
§11, "implements the frozen `core.ports.agent.AgentPort`; planning is an ordinary
`ReasoningRuntime.infer` call, never a new model integration"). Tool Runtime, Skill
Runtime, Hook Framework, and Workflow Runtime are certified and frozen; this design
integrates with all four exclusively through published interfaces, never through their
implementation packages.

---

## 1. What an Agent Is (and Is Not)

An **Agent** is a long-lived, autonomous orchestration entity: it holds an identity and
a capability model that persists across many goals and many conversations, decomposes
an incoming goal into a plan, resolves which capabilities the plan needs, and delegates
the actual work to Workflow Runtime. It never executes anything itself.

An Agent is **not**:
- a **Workflow** — a Workflow is a bounded, single-execution orchestration graph; an
  Agent is unbounded, surviving across many Workflow executions over its lifetime.
- a **Skill** — a Skill is a versioned, declarative capability manifest; an Agent
  *uses* Skills (indirectly, via Workflow) but is not one.
- a **Tool** — an Agent never executes a Tool call directly; only Workflow (via
  `ToolPort`) or Reasoning (via its own injected `ToolPort`, per M5.5B-2) do.
- a **Hook** — a Hook is a stateless interception point; an Agent is a stateful,
  identity-bearing entity.
- a **Provider** — an Agent never talks to a model vendor directly; all inference
  goes through `core.ports.reasoning.ReasoningPort`.

The critical distinction driving this design: **the unit of "executes over time" is
the Goal, not the Agent.** An Agent's identity is durable-but-idle between goals; a
Goal is the bounded, stateful unit of work, structurally analogous to a Workflow
execution. This resolves an apparent tension in the milestone brief ("long-lived
autonomous entity" vs. the platform's now-established "bounded execution" shape for
every other closed runtime) without inventing a third kind of statefulness.

---

## 2. Responsibilities (Ownership Boundary)

**Owns:**
- Agent registration, discovery, manifests, descriptors, lifecycle (identity side)
- Agent identity (`AgentIdentity`) and capability model (`AgentCapability`)
- Goal decomposition, capability resolution, workflow selection, delegation (planning
  side — structure and sequencing only; the actual reasoning content is Reasoning
  Runtime's, per §5)
- Conversation state, decision history (audit/replay side)
- The seam through which Reasoning, Memory, Knowledge, Prompt, and Context
  Runtimes are consumed (never their implementations)
- The seam through which Workflow Runtime is consumed for delegation (never its
  implementation)
- Agent observability, replay, diagnostics, statistics

**Never owns:**
- Workflow execution (Workflow Runtime owns this exclusively — an Agent submits a
  request, it never runs a node, never touches a `WorkflowGraph`)
- Tool execution (never touches `ToolPort` directly — only Workflow and Reasoning do)
- Skill execution/planning (Skill Runtime and Workflow Runtime's `WorkflowSkillPort`
  own this; an Agent never imports `skill/` or constructs a `SkillPlan`)
- Provider routing (Reasoning Runtime + Provider Runtime own this exclusively)
- Hook implementation (Hook Framework owns the interception mechanism; an Agent only
  calls out to it via a seam, same as Workflow does)

---

## 3. Ownership Matrix

| Concern | Owner |
|---|---|
| Agent manifest / descriptor / registry / lifecycle | **Agent Runtime** |
| Goal decomposition, capability resolution, workflow selection | **Agent Runtime** (planning structure) |
| The actual reasoning content behind a decomposition/selection decision | **Reasoning Runtime** (`ReasoningPort.run`) |
| Memory read/write | **Memory Runtime** (`MemoryPort`) |
| Knowledge retrieval | **Knowledge Runtime** (`KnowledgePort`) |
| Prompt template render/register | **Prompt Runtime** (`PromptPort`) |
| Budgeted context assembly | **Context Runtime** (`ContextPort`) |
| Workflow graph execution | **Workflow Runtime** (unchanged; Agent only submits requests) |
| Tool execution | **Tool Runtime** (via Workflow's `ToolPort`, or Reasoning's own injected `ToolPort` — never Agent) |
| Skill execution/planning | **Skill Runtime** / **Workflow Runtime's `WorkflowSkillPort`** (never Agent) |
| Hook interception mechanism | **Hook Framework** (Agent only calls out via a seam) |
| Model vendor calls | **Provider Runtime** (via Reasoning; Agent never touches it) |

---

## 4. Agent Model

### 4.1 `AgentManifest` / `AgentDescriptor` (identity + registration)

```python
@dataclass(frozen=True, slots=True)
class AgentCapability:
    kind: str        # "workflow" | "skill" | "tool" | "memory" | "knowledge"
    tag: str          # a namespaced reference the resolver interprets
    required: bool = True

@dataclass(frozen=True, slots=True)
class AgentManifest:
    namespace: str
    id: str
    version: str
    interface_version: str
    display_name: str
    persona_prompt_id: str | None = None      # a PromptPort template_id, opaque here
    capabilities: tuple[AgentCapability, ...] = ()
    default_memory_scope: str = "session"     # a MemoryTier name, validated structurally
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True, slots=True)
class AgentDescriptor:
    manifest: AgentManifest
    state: AgentLifecycleState
    registered_at: float
    updated_at: float
```

`AgentManifest` is a contract, never executable code — the same discipline Skill
Runtime established (Phase 0 §6, DL-0042 point b). `AgentCapability` mirrors
`SkillCapabilityRequirement(kind, tag)` exactly, extended with a `required` flag since
an Agent's plan may tolerate an optional capability being unavailable in a way a
Skill's dependency graph does not.

### 4.2 `AgentRegistry`

Registration / discovery / namespacing / version-resolution / descriptor-lifecycle —
identical shape to `WorkflowRegistry`/`SkillRegistry`/`HookRegistry`. No execution, no
capability resolution (that is `AgentCapabilityResolver`'s job, §6.2), no planning.

### 4.3 `AgentIdentity`

```python
@dataclass(frozen=True, slots=True)
class AgentIdentity:
    agent_id: str          # stable across the agent's lifetime
    namespace: str
    version: str
    display_name: str
    created_at: float
```

A thin, immutable identity token an `AgentGoal`/`AgentExecutionContext` carries by
reference — never re-derived from the registry mid-goal (the registry could
deprecate/retire a version while a goal it started under is still running; the
identity token is a stable snapshot, mirroring how `WorkflowExecution.graph_hash`
freezes the graph a run started against).

### 4.4 `AgentGoal`

```python
@dataclass(frozen=True, slots=True)
class AgentGoal:
    goal_id: str
    agent_id: str
    conversation_id: str
    description: str          # the natural-language goal, opaque to Agent Runtime
    constraints: dict[str, Any] = field(default_factory=dict)
    created_at: float
```

`description` is never parsed or interpreted by Agent Runtime code — it is handed to
Reasoning Runtime as-is inside a `ReasoningRequest`'s assembled context. Agent Runtime
treats it exactly as opaquely as Skill Runtime treats a `SkillManifest`'s `config`.

### 4.5 `AgentPlan`

```python
@dataclass(frozen=True, slots=True)
class PlanStep:
    step_id: str
    kind: str                  # "delegate_workflow" | "await_result" | "revise"
    workflow_ref: str | None = None   # namespace.id@version, if kind == delegate_workflow
    inputs: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True, slots=True)
class AgentPlan:
    plan_id: str
    goal_id: str
    steps: tuple[PlanStep, ...]
    plan_hash: str              # registry/planner-computed, never caller-trusted
    revision: int = 0
    created_at: float
```

One data-only `PlanStep` shape with a `kind` discriminator — the same "never a class
hierarchy" discipline `WorkflowNode` established. `plan_hash` is always
planner-computed over the sorted step representation, mirroring `WorkflowGraph`'s
`graph_hash` discipline (never trust a caller-supplied hash).

### 4.6 `AgentCapability` — resolution result

```python
@dataclass(frozen=True, slots=True)
class ResolvedCapability:
    requirement: AgentCapability
    resolved_ref: str | None    # an opaque ref the AgentWorkflowPort/AgentCapabilityResolver understands
    available: bool
```

### 4.7 `AgentMemoryContext` / `AgentKnowledgeContext` / `AgentPromptContext`

```python
@dataclass(frozen=True, slots=True)
class AgentMemoryContext:
    scope: str                 # a MemoryScope reference, structurally validated
    hits: tuple[str, ...]      # MemoryHit ids used, for replay/provenance — never raw content

@dataclass(frozen=True, slots=True)
class AgentKnowledgeContext:
    query_ref: str
    passage_ids: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class AgentPromptContext:
    template_id: str
    version: str
    fragment_hash: str          # content-hash of the rendered PromptFragment, never the text itself
```

These are **provenance/replay records** of calls Agent Runtime made through
`MemoryPort`/`KnowledgePort`/`PromptPort` — never a second copy of memory/knowledge/
prompt storage. This mirrors `HookVerdict.MODIFY`'s "carries a content-hash reference,
never the payload itself" discipline (§9.1 of the Hook design), applied here to avoid
Agent Runtime becoming a shadow store for data three other runtimes already own.

### 4.8 `AgentConversation`

```python
@dataclass(frozen=True, slots=True)
class AgentConversation:
    conversation_id: str
    agent_id: str
    goal_ids: tuple[str, ...]   # every goal raised within this conversation, in order
    started_at: float
    updated_at: float
```

A conversation is the container an Agent's identity persists across; a goal is one
bounded unit of work within it. This is the structural expression of §1's central
distinction.

### 4.9 `AgentDecision`

```python
@dataclass(frozen=True, slots=True)
class AgentDecision:
    decision_id: str
    goal_id: str
    kind: str                   # "decompose" | "resolve_capability" | "select_workflow" |
                                 # "delegate" | "revise_plan" | "abandon"
    reasoning_decision_ref: str | None   # opaque ref to the ReasoningPort Decision that produced this, if any
    payload_hash: str            # content-hash of the decision payload, never the raw payload
    created_at: float
```

Every planning step Agent Runtime takes produces exactly one `AgentDecision` — this
is the audit trail and the unit `AgentReplayRecord` chains over (§4.10). Mirrors
`core.ports.reasoning.Decision` in spirit (`kind` + `payload`) but is Agent-owned
because Agent's own decisions (capability resolution, workflow selection, delegation)
are not reasoning-kind decisions themselves — they are *informed by* a
`ReasoningPort.run` call but are structurally Agent Runtime's own record.

### 4.10 `AgentExecutionContext`

```python
@dataclass(frozen=True, slots=True)
class AgentExecutionContext:
    # --- structural duplicate of WorkflowExecutionContext's own fields, verbatim ---
    session_id: str
    trace_id: str
    org_id: str | None
    workspace_id: str | None
    variables: dict[str, Any]
    execution_id: str            # here: the *goal's* execution id
    graph_hash: str               # here: the delegated workflow's graph_hash, once known
    current_node_id: str          # here: repurposed as "current_step_id"
    checkpoint_id: str | None
    retry_count: int
    # --- additive, Agent-specific ---
    goal_id: str
    plan_id: str
    conversation_id: str
    decision_count: int
    delegated_execution_id: str | None   # the Workflow execution id this goal delegated to, if any
```

Extends `WorkflowExecutionContext` (which itself extends `SkillExecutionContext`) by
**structural field duplication** — the same "duplicate, don't cross-import" precedent
now applied a third time, reconciling "extend, preserve backward compatibility" with
"never import Workflow Runtime's or Skill Runtime's implementation packages." Field
names inherited verbatim keep their original meaning where it transfers directly
(`session_id`/`trace_id`/`org_id`/`workspace_id`/`variables`/`retry_count`) and are
explicitly repurposed with a documented mapping where the shape doesn't transfer 1:1
(`current_node_id` → "current step", `graph_hash` → "the delegated workflow's hash").

### 4.11 `AgentReplayRecord`

```python
@dataclass(frozen=True, slots=True)
class AgentReplayRecord:
    replay_id: str
    goal_id: str
    decision_hashes: tuple[str, ...]    # ordered AgentDecision.payload_hash chain
    final_state: str
    created_at: float
```

Pure hash-chain re-verification over the ordered `AgentDecision` history — never a
live re-plan, never a live re-call of `ReasoningPort`. Mirrors
`WorkflowReplayRecord`/`SkillPlanReplayRecord`/`HookReplayRecord` exactly.

---

## 5. Planning Model

Planning is **structure, not intelligence**. This is the single most important
inherited constraint from Phase 0 (DL-0042 point b: "planning is an ordinary
`ReasoningRuntime.infer` call, never a new model integration") and it shapes every
planning-owned responsibility below.

| Responsibility | Owner | Mechanism |
|---|---|---|
| Goal decomposition | Agent Runtime (structure) + Reasoning Runtime (content) | Agent builds a `ReasoningRequest` (via an assembled `context_ref` from `ContextPort`), calls `ReasoningPort.run`, deterministically parses the returned `Decision(kind=FINAL, payload={...})` into `PlanStep`s. Agent Runtime code never decides *what* the steps should be — only how to structure the request and parse the response. |
| Capability resolution | Agent Runtime | Each `AgentCapability` requirement is resolved via the injected `AgentCapabilityResolver` seam (§6.2) — never by Agent Runtime importing `skill`/`tool`/`workflow` registries directly. Mirrors Skill Runtime's own `CapabilityResolver` precedent exactly, applied a second time. |
| Workflow selection | Agent Runtime | Deterministic lookup: given a resolved `workflow` capability tag, look up an existing registered `WorkflowDescriptor` via the `AgentWorkflowPort` seam's `resolve(ref)` method (§6.1) — never a fuzzy/AI match; the *choice* of which capability tag to use came from the Reasoning-informed decomposition step above, this step is a pure lookup. |
| Workflow generation | **Explicitly out of scope for this design** — see Risks §13. An Agent's plan may only delegate to *already-registered* Workflows. Generating a novel `WorkflowGraph` at plan time would mean Agent Runtime constructing executable orchestration structure, which is exactly the kind of "code as intelligence" risk Skill Runtime's manifest-not-code decision (DL-0042 point b) was designed to foreclose. If a future milestone wants this, it must be an explicit, separately-designed capability — not a quiet default here. |
| Delegation | Agent Runtime | Builds an execution request from a `PlanStep(kind="delegate_workflow")` and submits it via `AgentWorkflowPort.submit(...)` (§6.1). Never executes the workflow itself. |
| Plan revision | Agent Runtime (structure) + Reasoning Runtime (content) | On a delegated workflow's partial/failed result, Agent Runtime assembles a fresh `context_ref` (now including the prior outcome) and calls `ReasoningPort.run` again; the returned decision becomes a new `AgentPlan` with `revision = previous.revision + 1`. Never a bespoke retry heuristic — revision is just decomposition run again with more context. |

This table is the practical resolution of "Design ownership for planning, no
implementation": every planning sub-responsibility is either (a) pure structure Agent
Runtime owns and will implement in a later bundle, or (b) intelligence content that
is explicitly *not* Agent Runtime's to own, dispatched to the already-frozen
`ReasoningPort`.

---

## 6. Delegation Model

### 6.1 `AgentWorkflowPort` — an Agent-owned seam, not `core.ports.workflow.WorkflowPort`

```python
class AgentWorkflowPort(ABC):
    @abstractmethod
    def resolve(self, workflow_ref: str) -> str:
        """Resolve a namespace.id[@version] reference to a concrete, registered
        workflow; return an opaque handle. Raises if unregistered."""

    @abstractmethod
    def submit(self, handle: str, inputs: Mapping[str, Any]) -> str:
        """Submit an execution request; return an execution_id. Never blocks
        on completion."""

    @abstractmethod
    def status(self, execution_id: str) -> str:
        """One of the WorkflowExecutionState values, as a string."""

    @abstractmethod
    def signal(self, execution_id: str, name: str, payload: Mapping[str, Any]) -> None:
        """Deliver an external signal (mirrors resume_after_event)."""
```

**Why not depend on `core.ports.workflow.WorkflowPort` directly?** Because literal
conformance of `workflow.execution.WorkflowExecutionRuntime` to that frozen M5.0 port
was never verified or implemented — M6.4A's own design explicitly recorded this as
"stated as a target, not assumed," mirroring Tool Runtime's identical, still-open
`ToolPort` conformance debt (M6.1B-5). Depending on an unverified port would let
Agent Runtime silently assume a conformance shim exists. Declaring `AgentWorkflowPort`
as Agent's own seam — mirroring exactly how Workflow itself declared
`WorkflowSkillPort`/`WorkflowHookAdapter` rather than assuming Skill/Hook exposed a
`core.ports` entry — keeps the same honest posture. **This is different from Agent's
relationship to Reasoning/Memory/Knowledge/Prompt/Context (§7)**, where the concrete
runtimes *do* literally implement their `core.ports` interfaces (verified by direct
inspection of `reasoning/runtime.py`, `memory/runtime.py`, `knowledge/runtime.py`,
`prompt/runtime.py`, `context/runtime.py` — each is `class XRuntime(BusConsumer,
XPort)`), so depending on those five ports directly costs nothing extra and
introduces no unverified assumption.

### 6.2 `AgentCapabilityResolver` — an Agent-owned seam

```python
class AgentCapabilityResolver(ABC):
    @abstractmethod
    def resolve(self, requirement: AgentCapability) -> ResolvedCapability:
        """Resolve one declared capability requirement against whichever
        registry (Skill/Tool/Workflow) the `kind` implies. Agent Runtime
        never imports those registries directly."""
```

Mirrors `skill.extensions.CapabilityResolver` exactly, applied a second time at the
Agent layer — an Agent's own capability requirements are one layer removed from a
Skill's (an Agent's `kind="workflow"` requirement, for instance, has no equivalent in
Skill Runtime's own `kind` vocabulary).

### 6.3 Delegation never becomes execution

Agent Runtime's own future `AgentExecutionEngine` (Bundle 3) will call
`AgentWorkflowPort.submit(...)` and then either poll `status(...)` or wait for a
resume/signal call — it will never construct a `WorkflowGraph`, never call
`topological_order`, never touch a `WorkflowNode`. The boundary is enforced the same
way Workflow enforces "never execute a Tool/Skill directly": by depending only on an
ABC seam whose concrete implementation lives entirely outside `agent/`.

---

## 7. Context Model

### 7.1 The dual-FSM restoration, scoped to Goal, not Agent

```
Descriptor lifecycle (AgentLifecycleState) — identical shape to Skill/Hook/Workflow's
own descriptor FSM:
  DRAFT → REGISTERED → ACTIVE → DEPRECATED → RETIRED

Goal execution lifecycle (AgentGoalState) — a second, distinct core.fsm definition,
scoped to one AgentGoal, not to the Agent's identity:
  RECEIVED → DECOMPOSING → DELEGATING → AWAITING_WORKFLOW → REVISING
                                              ↓                  ↓
                                          COMPLETED           (loop back to DELEGATING)
                                              ↓
                                           FAILED
  CANCEL reachable from any non-terminal state → ABANDONED
```

This is the design's central architectural call: Agent Runtime restores the
dual-FSM shape a second time (after Workflow Runtime), but the second FSM attaches to
`AgentGoal`, never to the Agent identity itself. An Agent identity has no "execution"
state of its own — it is either registered/active (descriptor concern) or it is not;
all "genuinely executes over time" behavior belongs to a Goal, exactly as it belongs
to a Workflow execution rather than a `WorkflowDescriptor`. A future contributor must
not "simplify" this into one FSM on the Agent record itself, nor invent a second FSM
scoped to the whole Agent (which would incorrectly imply an Agent can only pursue one
goal at a time — it cannot; `AgentConversation.goal_ids` is a tuple for exactly this
reason).

### 7.2 `AgentExecutionContext` (see §4.10) is the Goal-scoped equivalent of
`WorkflowExecutionContext`/`SkillExecutionContext` — carried by every planning and
delegation call for one goal, never by the Agent identity as a whole.

---

## 8. Memory / Knowledge Interaction

Agent Runtime depends directly on three already-frozen, already-implemented ports:

- `core.ports.memory.MemoryPort` — `store`/`retrieve`/`forget`, scoped via
  `MemoryScope(session_id, conversation_id, user_id, org_id)`. An Agent may write a
  decision outcome or a learned fact back to memory (e.g. `tier=MemoryTier.USER`)
  and later retrieve it for a subsequent goal in the same conversation.
- `core.ports.knowledge.KnowledgePort` — `retrieve`/`sources`, for ad hoc knowledge
  lookups a plan step needs outside the assembled context (e.g. resolving a
  capability's own documentation).
- `core.ports.prompt.PromptPort` — `render`/`register`, for agent-specific
  instruction templates (a persona prompt, a goal-decomposition instruction
  template) — Agent Runtime registers its own templates at startup via
  `AgentManifest.persona_prompt_id` but never renders raw text itself.

For the common case — assembling everything needed for a single `ReasoningPort.run`
call — Agent Runtime instead calls `core.ports.context.ContextPort.assemble(...)`
once, exactly as Reasoning Runtime's own `context_ref` field expects (`context.
runtime.ContextRuntime` already composes Memory + Knowledge + Prompt behind this one
seam — Agent Runtime does not re-implement that composition, it reuses the already-
certified Context Runtime for it). Direct `MemoryPort`/`KnowledgePort`/`PromptPort`
calls are reserved for the cases Context's one-shot assembly doesn't cover (writes,
ad hoc mid-plan lookups, template registration).

No new port is declared for any of the four — literal conformance is already proven
by direct inspection (`ReasoningRuntime(BusConsumer, ReasoningPort)`,
`MemoryRuntime(BusConsumer, MemoryPort)`, `KnowledgeRuntime(BusConsumer,
KnowledgePort)`, `PromptRuntime(BusConsumer, PromptPort)`, `ContextRuntime(BusConsumer,
ContextPort)`), unlike the Workflow-delegation seam in §6.1.

---

## 9. Hook Integration (design only — no implementation)

Agent Runtime declares its own `AgentHookAdapter` seam, mirroring
`workflow.extensions.WorkflowHookAdapter` exactly, for the same reason: Hook
Framework exposes no `core.ports` entry, so nothing can be assumed about its shape
from `core` alone.

```python
class AgentHookAdapter(ABC):
    @abstractmethod
    def run(self, point: str, payload: Mapping[str, Any]) -> None: ...
```

**Recommended** (not performed) additive `HookPoint` members, exercising Hook
Framework's own pre-declared AR-32 additive-extension mechanism:
- `before_agent` / `after_agent` — whole-conversation bracket, mirrors
  `before_workflow`/`after_workflow`
- `before_goal` / `after_goal` — per-goal bracket, the Agent-layer equivalent of
  `BEFORE_WORKFLOW_STEP`/`AFTER_WORKFLOW_STEP`
- `agent_failure` — mirrors `workflow_failure`

This brings the platform's total *recommended-but-unperformed* `HookPoint` additions
to eight (three from Workflow, five here) — tracked as a single backlog item, not
duplicated per-runtime (see Risks §13).

---

## 10. Architecture

### 10.1 Package Structure (illustrative — no code in this milestone)

```
platform/agent/
├── domain.py            # AgentManifest, AgentDescriptor, AgentIdentity, AgentCapability
├── lifecycle.py          # descriptor-only FSM (AgentLifecycleState)
├── registry.py           # AgentRegistry
├── extensions.py         # AgentCapabilityResolver, AgentWorkflowPort, AgentHookAdapter,
│                          # AgentPolicyEngine, AgentObservabilitySink, AgentReplaySink (interfaces only)
├── ids.py / serializer.py / config.py / factory.py
├── repositories/          # temporary registration-record repository
├── stores/
├── _run.py
└── planning/              # Bundle 2
    ├── domain.py          # AgentGoal, AgentPlan, PlanStep, ResolvedCapability
    ├── decomposer.py       # goal decomposition via ReasoningPort
    ├── capability.py       # capability resolution via AgentCapabilityResolver
    ├── workflow_selection.py
    ├── delegation.py       # execution-request construction (not submission)
    └── planner.py          # orchestrates decomposition → resolution → selection → plan
└── execution/              # Bundle 3
    ├── domain.py           # AgentGoalState, AgentConversation, AgentDecision,
    │                        # AgentExecutionContext, AgentReplayRecord
    ├── goal_lifecycle.py    # the second, goal-scoped FSM
    ├── engine.py            # AgentExecutionEngine: drives the goal FSM, calls the planner,
    │                        # submits to AgentWorkflowPort, applies plan revision
    ├── observability.py / replay.py / replay_engine.py / diagnostics.py / statistics.py
    ├── repositories/         # durable? — see §10.3
    └── runtime.py            # AgentExecutionRuntime facade
```

### 10.2 Dependency Graph

```
agent  →  core [, tenant]
agent  →  core.ports.reasoning.ReasoningPort   (direct — already-verified conformance)
agent  →  core.ports.memory.MemoryPort          (direct — already-verified conformance)
agent  →  core.ports.knowledge.KnowledgePort    (direct — already-verified conformance)
agent  →  core.ports.prompt.PromptPort          (direct — already-verified conformance)
agent  →  core.ports.context.ContextPort        (direct — already-verified conformance)

agent  ⇥  workflow   (never — AgentWorkflowPort is Agent's own seam)
agent  ⇥  skill       (never — capability resolution goes through AgentCapabilityResolver)
agent  ⇥  tool         (never — Agent never executes a tool)
agent  ⇥  hooks         (never — AgentHookAdapter is Agent's own seam)
agent  ⇥  reasoning/memory/knowledge/prompt/context (implementation packages — never;
                                                       only their `core.ports` interfaces)
```

Zero exception. Every arrow into a concrete runtime package is replaced by an arrow
into that package's `core.ports` interface wherever conformance is already proven;
everywhere conformance is unverified (Workflow, Skill, Tool, Hooks), Agent declares
its own seam, exactly as Workflow itself did one layer down.

### 10.3 Durable-repository question

Unlike Workflow's `WorkflowSnapshot` (the platform's one *named* durable exception,
ADR-0009), an `AgentGoal`'s in-flight state does **not** need its own durability
exception: a goal's actual state-over-time is fully reconstructible from (a) the
`AgentDecision` history (already content-hash-chained for replay) and (b) the
delegated `WorkflowExecution`'s own durable state (which Workflow Runtime already
persists durably). Agent Runtime's own repository can remain the platform's
standard *temporary* convention — recovering a goal after a process restart means
re-reading the delegated workflow's durable snapshot and replaying the decision
chain, never inventing a second parallel durable store. This is recorded explicitly
so a future implementer does not reflexively claim a second durable-exception ADR
by analogy with Workflow.

### 10.4 Ownership Matrix

(See §3 — the full matrix is not repeated here.)

### 10.5 Extension Strategy

Six declared, unimplemented seams, all interfaces only: `AgentCapabilityResolver`,
`AgentWorkflowPort`, `AgentHookAdapter`, `AgentPolicyEngine`, `AgentObservabilitySink`,
`AgentReplaySink` — the same "declare the seam now, implement the first concrete
adapter in a later bundle" discipline every prior closed runtime followed.

### 10.6 Composition Strategy

A future `AgentFactory` composition root wires: `AgentRegistry` +
`AgentCapabilityResolver` + `AgentWorkflowPort` + `AgentHookAdapter` +
`ReasoningPort`/`MemoryPort`/`KnowledgePort`/`PromptPort`/`ContextPort` (all five
injected as already-frozen interfaces, concretely satisfied by the real
Reasoning/Memory/Knowledge/Prompt/Context Runtime instances at the actual
application composition root, never inside `agent/` itself) into one
`AgentExecutionRuntime` facade — mirroring `WorkflowExecutionFactory`'s exact shape.

---

## 11. ADR Recommendations

1. **Agent Runtime's dual-FSM restoration attaches to `AgentGoal`, never to the
   Agent identity itself** — the platform's "genuinely executes over time" criterion
   for a second FSM (first applied to Workflow Runtime) is satisfied by the Goal,
   not the Agent record; an Agent's identity is durable-but-idle between goals. This
   must be recorded so a future contributor doesn't collapse this into one FSM (by
   analogy with Skill/Hook) or misplace the second FSM on the Agent record (which
   would incorrectly imply single-goal-at-a-time).
2. **Agent planning is an ordinary `ReasoningPort.run` call — no bespoke planning
   intelligence lives in `agent/` code.** Goal decomposition, workflow selection, and
   plan revision are all Agent-structured requests to, and deterministic parses of
   responses from, the already-frozen Reasoning Runtime. This ratifies Phase 0's own
   DL-0042 point (b) at the full-design level and forecloses the temptation to build
   a second, competing planning/inference engine inside `agent/`.
3. **Workflow delegation goes through an Agent-owned `AgentWorkflowPort` seam, not
   literal `core.ports.workflow.WorkflowPort`** — because `WorkflowExecutionRuntime`'s
   conformance to that frozen M5.0 port remains an open, unverified, deferred gap
   (the same honest posture as Tool Runtime's own M6.1B-5 debt). A future
   implementer must not silently assume conformance that has never been checked.
4. **Reasoning, Memory, Knowledge, Prompt, and Context integration depends directly
   on their `core.ports` interfaces, with zero Agent-owned duplicate seam** — unlike
   Workflow/Skill/Hooks/Tool, these five concrete runtimes already literally
   implement their respective ports (verified by direct inspection: `class
   XRuntime(BusConsumer, XPort)` for all five). Declaring a duplicate seam here
   would be pure ceremony with no honesty benefit, unlike §6.1's Workflow case.
5. **`AgentExecutionContext` extends `WorkflowExecutionContext` (itself extending
   `SkillExecutionContext`) by structural field duplication, a third application of
   the same precedent** — never inheritance, never a `workflow`/`skill` import.
6. **"Workflow generation" (an Agent authoring a novel `WorkflowGraph` at plan time)
   is an explicit, named non-goal of this design, not a silent gap** — an Agent's
   plan may only delegate to already-registered Workflows; generating orchestration
   structure at runtime would reopen the "code as intelligence" risk Skill Runtime's
   manifest-not-code decision was built to foreclose. This must be a separate,
   explicitly-commissioned future capability if ever pursued, never an assumed
   extension of ordinary delegation.

---

## 12. Bundle Breakdown (Exactly Four, Per This Milestone's Instruction)

| Bundle | Scope |
|---|---|
| **M6.5B-1 — Core, Registry, Identity, Lifecycle, Context** | `AgentManifest`/`AgentDescriptor`/`AgentIdentity`/`AgentCapability`, `AgentRegistry`, descriptor-only `AgentLifecycleState` FSM, `AgentExecutionContext` shape (structural duplication only — not yet threaded through any execution code), all six extension-point interfaces declared (unimplemented), serializer/config/factory/temporary repository. No planning, no execution, no delegation. |
| **M6.5B-2 — Planning, Capability Resolution, Goal Decomposition, Workflow Delegation** | `AgentGoal`/`AgentPlan`/`PlanStep`/`ResolvedCapability`; goal decomposition via `ReasoningPort`; capability resolution via `AgentCapabilityResolver`; workflow selection (lookup only, no generation) via `AgentWorkflowPort.resolve`; delegation request construction (not submission — no execution engine exists yet). Planning is fully exercised in isolation against fakes/stubs for `ReasoningPort`/`AgentCapabilityResolver`/`AgentWorkflowPort`. |
| **M6.5B-3 — Execution, Observability, Replay, Recovery, Diagnostics** | The goal-scoped `AgentGoalState` FSM, `AgentExecutionEngine` (drives the FSM, calls the Bundle 2 planner, submits via `AgentWorkflowPort.submit`, polls/awaits `status`/`signal`, applies plan revision on partial/failed delegated results), `AgentConversation`/`AgentDecision`, `AgentReplayRecord`/replay engine, observability sink (wired into the runtime's actual persistence path from the start — closing the now-twice-found "shipped sink never called" gap proactively, per precedent), diagnostics/statistics, `AgentExecutionRuntime` facade. |
| **M6.5C — Agent Runtime Production Certification** | Certification-only, mirroring M6.1B-5/M6.2B-3/M6.3C/M6.4C's own methodology: architecture/dependency/runtime-completeness/determinism/performance/documentation audits, technical debt classification, 7-category production-readiness score. |

---

## 13. Risks

- **"Workflow generation" pressure.** The milestone brief lists "workflow
  generation" as a planning responsibility to design ownership for; this design
  deliberately scopes it out of the actual delegation path (§5, §11 item 6) as a
  named non-goal rather than silently implementing a shortcut. The risk is a future
  bundle quietly reintroducing it as "just calling `WorkflowRegistry.register` with
  an LLM-authored graph" without the same scrutiny Skill Runtime's manifest
  decision received — this must go through its own explicit design milestone if
  ever pursued.
- **`AgentWorkflowPort` conformance debt compounds.** Agent's own seam wraps an
  already-unverified `WorkflowPort` conformance gap (Workflow Runtime's own, from
  M6.4A). If a future milestone "fixes" `WorkflowExecutionRuntime` to literally
  implement `core.ports.workflow.WorkflowPort`, `AgentWorkflowPort`'s adapter
  implementation should be revisited so it delegates to the real port rather than
  duplicating logic — tracked as a soft dependency between two open items, not
  actioned now.
- **HookPoint backlog growth.** Eight recommended-but-unperformed `HookPoint`
  members now exist across Workflow (3) and Agent (5, this design). None have been
  implemented. If this backlog is never addressed, "recommended" additive hook
  points risk becoming permanent aspirational documentation rather than real
  extension points — worth a future dedicated Hook Framework extension milestone
  rather than being added piecemeal by each new runtime's own design.
- **Legacy `api/routers/agents.py` naming collision.** A pre-existing, unrelated
  Control-Plane CRUD resource (`models.Agent`, a DB-backed org-scoped config
  record) already uses the name "Agent" in `api/`. This design's `AgentManifest`/
  `AgentDescriptor`/`AgentRegistry` are a completely separate, closed-runtime
  concept and must not be confused with, or wired into, that legacy resource
  without an explicit future integration decision.
- **Reasoning-driven planning determinism.** Since goal decomposition/workflow
  selection/plan revision route through `ReasoningPort.run`, their *content* is only
  as deterministic as the injected model/provider is (the deterministic
  `EchoProvider` in tests, a real vendor model in production). Agent Runtime's own
  code remains deterministic (parsing, hashing, sequencing); this must not be
  mistaken for the platform's usual full-stack determinism guarantee, and should be
  documented plainly wherever Agent Runtime's own determinism claims are audited.

---

## 14. Recommendation

Design approved for **M6.5B-1 — Agent Runtime Bundle 1 (Core, Registry, Identity,
Lifecycle, Context)** next, per this document's own four-bundle breakdown (§12).
Tool Runtime, Skill Runtime, Hook Framework, Workflow Runtime, Provider Runtime,
Memory Runtime, Knowledge Runtime, Prompt Runtime, Context Runtime, Pipeline
Runtime, and Reasoning Runtime all remain untouched and frozen.
