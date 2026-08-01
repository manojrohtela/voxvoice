# VoxVoice — Agent Builder (Epic 8, M8.10)

| | |
|---|---|
| **Document** | `docs/architecture/epic8/agent-builder.md` |
| **Status** | 🟢 **Implemented (M8.10)** — the first AI-native customer feature. Design/configure/validate/version/publish **Agents** (configuration objects inside a Project). Agent Builder / Versioning / Prompt Builder architecture. |
| **Date** | 2026-07-31 |
| **Scope** | Agent CRUD (create/update/archive/restore/duplicate/soft-delete), **publish/unpublish/rollback** with immutable versions, full agent configuration (identity/prompt/**provider-independent** model + voice/typed variables incl. **secrets**), validation (required/prompt-size/variable-refs/model+voice compatibility/duplicate-name/isolation), version history + comparison, per-agent durable activity, Studio screens, API, persistence, tests. **No runtime, no conversations, no deployment, no LLM calls.** **Excluded:** Conversation Runtime, Knowledge, RAG, Deployment, Monitoring. |
| **Authority** | Realizes the [Implementation Plan](../../roadmap/epic8-implementation-plan.md) under ADR-0021/0022. **No new ADR, no architectural redesign, no ownership change.** All prior 1653 tests still green; frozen Epics 5/6/7 untouched. |
| **Related** | [Project](project-lifecycle.md) · [Workspace](workspace-lifecycle.md) · [Organization](organization-lifecycle.md) Lifecycle · [Audit & Activity](audit-activity-platform.md) · [Foundation](customer-platform-foundation.md) · [Persistence](customer-platform-persistence-layer.md) · Constitution §A2.4/§A2.6/§A5/§A11.2 · [INDEX](../../INDEX.md) |

## Problem & approach

An **Agent** is the first customer-authored, AI-native object: a bundle of prompt + model +
voice + variables that will one day drive a conversation. M8.10 delivers the **authoring**
half only — an Agent is a **configuration object** that belongs to exactly one Project. It is
designed, validated, versioned and published; it is **never run** (no runtime, no
conversations, no deployment, no LLM calls — those are later, separate milestones).

The build is **reuse-first** (the milestone architecture rule). Agents live inside a Project,
so they slot onto everything the earlier milestones already own: **RBAC** (no new scope —
writes authorize via the existing workspace-scoped `PROJECT_MANAGE`, reads via `PROJECT_READ`);
the **durable Audit Platform** (M8.9) is the one history system — versioning + activity are
projections over it; and the **CQRS / Unit-of-Work / persistence** seams carry the new
commands unchanged. The Foundation grew only where a genuinely-new domain required it, and
every addition is additive and defaulted — `services.py` and every existing service are
byte-for-byte unchanged.

## Agent Builder architecture

- **Aggregates (new, `agent_domain.py`):** `Agent` (owned by one project; identity + status +
  version pointers + `AgentConfig`) and `AgentVersion` (an immutable publish snapshot).
  `AgentConfig` nests `PromptConfig` / `ModelConfig` / `VoiceConfig` / a tuple of
  `AgentVariable`s — all frozen, slotted value objects with `as_dict`/`from_dict` so the whole
  config round-trips through one JSON column.
- **Provider independence:** `ModelConfig`/`VoiceConfig` record an opaque `provider`/`model`
  string pair plus capability flags (streaming, structured output, reasoning mode) and sampling
  ranges. **Nothing binds to a vendor** — a new provider needs no redesign (Constitution
  Part-A vs Part-B separation).
- **Service (`agent_builder.py`):** `AgentBuilderService` owns the lifecycle
  (create/update/archive/restore/duplicate/delete + publish/unpublish/rollback) and reads
  (get/list/search/versions/compare). Every write authorizes via `PROJECT_MANAGE`, persists
  through the repository interfaces, and emits a durable `FoundationEvent`.
- **Soft delete + status:** `AgentStatus` = `draft` / `published` / `archived` / `deleted`.
  `deleted` and `archived` retain the row (audit/isolation); archived is restorable; deleted is
  hidden from every read and name-uniqueness check.
- **Secrets:** a `SECRET` variable keeps its value in the domain (so it round-trips through
  persistence for later runtime) but is **redacted at every read boundary** — `AgentView`,
  `AgentSummaryView`, and even the version-snapshot view strip secret values (`value: ""`,
  `has_value: true`). No API ever emits a secret (tested twice).

### Validation (`validate_agent`, deterministic, never raises)

Required name; **prompt size** cap (system + persona + instructions ≤ 20 000 chars);
**variable references** — every `{{name}}` in the prompt must resolve to a standard variable
(`company_name`/`support_email`/`website`/`business_hours`/`timezone`/`locale`) or a declared
one; **model compatibility** — temperature ∈ [0, 2], top_p ∈ [0, 1], positive max_tokens,
provider⇒model, structured-output⇒model; **voice compatibility** — when enabled, provider +
voice required and rate/volume in range. Duplicate-name and cross-project isolation are checked
in the service (they need the repositories). Findings are canonically-ordered
`FoundationDiagnostics`; the service raises `FoundationValidationError` on any error.

## Versioning architecture

Publish is the only thing that mints a version. `publish_agent` validates, supersedes the
previously-current published version (a single status flip — the only mutation a version ever
sees), and appends a new immutable `AgentVersion` whose `snapshot` is the full config +
descriptive fields at that instant. `version` / `published_version` pointers on the agent track
the latest and the live one.

**Rollback never rewrites history.** `rollback_agent(target)` restores the config from
`target`'s snapshot and **publishes it as a brand-new version** (with `rollback_target` recorded)
— so a rollback is itself an auditable forward step (v1 → v2 → *rollback to v1* → v3 whose config
equals v1's). `compare_versions` produces a deterministic dotted-key diff between any two
snapshots. **Version history reuses the durable Audit Platform** — every publish/rollback emits
`agent.published`/`agent.rolled_back`/`agent.version_created` into the M8.9 stream, and an
agent's activity feed is `ActivityProjectionService.resource_activity(org, agent_id)` — one
history system, not a second one, exactly as the prompt mandates.

## Layer changes (every Foundation change justified)

- **Foundation (additive, required):** new `agent_domain.py` (aggregates + config value objects
  + enums) and `agent_builder.py` (service + `validate_agent`); new `AgentRepository` /
  `AgentVersionRepository` interfaces + in-memory impls; new `AgentView`/`AgentSummaryView`/
  `AgentVersionView` DTOs; additive `FoundationEventType` members (`agent.*`), id prefixes
  (`agt`/`agv`), diagnostic codes (duplicate-name/prompt-too-large/variable-ref/model/voice),
  and one additive `ActivityProjectionService.resource_activity` method; facade + factory gain
  `agent_builder` and two defaulted repo params. **Why required:** an Agent is a new domain
  aggregate the control plane owns — it cannot be expressed by reusing an existing aggregate,
  and a domain service + its repository interface are domain-owned. **Why safe:** every change
  is additive and defaulted; `services.py` and all existing services/aggregates are untouched
  (verified by all 1653 prior tests passing).
- **Application (additive):** new `agent_builder.py` — commands, queries, use cases (one Unit of
  Work per mutation), registered into the existing buses; additive `agent.*` application-event
  types. M8.3 modules untouched.
- **Persistence (additive):** new `cp_agent` + `cp_agent_version` tables (config/snapshot as
  portable JSON), mapping, `SqlAgentRepository`/`SqlAgentVersionRepository`, **migration 0006**
  (create-table only, idempotent), composition wiring. Existing tables/repos/UoW untouched;
  `cp_agent_version` is append-only by contract (insert + supersede-flip only).
- **API (additive, thin):** new `agent_builder.py` router under
  `…/projects/{project_id}/agents` (create/list/search/get, patch, archive/restore/duplicate/
  delete, publish/unpublish/rollback, versions/compare/activity); new Pydantic schemas.
  Controllers stay transport-only (verified by the no-business-logic scan).
- **Studio (additive):** the Agent list, editor (identity + prompt builder with live counter +
  preview + model + voice), variable manager, publish/version-history + rollback, and per-agent
  activity — one Agents panel bound to the open project. No business logic in the UI.

## API surface (all additive)

```
POST   …/projects/{pid}/agents                      create
GET    …/projects/{pid}/agents                      list (include_archived)
GET    …/projects/{pid}/agents/search?q=            search
GET    …/projects/{pid}/agents/{id}                 get (secrets redacted)
PATCH  …/projects/{pid}/agents/{id}                 update (config_patch merges)
DELETE …/projects/{pid}/agents/{id}                 soft-delete
POST   …/agents/{id}/archive | /restore | /duplicate
POST   …/agents/{id}/publish | /unpublish | /rollback
GET    …/agents/{id}/versions                       version history
GET    …/agents/{id}/versions/compare?from=&to=     diff
GET    …/agents/{id}/versions/{number}              one version (secrets redacted)
GET    …/agents/{id}/activity                       durable per-agent history (M8.9)
```

## Testing

`tests/test_agent_builder.py` — 13 tests: Foundation unit (create/update + **config merge**,
duplicate-name, validation of prompt/variable/model, **secret redaction**, publish +
**immutable version + rollback**, archive/restore/soft-delete, duplicate), API + persistence
**Definition-of-Done** end-to-end (create multiple agents → configure prompt/model/voice/
variables → save draft → publish → browse version history → roll back), **agent activity reuses
the audit platform**, **history survives restart** over a file DB, authorization failure for a
viewer, **cross-project + cross-org isolation**, and the Studio agent screens served.
**13/13 pass; 1666/1666 repository-wide; ruff clean; frozen Epics 5/6/7 untouched; every prior
M8.2–M8.9 test green.**

## Architecture compliance

- No new ADR, no redesign, no ownership change. Agents are authoring/management config the
  control plane owns (ADR-0021); the service never actuates and never imports a frozen runtime.
- **Reuse-first:** no new RBAC scope (workspace-scoped `PROJECT_MANAGE`/`PROJECT_READ`);
  versioning + activity reuse the durable M8.9 Audit Platform; CQRS/UoW/persistence seams
  unchanged. The Foundation grew only where a new domain required it — additive and defaulted.
- Determinism (§A2.4) via the `Clock`/`IdGenerator` seams; fail-fast validation (§A2.6);
  org+workspace+project isolation on every query (§A11.2); one typed event per fact (§A5).
- Frozen Epics 5/6/7 byte-for-byte unchanged (git: no tracked source modified).

## Definition of Done

A customer can, using only the Studio UI + Control Plane API: open a Project, create multiple
Agents, configure prompts / models / voice / variables, save a draft, publish, browse version
history, and roll back to a previous version — **with no runtime execution**. Proven by
`test_definition_of_done_end_to_end`, `test_publish_creates_immutable_version_and_rollback`,
`test_history_survives_restart_over_file_db`, and `test_studio_ui_serves_agent_screens`.

## Known limitations / later hardening

- **No runtime** — by design. Prompts are validated and previewed, never executed; the config
  is the seam the future Conversation Runtime consumes.
- **Denial audits are transactional** (inherited from M8.9) — an `access.denied` inside a
  use-case Unit of Work rolls back with the failed op.
- **PROJECT-granular RBAC** still deferred (from M8.8) — project/agent tiers are recorded, and
  authorization is workspace-scoped; a `PROJECT` RoleScope is the named follow-up.
- No JS test runner for the Studio agent screens (verified via served-artifact + API tests).
