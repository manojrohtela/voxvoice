# VoxVoice — Durable Audit & Activity Platform (Epic 8, M8.9)

| | |
|---|---|
| **Document** | `docs/architecture/epic8/audit-activity-platform.md` |
| **Status** | 🟢 **Implemented (M8.9)** — hardening milestone. Replaces the in-memory activity stream with a durable, append-only Audit Store + projection read-model. Audit / Activity / Projection / Persistence architecture. |
| **Date** | 2026-07-28 |
| **Scope** | Durable audit store; activity store as projections; event persistence; projection builders (Organization/Workspace/Project/Recent/User); activity querying with **pagination + filtering + time-range**; API params + recent/user endpoints; Studio infinite-scroll + filters. **Storage change only** — business logic, ownership, and existing APIs unchanged. **Excluded:** Agent Builder, Knowledge, Deployment, Monitoring. |
| **Authority** | Realizes the [Implementation Plan](../../roadmap/epic8-implementation-plan.md) under ADR-0021/0022. **No new ADR, no architectural redesign, no ownership change.** All prior 1641 tests still green; frozen Epics 5/6/7 untouched. |
| **Related** | [Organization](organization-lifecycle.md) · [Workspace](workspace-lifecycle.md) · [Project](project-lifecycle.md) Lifecycle · [Foundation](customer-platform-foundation.md) · [Persistence](customer-platform-persistence-layer.md) · Constitution §A5 (one typed fact per producer) / §A11.2 (audit trail + isolation) · [INDEX](../../INDEX.md) |

## Problem & approach

M8.6–M8.8 emitted domain events into an **in-memory** `RecordingEventEmitter`; activity feeds
read that list. Correct, but **process-lifetime only** — history vanished on restart. M8.9
makes the audit stream **durable** with a **storage-only** change: the same `FoundationEvent`s
domain services already emit are now appended to a durable, append-only table, and activity
feeds are **projections** queried from it. No business logic, no ownership, and no existing API
contract changed.

The swap is a pure **seam substitution**, exactly the pattern the repositories use: the
Foundation defines the seams (write = `EventEmitter`, new read = `AuditReader`); the in-memory
default and the SQL implementation both satisfy them; the persistence composition root wires
the durable one in. Neither the Foundation domain services nor the Application know which
backing is live.

## Audit architecture

- **Write seam (unchanged):** `events.EventEmitter` — domain services emit exactly one
  immutable `FoundationEvent` per fact (§A5). **Not touched** this milestone.
- **Durable store:** `persistence.SqlAuditStore` implements `EventEmitter` — `emit()` appends
  one `cp_audit_event` **row** through the shared `Database.session`, so the audit row is part
  of the **same transaction** as the business write that caused it (the `SqlUnitOfWork` commits
  both together, or rolls both back). **Append-only, immutable:** the store exposes only
  `emit` (write) + `query`/`stream` (read) — no `update`, no `delete` (asserted by a test;
  §A5/§A11.2).
- **`cp_audit_event` schema:** `seq` (autoincrement PK → stable total order for pagination),
  `id`, `type`, `ts`, `org_id`, `actor`, `resource`, `workspace_id`, `project_id`, `data`
  (JSON). `workspace_id`/`project_id` are **hoisted out of the event payload** into indexed
  columns so workspace/project activity queries never scan JSON. Every filterable column is
  indexed.
- **Read seam (new, additive):** `audit.AuditReader` — `query(AuditQuery) → AuditPage` and
  `stream(org_id) → Iterable[AuditRecord]`. `AuditQuery` is an immutable filter
  (org/workspace/project/actor/event-types/resource/since/until + limit/offset); `AuditPage`
  carries the records **plus the total match count** for pagination (`has_more`/`next_offset`).
  The in-memory `RecordingEventEmitter` also implements it (over its own list) so the default
  Foundation keeps working unchanged.

## Projection architecture

`activity.ActivityProjectionService` (Foundation, read-model only) builds the five projections
by shaping `AuditQuery`s and running them through the injected reader — **no business logic, no
state of its own:**

| Projection | Filter |
|---|---|
| **Organization activity** | `org_id` |
| **Workspace activity** | `org_id` + (`workspace_id` OR `resource == workspace_id`) — workspace-level events **and** its projects' events |
| **Project activity** | `org_id` + (`project_id` OR `resource == project_id`) |
| **Recent activity** | `org_id` (org-wide landing feed) |
| **User activity** | `org_id` + `actor == user_id` |

All are **most-recent-first**, paginated, filterable by event type + time range. **Projections
are pure functions of the append-only log** — there is no separate materialized projection to
corrupt, so a *rebuild* is simply re-running the query over the durable events; a fresh process
over the same database reconstructs every projection identically (tested). `AuditReader.stream`
yields the whole org log oldest-first as the canonical rebuild basis.

The domain **summaries** (member counts, status) stay with the domain services; this platform
owns the **feed** only. The existing `/activity` responses keep `{summary, feed}` and gain
`{total, limit, offset, has_more, next_offset}` (additive).

## Persistence changes (additive only)

New append-only `cp_audit_event` table + `SqlAuditStore` (write + read) + **migration `0005`**
(create-table only; idempotent; no `ALTER`). `build_foundation` wires one `SqlAuditStore` as
**both** the Foundation's emitter and its `audit_reader` (replacing the in-memory
`RecordingEventEmitter` for the persistent app) — no interface change. Supported operations:
`append()` (via `emit`), `query()`, `stream()`, and projection **rebuild** (re-query the log).
Existing repositories, tables, and the Unit of Work are untouched.

## API changes (compatible, additive)

- The three existing activity routes (`GET …/activity` for org/workspace/project) gain
  **optional** query params — `limit`, `offset`, `actor`, `event_type` (repeatable), `since`,
  `until` — via one shared transport dependency (`activity_params.activity_filter`). Defaults
  reproduce the prior behaviour, so **no breaking change** (old callers unaffected; responses
  gain pagination fields only).
- Two **new** read-only endpoints on the org router:
  `GET /v1/organizations/{org_id}/activity/recent` and
  `GET /v1/organizations/{org_id}/activity/users/{user_id}`.
- Controllers stay transport-only (dispatch a query, serialize) — verified by the existing
  no-business-logic scan.

## Studio changes

The three activity feeds (Organization / Workspace / Project) now use one **reusable durable
activity feed** component with: an **event-type** filter, a **user** filter, a **time range**
(`datetime-local` since/until), and **infinite scroll** (a "Load more" button paging by offset,
showing `loaded / total`). Loading/empty/error states preserved. No business logic in the UI —
it calls the durable API.

## Performance

`seq` gives a stable index for ordering + pagination; `org_id`/`type`/`actor`/`ts`/
`workspace_id`/`project_id` are all indexed for efficient filtering; counts use `COUNT(*)` over
the filtered set (not row materialization). Projections are **incremental by construction**
(each query reads only its page). Deliberately **not** premature-optimized: no materialized
projection tables, no background workers yet — the append-only log + indexed columns is the
seam a future background/materialized rebuilder plugs into without an interface change.

## Testing

`tests/test_audit_platform.py` — 12 tests: Foundation read-model unit (filter/pagination/
time-range/actor over the in-memory reader), the five projections + cross-org isolation,
durable append/query/`stream`, **history survives restart** (fresh app over the same on-disk DB
→ full history), **large-history pagination** (81 events, every page walked, strictly-descending
`seq`, full non-overlapping coverage), **projection rebuild** from the durable log, an
**append-only** guard (store exposes no update/delete), the additive API params + recent/user
endpoints, **API history survives restart** over a file DB, and the Studio filter/infinite-scroll
controls served. **12/12 pass; 1653/1653 repository-wide; ruff clean; frozen Epics 5/6/7
untouched; every prior M8.2–M8.8 test green (storage swap preserved behaviour).**

## Architecture compliance

- No new ADR, no architectural redesign, no ownership change, no dependency inversion. Storage
  change only: business logic, validation, authorization, and existing API contracts unchanged.
- **Foundation** extended additively (new read seam + projection service + facade fields;
  `RecordingEventEmitter` gains the reader methods) — domain services and `services.py` are
  untouched. **Application** extended additively (existing activity queries gain an optional
  `filter` and now read the durable projection; two new read queries). **Persistence** additive
  (one new append-only table + store + migration). **API** compatible-additive.
- Append-only + immutable: audit records are never updated or deleted (Constitution §A5/§A11.2).
- Frozen Epics 5/6/7 byte-for-byte unchanged.

## Definition of Done

A customer can view Organization / Workspace / Project history, filter it (event type, user,
time range) and search/paginate it, and **reload after a restart without losing history** —
using only the Studio UI + Control Plane API. Proven by `test_history_survives_restart`,
`test_api_activity_survives_restart_over_file_db`, `test_api_activity_params_and_new_endpoints`,
and `test_large_history_pagination`.

## Known limitations / later hardening

- **Denial audits are transactional:** an `access.denied` event emitted inside a use-case Unit
  of Work is rolled back with the failed operation (so a denied write leaves no audit row).
  Persisting security-denial audits regardless of outcome needs a separate/autonomous audit
  transaction — named, not built here.
- No materialized projection tables or background rebuild workers yet (projections compute on
  read); the append-only log is the seam these plug into later.
- No JS test runner for the Studio infinite-scroll controls (verified via served-artifact +
  API-level pagination tests).
