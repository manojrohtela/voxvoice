# Tool Runtime (Epic 6, M6.1) — Implementation

| | |
|---|---|
| **Scope** | The execution layer for external capabilities an Agent invokes · **Status:** 🟡 Core (M6.1B-1) + Governance (M6.1B-2) + Observability & Replay (M6.1B-3) implemented; streaming/cancellation/workflow/MCP/A2A integration are explicit non-goals, deferred to M6.1B-4+ |
| **Milestone** | M6.1A — Design (approved, [tool-runtime-design.md](epic6/tool-runtime-design.md)) · M6.1B-1 — Core (2026-07-24) · M6.1B-2 — Governance (2026-07-24) · M6.1B-3 — Observability & Replay (2026-07-24, additive to the repository; zero Epic 5 changes; zero `tool`/`tool.governance` core changes) |
| **Basis** | The frozen M5.0 `core.ports.tool.ToolPort` (unexercised until now); the closed [Provider Runtime](provider-runtime.md)'s registry/catalog/normalizer/repository/lifecycle shape, mirrored, not imported |
| **Governs** | `platform/tool/` |

> **Implementation milestone.** Implements exactly the approved M6.1A design's core scope. No
> redesign. No Epic 5 package touched. No Provider/Reasoning/Pipeline change.

---

## 1. Executive Summary

The **Tool Runtime** is the execution layer for external capabilities an Agent will invoke —
calendar, email, Slack, CRM, database, search, filesystem, browser, HTTP APIs, internal
services. M6.1B-1 delivers the **core**: an immutable, versioned `ToolManifest` (the contract,
never executable code) paired with a separately registered `ToolAdapter`; a `ToolRegistry`
(registration/discovery/lifecycle — no execution logic) and a separate `ToolCapabilityCatalog`
(no intelligence); structural manifest + argument validation; two independent `core.fsm`
lifecycles (descriptor + execution); the deterministic offline `EchoToolAdapter`; a temporary
execution repository; and `execute`/`get`/`remove`/`history`/`expire`/`statistics`. Policy,
approval, secrets, a full observability engine, streaming, and Workflow/MCP/A2A integration are
explicit non-goals in this milestone — reserved as additive extension interfaces only
(`tool/extensions.py`).

## 2. Runtime Ownership

**Tool Runtime owns (this milestone):** tool registration, discovery, descriptor validation,
manifest loading, capability catalog, execution (validate → dispatch → normalize), descriptor
lifecycle, execution lifecycle, metadata, result normalization, and a temporary execution
repository.

**Tool Runtime never owns:** planning, reasoning, workflow orchestration, agent state, prompt
generation, memory/knowledge, provider routing, policy/approval/rate-limiting (M6.1B-2),
secrets (M6.1B-2), an observability engine (M6.1B-3), streaming/cancellation (M6.1B-4), or
Workflow/MCP/A2A integration (M6.3/M6.6/M6.7). A dependency-rules test asserts the runtime's
public surface never exposes a `plan`/`reason`/`infer`/`orchestrate_workflow`/`render_prompt`-
shaped method.

## 3. Domain Model

- **`ToolManifest`** — the versioned, diffable contract only (`name`, `namespace`, `version`,
  `description`, `capabilities`, `inputs`/`outputs` — a minimal JSON-Schema-shaped mapping,
  `permissions`, `classification`, `tags`, `visibility`, `owner`, `metadata`). Never executable
  code. Identity is `(namespace, name, version)`; `qualified_name` is `namespace.name`.
- **`ToolDescriptor`** — loaded runtime metadata: the manifest plus descriptor-lifecycle state
  and a registration timestamp. Deliberately a distinct object from the manifest (§8 of the
  design) — the registry replaces the descriptor on a state transition, never the manifest.
- **`Classification`** — `READ_ONLY` / `READ_WRITE` / `DESTRUCTIVE` / `INTERNAL` / `EXTERNAL` /
  `STREAMING` (reserved) / `ASYNC` (reserved). Metadata only in this milestone — carried on
  every `ToolResult`, consumed by nothing yet; the M6.1B-2 policy engine will gate on it.
- **`ToolRequest`** — `request_id`, `tool`, `namespace`, `version` (optional — unpinned
  resolves to the highest ACTIVE version), `arguments`, `metadata`, and an inline
  `session_id`/`trace_id`/`org_id` request context. Deliberately **not** an import of
  `pipeline.RequestContext` — see §7.
- **`ToolResult`** — `state`, `success`, `output`, `error`, `classification`, `usage`
  (duration/units), `trace` (ordered lifecycle events), `attachments` (URI-referenced, a
  placeholder shape — no storage backend wired), metadata, and content-hash ids
  (`request_hash`/`result_hash`).

## 4. Descriptor Lifecycle (`core.fsm`)

```
DRAFT --register--> REGISTERED --activate--> ACTIVE --deprecate--> DEPRECATED --retire--> RETIRED
                          └─────────────retire──────────────────────────┘
                                          └──────────────retire (direct)──────────────┘
```

A tool may be withdrawn before or after activation without forcing an intermediate
`DEPRECATED` hop. `ToolRegistry.list()` (the default discovery surface) returns `ACTIVE` only;
`list(include_deprecated=True)` also surfaces `DEPRECATED` (callers pinned to an exact version
may still invoke it — see §5).

## 5. Execution Lifecycle (`core.fsm`)

```
RECEIVED --validate--> VALIDATING --execute--> EXECUTING --complete--> COMPLETED
             │                        │
             ├──fail──> FAILED <──fail┤
             └─cancel─> CANCELLED <───┴─cancel
```

No `AUTHORIZING` state in this milestone — authorization is the Policy Engine's job (M6.1B-2,
an explicit non-goal here). `CANCEL` transitions are defined and FSM-tested but not exercised
by the core execution path (the deterministic `EchoToolAdapter` never suspends) — mirroring the
Provider Runtime core precedent of shipping complete-but-partially-unexercised FSMs.

Execution flow: resolve descriptor (unknown tool → `FAILED: unknown_tool`, without dispatch) →
reject non-`ACTIVE`/`DEPRECATED` descriptors (`FAILED: tool_retired`, without dispatch) →
validate arguments against the manifest's `inputs` contract (`FAILED: validation_error`,
without dispatch) → dispatch to the registered `ToolAdapter` (an adapter exception becomes
`FAILED: execution_error`, never propagated raw) → normalize into a `ToolResult` → persist to
the temporary repository → emit `tool.*` events.

## 6. Registry, Catalog, Validation

- **`ToolRegistry`** — pure bookkeeping over `(namespace, name, version)`. No execution logic
  (asserted by a dependency test: the class exposes no `execute`/`invoke`/`run` method).
  Version resolution with no explicit version picks the highest `ACTIVE` version (numeric-aware
  dotted-segment comparison, no external semver dependency); falls back to the highest
  registered version of any state if none is `ACTIVE`, so an exact-version lookup can still
  discover a withdrawn tool without ever silently invoking one via unpinned resolution.
- **`ToolCapabilityCatalog`** — a separate, read-only object (never `ToolRegistry` itself):
  `capabilities_for`, `supports`, `discover(capability)`, `catalog()`. No scoring/ranking (that
  is an Agent Runtime planning concern, not built here).
- **`validation.py`** — hand-rolled, dependency-free structural validation (no `jsonschema`
  package): `validate_manifest` (fail-fast at registration time) and `validate_arguments`
  (required-field + primitive-type checking against the manifest's minimal `type`/`properties`/
  `required` input contract). Unknown/extra arguments are not rejected in this milestone.

## 7. Key Design Clarifications (recorded during implementation)

- **No literal `core.ports.tool.ToolPort` conformance yet.** The frozen port's
  `register`/`list`/`invoke` shape is deliberately **not** implemented directly by
  `ToolRuntime` in this milestone — mirroring the Provider Runtime core precedent of not
  building `ProviderModelBridge` at M5.6B-1 (DL-0038). `ToolRuntime`'s own richer
  `register`/`list`/`execute` surface already covers the same semantics; a thin conformance
  shim is additive, deferred work (likely M6.7 Agent Platform Integration), not a redesign.
- **`ToolRequest` does not import `pipeline.RequestContext`/`PipelineCancellation`.** The
  M6.1A design's "reuse `PipelineCancellation`/`RequestContext` from M5.7" is read as *reuse
  the pattern*, not *cross-import the module* — every closed Epic 5 runtime duplicates its own
  `_run.py`/`ids.py`/config/serializer locally rather than importing a sibling's (a recorded
  M5.8 technical-debt item, not a new decision). Concretely: `tool` importing `pipeline` would
  create a cycle, since `pipeline` is the downstream composition root that will import `tool`
  once Agent Runtime exists — never the reverse. `ToolRequest` therefore carries its own
  minimal inline `session_id`/`trace_id`/`org_id` fields instead.
- **Reuses the existing `tool.*` `EventType`s exactly as anticipated at M5.0** —
  `TOOL_REQUESTED`/`TOOL_COMPLETED`/`TOOL_FAILED` with a `phase` discriminator (`received` →
  `validating` → `executing` → `completed`/`failed`), `EventSource.TOOL`. No bus change.
  Descriptor-lifecycle changes (register/activate/deprecate/retire) are bookkeeping, not
  content events, and are not published in this milestone.

## 8. Package Structure

```
platform/tool/
├── domain.py            # ToolManifest, ToolDescriptor, ToolRequest, ToolResult, both FSMs' states
├── ids.py                # content-hash ids: tl_ / tc_ / tr_
├── lifecycle.py          # descriptor FSM + execution FSM (core.fsm-based)
├── registry.py           # ToolRegistry — register/activate/deprecate/retire/list/find, no execution
├── catalog.py            # ToolCapabilityCatalog — capability-tag discovery, separate from registry
├── validation.py         # manifest + argument structural validation (no jsonschema dependency)
├── ports.py              # ToolAdapter (abstract), ToolExecutionOutcome
├── results.py            # ResultNormalizer — ToolExecutionOutcome -> ToolResult
├── events.py             # ToolPhase (reuses existing tool.* EventTypes)
├── observability.py      # Metric constants (basic counters only)
├── extensions.py         # Policy/Approval/Secrets/Streaming/Workflow/MCP/A2A/Observability interfaces
├── config.py             # ToolConfiguration (validated, no credentials)
├── serializer.py         # ToolSerializer (manifest/request/result <-> dict; event payloads)
├── factory.py            # ToolFactory.create/default (composition root)
├── runtime.py             # ToolRuntime(BusConsumer) — execute/get/remove/history/expire/statistics
├── _run.py                # local sync<->async bridge (duplicated per package, per precedent)
├── repositories/
│   └── tool_repository.py   # temporary repository (DocumentStore-backed; results only)
├── stores/
│   ├── echo_adapter.py   # EchoToolAdapter — deterministic offline default
│   └── local_stores.py   # InMemoryDocumentStore
└── __init__.py
```

## 9. Dependency Graph

```
tool → core
```

Acyclic, AST-verified (`test_tool_dependency_rules`): `tool` never imports `runtime`, `api`,
`gateway`, `replay`, `events` (the bus implementation), `memory`, `knowledge`, `context`,
`prompt`, `reasoning`, `provider`, `bridge`, `pipeline`, `voicebot`, `observability`, or
`tenant`. No sibling-runtime import; no cross-import of the future composition root.

## 10. Testing

`tests/test_tool_runtime.py` — 62 tests: manifest immutability + validation, descriptor/
manifest separation, classification (metadata-only carry-through), argument validation
(missing/mistyped/extra fields), both FSMs (happy path, illegal transitions, fail-from-any-
active-state, cancel-from-any-pre-terminal-state, terminal-state rejection), registry
(registration, duplicate rejection, version resolution — explicit pin / unpinned-highest-
active / no-active-fallback, namespacing isolation, lifecycle-filtered discovery, capability
lookup, scoped discovery, no-execution-logic-leakage), the capability catalog (separate-object
identity, capability/discovery/full-catalog queries), the `EchoToolAdapter` (determinism,
default-registration), execution (happy path, unknown tool, retired tool, deprecated-still-
invocable, validation failure, adapter-exception normalization, determinism, result-hash
sensitivity to arguments), the temporary repository (get/remove/idempotent-remove, session
history ordering, TTL expiry sweep), serialization round-trips (manifest/request/result),
configuration validation, replay/gateway-compatible standard envelopes (including a failed-
execution event check), full-cycle determinism, runtime module lifecycle, no-content-ingestion,
dependency/boundary rules, and no ownership leakage into planning/reasoning/workflow/prompt.

**698/698 repository tests pass** (636 prior Epic 5 + 62 new). Ruff clean across `tool/` and
its test file — the only ruff findings anywhere in the repository are the 2 pre-existing,
out-of-scope `voicebot/bot.py` findings certified at M5.8 (predate Epic 5, git-blamed to the M0
foundation-freeze commit).

## 11. Explicit Non-Goals (M6.1B-1)

No policy engine. No approval workflow. No secrets manager. No observability engine (basic
counters only). No streaming. No async execution. No Workflow/Agent/MCP/A2A integration. No
Skills. No Hooks. No Loop Engine. No external SDK. Offline deterministic execution only — all
per the M6.1B-1 prompt's explicit scope. Governance landed at M6.1B-2 (§13-19 below); the rest
remain the next milestones (M6.1B-3 through M6.1B-5, per the M6.1A design's breakdown).

## 12. M6.1B-1 Recommendation

Core implemented, tested, ruff-clean, zero Epic 5/M6.0/M6.1A regressions.

---

## 13. M6.1B-2 — Tool Runtime Governance

New additive `platform/tool/governance/` **subpackage of `tool`** (not a sibling runtime):
`ToolGovernance`, the facade tying tenancy → policy → authorization → feature gates → rate
limiting → approval into one deterministic `evaluate()` call producing a `GovernanceDecision`
(`ALLOW`/`DENY`/`PENDING`) + a full `ToolGovernanceTrace`. **Never executes a tool** —
`tool/runtime.py` (`ToolRuntime`) is untouched by this milestone; wiring `ToolGovernance` into
the execution path via the already-frozen `tool.extensions.PolicyEngine`/`ApprovalEngine`/
`SecretsManager` seams is additive, deferred integration work.

## 14. Governance Domain Model

- **`ToolPolicyEngine`** (implements `tool.extensions.PolicyEngine`) — deterministic six-level
  precedence: `SYSTEM → ORGANIZATION → WORKSPACE → AGENT → SKILL (reserved) → TOOL`. Conflict
  resolution (documented once, in `policies.py`, and here): a `DENY` at *any* level is absolute
  and can never be overridden by a narrower `ALLOW`/`PENDING` (fail-safe); absent a `DENY`, the
  most specific applicable rule wins (last-match-in-precedence-order, i.e. `TOOL` beats
  `SYSTEM`); absent any applicable rule, the default is `ALLOW` (open-unless-restricted, the
  same convention `ProviderAccessPolicy`'s empty allow-list already uses).
- **`ToolApprovalEngine`** (implements `tool.extensions.ApprovalEngine`) — offline, no UI/
  notifications. A 5-state FSM (`ApprovalState`: `PENDING → APPROVED/REJECTED/EXPIRED/
  CANCELLED`, all four direct from `PENDING`, `core.fsm`-based). One approval per request
  (content-hash `approval_id`).
- **`AuthorizationEngine`** + `GovernanceIdentity` (`USER`/`ORGANIZATION`/`WORKSPACE`/`SERVICE`/
  `AGENT` [future]) — a permissive `AllowAllAuthorizer` default, real RBAC/ABAC/OIDC/SSO
  reserved as extension interfaces only.
- **`EchoSecretsProvider`** (implements the existing `tool.extensions.SecretsManager` seam
  directly — no parallel interface) — deterministic, content-hashed placeholder token; never
  echoes a caller-supplied value back (there is none to echo).
- **`ToolTenancy`** — organization/workspace isolation, tool allow/deny-lists, regional
  restrictions, and manifest `Visibility` (`PUBLIC`/`ORG`/`PRIVATE`) gating. Runs *first*, before
  policy — mirrors `ProviderTenancy` running before provider selection.
- **`FeatureGateRegistry`** — deterministic, caller-supplied `{name: ENABLED|DISABLED|PREVIEW|
  INTERNAL}` mapping; ungated names default to `ENABLED`. No remote config.
- **`RateLimitEngine`** — a pure `evaluate_rate_limit` fixed-window function over an injected
  `RateLimitStore` (default `InMemoryRateLimitStore`); the caller always supplies `now` — no
  timers, no background sweep, no Redis.
- **`GovernanceRepository`** — temporary-only storage (mirrors `ToolRepository`) for approval
  records (mutable, FSM-advanced) and append-only policy/authorization/rate-limit evaluation
  logs.
- **`ToolGovernanceTrace`** — `tenant`/`policy`/`authorization`/`feature_gate`/`rate_limit`/
  `approval`. No execution data (that's `ToolResult`'s job).

## 15. Why No `tenant` Import (Key Clarification)

`tool/governance` does **not** import `tenant`, even though the M6.1A design anticipated
`tool → core[, tenant]`. Two reasons converge on the same answer: (a) `tool`'s own frozen
M6.1B-1 dependency test already asserts `tool` imports no `tenant`, and importing it now would
be exactly the reverse-dependency risk DL-0044 flagged for `pipeline`; (b) `GovernanceIdentity`
needed a richer, kind-tagged shape (user/organization/workspace/service/future-agent) than
widening the frozen M5.0 `tenant.TenantContext` would have allowed without touching a floor
package. So `governance/domain.py` locally duplicates the *shape* of `tenant.access`'s
permissive-default-hook pattern (`Effect`, `GovernanceIdentity`) — the same "duplicate, don't
cross-import" precedent already established for `_run.py`/`ids.py` across every closed runtime.
The dependency-rules test (`test_tool_governance_dependency_rules`) still forbids `tenant`.

## 16. Governance Events

Reuses the existing `Event`/`EventBus`/`EventSource.TOOL` envelope architecture unchanged. The
only change to `core.events`: nine additive `EventType` members (no existing member touched,
AR-32) — `TOOL_POLICY_EVALUATED`, `TOOL_APPROVAL_REQUESTED`, `TOOL_APPROVAL_GRANTED`,
`TOOL_APPROVAL_REJECTED`, `TOOL_APPROVAL_EXPIRED`, `TOOL_AUTHORIZATION_COMPLETED`,
`TOOL_RATE_LIMIT_EVALUATED`, `TOOL_SECRET_REQUESTED`, `TOOL_GOVERNANCE_COMPLETED`. Emission is
via a caller-injected `publish` callable (matching `BusConsumer.publish`'s signature) — `None` by
default, a safe no-op — since `ToolGovernance` is not itself a `BusConsumer` (it is a
stateless-per-call facade over injected engines + a repository, not a subscriber).

## 17. Governance Package Structure

```
platform/tool/governance/
├── domain.py          # PolicyLevel/Rule/Decision, ApprovalState/Record, GovernanceIdentity,
│                       #   Effect, AuthorizationDecision, TenantDecision, FeatureGate*,
│                       #   RateLimit*, ToolGovernanceTrace, GovernanceDecision
├── lifecycle.py        # ApprovalState FSM (core.fsm-based)
├── ids.py               # content-hash ids: ap_ / pe_ / az_ / rl_
├── policies.py           # ToolPolicies + ToolPolicyEngine (precedence + conflict resolution)
├── approval.py            # ToolApprovalEngine (implements tool.extensions.ApprovalEngine)
├── authorization.py        # IdentityAuthorizer, AllowAllAuthorizer, AuthorizationEngine
├── secrets.py                # EchoSecretsProvider (implements tool.extensions.SecretsManager)
├── tenancy.py                 # ToolTenancy — isolation + allow/deny-lists + visibility gating
├── feature_gates.py            # FeatureGateRegistry
├── rate_limit.py                 # RateLimitRule/State/Store + evaluate_rate_limit + Engine
├── config.py                      # GovernanceConfiguration (validated, no credentials)
├── serializer.py                   # GovernanceSerializer (round-trippable dict shapes)
├── repository.py                    # GovernanceRepository (temporary; DocumentStore-backed)
├── events.py                         # GovernancePhase -> EventType mapping
├── observability.py                   # Metric constants (basic counters only)
├── extensions.py                       # Enterprise seams: RBAC/ABAC/OIDC/SSO/Vault/KMS/... (interfaces only)
├── runtime.py                          # ToolGovernance — the facade (evaluate/approve/reject/...)
├── factory.py                          # GovernanceFactory.create/default (composition root)
└── __init__.py
```

## 18. Governance Testing

`tests/test_tool_governance.py` — 55 tests: policy precedence (deny-absolute, narrower-wins,
scoped-rule non-match, trail recording), the approval FSM (all 4 terminal transitions,
illegal-transition rejection, `status_for` narrow-seam mapping), authorization (default-allow,
custom-denial), the `EchoSecretsProvider` (deterministic, never echoes the raw key), tenancy
(deny-list, allow-list, org/region restriction, private/org visibility gating), feature gates
(ungated-enabled default, disabled/preview/internal opt-in semantics), rate limiting (allow-
then-deny, window reset, unbounded, non-mutating peek), the temporary repository (approval
round-trip, pending-filter), the `ToolGovernance` facade (allow/deny/pending paths, approve/
reject/cancel, TTL expiry sweep, feature-gate denial, rate-limit denial, secret request,
statistics), determinism (identical input -> identical decision; content-derived ids),
governance events (injected-publisher capture + no-publisher no-op), serialization round-trips,
configuration validation, dependency rules (no `tenant` import), and no-execution-ownership-
leakage (`ToolGovernance` exposes no `execute`/`invoke`/`run`/`aexecute`).

**753/753 repository tests pass** (698 prior + 55 new). Ruff clean across `tool/governance/` and
its test file — the only ruff findings anywhere in the repository remain the 2 pre-existing,
out-of-scope `voicebot/bot.py` findings certified at M5.8.

## 19. M6.1B-2 Recommendation

Governance implemented, tested, ruff-clean, zero Epic 5/M6.0/M6.1A/M6.1B-1 regressions
(`tool/runtime.py` untouched). Repository ready for **M6.1B-3 — Tool Runtime Observability &
Replay** next (per the M6.1A design's own milestone breakdown). Do not begin M6.1B-3 without an
explicit new commissioning prompt.

---

## 20. M6.1B-3 — Tool Runtime Observability & Replay

New additive `platform/tool/observability/` **subpackage of `tool`** (converted from the single
M6.1B-1 `observability.py` module — the legacy `Metric` class moved verbatim into `legacy.py`
and is still re-exported at the package's top level, so `tool/runtime.py`'s frozen
`from .observability import Metric` keeps resolving unchanged). Ties execution tracing,
governance-decision tracing, replay recording/validation, metrics, diagnostics, failure
analysis, and audit recording into one `ToolObservability` facade. **Never executes a tool,
never evaluates governance** — every value here is built *from* an already-finished
`ToolResult`/`GovernanceDecision`, never the other way around; `tool/runtime.py` and
`tool/governance/` are untouched by this milestone.

## 21. Observability Domain Model

- **`ExecutionTrace`** — a pure projection of a finished `ToolResult`: trace/request id, tool
  id/version/classification, FSM `transitions` (reconstructed deterministically — see §22),
  duration/start/end, `arguments_hash`/`output_hash`/`replay_hash` (content hashes only — never
  raw payloads), metadata.
- **`ToolGovernanceDecisionTrace`** — a pure projection of a `governance.GovernanceDecision`:
  policy verdict, `DecisionReason`, authorization verdict, approval state, tenant/feature-gate/
  rate-limit allowed flags, which secrets provider (if any) was consulted. No execution data.
- **`DecisionReason`** — `ReasonCode` + `SourceLevel` + optional `matched_rule` + `policy_version`
  + a mechanically-derived `explanation`. Deterministic by construction — never AI/free-text.
- **`ReplayRecord`** — the serialized `ToolRequest`, the governance + execution traces, the
  serialized `ToolResult`, and the `replay_hash` — everything needed to re-verify an execution
  later without a live registry/adapter reference.
- **`ReplayComparison`** — `matches` + `expected_hash`/`actual_hash` + named `mismatches`.
- **`FailureCategory`**/**`FailureAnalysis`** — a fixed, deterministic classification (§22): no
  AI, no heuristics.
- **`ToolAuditRecord`** — identity/org/workspace/tool/action/decision/timestamp + optional
  replay/trace references. Never secrets, arguments, or output.

## 22. Deterministic Reconstruction (Key Clarification)

`tool/runtime.py` (frozen at M6.1B-1) never populates `ToolResult.trace` (the embedded, always-
empty `tool.domain.ToolExecutionTrace` placeholder) and always leaves `ToolResult.state` as the
terminal FSM state (`COMPLETED`/`FAILED`) — the intermediate phase a failure occurred at is not
separately recorded on the result. Rather than touching the frozen runtime to add tracking, this
milestone reconstructs both **deterministically** from what already exists, verified directly
against `ToolRuntime.aexecute`'s own frozen control flow (read, not guessed) before writing
`reasons.classify_error`:

- `unknown_tool:` / `tool_retired` / `validation_error:` prefixes only ever occur during
  `VALIDATING` (before dispatch); `execution_error:` only ever occurs during `EXECUTING` (an
  adapter exception). These four are the *only* failure paths in the frozen runtime.
- `ExecutionTrace.transitions` is therefore reconstructed as `(received, validating, executing,
  completed)` on success, `(received, validating, failed)` for the three validation-phase
  failures, or `(received, validating, executing, failed)` for `execution_error`.
- `FailureAnalysis.category`/`.phase` use the exact same lookup table — one source of truth
  (`reasons.classify_error`), never duplicated logic.

A second clarification: `result.request_hash` (always correctly resolved by
`ToolRuntime._resolve`, which operates on an internal copy) is the *only* authoritative hash
source for `ExecutionTrace`/`ReplayRecord` — the caller-supplied `ToolRequest` object is never
mutated by the runtime, so its own `request_hash` field may still be empty. `replay.py` writes
`result.request_hash` into the stored `request_document` before persisting, so a later
`ReplayEngine.validate()` deserializes a request whose hash already matches what was used to
compute the original `output_hash`/`replay_hash` — without this, replay validation would spuriously
mismatch on every record (caught and fixed during this milestone's own smoke-testing).

## 23. ReplayEngine

Deterministic replay validation against **`EchoToolAdapter` only** — never a live registry
lookup, never a vendor SDK. `validate(record)` re-executes the deserialized request through
`EchoToolAdapter`, recomputes the hash chain, and compares it to the hash chain captured at
record time; `compare(a, b)` is a pure hash-equality check between two records (no re-execution).
Replaying a record whose *original* execution used a different (but still deterministic)
adapter than `EchoToolAdapter` correctly reports a mismatch — replay proves determinism of the
platform's own reference adapter, not of arbitrary registered tools (a test asserts this).

## 24. Metrics, Diagnostics, Statistics

- **`ToolRuntimeMetrics`** — in-memory counters only (executions/success/failure/cancelled
  [reserved]/replay counts/approval/policy/authorization counts, average/max latency, per-tool
  and per-classification usage). No Prometheus, no OpenTelemetry, no exporters.
- **`ToolRuntimeDiagnostics`** — a read-only aggregator (`build_diagnostics`) over an already-
  live `ToolRegistry`/`ToolCapabilityCatalog`, a `ToolRuntimeMetrics` snapshot, and caller-
  supplied governance/repository/replay counts — never mutates what it inspects.
- **`ToolRuntimeStatistics`** — pure functions (`compute_statistics`, `latency_distribution`)
  over a sequence of `ToolResult`/`ExecutionTrace`: per-tool/per-classification/per-namespace/
  per-organization counts, replay ratio, failure ratio, and a nearest-rank latency distribution
  (min/max/avg/p50/p95 — no external stats library).

## 25. Events, Repository, Extension Interfaces

Reuses the existing `Event`/`EventBus`/`EventSource.TOOL` envelope unchanged; the only change to
`core.events` is eight additive `EventType` members (`tool.execution.traced`,
`tool.replay.recorded/validated/mismatch`, `tool.audit.recorded`, `tool.diagnostics.generated`,
`tool.metrics.updated`, `tool.failure.analyzed` — no existing member touched). `ReplayRepository`
(temporary, `DocumentStore`-backed, mirrors `ToolRepository`/`GovernanceRepository`) stores
`ReplayRecord`s and `ToolAuditRecord`s only — no durable persistence. Nine enterprise extension
interfaces (`OpenTelemetryExporter`, `PrometheusExporter`, `GrafanaDashboardProvider`,
`CloudLoggingSink`, `EnterpriseSIEMSink`, `ExternalAuditSink`) are reserved, interface-only.

## 26. Observability Package Structure

```
platform/tool/observability/
├── legacy.py            # Metric (M6.1B-1, verbatim) — re-exported at top level
├── domain.py              # ReasonCode/SourceLevel/DecisionReason, ExecutionTrace, ReplayRecord,
│                           #   ReplayComparison, FailureCategory/Analysis, ToolAuditRecord
├── ids.py                  # content-hash ids: tt_ / rp_ / ad_
├── reasons.py                # classify_error, reason_for_decision, matched_rule_repr
├── trace.py                    # execution_trace_for (ExecutionTrace builder)
├── governance_trace.py           # governance_trace_for (ToolGovernanceDecisionTrace builder)
├── replay.py                       # replay_record_for (ReplayRecord builder)
├── replay_engine.py                 # ReplayEngine — validate/compare (EchoToolAdapter only)
├── metrics.py                         # ToolRuntimeMetrics (in-memory counters only)
├── failure.py                          # analyze_failure (fixed lookup table)
├── audit.py                             # audit_record_for (ToolAuditRecord builder)
├── statistics.py                         # compute_statistics, latency_distribution
├── diagnostics.py                          # ToolRuntimeDiagnostics + build_diagnostics
├── config.py                                # ObservabilityConfiguration (validated)
├── serializer.py                              # ObservabilitySerializer (versioned, round-trippable)
├── repository.py                                # ReplayRepository (temporary; DocumentStore-backed)
├── events.py                                      # ObservabilityPhase -> EventType mapping
├── extensions.py                                    # OTel/Prometheus/Grafana/SIEM/... (interfaces only)
├── runtime.py                                         # ToolObservability — the facade
├── factory.py                                          # ObservabilityFactory.create/default
└── __init__.py
```

## 27. Observability Testing

`tests/test_tool_observability.py` — 40 tests: `ExecutionTrace` (success/failure transitions,
no-raw-payload assertion, determinism), `DecisionReason`/`ToolGovernanceDecisionTrace` (allowed,
policy-denied-with-matched-rule, tenant-short-circuit, determinism/immutability), `ReplayRecord`
+ `ReplayEngine` (round-trip validation, tampered-record mismatch detection, two-record
comparison, EchoToolAdapter-only replay proof), `ToolRuntimeMetrics` (execution/failure/
classification tracking), `FailureAnalysis` (success->None, unknown-tool/validation
classification, runtime-error-prefix mapping), the `ToolObservability` facade (trace->metrics,
replay round-trip, unknown-replay error, audit never includes secrets, diagnostics, statistics,
injected-publisher event capture, no-publisher no-op), the temporary `ReplayRepository`,
serialization round-trips (execution trace/governance trace/replay record/reason/audit/failure
analysis), configuration validation, dependency rules (still no `tenant`/sibling-runtime import),
and no-execution-or-governance-ownership-leakage (`ToolObservability` exposes no `execute`/
`invoke`/`run`/`aexecute`/`evaluate`/`authorize`).

**793/793 repository tests pass** (753 prior + 40 new). Ruff clean across `tool/observability/`
and its test file — the only ruff findings anywhere in the repository remain the 2 pre-existing,
out-of-scope `voicebot/bot.py` findings certified at M5.8.

## 28. M6.1B-3 Recommendation

Observability & Replay implemented, tested, ruff-clean, zero Epic 5/M6.0/M6.1A/M6.1B-1/M6.1B-2
regressions (`tool/runtime.py` and `tool/governance/` untouched). Repository ready for
**M6.1B-4 — Tool Streaming & Cancellation** next (per the M6.1A design's own milestone
breakdown). Do not begin M6.1B-4 without an
explicit new commissioning prompt.
