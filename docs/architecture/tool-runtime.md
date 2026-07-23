# Tool Runtime (Epic 6, M6.1) — Implementation

| | |
|---|---|
| **Scope** | The execution layer for external capabilities an Agent invokes · **Status:** 🟡 Core implemented (M6.1B-1); policy/security/observability-engine/streaming/workflow/MCP/A2A integration are explicit non-goals, deferred to M6.1B-2+ |
| **Milestone** | M6.1A — Design (approved, [tool-runtime-design.md](epic6/tool-runtime-design.md)) · M6.1B-1 — Core (2026-07-24, additive to the repository; zero Epic 5 changes) |
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

## 11. Explicit Non-Goals (this milestone)

No policy engine. No approval workflow. No secrets manager. No observability engine (basic
counters only). No streaming. No async execution. No Workflow/Agent/MCP/A2A integration. No
Skills. No Hooks. No Loop Engine. No external SDK. Offline deterministic execution only — all
per the M6.1B-1 prompt's explicit scope. These are the next milestones (M6.1B-2 through
M6.1B-5, per the M6.1A design's breakdown).

## 12. Recommendation

Core implemented, tested, ruff-clean, zero Epic 5/M6.0/M6.1A regressions. Repository ready for
**M6.1B-2 — Tool Policy & Security** next (per the M6.1A design's own milestone breakdown).
