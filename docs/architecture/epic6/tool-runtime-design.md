# M6.1A — Tool Runtime Design

**Status:** Design-only · No implementation · Epic 5 untouched
**Milestone:** M6.1A (Epic 6, first sub-milestone after M6.0 Phase 0)
**Depends on:** `core.ports.tool.ToolPort` (frozen since M5.0, unexercised until now)
**Related:** `docs/architecture/epic6/epic6-agent-platform-architecture.md` §Tool Runtime

---

## 1. Executive Summary

The Tool Runtime is the execution layer through which an Agent invokes external
capabilities — calendar, email, Slack, CRM, database, search, filesystem, browser,
HTTP APIs, internal services, and any future enterprise integration. It implements
the frozen `core.ports.tool.ToolPort` exactly as designed at M5.0: `register`,
`list`, `invoke`. It owns registration, discovery, validation, execution, lifecycle,
permissions, policy, and observability of tools — and nothing else. It does not
plan, reason, orchestrate multi-step behavior, or route to AI providers; those
remain Agent, Reasoning, and Provider Runtime responsibilities respectively.

Architecturally the Tool Runtime is a peer of Provider Runtime (M5.6), not a
descendant of it: same shape (registry + descriptor + execution engine + policy
layer + observability), same frozen-port-implementation extension mechanism, same
"registry owns catalog, no orchestration inside it" discipline. No Epic 5 package
is modified. This document is architecture only; M6.1B implementation milestones
follow in sequence once this design is approved.

---

## 2. Tool Runtime Vision

A tool is a typed, versioned, permissioned unit of external capability with a
declared input/output contract. The runtime that hosts it must guarantee three
things regardless of which concrete tool is invoked: **the call is validated
against a known schema before it executes, the call is authorized against policy
before it executes, and the result — success or failure — is captured in a
uniform, replayable shape.** Everything else (what the tool actually does — send
an email, query a CRM) is delegated to a tool implementation the runtime treats as
an opaque, sandboxed unit of execution.

The Tool Runtime is provider-neutral and integration-neutral: it has no
Slack-specific, CRM-specific, or database-specific code. All of that lives in tool
implementations registered against the runtime's generic contract, mirroring how
Provider Runtime has no vendor-specific code — only adapters.

---

## 3. Responsibilities

**Owns:**
- Tool registration (structured `ToolSpec` intake, one call site: `register`)
- Tool discovery (`list`, capability-filtered catalog queries)
- Capability catalog (queryable index: name, capability tags, version, owner)
- Tool execution (`invoke`: validate → authorize → dispatch → normalize result)
- Tool lifecycle (draft → active → deprecated → retired, per version)
- Tool validation (schema-checking `ToolCall.arguments` against `ToolSpec.schema`)
- Tool metadata (descriptor fields: owner, visibility, tags, permissions)
- Tool versioning (multiple concurrent versions of the same tool name)
- Tool permissions (which tenant/org/agent may invoke which tool)
- Tool policies (rate limits, approval gates, feature flags, org restrictions)
- Tool observability (metrics, events, execution logs, audit trail)
- Tool execution results (`ToolResult`: uniform success/failure/metadata shape)

**Explicitly does not own** (boundary is load-bearing, restated from the M6.0
ownership matrix):
- Planning — Agent Runtime's job (which tool to call, in what order)
- Reasoning — Reasoning Runtime's job (why a tool call was decided)
- Workflow — Workflow Runtime's job (durable multi-step orchestration)
- Agent state — Agent Runtime's job (conversation/task state across turns)
- Prompt generation — Prompt Runtime's job (how a tool result becomes a message)
- Memory / Knowledge — those runtimes' jobs (nothing about tool results is stored
  as long-term memory or knowledge by this runtime; a caller may choose to feed a
  `ToolResult` into Memory Runtime afterward, but that composition happens above
  Tool Runtime, not inside it)
- Provider routing — Provider Runtime's job (no AI model is called by this
  runtime; an HTTP-calling tool is not a provider)

This mirrors the Provider Runtime precedent exactly: Provider Runtime owns
*how a model is called*, never *what to say to it* (Prompt's job) or *whether to
call it at all* (Reasoning's job). Tool Runtime owns *how a tool is called*, never
*which tool or when* (Agent's job).

---

## 4. Ownership Matrix

| Capability | Owner |
|---|---|
| Tool registration/discovery/catalog | **Tool Runtime** |
| Tool schema validation | **Tool Runtime** |
| Tool execution + sandboxing | **Tool Runtime** |
| Tool permissions/policy/rate limits | **Tool Runtime** |
| Tool observability/audit | **Tool Runtime** |
| Deciding which tool to call, in what sequence | Agent Runtime (M6.4) |
| Deciding *why* (LLM reasoning behind the call) | Reasoning Runtime (frozen, M5.5) |
| Durable multi-step tool chains | Workflow Runtime (M6.3) |
| Turning a `ToolResult` into a prompt message | Prompt Runtime (frozen, M5.4) |
| Exposing tools to external protocol consumers | MCP Runtime (M6.6, bridges *into* Tool Runtime) |
| AI model invocation | Provider Runtime (frozen, M5.6) — never invoked by Tool Runtime |

---

## 5. Tool Lifecycle

Two lifecycles exist, deliberately kept separate: the **descriptor lifecycle**
(does this tool version exist and may it be invoked at all) and the **execution
lifecycle** (the state of one specific `ToolCall`).

### 5.1 Descriptor lifecycle (per tool version)

```
DRAFT ──register──> ACTIVE ──deprecate──> DEPRECATED ──retire──> RETIRED
                        │                                          ^
                        └──────────────retire (direct)─────────────┘
```

- `DRAFT`: registered internally, not yet discoverable via `list()`.
- `ACTIVE`: discoverable and invocable.
- `DEPRECATED`: still invocable (for callers pinned to it), excluded from default
  discovery, emits a deprecation-warning event on every invoke.
- `RETIRED`: not invocable; `invoke` returns a typed `ToolResult(ok=False,
  error="tool_retired")` without dispatching.

This reuses the `core.fsm` typed-transition pattern established in every prior
Epic 5 runtime (`FSMError` on illegal transitions, no ad hoc state strings).

### 5.2 Execution lifecycle (per `ToolCall`)

```
RECEIVED → VALIDATING → AUTHORIZING → EXECUTING → COMPLETED
                                           │
                                           ├──> FAILED
                                           ├──> TIMED_OUT
                                           └──> CANCELLED
```

- `RECEIVED`: `ToolCall` accepted, call_id assigned/confirmed.
- `VALIDATING`: arguments checked against `ToolSpec.schema`; failure → `FAILED`
  with `error="validation_error"` (never reaches EXECUTING).
- `AUTHORIZING`: policy engine evaluates permission/rate-limit/approval-gate
  verdicts; denial → `FAILED` with `error="policy_denied"` (never reaches
  EXECUTING) — mirrors Provider Runtime's policy-engine-as-verdicts-only pattern.
- `EXECUTING`: the sandboxed tool implementation runs; a `PipelineCancellation`
  token (reused from M5.7, not reinvented) may interrupt this state → `CANCELLED`.
- `COMPLETED` / `FAILED` / `TIMED_OUT` / `CANCELLED`: terminal states; each
  produces exactly one `ToolResult`.

This dual-lifecycle split (descriptor vs. execution) is the same shape as
Provider Runtime's `ProviderRequestState` / `ProviderHealthState` dual FSM — one
FSM for "is this thing usable at all," one for "how did this specific call go."

---

## 6. Tool Registry Design

**Registration model.** `register(spec: ToolSpec) -> None` is the single intake
path (already frozen). Internally, the registry stores `(name, version) →
ToolRegistryEntry`, where the entry wraps the immutable `ToolSpec` plus mutable
runtime state (descriptor lifecycle state, registration timestamp, owner). No
routing or selection logic lives in the registry — exactly as Provider Runtime's
`ProviderRegistry` holds providers without choosing between them.

**Discovery.** `list() -> Sequence[ToolSpec]` returns the ACTIVE catalog by
default (frozen signature, unchanged). An additive, non-frozen
`list_scoped(*, tenant=None, capability=None, include_deprecated=False)` method
provides capability- and tenant-filtered discovery for the Agent Runtime's
planning step, following the same "narrow frozen port + generous additive
methods" pattern used throughout Epic 5 (e.g., `ProviderRegistry.find`,
`.healthy`, `.degraded` sit beside the frozen core operations).

**Versioning.** Tool identity is `(name, version)`, not `name` alone — a single
logical tool ("send_email") may have v1 and v2 registered concurrently. Discovery
without a version pin returns the highest ACTIVE version by default; an agent
may request a pinned version explicitly via `ToolCall` metadata (an additive
field, not a frozen-port change).

**Capability matching.** Each `ToolSpec` carries capability tags (e.g.,
`"communication.email"`, `"data.query"`) in its descriptor metadata. The Agent
Runtime's planning step queries by capability, not by tool name, so tool
implementations can be swapped (e.g., one org's CRM tool for another's) without
changing agent-level prompts or plans — same intent as Provider Runtime's
`CapabilityCatalog`.

**Namespacing.** Tool names are namespaced by origin to prevent collision:
`internal.<name>` for first-party tools, `mcp.<server>.<name>` for tools bridged
in via MCP Runtime (M6.6), `org.<org_id>.<name>` for tenant-private tools. This
mirrors the `pr_`/`pp_`/`pc_`/`pz_` prefixed-id convention from Provider Runtime —
namespace is baked into the identifier, not tracked out-of-band.

---

## 7. Tool Manifest Design

**Decision: hybrid — declarative manifest (JSON-Schema-backed) is the source of
truth for the contract; a thin Python adapter class supplies the execution
body.** Neither pure JSON/YAML nor pure Python class alone is sufficient.

| Option | Verdict | Reason |
|---|---|---|
| Pure JSON/YAML manifest | Rejected alone | Cannot express the actual execution logic — would need a second, disconnected registration step for the handler, inviting drift between declared schema and real behavior. |
| Pure Python class | Rejected alone | Re-legitimizes "tool = arbitrary code," which is exactly the invariant M5.8 certified as absent (zero dynamic code execution) and M6.0 deliberately kept Skills away from. A tool handler differs from a Skill in one critical way — it always executes through the Tool Runtime's sandboxed dispatch, never loaded and run as free-standing code by an LLM — so class-based handlers are permitted here, but only when validated as a **pre-registered, developer-shipped adapter**, never a manifest-supplied code string or dynamically imported path. |
| Manifest only, no handler | Rejected | A tool with no runnable behavior is not a tool, just a Skill (which already covers "described, not executed" — see M6.0 Skill Runtime). Keeping both would duplicate a concept. |
| **Hybrid: manifest (contract) + registered adapter (execution)** | **Recommended** | The manifest (`ToolSpec.schema` plus descriptor metadata) is the versioned, auditable, diffable contract — the same shape whether the tool is internal, MCP-bridged, or org-specific. The adapter is a plain Python object implementing a narrow `ToolAdapter` protocol (`async def execute(call: ToolCall) -> ToolResult`), registered at process-startup composition time alongside its manifest — never resolved from a string at runtime. This is structurally identical to how Provider Runtime pairs a `ProviderDescriptor` (declarative) with a registered `AdapterPort` implementation (code) — same split, same trust boundary. |

The manifest itself is authored as JSON-Schema-shaped Python data (matching
`ToolSpec.schema: Mapping[str, Any]`, already frozen) rather than a separate YAML
file, so there is exactly one format to validate, version, and diff — no
JSON⇄YAML⇄Python triple-parsing surface.

---

## 8. Tool Descriptor Design

Immutable, frozen dataclass — same construction pattern as `ProviderDescriptor`.

```python
@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    name: str                          # namespaced, e.g. "internal.send_email"
    version: str                       # semver string, e.g. "1.2.0"
    description: str
    schema: Mapping[str, Any]          # JSON-Schema for ToolCall.arguments
    output_schema: Mapping[str, Any]   # JSON-Schema for ToolResult.output
    capabilities: frozenset[str]       # e.g. {"communication.email"}
    permissions: frozenset[str]        # required scopes to invoke
    tags: frozenset[str]               # free-form discovery tags
    owner: str                         # team/service that owns this tool
    visibility: str                    # "public" | "org" | "private"
    lifecycle_state: str               # DRAFT | ACTIVE | DEPRECATED | RETIRED
    timeout_ms: int = 30_000
    idempotent: bool = False           # safe to retry without side effects
    streaming: bool = False            # supports incremental ToolResult chunks
```

`ToolSpec` (frozen at M5.0) remains the minimal port-level shape; `ToolDescriptor`
is the runtime's richer, additive internal record — same relationship as
`ProviderRequest` (port-adjacent) to `ProviderRegistryEntry` (internal, richer).

---

## 9. Tool Execution Model

**Request.** Caller (typically Agent Runtime) constructs a `ToolCall` (frozen:
`call_id`, `name`, `arguments`) and passes it to `invoke`.

**Validation.** Arguments checked against the resolved `ToolDescriptor.schema`.
Any failure short-circuits to a `FAILED` `ToolResult` — the tool implementation
is never invoked with invalid input.

**Authorization.** The Tool Policy Engine (§11) evaluates the call against
permission scopes, rate limits, org/tenant restrictions, and any configured
approval gate. A denial short-circuits to `FAILED` before execution — no partial
side effects are possible from a denied call.

**Execution.** The registered adapter's `execute(call) -> ToolResult` runs inside
a sandboxed dispatch boundary (§10). A wall-clock timeout (from
`ToolDescriptor.timeout_ms`) and a cooperative `PipelineCancellation` token (reused
from M5.7, not reinvented) both apply.

**Result.** Every terminal state — success, failure, timeout, cancellation —
produces exactly one `ToolResult`, normalized to a uniform shape (§11's Results
section) regardless of which adapter produced it. This mirrors
`ResponseNormalizer` in Provider Runtime: heterogeneous backends, one normalized
output shape.

**Failure.** Distinguished failure categories (`validation_error`,
`policy_denied`, `execution_error`, `timeout`, `cancelled`) are carried in
`ToolResult.error`, letting the Agent Runtime's planning step react differently
(e.g., retry on `timeout`, don't retry on `policy_denied`).

**Cancellation.** Cooperative only, via the same `PipelineCancellation` token
type already frozen in `platform/pipeline/`. No new cancellation primitive is
introduced.

**Timeout.** Enforced by the runtime around the adapter call, independent of
whether the adapter itself respects cancellation — a defensive outer bound, same
posture as Reasoning Runtime's inference timeout handling.

**Replay.** Every `ToolCall` + resulting `ToolResult` + the resolved
`ToolDescriptor` version is content-hashed and recorded (via the runtime's own
temporary repository, following the Epic 5 pattern) so a call can be replayed
deterministically against a fixed descriptor version for debugging/audit —
mirroring Provider Runtime's `ProviderExecutionTrace`.

---

## 10. Tool Policies

**Permission model.** Each `ToolDescriptor.permissions` declares required scopes
(e.g., `"email.send"`); a caller's `RequestContext`-derived scope set (extended
from M5.7's `RequestContext`, not replaced) must be a superset for authorization
to pass.

**Rate limits.** Per-tool, per-tenant, per-org token-bucket limits, evaluated in
the Policy Engine before execution — same "policy is a verdict, not an action"
discipline as Provider Runtime's `ProviderPolicyEngine`.

**Organization restrictions.** An org may disable specific tools/capabilities
entirely (e.g., disable all filesystem tools for a regulated tenant).

**Tenant isolation.** Tenant-private tools (`org.<org_id>.*` namespace) are
invisible to and non-invocable by any other tenant, enforced at both discovery
(`list_scoped`) and authorization time (defense in depth, not discovery-only).

**Feature flags.** A tool can be gated behind a rollout flag independent of its
lifecycle state (e.g., ACTIVE but flag-gated to 10% of orgs) — additive, not a
lifecycle state, since flag rollout and lifecycle maturity are different axes.

**Approval gates.** High-risk tools (e.g., "send_payment") may declare
`requires_approval=True`; authorization for such a call returns a `PENDING`
verdict routed to a human-approval mechanism rather than an immediate
allow/deny — the one execution-model extension beyond Provider Runtime's
policy shape, justified by tools having real-world side effects that model
inference calls do not.

---

## 11. Tool Security

**Sandbox.** Every adapter executes behind a narrow `ToolAdapter` interface
boundary; the runtime never `eval`s, `exec`s, or dynamically imports adapter code
from manifest-supplied strings (continuing the M5.8-certified zero-dynamic-
execution invariant). Process-level sandboxing (subprocess isolation, network
egress allow-lists, filesystem scoping) is a deployment-time concern documented
here as a requirement, not built into the runtime's core logic.

**Secrets.** Tool adapters that need credentials (e.g., a Slack bot token) never
receive them via `ToolCall.arguments` (which is agent/LLM-visible). Credentials
are resolved by the adapter itself from a secrets provider at execution time,
outside the arguments schema entirely — the runtime enforces this by never
including secret-typed fields in a `ToolDescriptor.schema` exposed to discovery.

**Credential ownership.** The tool's registering team owns and rotates its own
credentials; the Tool Runtime never stores or proxies raw secrets — it stores
only descriptor metadata and execution records.

**Least privilege.** `ToolDescriptor.permissions` is the enforced ceiling; an
adapter cannot request or use scopes beyond what its own descriptor declares
(verified at registration time — a mismatch between declared and used
permissions is a registration-time error, not a silent runtime bypass).

**Audit trail.** Every execution (§9) is recorded with caller identity, resolved
descriptor version, verdict trail from the Policy Engine, and terminal result —
sufficient to reconstruct "who invoked what, with what authorization, and what
happened" for any call, matching the audit bar M5.8 certified for Provider
Runtime.

**Validation.** Schema validation (§9) is mandatory and occurs before
authorization and execution, never optional per-adapter — a single enforced
choke point, not delegated to individual tool implementations.

---

## 12. Tool Results

**Success model.** `ToolResult(call_id, ok=True, output=<schema-validated
payload>)`.

**Failure model.** `ToolResult(call_id, ok=False, error=<category string>)`
(§9's failure categories) plus additive diagnostic metadata (not on the frozen
port fields, layered alongside).

**Metadata.** An additive `ToolExecutionMetadata` (descriptor version used,
adapter identity, duration_ms, retried: bool) attached alongside the frozen
`ToolResult`, mirroring how `ProviderMetadata` sits alongside `ProviderResponse`.

**Usage.** For tools with a meterable cost (e.g., a paid search API), an
additive `ToolUsage` record (units consumed, cost_estimate) — optional, only
populated when the adapter reports it, mirroring `ProviderUsage`'s optionality.

**Execution trace.** A `ToolExecutionTrace` (list of timestamped lifecycle
transitions from §5.2) attached to the result for replay/debugging — same shape
as `ProviderExecutionTrace`.

**Attachments.** Tools that return binary/file content (e.g., a generated
report) attach it as a reference (URI + content-hash), never inline raw bytes in
`ToolResult.output`, keeping results serializable and boundable in size.

**Streaming support.** For long-running tools that can report partial progress
(descriptor `streaming=True`), an additive `stream_results` variant yields
successive partial `ToolResult`s before the terminal one — layered on top of the
frozen `invoke`, not a replacement for it, matching how Provider Runtime's
frozen `stream()` sits beside its non-streaming path.

---

## 13. Tool Observability

**Metrics.** Invocation count, success/failure rate, latency percentiles,
timeout rate — all per `(tool name, version)`, following `Metric` constant
conventions established in Provider Runtime and Runtime Pipeline.

**Events.** Reuses existing `EventType`s plus a tool-scoped `phase` discriminator
(registered / invoked / validated / authorized / executed / completed / failed) —
same event-bus reuse discipline as every prior Epic 5 runtime; no new event bus.

**Execution logs.** One structured log entry per lifecycle transition (§5.2),
correlated by `call_id` and the pipeline-level `trace_id` (from M5.7's
`RequestContext`) so a tool call can be located within a full end-to-end request
trace.

**Latency.** Wall-clock duration split into validation / authorization /
execution phases, so slow-tool diagnosis doesn't require guessing which phase
dominated.

**Failures.** Categorized by the §9 failure taxonomy, aggregable per category to
distinguish "tools are broken" from "policy is too strict" from "callers send
bad input."

**Usage.** Aggregated `ToolUsage` rollups per tenant/org, feeding the same
billing/cost-observability seam Provider Runtime already established.

**Audit.** The §11 audit trail is queryable by tool name, caller, tenant, and
time range — a durable, compliance-facing view distinct from the ephemeral
metrics/logs above.

---

## 14. Tool Extension Strategy

Future tools are added by: (1) authoring a `ToolDescriptor` + JSON-Schema
manifest, (2) implementing the narrow `ToolAdapter` protocol, (3) registering
both together at composition-root startup. No runtime redesign is required for
any future tool — including future categories not enumerated here — because the
runtime's contract (`ToolSpec`/`ToolCall`/`ToolResult`) is generic by
construction, not enumerated per integration type. This is the same "closed
runtime, open registry" pattern used by Provider Runtime's adapter model
(`AsyncAdapter`, `BatchAdapter`, `StreamingAdapter`, `LocalAdapter`,
`EnterpriseAdapter` — five adapter shapes added without touching the frozen
core).

MCP Runtime (M6.6) plugs in via the sanctioned bridge mechanism: an
`MCPToolBridge` (structurally identical to `ProviderModelBridge`) translates
MCP-exposed tools into `ToolDescriptor` + `ToolAdapter` registrations at startup
— Tool Runtime never has MCP-specific code inside it.

---

## 15. Architecture

### 15.1 Package Structure

```
platform/tool/
├── domain.py            # ToolDescriptor, ToolAdapter protocol, lifecycle states
├── ids.py               # content-hash ids: tl_ (descriptor), tc_ (call), tr_ (result)
├── lifecycle.py         # descriptor FSM + execution FSM (core.fsm-based)
├── registry.py          # ToolRegistry — register/list/list_scoped/get, no routing
├── catalog.py           # CapabilityCatalog — capability-tag discovery
├── validation.py        # JSON-Schema argument/output validation
├── execution.py         # ExecutionEngine — validate → authorize → dispatch → normalize
├── policies.py          # ToolPolicyEngine, RateLimitPolicy, ApprovalGatePolicy, etc.
├── security.py          # least-privilege check, secret-field exclusion guard
├── results.py           # ToolResult normalization, ToolExecutionMetadata, ToolUsage
├── observability.py     # Metric constants, ToolExecutionTrace
├── events.py             # tool-scoped phase events (reuses existing EventType)
├── repositories/
│   └── tool_repository.py   # temporary repository (execution records, replay)
├── stores/
│   └── echo_adapter.py  # deterministic offline default ToolAdapter (parity with EchoProviderAdapter)
├── extensions.py        # future adapter-shape hooks (mirrors provider/extensions.py)
├── config.py
├── serializer.py
├── factory.py           # ToolFactory.create/default
└── __init__.py
```

### 15.2 Dependency Graph

```
tool → core[, tenant]
```

Same acyclic shape as every Epic 5 runtime. Tool Runtime does not import Agent,
Workflow, Reasoning, Provider, or Prompt. It is discovered and invoked by those
runtimes (chiefly Agent Runtime, M6.4) via `core.ports.tool.ToolPort` only —
callers depend on Tool Runtime's frozen port, Tool Runtime depends on nothing
above `core`.

### 15.3 Ownership Matrix

(See §4 above — repeated here per the deliverables list for document
completeness; content is identical.)

### 15.4 Execution Diagram

```
Agent Runtime (M6.4, not yet built)
        │  ToolCall
        ▼
┌───────────────────────────────────────────────────────────┐
│                      Tool Runtime                          │
│                                                              │
│  invoke(call) ─▶ ToolRegistry.get(call.name)                │
│                       │                                     │
│                       ▼                                     │
│              validation.py (schema check)                   │
│                       │ pass                                │
│                       ▼                                     │
│              policies.py (ToolPolicyEngine verdict)          │
│                       │ ALLOW                                │
│                       ▼                                     │
│              execution.py (ExecutionEngine)                  │
│                       │                                     │
│                       ▼                                     │
│         registered ToolAdapter.execute(call)                 │
│                       │                                     │
│                       ▼                                     │
│              results.py (normalize → ToolResult)              │
│                       │                                     │
│                       ▼                                     │
│         observability.py + tool_repository (trace/audit)      │
└───────────────────────────────────────────────────────────┘
        │  ToolResult
        ▼
Agent Runtime
```

### 15.5 Sequence Diagram (one call, happy path)

```
Agent Runtime      ToolRegistry      Validator      PolicyEngine      Adapter      Repository
     │  invoke(call)   │                 │               │               │             │
     │────────────────>│                 │                 │               │             │
     │   get(name,ver) │                 │                 │               │             │
     │<────────────────│ ToolDescriptor  │                 │               │             │
     │  validate(call, descriptor.schema)│                 │               │             │
     │─────────────────────────────────>│                 │               │             │
     │<─────────────────────────────────│  OK             │               │             │
     │  authorize(call, descriptor.permissions)             │               │             │
     │──────────────────────────────────────────────────>│                 │             │
     │<──────────────────────────────────────────────────│  ALLOW          │             │
     │  execute(call)                                                     │             │
     │────────────────────────────────────────────────────────────────────>│             │
     │<────────────────────────────────────────────────────────────────────│ raw output  │
     │  record(call, descriptor_version, trace, result)                                  │
     │───────────────────────────────────────────────────────────────────────────────────>│
     │<───────────────────────────────────────────────────────  ToolResult ──────────────│
```

### 15.6 Bridge Strategy

Tool Runtime itself is a bridge *destination*, not a bridge author. Two bridges
target it, both built in later Epic 6 milestones, neither built now:
- `MCPToolBridge` (M6.6): external MCP servers' tools → `ToolDescriptor` +
  `ToolAdapter` registrations.
- Any future protocol adapter (e.g., OpenAPI-spec-driven tool generation)
  follows the same shape: translate external shape → `ToolDescriptor` +
  `ToolAdapter`, register, never modify Tool Runtime's core.

### 15.7 Registry Architecture

`ToolRegistry` holds `(name, version) → ToolRegistryEntry` with a secondary
capability-tag index (`CapabilityCatalog`) for fast discovery-by-capability —
same two-index shape (identity index + capability index) as Provider Runtime's
`ProviderRegistry` + `CapabilityCatalog` pairing. No selection/ranking logic
lives here; that's the Agent Runtime's planning concern (analogous to how
routing lives in Provider Runtime's separate `router.py`, not in `registry.py`).

### 15.8 Composition Strategy

`ToolFactory.create(*, adapters: Sequence[tuple[ToolDescriptor, ToolAdapter]],
policies: ToolPolicies | None = None, clock: Clock | None = None) ->
ToolRuntime` — registers all supplied descriptor/adapter pairs at construction
time (composition-root responsibility, same as every Epic 5 factory).
`ToolFactory.default()` registers only the deterministic offline
`EchoToolAdapter` for testing parity with `EchoProviderAdapter`.

---

## 16. ADR List (Recommended)

1. **ADR-Tool-01: Tool contract is manifest + registered adapter, not
   arbitrary code.** Records the §7 decision and its grounding in the M5.8
   zero-dynamic-execution invariant.
2. **ADR-Tool-02: Tool identity is `(name, version)`, not `name` alone.**
   Records the versioning model (§6) and its implication for discovery
   defaults.
3. **ADR-Tool-03: Approval gates introduce a `PENDING` policy verdict.**
   Records the one execution-model addition (§10) beyond the
   allow/deny binary Provider Runtime's policy engine uses, and why tools'
   real-world side effects justify it.
4. **ADR-Tool-04: Tool Runtime never receives or proxies secrets.** Records
   the §11 boundary — adapters resolve their own credentials — and why this
   is enforced at the schema level (secret-typed fields excluded from
   discoverable descriptors) rather than by convention alone.

---

## 17. Milestone Breakdown

- **M6.1B-1 — Tool Runtime Core.** `domain.py`, `ids.py`, `lifecycle.py` (both
  FSMs), `registry.py`, `catalog.py`, `validation.py`, `execution.py` (happy
  path only: validate → authorize-stub-allow-all → dispatch → normalize),
  `results.py`, `EchoToolAdapter`, `factory.py`, dependency-rules test.
  Independently testable: register a tool, invoke it, get a normalized result,
  with policy as a permissive stub.
- **M6.1B-2 — Tool Policy & Security.** `policies.py` (full: permission,
  rate-limit, org-restriction, feature-flag, approval-gate verdicts),
  `security.py` (least-privilege check, secret-field exclusion guard).
  Independently testable: policy denials short-circuit before execution;
  approval-gated calls return `PENDING`, never execute without a separate
  approval signal.
- **M6.1B-3 — Tool Observability & Replay.** `observability.py`, `events.py`,
  `tool_repository.py` (execution records + replay), `ToolExecutionTrace`.
  Independently testable: every call produces a queryable audit record; a
  replayed call against a pinned descriptor version reproduces the same
  validated/authorized path deterministically.
- **M6.1B-4 — Tool Streaming & Cancellation.** `stream_results` additive path,
  `PipelineCancellation` wiring, wall-clock timeout enforcement.
  Independently testable: a long-running tool call can be cancelled
  mid-execution and yields partial results before a terminal `CANCELLED`
  result.
- **M6.1B-5 — Tool Runtime Closure.** Integration test suite, dependency/
  boundary tests, `docs/architecture/epic6/tool-runtime-closure.md` audit
  (mirrors M5.8's discipline at Tool Runtime scope), Decision Log entry.

Each milestone ends with its own passing test suite before the next begins —
same discipline Provider Runtime followed (M5.6B-1 core → M5.6B-2 intelligence).

---

## 18. Risks

- **Approval-gate `PENDING` verdict has no consumer yet.** Nothing in Epic 5 or
  this design defines who resolves a pending approval. Mitigated by scoping
  M6.1B-2 to define the verdict shape only; the approval *resolution* mechanism
  (human-in-the-loop UI/API) is explicitly deferred to Developer Platform
  (M6.9) or a dedicated later milestone — flagged, not silently assumed.
- **Secret-field exclusion is a schema-authoring discipline, not a runtime
  guarantee.** A careless `ToolDescriptor.schema` could still declare a secret
  field. Mitigated by a registration-time linter check in `security.py`
  (M6.1B-2) that rejects known secret-shaped field names/patterns, but this is
  a heuristic, not a proof — documented as a residual risk, matching M5.8's own
  "known limitations, stated plainly" precedent.
- **Sandbox is a deployment concern documented, not enforced, in-runtime.** A
  process-level escape from a poorly-written adapter is out of this runtime's
  control. Mitigated by requiring third-party/community adapters to go through
  a review gate before registration (a process control, documented in the
  eventual Developer Platform SDK, not a Tool Runtime code control).
- **Tool result attachments (binary content) need a storage backend not yet
  chosen.** URI-referenced attachments (§13) assume some object store exists.
  Mitigated by deferring attachment storage backend selection to M6.1B-3,
  where it can reuse whatever temporary-repository storage backend Epic 5
  already uses, rather than inventing a new one now.

---

## 19. Recommendation

Approve this design and proceed to **M6.1B-1 (Tool Runtime Core)** next. The
design introduces no new extension mechanism beyond the three already
sanctioned in M6.0, touches zero Epic 5 packages, and implements a port that
has sat frozen and unexercised since M5.0 — the lowest-risk, most-precedented
starting point available for Epic 6 implementation.

**Stop — design only. No implementation performed. Epic 5 and Epic 6's M6.0
architecture doc remain untouched.**
