# VoxVoice — Tool Framework (Epic 8, M8.11)

| | |
|---|---|
| **Document** | `docs/architecture/epic8/tool-framework.md` |
| **Status** | 🟢 **Implemented (M8.11)** — a provider-independent Tool Framework that lets published Agents declare, configure and validate tools. Tool Catalog / Configuration / Agent Integration architecture. |
| **Date** | 2026-07-31 |
| **Scope** | Tool catalog + registration + configuration (typed data), enable/disable, ordering, validation, version; agent integration (attach/detach/enable/disable/reorder/configure); durable per-tool activity; Studio screens; API; persistence; tests. **No tool execution, no LLM, no RAG, no deployment, no external calls.** **Excluded:** Conversation Runtime, Knowledge retrieval. |
| **Authority** | Realizes the [Implementation Plan](../../roadmap/epic8-implementation-plan.md) under ADR-0021/0022. **No new ADR, no architectural redesign, no ownership change.** All prior 1666 tests still green; frozen Epics 5/6/7 untouched. |
| **Related** | [Agent Builder](agent-builder.md) · [Project](project-lifecycle.md) Lifecycle · [Audit & Activity](audit-activity-platform.md) · [Foundation](customer-platform-foundation.md) · [Persistence](customer-platform-persistence-layer.md) · Constitution §A2.4/§A2.6/§A5/§A11.2 · [INDEX](../../INDEX.md) |

## Problem & approach

Published Agents need **tools** — declared, configured capabilities (an HTTP call, a webhook,
a database lookup, an MCP server, …) that a future Conversation Runtime will invoke. M8.11
delivers the **declaration + configuration + validation** half only: a Tool is a
configuration object owned by one Project, reusable across that project's agents, attached to
agents in an ordered, individually-toggleable list. **Nothing is executed** — the framework
knows nothing about any LLM, calls no external API, and runs no tool. Conversation Runtime (a
later milestone) consumes it.

The build is **reuse-first**. Tools live inside a Project, so they slot onto the earlier
milestones: **RBAC** (no new scope — writes authorize via workspace-scoped `PROJECT_MANAGE`,
reads via `PROJECT_READ`); the durable **Audit Platform** (M8.9) is the tool/attachment
history; and the **CQRS / Unit-of-Work / persistence** seams carry the new commands unchanged.
The Foundation grew only where a genuinely-new domain required it — additive and defaulted;
`services.py` and every existing service are byte-for-byte unchanged.

## Tool Framework architecture

- **Aggregates (new, `tool_domain.py`):** `Tool` (owned by one project; identity + category +
  status + enable flag + version + `ToolConfig`) and `AgentTool` (the attachment carrying
  ordering, an enable flag, and a per-agent config override). Frozen, slotted value objects.
- **Categories are metadata only:** `ToolCategory` = Knowledge / HTTP API / Webhook / Database /
  Function / MCP / Custom — no implementation is implied or wired.
- **Configuration is typed data** (`ToolConfig`): `config_schema` / `input_schema` /
  `output_schema` (JSON-schema-shaped dicts), `settings` (concrete values), `timeout_ms`,
  `RetryPolicy`, and `depends_on` — **no SDK object, no provider client** ever. It round-trips
  through one portable JSON column.
- **Tool execution is abstracted away entirely:** there is no execute path anywhere in the
  slice. The framework declares and validates; the config is the seam the future runtime reads.
- **Services (`tool_framework.py`):** `ToolCatalogService` (register/update/enable/disable/
  archive/restore/soft-delete + list/get/search) and `AgentToolService` (attach/detach/enable/
  disable/reorder/configure + list). Every write authorizes via `PROJECT_MANAGE`, persists
  through the repository interfaces, and emits a durable `FoundationEvent`.
- **Status + enable:** `ToolStatus` = `draft`/`published`/`archived`/`deleted`; `enabled` is a
  separate flag (a published tool can be disabled). Deleted/archived retain the row; deleted is
  hidden from every read and the name-uniqueness check.
- **Version:** a monotonic `version` counter, bumped on each **config** change (a metadata-only
  rename does not bump it). Tool history is the durable audit stream — no second history system.

### Validation (`validate_tool` + service checks)

Required name; a **known category**; **typed-data** schemas (each of config/input/output/settings
must be a dict — never an SDK object); a **positive timeout**; a non-negative retry count.
Duplicate-name (per project), org/workspace/project **isolation**, and **circular references**
are checked in the service against the repositories: `_check_no_cycle` overlays the candidate's
`depends_on` edges on the project's live tool-dependency graph and rejects any cycle (DFS).

## Agent integration

An Agent attaches multiple tools through `AgentTool`. `attach_tool` enforces **project
isolation** (only a tool from the agent's own project may be attached) and rejects duplicates;
`reorder_tools` requires exactly the currently-attached set and rewrites positions; per-attachment
`enabled` and `config_override` are independent of the underlying tool. Detach removes the join
row. **No attachment ever executes a tool.** Every operation emits an audit event
(`tool.attached`/`detached`/`attachment_enabled`/`attachment_disabled`/`attachment_configured`/
`reordered`).

## Layer changes (every Foundation change justified)

- **Foundation (additive, required):** new `tool_domain.py` (`Tool`/`AgentTool` + `ToolConfig`/
  `RetryPolicy` + enums) and `tool_framework.py` (`ToolCatalogService` + `AgentToolService` +
  `validate_tool`); new `ToolRepository`/`AgentToolRepository` (+ in-memory); new `ToolView`/
  `ToolSummaryView`/`AgentToolView` DTOs; additive `FoundationEventType.tool.*`, id prefixes
  (`tool`/`atl`), and three diagnostic codes; facade + factory gain `tools`/`agent_tools` and
  two defaulted repo params. **Why required:** a Tool and an Agent↔Tool attachment are new
  control-plane aggregates — they cannot reuse an existing aggregate, and a domain service + its
  repository interface are domain-owned. **Why safe:** additive and defaulted; `services.py` and
  all existing aggregates untouched (all 1666 prior tests pass).
- **Application (additive):** new `tool_framework.py` — commands, queries, use cases (one Unit
  of Work per mutation), registered into the existing buses; additive `tool.*` application-event
  types.
- **Persistence (additive):** new `cp_tool` + `cp_agent_tool` tables (config/override as portable
  JSON), mapping, `SqlToolRepository`/`SqlAgentToolRepository`, **migration 0007** (create-table
  only, idempotent), composition wiring. Existing tables/repos/UoW untouched.
- **API (additive, thin):** two routers — the tool catalog under `…/projects/{id}/tools` and the
  attachment under `…/agents/{agent_id}/tools`. Controllers stay transport-only (verified by the
  no-business-logic scan).
- **Studio (additive):** Tool Catalog + Library + Details + Configuration cards on the project,
  and an Agent Tool Assignment + Ordering section (with validation-error surface) inside the
  agent editor. No business logic in the UI.

## API surface (all additive)

```
# tool catalog (within a project)
POST   …/projects/{pid}/tools                       register
GET    …/projects/{pid}/tools                       list (include_archived)
GET    …/projects/{pid}/tools/search?q=             search
GET    …/projects/{pid}/tools/{id}                  get
PATCH  …/projects/{pid}/tools/{id}                  update (config_patch; bumps version)
DELETE …/projects/{pid}/tools/{id}                  soft-delete
POST   …/tools/{id}/enable | /disable | /archive | /restore
GET    …/tools/{id}/activity                        durable tool history (M8.9)
# agent↔tool attachment (within an agent)
POST   …/agents/{aid}/tools                         attach
GET    …/agents/{aid}/tools                         list (ordered)
POST   …/agents/{aid}/tools/reorder                 reorder
DELETE …/agents/{aid}/tools/{tool_id}               detach
POST   …/agents/{aid}/tools/{tool_id}/enable | /disable
PATCH  …/agents/{aid}/tools/{tool_id}               configure (per-agent override)
```

## Testing

`tests/test_tool_framework.py` — 10 tests: Foundation unit (create/update + **version bump**,
duplicate-name + validation, **circular-reference** rejection, enable/disable/archive/restore/
soft-delete, agent **attach/detach/enable/reorder** + **project isolation**), API + persistence
**Definition-of-Done** end-to-end (create reusable tools → configure → attach to an agent →
enable/disable → reorder → view history), **history survives restart** over a file DB,
authorization failure for a viewer, **cross-project + cross-org isolation**, and the Studio tool
screens served. **10/10 pass; 1676/1676 repository-wide; ruff clean; frozen Epics 5/6/7
untouched; every prior M8.2–M8.10 test green.**

## Architecture compliance

- No new ADR, no redesign, no ownership change. Tools are authoring/management config the
  control plane owns (ADR-0021); the framework never actuates, never imports a frozen runtime,
  and **knows nothing about any LLM**.
- **Tool execution abstracted behind interfaces:** there is no execute path; configuration is
  typed data (no SDK objects / provider clients). Conversation Runtime is the future consumer.
- **Reuse-first:** no new RBAC scope; history reuses the durable M8.9 Audit Platform; CQRS/UoW/
  persistence seams unchanged. The Foundation grew only where a new domain required it.
- Determinism (§A2.4), fail-fast validation (§A2.6), one typed event per fact (§A5), org+
  workspace+project isolation on every query (§A11.2). Frozen Epics 5/6/7 byte-for-byte unchanged.

## Definition of Done

A customer can, using only the Studio UI + Control Plane API: create reusable tools, configure
them, attach them to Agents, enable/disable them, reorder them, and view tool history — **with no
tool execution**. Proven by `test_definition_of_done_end_to_end`,
`test_agent_attachment_reorder_and_isolation`, `test_history_survives_restart_over_file_db`, and
`test_studio_ui_serves_tool_screens`.

## Known limitations / later hardening

- **No execution** — by design. Tools are declared, configured and validated; running them is
  the future Conversation Runtime's job (the config is the seam it consumes).
- **PROJECT-granular RBAC** still deferred (from M8.8) — tool writes are workspace-scoped.
- Circular-reference detection covers `depends_on` among a project's tools; there is no cross-
  project composition (tools are project-scoped), so that graph is the whole surface.
- No JS test runner for the Studio tool screens (verified via served-artifact + API tests).
