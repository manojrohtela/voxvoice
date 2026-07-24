# Hook Framework — Architecture Design (M6.3A)

**Status:** Design only — zero implementation. Supersedes Phase 0's brief §9 sketch
(`epic6-agent-platform-architecture.md`) with the complete design this milestone
commissioned. Tool Runtime and Skill Runtime are certified and **frozen**; this
document proposes zero changes to either.

**Milestone:** M6.3A (design-only). Implementation is explicitly out of scope —
see §12 Bundle Plan for what comes next.

---

## 1. What a Hook Is (and Is Not)

A **Hook** is a deterministic, named lifecycle-interception point with an
explicit verdict contract (allow / deny / modify). The Hook Framework owns the
*interception mechanism* — registration, ordering, filtering, verdict
aggregation, observability, replay metadata — and **never** owns *what a hook
decides*. That decision lives entirely in the injected callback a caller
registers; the Framework is a neutral collection-and-aggregation library, not a
policy author.

A Hook is explicitly **not**:

| Not this | Why the distinction matters |
|---|---|
| A Skill | A Skill *composes* capabilities into a reusable, versioned unit a plan resolves; a Hook *intercepts* an already-happening lifecycle moment. Skills are discovered and planned; Hooks are invoked at a point. |
| A Tool | A Tool performs a real-world side effect an Agent invokes; a Hook only observes or gates that invocation — it never performs the side effect itself. |
| An Agent | An Agent reasons and plans across turns; a Hook has no memory, no reasoning, no multi-step state — one `evaluate(event) -> HookVerdict` call, nothing more. |
| A Workflow | A Workflow orchestrates a declared graph of steps; a Hook is not a step, it is a cross-cutting observation point *around* steps (and around Tool calls, and around Agent handoffs). |
| A Provider | A Provider routes to a model backend; Hooks never route model calls, they only intercept lifecycle events emitted by whichever runtime reaches a named point. |
| Business logic | The Hook Framework itself contains zero domain rules. Every verdict-producing decision is the injected callback's responsibility — identical in spirit to how `ToolPolicyEngine`/`SkillPolicyEngine` decide nothing about *why* a policy exists, only how a verdict is produced and consumed. |

This is the same "owns the mechanism, never the decision" boundary the
platform already applies to every policy-shaped seam (`tool.governance`,
`skill.planning.policy`) — Hooks are simply the first mechanism designed to be
**cross-cutting across runtimes** rather than owned by one runtime.

---

## 2. Responsibilities (Ownership Boundary)

**Owns:**

- Hook registration (manifest validation, identity, namespacing)
- Hook discovery (by point, by runtime scope, by tag)
- Hook manifests (the versioned, diffable contract)
- Hook descriptors (manifest + lifecycle state)
- Hook lifecycle (registration/activation/deprecation/retirement)
- Hook priority (an explicit, declared ordering signal)
- Hook ordering (deterministic execution sequence at a point)
- Hook filtering (which registered hooks apply to a given point + scope)
- Hook execution policy (fail-open vs. fail-closed per point, isolation)
- Hook context (the immutable event shape passed to `evaluate`)
- Hook metadata
- Hook observability (which hooks ran, what they decided, timing)
- Hook replay metadata (was the same set of hooks invoked, in the same order,
  producing the same verdicts)

**Never owns:**

- Tool execution (Tool Runtime's own concern, unchanged)
- Skill planning (Skill Runtime's own concern, unchanged)
- Workflow orchestration (future Workflow Runtime's own concern)
- Agent reasoning (future Agent Runtime's own concern)
- Provider routing (Provider Runtime's own concern, unchanged)
- **Why** any given hook allows, denies, or modifies anything — that is the
  registered callback's business logic, never the Framework's

---

## 3. Ownership Matrix

| Concept | Owner | Never |
|---|---|---|
| `HookManifest` | Hook Framework | Executable code (a manifest is data, exactly like `ToolManifest`/`SkillManifest`) |
| `HookDescriptor` | Hook Framework | Runtime execution state of the *guarded* action |
| `HookRegistry` | Hook Framework | Deciding a verdict, scoring hooks, resolving capabilities |
| `HookOrdering`/`HookPriority` | Hook Framework | Priority *inference* from usage/scoring — priority is always an explicit, caller-declared manifest field |
| `HookFilter` | Hook Framework | Anything beyond point/scope/tag matching — no ranking, no intelligence |
| `HookExecutionPolicy` | Hook Framework | The guarded runtime's own retry/timeout policy (untouched) |
| `HookExecutionContext` | Hook Framework | Raw credentials/secrets (payload is referenced by content-hash, never inlined) |
| `Hook` (the ABC) | The registering caller | The Framework never implements a concrete `Hook` — every concrete hook is business logic living outside this package |
| `HookObservabilitySink`/`HookReplaySink` | Hook Framework (interface) + a future concrete sink | Real telemetry backends (reserved extension, zero implementation this design) |
| Tool execution / Skill planning / Workflow orchestration / Agent reasoning / Provider routing | Their own respective runtimes | The Hook Framework, ever |

---

## 4. Hook Manifest

Mirrors the platform's now-established manifest pattern (`ToolManifest`,
`SkillManifest`): an immutable, versioned, diffable **contract**, never
executable code. The concrete callback a manifest's `id` resolves to at
registration time is a separately-injected `Hook` instance — the manifest
never embeds logic, exactly like a `SkillManifest`'s `instructions` field is
prose, never code.

```python
class HookPoint(StrEnum):
    """Deterministic, named lifecycle points. Additive only (AR-32) — a new
    point is a backward-compatible enum member, never a breaking change."""
    BEFORE_PLAN = "before_plan"
    AFTER_PLAN = "after_plan"
    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"
    BEFORE_HANDOFF = "before_handoff"
    AFTER_HANDOFF = "after_handoff"
    BEFORE_WORKFLOW_STEP = "before_workflow_step"
    AFTER_WORKFLOW_STEP = "after_workflow_step"
    ON_LOOP_ITERATION = "on_loop_iteration"
    ON_ERROR = "on_error"


class HookRuntimeScope(StrEnum):
    """Which runtime(s) a hook is declared to attach to — a pure filter tag,
    never a live import. The Hook Framework does not know what `TOOL` *is*,
    only that a caller invoking `HookEngine.run(point, scope=TOOL, ...)` wants
    hooks tagged for that scope."""
    TOOL = "tool"
    SKILL = "skill"
    WORKFLOW = "workflow"
    AGENT = "agent"
    ANY = "any"  # attaches regardless of the calling runtime's scope


@dataclass(frozen=True, slots=True)
class HookCapabilityRequirement:
    """A declared, structural need — e.g. 'this hook requires org_id present
    in the event' — never resolved or enforced by the Framework itself
    (identical posture to `SkillCapabilityRequirement`: declared, validated
    for well-formedness only, never resolved here)."""
    field: str
    optional: bool = False


@dataclass(frozen=True, slots=True)
class HookManifest:
    id: str
    namespace: str = "internal"
    version: str = "1.0.0"
    interface_version: str = "1.0"
    description: str = ""
    points: frozenset[HookPoint] = frozenset()
    scope: frozenset[HookRuntimeScope] = frozenset()
    priority: int = 0  # explicit, declared — never inferred (§11 Ordering Model)
    required_context: tuple[HookCapabilityRequirement, ...] = ()
    tags: frozenset[str] = frozenset()
    owner: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}.{self.id}"
```

`interface_version` validated fail-fast against a `SUPPORTED_INTERFACE_VERSIONS`
constant, identical to `SkillManifest`'s own convention. A manifest declaring
an empty `points` set is rejected at registration (a hook that attaches
nowhere is a validation error, not a silent no-op).

---

## 5. Hook Descriptor

Identical shape to `ToolDescriptor`/`SkillDescriptor`: the immutable manifest
plus registry-assigned lifecycle bookkeeping, replaced (never mutated) on a
state transition.

```python
class HookLifecycleState(StrEnum):
    DRAFT = "draft"
    REGISTERED = "registered"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


@dataclass(frozen=True, slots=True)
class HookDescriptor:
    manifest: HookManifest
    state: HookLifecycleState = HookLifecycleState.DRAFT
    registered_at: float = 0.0
    sequence: int = 0  # monotonic registration order — the ordering tie-break (§11)

    @property
    def qualified_name(self) -> str:
        return self.manifest.qualified_name
```

**Exactly one lifecycle FSM** — the same deliberate asymmetry Skill Runtime
established relative to Tool Runtime, extended here for the same reason: a
Hook never *executes* an underlying action (it only evaluates and returns a
verdict — a single, synchronous-or-async decision, not a multi-phase
execution). `core.fsm` reused, locally duplicated transition table (the
established precedent): `DRAFT → REGISTERED → ACTIVE → DEPRECATED → RETIRED`,
with direct `RETIRE` edges from `REGISTERED` and `ACTIVE`, identical shape to
Tool's and Skill's own descriptor lifecycle.

---

## 6. Registry

`HookRegistry` — registration, discovery, namespacing, version resolution,
lifecycle transitions. Mirrors `SkillRegistry`'s "intentionally dumb"
discipline exactly: **no verdict evaluation, no ordering decision beyond
assigning the monotonic `sequence` counter at registration, no capability
resolution.**

```python
class HookRegistry:
    def register(self, manifest: HookManifest) -> HookDescriptor: ...
    def deregister(self, namespace: str, id_: str, version: str) -> None: ...
    def activate/deprecate/retire(...) -> HookDescriptor: ...
    def get_descriptor(self, namespace, id_, version=None) -> HookDescriptor: ...
    def list_for(
        self, point: HookPoint, scope: HookRuntimeScope, *, include_deprecated=False,
    ) -> tuple[HookDescriptor, ...]:
        """Every ACTIVE (or optionally DEPRECATED) hook attached to `point` and
        matching `scope` (or declaring HookRuntimeScope.ANY), already in
        deterministic order (§11) — the one query shape every runtime call-site
        actually needs."""
```

A separate `HookFilter` (§9) composes with `list_for` for tag-based
refinement, mirroring `SkillCapabilityCatalog`'s separation from
`SkillRegistry` — the registry stays dumb; filtering intelligence (still zero
scoring, purely structural matching) lives in its own module.

A temporary `HookRepository` (a registration-record log, exactly mirroring
`SkillRepository`'s convention) — the registry itself remains the
durable-for-process catalog, never expiring on its own.

---

## 7. Ordering Model

Deterministic, two-key sort, evaluated once per `list_for` call (never cached
across a mutating registration, so a newly-activated hook is picked up on the
next call without a rebuild step):

1. **Primary key — `priority` (descending):** an explicit, manifest-declared
   integer. Higher priority runs first. This is a **caller-declared** signal,
   never an inferred one — see ADR item 3 (§11.1) reconciling this against
   Phase 0's "never priority-inferred" language.
2. **Secondary key — `sequence` (ascending):** the monotonic counter the
   registry assigns at registration time (§5), used only to break ties when
   two hooks share identical priority. Never wall-clock-derived, never
   randomized — the same registration produces the same order every time.

```python
def ordered_hooks(descriptors: Sequence[HookDescriptor]) -> tuple[HookDescriptor, ...]:
    return tuple(sorted(descriptors, key=lambda d: (-d.manifest.priority, d.sequence)))
```

### 11.1 Reconciling "declared priority" with Phase 0's "never priority-inferred"

Phase 0's §9 sketch stated execution order is "registration order... never
priority-inferred." This design does not contradict that — it **clarifies**
it. "Never priority-inferred" was about the Framework never *guessing* a
hook's importance from usage statistics, scoring, or heuristics (the same
"no scoring" discipline `SourceSelector` and `SkillCapabilityCatalog` already
apply). An explicit, caller-declared `priority` integer in the manifest is not
an inference — it is data the registering caller supplies, structurally
validated, exactly like a `SkillDependency` is declared data, not an inferred
edge. Registration order remains the **only** tie-break, preserving Phase 0's
determinism guarantee in full. This reconciliation is recorded as ADR item 3
(§11 → ADR list, §11).

---

## 8. Execution Policy

`HookExecutionPolicy` — per-`HookPoint` configuration governing how a
verdict set is aggregated and how a failing hook is handled. Never hardcoded;
always caller-configurable per point (mirrors `StreamingPolicy`'s own
per-caller-configurable posture from Tool Runtime's streaming bundle).

```python
class HookFailureMode(StrEnum):
    FAIL_CLOSED = "fail_closed"  # a hook that raises counts as its own DENY vote
    FAIL_OPEN = "fail_open"      # a hook that raises is logged and excluded from aggregation


@dataclass(frozen=True, slots=True)
class HookExecutionPolicy:
    point: HookPoint
    failure_mode: HookFailureMode
    aggregation: HookAggregationRule  # e.g. DENY_WINS, ALL_MUST_ALLOW, FIRST_MODIFY_WINS
    isolation: bool = True  # always True in this design — one hook's exception
                             #   never prevents sibling hooks at the same point
                             #   from running (collected, not first-wins)
```

**Default posture** (matching Phase 0 §9, now formalized as a concrete
default configuration rather than prose): `before_*` points default to
`FAIL_CLOSED` with `DENY_WINS` aggregation (a gate — the safe default for
anything that can block a real action); `after_*`/observational points
default to `FAIL_OPEN` (a failing observer must never retroactively block an
action that already happened) but every isolated failure is still recorded by
observability, never silently swallowed.

**Isolation** is a hard invariant, not a policy knob: every hook runs in its
own try/except; the Framework "walks the whole list" at a point regardless of
any single hook's outcome, mirroring `FailoverController`'s own
"don't-stop-at-the-first-problem" shape.

**Security:** a `HookExecutionContext` never carries raw credentials or
secrets — the same rule Provider Runtime and Tool Runtime governance already
enforce. A hook that needs to evaluate, say, a Tool call receives the typed,
already-validated `ToolCall` shape (or a content-hash reference to it), never
an unvalidated raw payload.

---

## 9. Context

`HookExecutionContext` (an "event," in Phase 0's vocabulary — renamed
`HookEvent` here to avoid confusion with `core.events.Event`, the platform's
own envelope, which a `HookEvent` is deliberately distinct from and does not
subclass) — immutable, provider-neutral, transport-neutral. Inline
session/trace/org fields, no `tenant.TenantContext`/`pipeline.RequestContext`
import — identical precedent to `ToolRequest` (DL-0044), `GovernanceIdentity`
(DL-0045), and `SkillExecutionContext` (DL-0051).

```python
@dataclass(frozen=True, slots=True)
class HookEvent:
    event_id: str                       # content-hash, deterministic
    point: HookPoint
    scope: HookRuntimeScope
    session_id: str = ""
    trace_id: str = ""
    org_id: str | None = None
    workspace_id: str | None = None
    payload_hash: str = ""              # content-hash reference to the typed,
                                          #   already-validated payload — never
                                          #   raw credentials or an unvalidated blob
    created_at: float = 0.0
    metadata: Mapping[str, str] = field(default_factory=dict)


class HookOutcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    MODIFY = "modify"


@dataclass(frozen=True, slots=True)
class HookVerdict:
    outcome: HookOutcome
    modified_payload_hash: str | None = None  # a content-hash reference only —
                                                #   the Hook Framework NEVER applies
                                                #   the modification itself; interpreting
                                                #   and applying a MODIFY verdict is the
                                                #   calling runtime's own responsibility
    reason: str = ""
    hook: str = ""  # qualified_name of the hook that produced this verdict
```

**Extension model:** `Hook` is an ABC with exactly one method —

```python
class Hook(ABC):
    @abstractmethod
    def evaluate(self, event: HookEvent) -> HookVerdict: ...
```

— deliberately the simplest extension shape in the whole platform, since
hooks are the most likely place a third party will extend the system (Phase 0
§9's own framing, retained verbatim: this is a design constraint, not a
suggestion).

`HookEngine.run(point, scope, event, descriptors)` collects one `HookVerdict`
per matching, ordered hook, applies the point's `HookExecutionPolicy`
aggregation rule, and returns an aggregated result plus the full per-hook
verdict list (never silently discarding individual verdicts — a caller that
wants to know *which* hook denied always can).

---

## 10. Architecture

### Package Structure

```
platform/hooks/
├── domain.py             # HookPoint, HookRuntimeScope, HookManifest, HookDescriptor,
│                          #   HookLifecycleState, HookCapabilityRequirement,
│                          #   HookEvent, HookOutcome, HookVerdict,
│                          #   HookExecutionPolicy, HookFailureMode, HookAggregationRule
├── ids.py                 # content-hash ids (local copy of the tool/skill ids pattern)
├── lifecycle.py           # ONE core.fsm lifecycle (DRAFT..RETIRED) — no execution FSM
├── registry.py            # HookRegistry — registration/discovery/namespacing/
│                          #   version-resolution/lifecycle; assigns `sequence`
├── ordering.py            # deterministic (priority desc, sequence asc) sort — §11
├── filter.py               # HookFilter — point/scope/tag matching, no scoring
├── policy.py                # HookExecutionPolicy defaults per point (§8)
├── engine.py                 # HookEngine — runs registered hooks for a point,
│                             #   collects verdicts, applies aggregation — never
│                             #   performs the guarded action itself
├── context.py                 # HookEvent / HookVerdict builders
├── validation.py                # manifest structural validation (mirrors skill/validation.py)
├── serializer.py                  # manifest/descriptor/event/verdict <-> dict, versioned
├── config.py                       # HookConfiguration (validated, no credentials)
├── factory.py                       # HookFactory.create/default (composition root)
├── extensions.py                     # Hook (ABC), HookObservabilitySink, HookReplaySink —
│                                     #   interfaces only, no implementation
├── repositories/
│   └── hook_repository.py             # temporary registration log — the registry
│                                       #   itself is the durable-for-process catalog
└── __init__.py
```

### Dependency Graph

```
hooks → core[, tenant]
```

No `tool`/`skill`/`workflow`/`agent`/`provider` import, ever. This is the
critical property that makes Hooks genuinely cross-cutting: `hooks` is a
**library every consumer runtime imports**, never the reverse — the same
shape `core.fsm` and `core.events` already have relative to every runtime in
the repository. A future Tool Runtime, Skill Runtime, Workflow Runtime, or
Agent Runtime adds `from hooks import HookEngine, HookPoint, HookRuntimeScope`
and calls `engine.run(point, scope, event, registry.list_for(point, scope))`
at its own defined lifecycle moments — an additive change to *that* runtime's
own code, made by *that* runtime's own milestone, never by the Hook Framework
reaching into it.

### Ownership Matrix

See §3.

### Extension Strategy

- `Hook` (ABC, one method) — the sole seam for third-party/business-logic
  extension. Every concrete hook implementation lives outside this package.
- `HookObservabilitySink` / `HookReplaySink` — reserved, unimplemented
  interfaces (mirrors `SkillObservabilitySink`/`SkillReplaySink`'s own
  Bundle-1-reserved, Bundle-2-implemented precedent).
- `HookFilter` — additive tag-based refinement without touching `HookRegistry`.

### Composition Strategy

`HookFactory.create(config, *, hooks=(), ...)` — identical shape to
`ToolFactory`/`SkillFactory`'s own composition-root convention. `HookEngine`
is constructed with an injected `HookExecutionPolicy` map (per point),
defaulting to §8's posture when a point has no explicit override.

---

## 11. Integration: How Hooks Attach to Existing/Future Runtimes

**No runtime dependency is ever introduced in the `hooks → *` direction.**
Integration is always the consuming runtime importing `hooks`, never `hooks`
importing the consumer:

| Runtime | Integration shape | Status |
|---|---|---|
| Tool Runtime | `tool/runtime.py` would call `HookEngine.run(BEFORE_TOOL_CALL, ...)` / `AFTER_TOOL_CALL` around its own execution FSM transitions | **Deferred** — Tool Runtime is certified and frozen; wiring this in requires an explicit future "integrate Hooks into Tool Runtime" milestone that unfreezes `tool/` for an additive, non-breaking change (new call-sites only, no redesign) |
| Skill Runtime | `skill/planning/runtime.py` would call `HookEngine.run(BEFORE_PLAN, ...)` / `AFTER_PLAN` around `create_plan` | **Deferred** — same reasoning; Skill Runtime is certified and frozen |
| Workflow Runtime | `BEFORE_WORKFLOW_STEP` / `AFTER_WORKFLOW_STEP` around each `WorkflowNode`'s execution | **Prospective** — Workflow Runtime does not exist yet; its own future design/implementation milestones can wire hooks in from the start, at zero retrofit cost |
| Agent Runtime | `BEFORE_HANDOFF` / `AFTER_HANDOFF` around `AgentPort.handoff`; `ON_LOOP_ITERATION` / `ON_ERROR` around the agent loop | **Prospective** — same reasoning |

This asymmetry (two runtimes need a deferred retrofit, two get it for free by
being built after Hooks exist) is itself a reason to sequence Hook Framework
before Workflow/Agent Runtime in the M6.0 milestone plan — confirmed as the
right ordering, not a coincidence.

---

## 12. ADR List (Recommended)

1. **The Hook Framework is a cross-cutting library, not a `core.ports` entry**
   — formal ratification of Phase 0's already-proposed **ADR-0010**, now
   backed by the complete design in this document (§10 dependency graph;
   `hooks` has no runtime-boundary port because it is consumed *by* runtimes,
   never *as* one).
2. **Hooks apply the platform's manifest/descriptor/registry/lifecycle
   pattern**, even though Phase 0's original sketch only specified
   `HookEvent`/`HookVerdict`/`HookRegistry`/`HookEngine` — recording why
   `HookManifest`/`HookDescriptor` were added: every other registrable entity
   in the repository (Tool, Skill) gets this treatment, and Hooks being
   registrable, versioned, and independently lifecycle-managed by third
   parties makes the inconsistency of omitting it larger, not smaller.
3. **Priority is declared, never inferred** — reconciling Phase 0's "never
   priority-inferred" language with this design's explicit `priority` manifest
   field (§11.1); registration-order remains the sole tie-break, preserving
   Phase 0's determinism guarantee.
4. **A `MODIFY` verdict carries a content-hash reference only, never inline
   mutation logic** — the Hook Framework decides *that* a payload should be
   modified and *by which hook*, never *how*; applying the modification is
   always the calling runtime's own responsibility. This keeps Hook Verdicts
   serializable, replayable, and free of any live payload-mutation code path
   inside a package that must stay provider- and runtime-neutral.
5. **No direct runtime dependency in either direction; Tool/Skill integration
   is explicitly deferred** — recording that "just add a hook call inside
   `tool/runtime.py` now, it's a tiny change" is the most likely future
   shortcut attempt to guard against, since Tool Runtime is certified and
   frozen and any change to it — however small — requires an explicit
   unfreeze decision, not a Hook Framework milestone unilaterally reaching in.

---

## 13. Bundle Plan (Exactly Two Future Phases)

Per this milestone's explicit instruction, the implementation path is
deliberately narrower than Tool Runtime's five bundles or Skill Runtime's
three — Hooks are, by design, the simplest extension shape in the platform,
and do not warrant further splitting.

| Phase | Scope |
|---|---|
| **M6.3B — Hook Framework Bundle (Complete Implementation)** | Everything in §4–§10: `HookManifest`/`HookDescriptor`, `HookRegistry` (+ temporary `HookRepository`), `HookOrdering`, `HookFilter`, `HookExecutionPolicy` (with the §8 default posture), `HookEvent`/`HookVerdict`, `HookEngine`, serializer/config/factory, `Hook`/`HookObservabilitySink`/`HookReplaySink` interfaces (the latter two reserved, matching Skill Runtime's own Bundle-1-reserve/Bundle-2-implement precedent — but since Hooks get only one implementation bundle, a decision is needed at M6.3B kickoff on whether observability/replay ship a first concrete implementation immediately or stay interface-only pending a dedicated future consumer, mirroring the same "ownership only vs. concrete default" judgment call this repository's Skill Runtime certification flagged as needing to be made deliberately, not by default). No integration into Tool/Skill/Workflow/Agent Runtime — that remains each runtime's own future milestone (§11). |
| **M6.3C — Hook Framework Production Certification** | Full architecture/dependency/runtime-completeness/determinism/performance/documentation audit of M6.3B, mirroring the M6.1B-5/M6.2B-3 certification methodology (7-category, 100-point scoring; Blocking/High/Medium/Low technical debt classification; explicit PASS/FAIL closure recommendation). |

This supersedes Phase 0's tentative "M6.3A design / M6.3B-1 core / M6.3B-2
intelligence" placeholder numbering (`epic6-agent-platform-architecture.md`
§milestone table) — this design's own explicit two-phase instruction takes
precedence, the same way M6.2A's approved 3-bundle breakdown superseded its
own Phase 0 placeholder numbering for Skill Runtime.

---

## 14. Risks

| Risk | Mitigation |
|---|---|
| Hook Framework becomes a second, competing policy-engine pattern next to every runtime's own `PolicyEngine` | §1/§2 explicitly scope Hooks to *lifecycle interception*, not general policy — each runtime's own `PolicyEngine` (allow-list, quotas, tenancy, rate limits) is unaffected and unreplaced. Restated from Phase 0's own risk register, still live. |
| Wiring Hooks into Tool/Skill Runtime requires unfreezing two certified packages | §11 records this as an explicit, deferred, future-milestone decision — never something this design or M6.3B silently does as a side effect. |
| `MODIFY` verdicts are ambiguous if a calling runtime doesn't correctly interpret `modified_payload_hash` | ADR item 4 (§12) keeps the Framework's contribution to a content-hash reference only; each future integration milestone (§11) must include an explicit test proving its own call-site applies `MODIFY` correctly — Hook Framework's own certification (M6.3C) cannot verify this in isolation since no real consumer exists yet at that point. |
| Two hooks registered with identical `priority` produce ambiguous-looking order to a casual reader | §11's `sequence` tie-break is deterministic and tested at M6.3B; documented clearly so a future contributor does not mistake identical-priority hooks for "unordered." |
| `FAIL_OPEN` on `after_*`/observational points could silently swallow a real integration bug | §8 requires every isolated hook failure to be recorded by observability (never merely logged to stdout) — M6.3B must test that a raising hook still produces a recorded failure trace even under `FAIL_OPEN`. |
| Third-party `Hook` implementations receiving `HookEvent.payload_hash` might expect the raw payload instead | §9 documents this as a deliberate security boundary (no raw credentials/unvalidated payloads) — an SDK-level convenience helper resolving a hash back to its typed source object is legitimate future work, but is explicitly not part of `hooks/`'s own scope (it would belong to whichever runtime owns the original payload). |

---

## 15. Recommendation

Design approved for implementation. Recommend proceeding to **M6.3B — Hook
Framework Bundle (Complete Implementation)** on explicit commission, following
the two-phase plan in §13. Zero changes to Tool Runtime, Skill Runtime, or any
Epic 5 runtime are proposed or required by this design.
