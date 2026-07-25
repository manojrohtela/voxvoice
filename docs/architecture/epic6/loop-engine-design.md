# Loop Engine — Design (M6.6A)

Supersedes Phase 0's brief sketch of the Loop Engine (`epic6-agent-platform-architecture.md`,
"a pure propose→critique→repair→stop-check library, zero runtime dependencies").
Tool Runtime, Skill Runtime, Hook Framework, Workflow Runtime, and Agent Runtime
are certified and frozen; this design does not modify any of them and integrates
only through `core.ports.reasoning.ReasoningPort`.

---

## 1. What Loop Engine Is (and Is Not)

**Loop Engine is a deterministic orchestration *library*** — a
propose→critique→repair→validate→stop-check pipeline that iteratively refines
one proposal until it converges, exhausts its iteration budget, or is stopped
externally. It owns iteration structure and stopping policy; it owns nothing
about *what* a good proposal is.

Loop Engine is **not**:
- a **Runtime** — it has no registry, no manifest, no descriptor, no lifecycle
  FSM, no repository. Every other Epic 6 capability certified so far (Tool,
  Skill, Hooks, Workflow, Agent) is a registrable, discoverable, versioned
  closed runtime; Loop Engine is the first that is **not**, because a loop is
  not a capability a caller looks up by `(namespace, id, version)` — it is a
  computation a caller invokes directly, with all its inputs supplied at the
  call site, the same way a caller invokes `core.fsm.StateMachine` or
  `core.policies.RetryPolicy.delay_for` directly rather than through a
  registry.
- an **Agent** — an Agent is a long-lived identity coordinating goals across a
  conversation; a loop is a single, bounded refinement pass with no identity
  or persistence of its own.
- a **Workflow** — a Workflow is a declarative orchestration graph over nodes
  and edges; a loop has no graph, no branching topology — only a fixed,
  five-stage pipeline repeated until it stops.
- a **Skill** — a Skill is a versioned, declarative capability manifest; a
  loop is not registered, versioned, or capability-declaring at all.
- a **Tool** — a loop never executes anything external; it only iterates on a
  proposal via `ReasoningPort` and an injected validator.
- a **Provider** — a loop never talks to a model vendor directly; all
  intelligence goes through the already-frozen `core.ports.reasoning.ReasoningPort`.

**The single governing constraint**: Loop Engine owns iterative-improvement
*structure* only. It never implements reasoning (that's `ReasoningPort`),
never implements validation (that's an injected `LoopValidator`, entirely the
caller's domain logic), never executes a workflow/skill/tool, and never
persists anything itself.

---

## 2. Responsibilities

**Owns:**
- Iteration structure (the propose→critique→repair→validate→stop-check
  sequence and its repetition)
- Proposal generation, critique, and repair **request/response structuring**
  over `ReasoningPort` — never the intelligence content itself
- Validation **orchestration** (calling the injected validator, reading its
  verdict) — never the validation algorithm
- Convergence detection and stopping-condition **policy** (mechanical checks
  over already-computed facts)
- Iteration history, loop statistics, replay metadata, diagnostics — all as
  ownership declarations in this design; concrete shapes only, no
  implementation

**Never owns:**
- Reasoning implementation (Reasoning Runtime's, via `ReasoningPort`)
- Workflow execution, planning, tool execution, skill execution, provider
  routing (each already owned elsewhere, per the platform's own ownership
  matrix)
- Persistence of any kind — a loop's state is entirely the calling runtime's
  responsibility to persist, if persistence is ever needed at all

---

## 3. Ownership Matrix

| Concern | Owner |
|---|---|
| Iteration sequencing (propose→critique→repair→validate→stop-check→repeat) | **Loop Engine** |
| The actual content of a proposal/critique/repair | **Reasoning Runtime** (`ReasoningPort.run`) |
| Whether a proposal is *correct* | **The caller's injected `LoopValidator`** (delegated, domain-specific) |
| Whether to stop (max iterations / confidence / validation success / convergence / external signal) | **Loop Engine** (mechanical policy over already-computed facts) |
| Whether the *content* of "confidence" or "convergence" is meaningful | **The caller** (Loop Engine only compares numbers/structural equality it is handed) |
| Model vendor calls | **Provider Runtime** (via Reasoning; Loop Engine never touches it) |
| Persisting loop state across a process restart | **The calling runtime** (Agent Runtime, Workflow Runtime, or any future caller) — never Loop Engine itself |
| Tool/Skill/Workflow execution | **Tool/Skill/Workflow Runtime** respectively — Loop Engine never touches any of them |

---

## 4. Loop Model

### 4.1 `LoopDefinition` — the caller-constructed recipe for one loop invocation

```python
@dataclass(frozen=True, slots=True)
class LoopStoppingPolicy:
    max_iterations: int = 5
    confidence_threshold: float | None = None   # None -> confidence not used to stop
    stop_on_validation_success: bool = True
    stop_on_convergence: bool = True             # successive proposals structurally identical

@dataclass(frozen=True, slots=True)
class LoopDefinition:
    loop_id: str                    # caller-supplied; never registry-assigned, there is no registry
    task_description: str           # opaque prose, handed to ReasoningPort as-is, never parsed
    stopping_policy: LoopStoppingPolicy = field(default_factory=LoopStoppingPolicy)
    metadata: Mapping[str, str] = field(default_factory=dict)
```

`LoopDefinition` is **never persisted or looked up** — it is constructed by
the caller at the call site and passed directly into the engine, exactly once
per invocation. There is no `LoopRegistry` to register it with, mirroring the
explicit "no Registry, no Manifest, no Descriptor" constraint.

### 4.2 `LoopProposal` / `LoopCritique` / `LoopRepair` / `LoopValidation` — the four pipeline stage outputs

```python
@dataclass(frozen=True, slots=True)
class LoopProposal:
    proposal_id: str
    content_ref: str        # a content-hash reference to the proposal's own content —
                             # never the raw content itself, the same "hash reference,
                             # never the payload" discipline every closed runtime follows
    reasoning_decision_ref: str | None = None

@dataclass(frozen=True, slots=True)
class LoopCritique:
    critique_id: str
    proposal_id: str
    content_ref: str
    reasoning_decision_ref: str | None = None

@dataclass(frozen=True, slots=True)
class LoopRepair:
    repair_id: str
    critique_id: str
    content_ref: str          # the repaired proposal's content-hash reference
    reasoning_decision_ref: str | None = None

@dataclass(frozen=True, slots=True)
class LoopValidation:
    validation_id: str
    proposal_id: str           # the proposal (initial or repaired) being validated
    passed: bool
    confidence: float | None = None
    reason: str = ""
```

`LoopProposal`/`LoopCritique`/`LoopRepair` are all outputs of a `ReasoningPort.run`
call, deterministically parsed the same way `agent.planning.decompose_goal`
parses a `Decision(kind=FINAL)` — never a bespoke generation algorithm.
`LoopValidation` is the **one** stage shape Loop Engine never produces itself
— it is always the return value of an injected `LoopValidator.validate(...)`
call (§6).

### 4.3 `LoopIteration` — one full pass through the pipeline

```python
@dataclass(frozen=True, slots=True)
class LoopIteration:
    iteration_number: int
    proposal: LoopProposal
    critique: LoopCritique | None      # None on the final, no-critique-needed pass
    repair: LoopRepair | None          # None if the proposal already validated
    validation: LoopValidation
    stopped: bool                      # did the stop-check end the loop after this iteration
    stop_reason: str = ""              # "max_iterations" | "validation_success" |
                                        # "confidence_threshold" | "convergence" | "external_stop" | ""
```

### 4.4 `LoopResult`

```python
@dataclass(frozen=True, slots=True)
class LoopResult:
    loop_id: str
    final_proposal_id: str
    converged: bool
    iteration_count: int
    stop_reason: str
    iterations: tuple[LoopIteration, ...]
    completed_at: float = 0.0
```

### 4.5 `LoopStatistics`

```python
@dataclass(frozen=True, slots=True)
class LoopStatistics:
    loop_count: int = 0
    avg_iteration_count: float = 0.0
    per_stop_reason: dict[str, int] = field(default_factory=dict)
    convergence_rate: float = 0.0        # fraction of loops that converged vs. exhausted budget
```

### 4.6 `LoopReplayRecord`

```python
@dataclass(frozen=True, slots=True)
class LoopReplayRecord:
    replay_id: str
    loop_id: str
    iteration_hashes: tuple[str, ...]    # one content-hash per LoopIteration, ordered
    stop_reason: str
    replay_hash: str
    replay_version: str = "1"
    replay_timestamp: float = 0.0
```

Pure hash-chain re-verification over the ordered iteration sequence — never a
live re-run of propose/critique/repair/validate. Mirrors every other closed
runtime's replay discipline (`WorkflowReplayRecord`, `AgentReplayRecord`)
exactly, applied a fourth time.

### 4.7 `LoopExecutionContext` — a wholly independent shape, never `AgentExecutionContext`

```python
@dataclass(frozen=True, slots=True)
class LoopExecutionContext:
    loop_id: str
    session_id: str
    trace_id: str
    org_id: str | None = None
    iteration: int = 0
    variables: Mapping[str, Any] = field(default_factory=dict)   # loop-carried state only
```

**Explicitly does not extend, duplicate, or reference `AgentExecutionContext`**
(the milestone's own explicit instruction). Every prior "extend a context"
decision in this platform (`WorkflowExecutionContext` extending
`SkillExecutionContext`; `AgentExecutionContext` extending
`WorkflowExecutionContext`) was extending a shape *within the same
delegation lineage* — Skill → Workflow → Agent is a chain of "the caller
above delegates execution to the callee below." Loop Engine has no place in
that lineage: it is a general-purpose refinement primitive any caller
(Agent Runtime's own future plan-revision step, Workflow Runtime's own future
retry-with-improvement node, a future MCP tool-call refinement, or a caller
outside Epic 6 entirely) could adopt, so binding it to `AgentExecutionContext`'s
accumulated fields (`goal_id`, `plan_id`, `delegated_execution_id`, ...) would
wrongly assume every future caller is Agent Runtime. `LoopExecutionContext`
therefore carries only the inline session/trace/org fields every closed
runtime's own context shape independently carries (the same precedent
`ToolRequest`/`SkillExecutionContext`/`HookEvent`/`WorkflowExecutionContext`/
`AgentExecutionContext` each apply on their own terms) plus loop-specific
`iteration`/`variables` — nothing borrowed, nothing extended.

---

## 5. Pipeline

```
Initial proposal (ReasoningPort.run, deterministically parsed)
        ↓
Critique (ReasoningPort.run over the current proposal, deterministically parsed)
        ↓
Repair (ReasoningPort.run over the proposal + critique, deterministically parsed)
        ↓
Validation (delegated: LoopValidator.validate(repaired-or-original proposal))
        ↓
Stop check (mechanical policy: max_iterations / validation.passed /
            confidence_threshold / convergence / external stop token)
        ↓
   ┌────┴────┐
 stop      repeat (repaired proposal becomes next iteration's starting proposal)
   ↓
LoopResult
```

**Ownership of each arrow**: every `ReasoningPort.run` call is Loop Engine
*structuring* a request and *parsing* a response — never generating content
itself, exactly Agent Runtime's own planning discipline (§5 of the M6.5A
design) applied a second time, now to iterative refinement instead of goal
decomposition. The validation arrow is the **one** step whose entire content
is delegated — Loop Engine calls `LoopValidator.validate(...)` and only reads
the returned `LoopValidation.passed`/`confidence`/`reason`; it never computes
correctness itself under any circumstance.

**Critique/repair are optional per iteration**: if the initial proposal
already validates, `LoopIteration.critique`/`.repair` are `None` and the loop
stops immediately with `stop_reason="validation_success"` — the pipeline
never manufactures a critique for a proposal that already passed.

---

## 6. Stopping Policy

Loop Engine owns **only the mechanical policy**, never the underlying
correctness/quality judgment:

| Condition | What Loop Engine checks | What it does *not* do |
|---|---|---|
| `max_iterations` | `iteration_number >= policy.max_iterations` — a plain integer comparison | Never decides what a reasonable `max_iterations` is; that's the caller's `LoopDefinition` |
| `validation success` | Reads `LoopValidation.passed` (a fact the injected validator already computed) | Never itself decides whether the proposal is correct |
| `confidence_threshold` | `LoopValidation.confidence >= policy.confidence_threshold`, if both are present — a plain float comparison | Never decides what confidence *means* — that's whatever the validator or `ReasoningPort` returns |
| `convergence` | Structural equality between the current iteration's repaired proposal content-hash and the previous iteration's — a plain hash comparison | Never judges "close enough"; only exact structural identity counts as converged |
| `external_stop` | Reads an injected cancellation-style token's `is_cancelled()`, exactly mirroring `WorkflowCancellationToken`/`AgentCancellationToken`'s own shape | Never decides *why* the caller wants to stop |

This table is the practical resolution of "Loop Engine owns only policy… it
does NOT own the validation algorithm itself": every stopping condition is a
mechanical read-and-compare over a fact some other party (the validator, the
caller, `ReasoningPort`) already produced.

---

## 7. Context

`LoopExecutionContext` (§4.7) owns loop-specific state only: `loop_id`,
`iteration`, and `variables` (whatever carried state the caller's proposal/
critique/repair prompts need threaded between iterations — e.g. an
accumulated list of prior critique summaries). It carries the same inline
`session_id`/`trace_id`/`org_id` fields every closed runtime's own context
shape independently carries, for the same reason each of them does (no
`tenant.TenantContext`/`pipeline.RequestContext` import, per the
platform-wide precedent) — but **it is not part of the Skill→Workflow→Agent
extension lineage**, and no future milestone should retrofit it into that
chain by analogy; it is deliberately its own, independent shape (§4.7's own
rationale, restated as the binding constraint here).

---

## 8. Architecture

### 8.1 Package Structure (illustrative — no code in this milestone)

```
platform/loop/
├── core/
│   ├── domain.py         # LoopDefinition, LoopStoppingPolicy, LoopProposal,
│   │                      # LoopCritique, LoopRepair, LoopValidation,
│   │                      # LoopIteration, LoopResult, LoopExecutionContext
│   ├── pipeline.py        # the propose→critique→repair→validate→stop-check
│   │                       # sequencing function(s) — a pure library entry point,
│   │                       # not a class hierarchy
│   ├── stopping.py         # the mechanical stopping-policy checks (§6)
│   ├── validator.py         # LoopValidator (ABC, interface only — never
│   │                         # implemented by Loop Engine itself)
│   ├── cancellation.py        # LoopCancellationToken (local duplicate of the
│   │                           # established PipelineCancellation shape)
│   └── ids.py                  # local content_hash/id-suffix helpers
├── diagnostics/
│   └── diagnostics.py            # LoopDiagnostics + a pure builder function
│                                   # (ownership/shape only, per this milestone)
└── replay/
    ├── domain.py                   # LoopReplayRecord (ownership/shape only)
    └── replay_engine.py             # LoopReplayEngine interface/shape
                                       # (ownership only — no implementation)
```

**No `loop/runtime/` package** — there is no registrable, lifecycle-managed
facade the way every other Epic 6 capability has one; `pipeline.py`'s entry
point(s) are plain functions/classes a caller imports and calls directly,
mirroring how a caller uses `core.fsm.StateMachine` or `core.policies.
RetryPolicy` directly rather than through a registry-backed runtime object.

### 8.2 Dependency Graph

```
loop  →  core [, tenant]     — same as every closed runtime, but here it is the *entire* graph
loop  →  core.ports.reasoning.ReasoningPort   (direct — already-verified conformance,
                                                the same "costs nothing extra" case
                                                Workflow's use of ToolPort and Agent's
                                                use of ReasoningPort/ContextPort both are)

loop  ⇥  tool / skill / hooks / workflow / agent   (never — zero runtime imports,
                                                      the explicit M6.6A constraint)
loop  ⇥  reasoning (the implementation package)      (never — only its `core.ports`
                                                        interface)
```

Loop Engine's dependency graph is the simplest of any Epic 6 capability so
far: `core` plus exactly one already-frozen port. There is no seam analogous
to `WorkflowSkillPort`/`AgentWorkflowPort` to declare, because Loop Engine
never delegates to another *runtime* at all — its only external dependency
is the caller-injected `LoopValidator`, which is Loop Engine's own interface
(§6.1), not a stand-in for a sibling runtime's unverified port.

### 8.3 Ownership Matrix

(See §3 — not repeated here.)

### 8.4 Extension Strategy

One declared, unimplemented seam: `LoopValidator` (ABC, `validate(proposal) ->
LoopValidation`). This is the only extension point Loop Engine needs, since
it delegates exactly one thing (correctness) and generates everything else
itself via the already-frozen `ReasoningPort`.

### 8.5 Composition Strategy

There is no composition root / factory the way every other Epic 6 capability
has one (`WorkflowFactory`, `AgentFactory`), because there is nothing to
compose into a long-lived object — a caller constructs a `LoopDefinition`,
injects a `ReasoningPort` and a `LoopValidator`, and calls the pipeline
function directly, once, per loop invocation. If a future caller (e.g. Agent
Runtime, in a future unfreeze milestone) wants to run many loops as part of
its own longer-lived orchestration, *that caller* owns whatever composition
root wires Loop Engine's inputs together — Loop Engine itself stays a stateless
library with no object to compose.

---

## 9. ADR Recommendations

1. **Loop Engine is the platform's first Epic 6 capability with no
   registrable identity** — no Registry, Manifest, Descriptor, Lifecycle FSM,
   Repository, or Runtime facade. It is invoked directly by a caller, the
   same way `core.fsm`/`core.policies` are used directly, never looked up.
   This should be recorded explicitly so a future contributor does not
   "complete the pattern" by adding a `LoopRegistry` by analogy with Tool/
   Skill/Hooks/Workflow/Agent.
2. **Propose/critique/repair are `ReasoningPort.run` calls; validation is
   delegated to an injected `LoopValidator`** — the platform's clearest
   expression yet of "structure vs. intelligence vs. correctness" as three
   genuinely distinct ownership categories, extending the "planning is an
   ordinary `ReasoningPort.run` call" precedent (Agent Runtime, DL-0042
   point b) with a fourth category: correctness evaluation, which is neither
   Loop Engine's structure nor Reasoning's intelligence, but the caller's own
   domain logic.
3. **`LoopExecutionContext` is a wholly independent shape, never extending
   or duplicating `AgentExecutionContext`** — because Loop Engine sits
   outside the Skill→Workflow→Agent delegation lineage (it is a general-
   purpose primitive any future caller may adopt, not a callee one level
   below Agent Runtime specifically). This reconciles the platform's
   otherwise-consistent "extend by structural duplication" precedent with
   the milestone's own explicit "never reuse `AgentExecutionContext`"
   instruction — the precedent still applies *within* a delegation lineage;
   Loop Engine simply isn't in one.
4. **Stopping/convergence is a set of mechanical, deterministic checks over
   already-computed facts** (iteration count, `validation.passed`, a
   confidence-threshold comparison, structural proposal-hash equality, an
   external cancellation token) — Loop Engine never itself judges quality or
   correctness, mirroring `WorkflowFailurePolicy`'s own "policy over already-
   computed facts, never computing the facts" discipline.
5. **Replay is a pure hash-chain over the iteration history, never a live
   re-run** — the same discipline `WorkflowReplayRecord`/`AgentReplayRecord`
   already established, applied a fourth time with zero new reasoning.
6. **No persistence of any kind lives in Loop Engine** — unlike every other
   Epic 6 capability (each of which ships at least a temporary repository),
   Loop Engine ships none at all; if a future caller needs to resume a loop
   across a process restart, that caller's own repository (e.g. a future
   `AgentGoalSnapshot` extension) carries the iteration history, never a
   `LoopRepository` Loop Engine itself would have to invent.

---

## 10. Bundle Breakdown (Exactly Three, Per This Milestone's Instruction)

| Bundle | Scope |
|---|---|
| **M6.6B-1 — Core, Loop Model, Policies** | `loop/core/`: `LoopDefinition`/`LoopStoppingPolicy`/`LoopProposal`/`LoopCritique`/`LoopRepair`/`LoopValidation`/`LoopIteration`/`LoopResult`/`LoopExecutionContext`; the stopping-policy mechanical checks (§6); `LoopValidator` (interface only); `LoopCancellationToken`. No pipeline execution yet — shapes and policy-check functions only, exercised in isolation. |
| **M6.6B-2 — Execution, Replay, Observability, Diagnostics** | `loop/core/pipeline.py` (the actual propose→critique→repair→validate→stop-check→repeat sequencing, calling `ReasoningPort` + the injected `LoopValidator`); `loop/replay/` (`LoopReplayRecord` + replay engine — pure hash-chain re-verification); `loop/diagnostics/` (diagnostics + statistics). |
| **M6.6C — Loop Engine Production Certification** | Certification-only, mirroring every prior Epic 6 certification's own methodology: architecture/dependency/runtime-completeness/determinism/performance/documentation audits, technical debt classification, 7-category production-readiness score. |

---

## 11. Risks

- **Pattern-completion pressure.** Every prior Epic 6 capability shipped a
  Registry/Manifest/Descriptor/Lifecycle/Repository; a future contributor
  building Bundle 1 or 2 may reflexively add one "to be consistent." This
  design records explicitly (ADR item 1) that Loop Engine's entire value is
  in *not* having one — consistency-by-analogy would be the wrong call here.
- **Critique/validation conflation.** A future implementer might treat
  `LoopCritique` (an LLM-judged, soft assessment via `ReasoningPort`) and
  `LoopValidation` (a hard, delegated correctness check) as interchangeable,
  since both "assess" the proposal. They are architecturally distinct:
  critique feeds repair; validation feeds the stop decision. Conflating them
  would silently let Loop Engine's own reasoning stand in for the caller's
  domain-specific correctness check, reopening exactly the "Loop Engine
  never evaluates correctness itself" constraint this design exists to
  enforce.
- **An unimplemented `LoopValidator` is a silent no-op risk.** Since
  `LoopValidator` ships only as an interface (mirroring every other closed
  runtime's own unimplemented-seam precedent), a caller who wires in a
  stub validator (always-pass or always-fail) gets a loop that either never
  iterates or never stops on success — this is an honest scoping fact to
  surface at integration time, not a defect in this design.
- **Convergence-by-exact-hash may rarely fire.** Structural equality between
  successive proposal content-hashes is the simplest, most deterministic
  convergence check available, but real proposals may oscillate or improve
  by decreasing amounts without ever becoming byte-identical — `max_iterations`
  remains the practical backstop. This is a scoping decision, not a gap, but
  worth flagging for whichever bundle first exercises Loop Engine against a
  real reasoning provider.
- **No composition root means every caller re-derives its own wiring.**
  Without a `LoopFactory`, each future adopter (Agent Runtime, Workflow
  Runtime, or others) must independently wire `ReasoningPort` + `LoopValidator`
  + `LoopDefinition` together. This is a deliberate simplicity trade-off
  (§8.5) but should be revisited if three or more callers end up duplicating
  near-identical wiring.

---

## 12. Recommendation

Design approved for **M6.6B-1 — Loop Engine Bundle 1 (Core, Loop Model,
Policies)** next, per this document's own three-bundle breakdown (§10). Tool
Runtime, Skill Runtime, Hook Framework, Workflow Runtime, and Agent Runtime
all remain untouched and frozen.
