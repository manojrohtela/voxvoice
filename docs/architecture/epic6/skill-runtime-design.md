# M6.2A — Skill Runtime Design

**Status:** Design-only · No implementation · Epic 5 + Tool Runtime untouched
**Milestone:** M6.2A (Epic 6, second sub-milestone after Tool Runtime's M6.1B-5 closure)
**Depends on:** No frozen `core.ports.skill` exists (none was anticipated at M5.0 —
confirmed by direct inspection of `platform/core/ports/`); Skill Runtime is
discovered structurally through its own published surface instead (§14, ADR-4)
**Related:** `docs/architecture/epic6/epic6-agent-platform-architecture.md` §8
(Skills Architecture, Phase 0 sketch — this document supersedes its brevity while
preserving every verdict it already reached: manifest not code, resolution/
discovery/versioning only, never execution)

---

## 1. Executive Summary

- A **Skill** is a reusable, versioned, declarative capability that *composes*
  existing platform components (Tools, other Skills, and runtime services) —
  it is not an Agent, not a Workflow, not a Tool, not a Prompt, and never
  executable code.
- Skill Runtime owns registration, discovery, manifests, descriptors,
  versioning, lifecycle, dependency-graph validation, structural validation,
  execution *planning* (never execution), metadata, capability declaration,
  composition, execution context, and observability/replay *metadata* only.
- It never owns Tool execution, Workflow execution, Agent planning, Memory,
  Reasoning, Provider routing, MCP, or A2A.
- Architecturally a peer of Tool Runtime, not a descendant: same registry +
  descriptor + lifecycle + factory shape, same "registry stays dumb, a
  separate catalog handles discovery" discipline — but with exactly **one**
  lifecycle FSM, not two, because there is no execution to have a second FSM
  for (a deliberate, explicit asymmetry from Tool Runtime).
- `SkillManifest` is a **hybrid**: YAML is the human-authoring/interchange
  format, a frozen Python dataclass is the runtime's canonical, validated,
  content-hashed value type — never executable code, mirroring both the Tool
  Manifest precedent and Claude Skills' own YAML-frontmatter-plus-prose shape.
- Skills compose Tools/services only as **declared capability requirements**
  (data, e.g. `tool:calendar.create_event`) — Skill Runtime never resolves
  whether a capability is actually satisfiable; that is deferred to whichever
  future runtime (Agent Runtime, per Phase 0) actually has execution
  authority, via an injected `CapabilityResolver` extension point.
- Skills compose other Skills through a **hard, registry-validated dependency
  graph** (DFS cycle-rejected at registration — acyclic by construction),
  distinct from soft `skill:`-kind capability requirements (a preference, not
  a hard need).
- `SkillExecutionPlanner` produces a deterministic, replayable `SkillPlan`
  (a topologically-sorted composition order + per-requirement resolution
  verdicts) — planning only, execution belongs to future runtimes.
- `SkillExecutionContext` is designed now to become the standard execution
  contract future Workflow/Agent/MCP/A2A Runtimes will accept.
- `skill → core[, tenant]` only — **no `tool` import**, a new explicit
  decision (ADR-4) recorded because it is the one place a future contributor
  will be most tempted to shortcut.
- Policies, Observability, and Replay are **ownership declarations only** in
  this milestone — interfaces reserved, zero implementation, deferred to
  Bundle 2.
- 4 ADRs recommended; exactly 3 implementation bundles (Core → Planning/
  Context/Policies/Observability/Replay → Production Certification), per the
  M6.2A prompt's explicit "do not split further" constraint.
- Zero Tool/Provider/Memory/Knowledge/Context/Prompt/Reasoning/Pipeline
  changes — Tool Runtime's own already-published, read-only surfaces
  (`ToolRegistry`/`ToolCapabilityCatalog`) are the only integration seam a
  future `CapabilityResolver` adapter may wrap, never modified, never
  imported directly by `skill`.

---

## 2. Skill Runtime Vision

Claude Skills, Claude Code Skills, MCP resource/prompt manifests, and OpenAI
Agents SDK's "tool + instructions" bundles all converge on the same shape: a
**versioned, declarative unit of guidance** that a host resolves and injects at
plan time, distinct from the executable primitives (tools, functions) it
references. LangGraph's reusable subgraph nodes and CrewAI's task composition
contribute the other half of the picture: composition of reusable units into a
larger plan is itself a first-class, inspectable data structure, not an
emergent side effect of code calling code.

Skill Runtime is the platform's answer to both halves: a manifest+registry
layer (mirroring Prompt's `TemplateRegistry` far more than it mirrors Tool's
execution engine) whose *output* is a deterministic composition plan a future
orchestrator (Agent Runtime) consumes. It never executes a Tool, never calls a
model, never holds conversation state. One verb, one owner — exactly the
discipline Epic 5 and Tool Runtime both already established.

**Patterns extracted (architecture only, no implementation copied):**

| Source | Pattern extracted |
|---|---|
| Claude Skills | A skill is data + instructions the host loads on demand, not code the host runs |
| Claude Code Skills | Skills are versioned and discoverable by name; instructions are prose, resources are referenced, not inlined |
| OpenAI Agents SDK | A reusable capability bundle declares what it needs (tools) without holding a live reference to them |
| MCP (resource/prompt manifests) | Declarative, versioned, namespaced manifests resolved by a host registry — content, not code, crosses the trust boundary |
| LangGraph (reusable nodes) | Composition is an explicit, inspectable graph structure, not implicit control flow |
| CrewAI (task composition) | Tasks/capabilities can be hard dependencies or soft/optional context, and that distinction is meaningful to the composer |

---

## 3. Responsibilities

**Skill Runtime owns:**

Skill registration · Skill discovery · Skill manifests · Skill descriptors ·
Skill versioning · Skill lifecycle · Skill dependency graph (validation only —
composition/resolution logic is planning) · Skill validation (structural) ·
Skill execution *planning* · Skill metadata · Skill capability *declaration* ·
Skill composition (the hard dependency graph) · Skill execution context (the
shape, not its consumption) · Skill observability (metadata/ownership) · Skill
replay metadata (ownership).

**Skill Runtime never owns:**

Tool execution · Workflow execution · Agent planning (multi-skill/multi-tool
orchestration across a conversation) · Memory · Reasoning · Provider routing ·
MCP · A2A.

---

## 4. Ownership Matrix

| Component | Owns | Never Owns (Extension / Elsewhere) |
|---|---|---|
| `SkillRegistry` | Registration, discovery, version resolution, namespacing, lifecycle transitions, dependency-graph structural validation (cycle rejection) | Capability satisfiability (→ `CapabilityResolver`, injected); execution (nowhere in this runtime) |
| `SkillCapabilityCatalog` | Read-only capability-tag discovery across registered skills | Scoring/ranking/selection (→ a future Agent Runtime planning concern, exactly like `ToolCapabilityCatalog`) |
| `SkillExecutionPlanner` | Deterministic composition ordering + requirement-verdict annotation | Actually invoking a Tool/Skill/service (→ Workflow/Agent Runtime, future) |
| `SkillExecutionContext` | The immutable execution-contract *shape* | Its consumption/enforcement (→ whichever future runtime accepts it) |
| `CapabilityResolver` (extension, unimplemented) | The seam a caller injects to answer "can this be satisfied" | Skill Runtime never implements one itself — it has no `tool`/`memory`/`knowledge`/`provider` import |
| `SkillPolicyEngine` (extension, unimplemented) | Reserved: allow/deny/visibility verdicts over skills | Real policy logic (Bundle 2) |
| `SkillObservabilitySink` (extension, unimplemented) | Reserved: resolution/registration audit + metrics | A metrics engine (Bundle 2) |
| `SkillReplayRecord`/sink (extension, unimplemented) | Reserved: plan-determinism verification metadata | A replay engine (Bundle 2) |

No capability appears in two rows' "owns" column — the single-owner rule is
preserved exactly as Epic 5 and Tool Runtime both already established.

---

## 5. Skill Manifest Design

**Format decision: hybrid — YAML for authoring/interchange, a frozen Python
dataclass for the runtime's canonical value type. Never executable code.**

| Option | Verdict | Why |
|---|---|---|
| JSON only | ❌ Rejected | Poor authoring ergonomics for multi-paragraph `instructions` prose; the same reason Phase 0 already rejected Markdown-only for lacking structured fields — JSON solves the structure problem but reintroduces the prose-ergonomics problem |
| YAML only (parsed ad hoc at read time) | ❌ Rejected as the sole runtime shape | Not a validated, replay-safe, content-hashable value type on its own — two reads of "the same" YAML could silently diverge if the parser or file changes between them |
| Code package (importable Python) | ❌ Rejected | Identical reasoning to Tool Manifest's rejection of vendor code and Phase 0's rejection of a code-package skill format — violates the zero-dynamic-execution invariant and the deterministic/sandboxed trust model |
| **Hybrid: YAML on disk, frozen dataclass at runtime (recommended)** | ✅ | Authors write YAML (frontmatter-style metadata + a markdown `instructions` body — exactly Claude Skills' own shape); a deterministic, versioned parser loads it once into an immutable, content-hashed `SkillManifest` at registration time. The manifest is the runtime's only value type from that point forward — the YAML file is provenance, not a live dependency. Mirrors the Tool Manifest precedent exactly: `ToolManifest.inputs`/`.outputs` are "JSON-Schema-shaped" *fields* inside a Python object, never raw JSON at the domain layer |

**`SkillManifest` fields** (frozen, slotted, immutable):

```
id: str
namespace: str = "internal"          # internal.* / org.<org_id>.* / market.<vendor>.*
version: str = "1.0.0"
description: str = ""
instructions: str = ""               # markdown prose — ONE field, never parsed as code
required_capabilities: frozenset[SkillCapabilityRequirement] = frozenset()
dependencies: tuple[SkillDependency, ...] = ()     # hard skill-to-skill composition
resources: tuple[SkillResource, ...] = ()          # URI-referenced, never inline bytes
visibility: SkillVisibility = PUBLIC               # PUBLIC / ORG / PRIVATE
tags: frozenset[str] = frozenset()
owner: str = ""
metadata: Mapping[str, str] = {}                   # open escape hatch, same pattern as Tool
```

Identity is `(namespace, id, version)` — `qualified_name` is `namespace.id`,
exactly mirroring `ToolManifest.qualified_name`. `SkillResource` mirrors
`ToolAttachment`: `uri`, `content_type`, `content_hash` — never inline bytes.

---

## 6. Skill Descriptor Design

`SkillDescriptor` = `manifest` + lifecycle `state` + `registered_at` — a
distinct object from the manifest, replaced wholesale on a lifecycle
transition, never mutating the manifest (identical discipline to
`ToolDescriptor`/`ToolManifest`).

**One Skill-specific addition beyond the Tool precedent:** a
`dependency_closure: tuple[tuple[str, str, str], ...]` field — the fully
resolved, transitively-flattened `(namespace, id, version)` set this skill
depends on, computed **once**, deterministically, at registration time (when
the dependency graph is validated as acyclic) and cached on the descriptor.
This exists because Skills — unlike Tools — can depend on other Skills, so the
descriptor is the natural, single place to cache an already-validated closure
rather than re-walking the dependency graph on every read.

---

## 7. Skill Registry Design

`SkillRegistry` mirrors `ToolRegistry` exactly in shape and discipline
("intentionally dumb" — no execution logic, no planning, no scoring):

- **Registration**: `register(manifest)` → `SkillDescriptor` in `DRAFT`.
  Rejects a duplicate `(namespace, id, version)` (fail-fast `ConfigError`,
  identical convention to Tool).
- **Dependency validation** (structural, not planning): at registration, every
  declared `SkillDependency` must resolve to an already-registered skill;
  DFS cycle detection over the skill-to-skill dependency edges rejects any
  registration that would close a cycle back to itself, with a deterministic
  error naming the exact cycle (mirrors `ConfigError`'s existing message
  style). This is bookkeeping — structural graph validity — not composition
  planning (§10), exactly as `validate_manifest` is structural validation, not
  intelligence.
- **Lifecycle**: `activate`/`deprecate`/`retire`, identical direct-RETIRE-edge
  shape to Tool's descriptor lifecycle (§8).
- **Version resolution**: unpinned lookup resolves the highest `ACTIVE`
  version; falls back to the highest registered version of any state if none
  is `ACTIVE` (identical "never silently invoke/reference a withdrawn skill
  via unpinned resolution, but still discoverable by exact version" rule as
  Tool's `get_descriptor`). Numeric-aware dotted-segment version comparison —
  a locally duplicated `_version_key`, no external semver dependency, same
  "duplicate, don't cross-import" precedent as every other closed runtime.
- **Namespacing**: `internal.*` / `org.<org_id>.*` / `market.<vendor>.*` — the
  third form extends Tool's `mcp.<server>.*` analog to a future skill
  marketplace (Claude Skills' own plugin/marketplace distribution model).
- **Discovery**: `list`/`list_scoped`/`names`/`versions_for` — identical
  surface shape to `ToolRegistry`.

`SkillCapabilityCatalog` — a **separate**, read-only object (never
`SkillRegistry` itself, same separation Tool Runtime already certified):
`capabilities_for(id)`, `discover(capability)`, `catalog()`. No scoring, no
ranking — a future Agent Runtime planning concern, not this catalog's.

---

## 8. Skill Capability Model

```
class CapabilityKind(StrEnum):
    TOOL = "tool"
    MEMORY = "memory"
    KNOWLEDGE = "knowledge"
    PROVIDER = "provider"
    SKILL = "skill"          # a soft dependency on another skill's capability

@dataclass(frozen=True, slots=True)
class SkillCapabilityRequirement:
    kind: CapabilityKind
    tag: str                 # e.g. "calendar.create_event", namespaced like Tool's own tags
    optional: bool = False
```

**The central boundary this milestone must get right:** Skill Runtime **declares**
capability requirements (structural validation only — well-formed `kind`,
non-empty `tag`) and **never resolves** whether a requirement is actually
satisfiable. Resolution is the job of whichever runtime actually holds
execution authority over that capability's owning registry — Tool Runtime for
`tool:`, Memory Runtime for `memory:`, Knowledge Runtime for `knowledge:`,
Provider Runtime for `provider:`, Skill Runtime's own registry for `skill:`.

**Extension point — `CapabilityResolver`** (interface only, `skill/extensions.py`):

```
class CapabilityResolver(ABC):
    @abstractmethod
    def can_satisfy(self, requirement: SkillCapabilityRequirement) -> bool: ...
```

A future caller (Agent Runtime, per Phase 0) injects a concrete resolver that
wraps Tool Runtime's **already-published, read-only** `ToolCapabilityCatalog.supports()`
for `tool:`-kind requirements, and the equivalent surfaces on Memory/Knowledge/
Provider for their kinds. **Skill Runtime itself imports none of `tool`,
`memory`, `knowledge`, or `provider`** — this is the literal meaning of
"integrate through published Tool Runtime extension points only": the
integration happens at the composition root that injects the resolver, never
inside `skill/`.

---

## 9. Skill Composition Model

A Skill composes exactly three things, through two distinct mechanisms:

| Composed thing | Mechanism | Hard or soft | Validated when |
|---|---|---|---|
| Tools | `SkillCapabilityRequirement(kind=TOOL, ...)` | Always soft (declared need, not a reference) | Structurally at registration; satisfiability at future plan time |
| Other Skills (dependency) | `SkillDependency(namespace, id, version_range)` | Hard | At registration (existence + acyclicity) |
| Other Skills (capability) | `SkillCapabilityRequirement(kind=SKILL, ...)` | Soft/optional | Same as Tools |
| Runtime services (Memory/Knowledge/Provider) | `SkillCapabilityRequirement(kind=..., ...)` | Always soft | Same as Tools |

**Acyclic guarantee**: `SkillRegistry.register()` performs DFS cycle detection
over skill-to-skill `dependencies` edges — a registration that would close a
cycle back to itself is rejected deterministically, before the descriptor
transitions out of `DRAFT`. This is the *data*-graph analog of the
AST-verified *package*-graph acyclic rule Tool Runtime's own dependency test
already enforces — same guarantee, applied one layer up.

Composition **resolution** (turning the validated graph + capability
requirements into an ordered, annotated plan) is planning-time only (§10) —
Skill Runtime never executes any part of what it composes.

---

## 10. Skill Lifecycle

```
DRAFT --register--> REGISTERED --activate--> ACTIVE --deprecate--> DEPRECATED --retire--> RETIRED
                         └─────────────retire──────────────────────────┘
                                         └──────────────retire (direct)──────────────┘
```

Built on `core.fsm` (reused framework, locally duplicated transition table —
the established precedent). Identical shape to Tool's descriptor lifecycle,
including the direct `RETIRE` edges from `REGISTERED` and `ACTIVE` (a skill may
be withdrawn before or after activation without an intermediate hop) — same
justification, verbatim.

**Deliberate divergence from Tool Runtime**: there is exactly **one** lifecycle
here, not two. Tool Runtime needs a second, *execution* lifecycle
(`RECEIVED→VALIDATING→EXECUTING→COMPLETED/FAILED/CANCELLED`) because it
executes. A Skill never executes — there is no execution to have a second FSM
for. This asymmetry is intentional and should not be "corrected" by a future
contributor assuming every Epic 6 runtime needs a dual-FSM shape merely
because Tool Runtime has one.

---

## 11. Skill Execution Planning Model

`SkillExecutionPlanner` — **planning only**; it never invokes a Tool, calls a
model, or holds conversation state.

Given a requested `(namespace, id, version | None)` and an optionally-injected
`CapabilityResolver`, it produces a `SkillPlan`:

```
@dataclass(frozen=True, slots=True)
class SkillPlanStep:
    skill: tuple[str, str, str]              # (namespace, id, version)
    requirements: tuple[SkillCapabilityRequirement, ...]
    verdicts: Mapping[str, RequirementVerdict]  # tag -> verdict, keyed per requirement

class RequirementVerdict(StrEnum):
    SATISFIABLE = "satisfiable"
    UNSATISFIABLE = "unsatisfiable"
    UNKNOWN = "unknown"          # no resolver injected for this capability kind — never guessed

@dataclass(frozen=True, slots=True)
class SkillPlan:
    plan_id: str                              # content-hash of the ordered steps
    steps: tuple[SkillPlanStep, ...]           # deterministic topological order
    created_at: float
```

**Determinism**: the topological sort uses a stable tie-break —
`(namespace, id, version)` — whenever more than one valid ordering exists, so
the same dependency closure always produces the same `plan_id`. **`UNKNOWN` is
a first-class, honest verdict** — never silently treated as `SATISFIABLE` —
precisely because Skill Runtime has no visibility into Tool/Memory/Knowledge/
Provider registries unless a resolver was explicitly injected.

---

## 12. SkillExecutionContext

Immutable, frozen, slotted — designed now to become the standard execution
contract future Workflow/Agent/MCP/A2A Runtimes will accept, exactly as
`ToolRequest`'s shape became the tool-call contract for this runtime's own
callers.

```
@dataclass(frozen=True, slots=True)
class SkillExecutionContext:
    context_id: str                    # content-hash, deterministic
    skill: tuple[str, str, str]        # (namespace, id, version)
    session_id: str = ""
    trace_id: str = ""
    org_id: str | None = None
    workspace_id: str | None = None    # inline fields — no tenant.TenantContext/
                                        #   pipeline.RequestContext import; identical
                                        #   precedent to ToolRequest (DL-0044) and
                                        #   GovernanceIdentity (DL-0045)
    plan: SkillPlan                    # embeds the already-resolved SkillPlan —
                                        #   a consumer never re-plans non-deterministically
    capability_bindings: Mapping[str, str] = {}  # tag -> concrete resource id; populated
                                                  #   ONLY by whichever runtime has actual
                                                  #   execution authority — Skill Runtime
                                                  #   never populates this itself
    metadata: Mapping[str, str] = {}
```

A `SkillContextSerializer` (versioned `schema_version`, round-trippable dict
shapes) provides the same replay-safe (de)serialization convention already
proven across every other serializer in the repository.

---

## 13. Skill Policies (Ownership Only)

Skill Runtime owns the policy **surface**: visibility/allow-list/deny-list
over which skills may be registered/discovered/composed, and a
capability-requirement policy (e.g. "this skill may only declare capabilities
from an approved list"). Reuses the exact `PolicyVerdict` (ALLOW/DENY/PENDING)
shape `tool.extensions.PolicyEngine` already established.

```
class SkillPolicyEngine(ABC):
    @abstractmethod
    def authorize(self, descriptor: SkillDescriptor) -> PolicyVerdict: ...
```

Interface only, `skill/extensions.py`. Zero implementation — reserved for
Bundle 2, exactly mirroring how `tool.extensions.PolicyEngine` was reserved at
M6.1B-1 and built at M6.1B-2.

---

## 14. Skill Observability (Ownership Only)

Skill Runtime owns: registration/discovery audit trail, skill usage/discovery
metrics, and skill-plan traces (mirrors `tool.observability`'s
`ExecutionTrace`/`ToolRuntimeMetrics` pattern, applied to planning data instead
of execution data).

```
class SkillObservabilitySink(ABC):
    @abstractmethod
    def record(self, event: SkillObservabilityEvent) -> None: ...
```

Interface only. Zero implementation — reserved for Bundle 2.

---

## 15. Skill Replay (Ownership Only)

Skill Runtime owns replay **metadata** for a resolved `SkillPlan` — enough to
verify a later run resolved the same skill version + capability bindings +
plan order. Since a Skill never executes, there is no `ToolResult`-equivalent
execution artifact to replay — only "was this the same plan," a narrower,
purely-planning-data notion of replay.

```
@dataclass(frozen=True, slots=True)
class SkillReplayRecord:
    replay_id: str
    plan_document: Mapping[str, object]   # the serialized SkillPlan
    plan_hash: str
    replay_timestamp: float

class SkillReplaySink(ABC):
    @abstractmethod
    def record(self, replay: SkillReplayRecord) -> None: ...
```

Interface + shape declared. Zero implementation — reserved for Bundle 2.

---

## 16. Architecture

### Package Structure

```
platform/skill/
├── domain.py            # SkillManifest, SkillDescriptor, SkillLifecycleState,
│                         #   SkillDependency, SkillResource, SkillVisibility,
│                         #   CapabilityKind, SkillCapabilityRequirement,
│                         #   SkillPlan, SkillPlanStep, RequirementVerdict,
│                         #   SkillExecutionContext
├── ids.py                 # content-hash ids (local copy of the tool.ids pattern)
├── lifecycle.py             # ONE core.fsm lifecycle (DRAFT..RETIRED) — no execution FSM
├── registry.py                # SkillRegistry — registration/discovery/version-resolution/
│                              #   namespacing/lifecycle/dependency-cycle validation
├── catalog.py                   # SkillCapabilityCatalog — read-only, no scoring
├── validation.py                  # manifest structural validation (mirrors tool/validation.py)
├── planner.py                       # SkillExecutionPlanner — planning only
├── serializer.py                      # manifest/descriptor/context/plan <-> dict, versioned
├── config.py                            # SkillConfiguration (validated, no credentials)
├── factory.py                             # SkillFactory.create/default (composition root)
├── extensions.py                          # CapabilityResolver, SkillPolicyEngine,
│                                          #   SkillObservabilitySink, SkillReplaySink —
│                                          #   interfaces only, no implementation
├── repositories/
│   └── skill_repository.py                 # the REGISTRY is the durable-for-process
│                                            #   catalog (never expires, like ToolRegistry
│                                            #   itself); only replay records (Bundle 2)
│                                            #   are temporary — see Risks §18
└── __init__.py
```

### Dependency Graph

```
skill → core[, tenant]
```

**No `tool` import** — a new, explicit decision (ADR-4, §17). Matches the
Phase-0 sketch's own `skill → core[, tenant]` line exactly; this design
confirms and justifies it rather than silently inheriting it.

### Ownership Matrix

See §4.

### Runtime Boundaries

Skill Runtime never executes; never imports `tool`/`memory`/`knowledge`/
`provider`/`reasoning`/`pipeline`/`bridge`; never plans agent-level, multi-turn
behavior (only its own skill-composition plans); may emit lifecycle/
registration events reusing the existing `Event`/`EventBus`/`EventSource`
envelope with additive `EventType` members (deferred — ownership declared,
zero implementation, per §14).

### Extension Strategy

- `SkillSource` (mirrors `ContextSource` exactly, per Phase 0 §8) — pluggable
  skill provenance (local files, a future marketplace), registers additively.
- `CapabilityResolver` — the sole seam into Tool/Memory/Knowledge/Provider,
  injected by a future composition root, never imported by `skill` itself.
- `SkillPolicyEngine` / `SkillObservabilitySink` / `SkillReplaySink` — reserved,
  unimplemented, Bundle 2.

### Composition Strategy

`SkillFactory.create(config, *, skills=(), capability_resolver=None, ...)` —
identical shape to `ToolFactory`/`GovernanceFactory`/`ObservabilityFactory`/
`StreamingFactory`'s own composition-root convention.

---

## 17. ADR List (Recommended)

1. **Skills are a versioned manifest, not code** — formal ratification of
   Phase 0's already-proposed ADR-0008, now backed by the complete design in
   this document (hybrid YAML/dataclass format, §5).
2. **Skill capability requirements are declared, never resolved, by Skill
   Runtime** — recording the "requires without owning" boundary explicitly,
   since "just let Skill Runtime check the Tool Registry directly, it's
   simpler" is the most likely future shortcut attempt to guard against.
3. **Skill-to-skill composition is a two-tier model**: a hard, registry-
   validated dependency graph (`dependencies`) distinct from soft `skill:`-kind
   capability requirements — recording why these must not collapse into one
   mechanism (hard dependencies need acyclicity guarantees at registration;
   soft requirements are resolved, or not, at plan time).
4. **No `SkillPort` at this design stage** — no M5.0 port anticipated this
   shape (confirmed by direct inspection); Skill Runtime is discovered
   structurally through its own public registry/catalog surface plus the
   reserved `CapabilityResolver`/`SkillSource` extension points, mirroring
   Tool Runtime's own recorded `ToolPort`-conformance deferral (DL-0044). A
   thin port conformance shim remains additive future work once an actual
   caller (Agent Runtime, M6.5A) exists.

---

## 18. Risks

- **Capability-resolution ambiguity**: without an injected resolver, Skill
  Runtime cannot know if a capability is truly satisfiable. Mitigated by the
  Planning Model's explicit `UNKNOWN` verdict — never silently assumed
  `SATISFIABLE`.
- **Marketplace-scale dependency cycles**: multiple vendor-sourced skills
  declaring dependencies on each other could produce hard-to-diagnose
  registration failures at scale. Mitigated by deterministic, cycle-naming
  error messages (mirrors `ConfigError`'s existing style) — a Bundle 1
  requirement, not deferred.
- **Manifest format drift**: the YAML-authoring / dataclass-runtime boundary
  must stay round-trip-safe as the shape evolves. Mitigated by the same
  `schema_version`-stamped serializer convention already proven in
  `tool.observability`.
- **Repository durability confusion**: a registered `SkillManifest`/
  `SkillDescriptor` is a *catalog* entry (long-lived, like Prompt's
  `TemplateRegistry`), not an ephemeral execution record (like Tool's
  `ToolResult`). Copy-pasting Tool's "temporary only" repository convention
  verbatim without this distinction would be a real design mistake — Bundle 1
  must explicitly decide the registry itself never expires; only *replay*
  records (Bundle 2) are temporary.
- **No `SkillPort` means discovery-based integration, not conformance-checked
  integration** — a risk of divergence between what Agent Runtime (M6.5A)
  expects and what Skill Runtime provides. Mitigated by reviewing the injected
  `CapabilityResolver`/`SkillExecutionContext` shapes jointly when Agent
  Runtime's own design milestone begins.

---

## 19. Milestone Breakdown

Exactly three implementation bundles, per this milestone's explicit
"do not split further" constraint:

| Bundle | Content |
|---|---|
| **Bundle 1 — Core** | `domain.py`, `ids.py`, `lifecycle.py` (one `core.fsm`), `registry.py` (+ dependency-cycle validation), `catalog.py`, `validation.py`, `config.py`/`factory.py`/`serializer.py`, the durable-registry-vs-temporary-replay repository decision made concrete |
| **Bundle 2 — Planning, Context, Policies, Observability, Replay** | `planner.py` (`SkillExecutionPlanner`), `SkillExecutionContext` + serializer, a real `SkillPolicyEngine` implementation (mirrors `ToolPolicyEngine`), a real `SkillObservabilitySink`/metrics implementation (basic counters only, mirrors `ToolRuntimeMetrics`), `SkillReplayRecord` + a plan-determinism verification engine |
| **Bundle 3 — Production Certification** | Identical certification methodology to M6.1B-5, scoped to Skill Runtime: architecture/dependency/runtime-completeness/determinism/performance/documentation/repository audit, technical debt review, production readiness score, closure recommendation |

---

## 20. Recommendation

Design approved for implementation. Proceed to **Bundle 1 (M6.2B-1 — Skill
Runtime Core)** next. No code was written in this milestone — zero changes to
Tool Runtime, Provider Runtime, Memory Runtime, Knowledge Runtime, Context
Runtime, Prompt Runtime, Reasoning Runtime, or Runtime Pipeline, verified by
`git status` showing only documentation changes.
