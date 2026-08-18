# ADR-0034 — Durable storage seam for `core`-only edge packages: a scoped persistence sub-package, never a widened import budget

| | |
|---|---|
| **Status** | **Accepted 2026-08-18** (ratified at M10.1). Recommended by the Epic 10 **M10.0 Blueprint** (accepted; not yet persisted to `docs/architecture/epic10/`) and settled at the **M10.0a** owner decision gate (correction C-3, decisions OD-2/OD-4/OD-5); this milestone ratifies it. |
| **Amends** | Introduces the structural seam by which a `core`-only edge package acquires durable persistence. Amends no Constitution Part-A/Part-B section and no frozen Epic. It **introduces no new top-level directory** — Constitution §A14 already assigns the outbox to `events/` — so it is not an ADR-0018/0022/0031-style placement record but the dependency-direction record C-3 identified as the real question. |
| **Deciders** | Technical Owner |
| **Related** | [ADR-0031](ADR-0031-billing-plane-repository-placement.md) (**IR1a precedent**: scoped `sqlmodel`/`sqlalchemy` exception + own `Database`), [ADR-0032](ADR-0032-event-durability-and-delivery-semantics.md) (durability semantics), [ADR-0033](ADR-0033-metric-stream-seam.md) (metric-stream seam), [ADR-0009](ADR-0009-workflow-durable-repository-exception.md) (durable-repository precedent), [ADR-0006](ADR-0006-single-system-of-record.md), Constitution §A14 (folder map) / §A4.1 (layering) / §A12 (additive extension), CODING_STANDARDS §2 (folder map not locally negotiable), ARCHITECTURE_RULES AR-10 (`core/` imports nothing above it) / AR-14 / AR-15 (acyclic) |

## Status

**Accepted 2026-08-18**, ratified at milestone **M10.1**. Authorizes no code. It fixes where and how
durable storage attaches to a `core`-only package, binding every Epic 10 implementation milestone
(M10.2, M10.4–M10.7). Migrations `0001`–`0033` are untouched; `platform/billing/**` unchanged.

## Context

`platform/events/` sits at `core ← events ← runtime` and, apart from the M1 webhook module's
`httpx`, imports `core` + stdlib. A durable event log needs persistence. The repository's SQL
infrastructure lives in `platform/api/persistence/`. Naively reaching for it would produce
`events → api`, inverting the DAG and breaking AR-14's intent.

**C-3 established that the obvious framing was wrong.** Constitution §A14 already assigns both of
Epic 10's homes:

```
│   ├── events/          # Event Bus implementations, outbox, webhook dispatcher
│   ├── observability/   # metric taps, tracing, cost meter, log configuration
```

The **outbox is already constitutionally owned by `events/`**. Epic 10 introduces **no new
top-level directory**, so CODING_STANDARDS §2's *"New top-level directories require an ADR"* — the
trigger behind ADR-0018, ADR-0022 and ADR-0031 — does not fire. `events*` and `observability*` are
already in `pyproject.toml`'s `packages.find` include list; no packaging change is implied either.

The real question is narrower and outlives Epic 10: **how does a package confined to `core` +
stdlib acquire durable persistence without inverting the dependency graph?**

That question already has a ratified answer in this repository. ADR-0031 §A2 created **IR1a**:

> "New **IR1a**: `billing/persistence/**` may additionally import `sqlmodel`/`sqlalchemy`. No other
> billing module may. … **Storage cannot be implemented under 'core + stdlib only'.** The exception
> is **scoped to one sub-package** and has its own assertion; the boundary test was **tightened into
> per-layer checks, not relaxed**, so `billing/foundation` and `billing/application` keep IR1 at
> full strength."

And `billing/persistence/database.py` records the companion move:

> "Billing brings its own `Database` rather than importing the Epic 8 one because ADR-0031 IR3
> forbids `billing` importing `api` — and because it is what keeps the plane extractable into its
> own service and its own database later as a deployment decision rather than a re-architecture."

This ADR generalizes that proven pattern to `core`-only edge packages and applies it to `events/`.

## Decision

### DS1 — This is a separate ADR from ADR-0032

ADR-0032 decides **semantics** (at-least-once, ordering, append-only, outbox, retry, DLQ,
quarantine, retention intent). ADR-0034 decides **structure** (where storage lives, what may import
what, engine, tables, migrations). They are kept apart deliberately: the semantic contract must be
readable without knowing the storage engine, and the structural precedent outlives this epic
(**OD-2**). Neither is folded into the other.

### DS2 — Scope: the durable storage seam for `core`-only edge packages

This ADR governs how a package whose declared budget is `core` + stdlib obtains durable storage.
Epic 10 applies it to `events/`. Any future `core`-only edge package (`gateway/`, `replay/`, …)
needing durability follows this same shape or writes a superseding ADR — it does not improvise.

### DS3 — The exception is limited to `platform/events/persistence/**`

Durable storage for the event spine lives in a **new sub-package `platform/events/persistence/`**.
The exception this ADR grants applies **there and nowhere else**.

### DS4 — `sqlmodel` / `sqlalchemy` are permitted **only** within that sub-package

`platform/events/persistence/**` may import `sqlmodel` and `sqlalchemy`. **No other `events`
module may** — modelled exactly on IR1a.

### DS5 — The events plane owns its own `Database`

`platform/events/persistence/` brings its **own `Database`** object owning its engine and session
lifetime. It does **not** import `api.persistence.database`, and does not reach into the Customer
Platform's or Billing's engine.

Two reasons, both from the ADR-0031 precedent: importing `api` would invert the DAG; and a plane
whose storage lives in another plane's package cannot later be extracted as a deployment decision
rather than a re-architecture.

### DS6 — The boundary is an executable per-layer assertion

The exception is enforced by an **executable boundary assertion**, per-layer, in the existing
AST/import-boundary suite:

* `platform/events/persistence/**` **may** import `sqlmodel`/`sqlalchemy`;
* `platform/events/bus.py` and **every other** `events` module outside `persistence/` **may not** —
  asserted to contain no session, no table, and no SQL;
* `core` acquires nothing (AR-10 unchanged);
* no cycle is introduced (AR-15).

As in ADR-0031 §A2, the boundary suite is **tightened, not relaxed**: adding a scoped permission
requires adding the per-layer checks that keep the permission scoped.

### DS7 — No relaxation of the import budget anywhere else

This ADR grants **exactly one** scoped exception. It does not widen the budget for `events/bus.py`,
any other `events/` module, `core`, `observability/` (which stays `core` + stdlib — ADR-0033 MS8),
`replay/`, `gateway/`, or any unrelated edge package. A future package wanting the same permission
needs its own scoped grant and its own assertion.

### DS8 — Migration ownership starts at `0034`

`platform/events/persistence/migrations/` owns migrations from **`0034`** onward, **continuing the
platform's single global chain** rather than starting a second one. This is the Epic 9 precedent
applied unchanged:

> "This package **continues the platform's single migration chain**, it does not start a second
> one: the Customer Platform's chain ends at `0012`, so Billing begins at `0013`. One global
> sequence, ordered and contiguous, is what makes 'which schema version is this database at?' a
> question with one answer."

Binding rules:

* migrations `0001`–`0033` are **untouched** — no edit, no renumber, no backfill, no `ALTER` of any
  frozen table;
* the package owns its **own ordered `MIGRATIONS` tuple**, append-only, never reordered;
* migrations are **declarative schema only, no application logic**, and **idempotent
  (create-if-absent)** — the shape of `v0012_observability.py`;
* migration head `0033` remains the **historical** Epic 9 record.

### DS9 — Table prefix is `evt_*`

Every Epic 10 table carries the **`evt_`** prefix. **Not** `cp_*` (Customer Platform), **not**
`cp_billing_*` (reserved by Epic 9 and frozen), and never unprefixed.

### DS10 — SQLite by default, Postgres-compatible by URL

The engine is **SQLModel/SQLAlchemy over SQLite by default**, accepting any SQLAlchemy URL so
Postgres works unchanged — the shape both `api/persistence/database.py` and
`billing/persistence/database.py` already use. **No new dependency is introduced**; `sqlmodel` and
`sqlalchemy` are already declared in `platform/pyproject.toml`.

**Testing consequence, and it is not optional.** Restart-durability tests use **file-backed SQLite
via `tmp_path`**, never in-memory SQLite with `StaticPool`. A `StaticPool` in-memory database is
pinned to one connection for the object's lifetime and cannot express a restart at all; a test
built on it would prove nothing while appearing to pass. The mandated sequence is: publish →
persist → terminate and recreate the runtime → reopen the durable store → recover → re-derive →
verify exact event identity and order (ADR-0032 EV9).

All test execution remains **offline and hermetic**: no network, no credentials, no vendor service.

### DS11 — The clock is injected

Every time value on a durable-storage path — timestamps, retention window boundaries, retry timing —
comes from an **injected clock**. Ambient `time.time()` is prohibited, matching the discipline
already present in `billing/foundation/seams.py` (`Clock`/`SystemClock`) and in
`InProcessEventBus(clock=…, id_factory=…)`. Identity generation is injected on the same basis.

### DS12 — Retention is explicit configuration

Retention is **explicit configuration** with **no implicit deletion default**, applied **only as a
policy over a closed window**. A deployment that configures nothing deletes nothing. (Semantics:
ADR-0032 ED9.)

### DS13 — Retention never deletes un-drained or un-metered events

The retention policy **must not delete an event that has not been drained**, and **must not delete
an event that has not been metered**. A policy able to remove a fact before it has been consumed
and metered is a revenue defect, and the storage layer is where that guarantee is enforceable.

## Dependency Direction

```
core                                   ← imports nothing above it (AR-10, unchanged)
  ↑
events/            (core + stdlib)     ← bus, outbox orchestration, retry, DLQ, quarantine
  ↑
events/persistence/ (+ sqlmodel/sqlalchemy, scoped — DS3/DS4)
  ↑
runtime/                               ← unchanged
```

`events → api` is **not** created. `core → api` is **not** created. The graph stays acyclic
(AR-15). Storage is reached through a port declared in `events/` and bound to its concrete adapter
at a composition root — the same dependency inversion ADR-0031 used for Billing and that
`MetricStreamPort` itself uses.

## Consequences

**Gained.** Durable persistence for the event spine without inverting the DAG · a **standing,
reusable precedent** for the next `core`-only package needing durability · the events plane stays
extractable into its own service and database as a *deployment* decision, not a re-architecture ·
one global migration chain keeps "which schema version is this?" a question with one answer.

**Accepted costs.** A second `Database` object and a second migration registry now exist (the third
and third respectively, after `api` and `billing`) — deliberate duplication that buys plane
independence · the boundary suite grows per-layer checks · `evt_*` tables share a database with
`cp_*` and `cp_billing_*` by default, which is a deployment convenience and must never become an
architectural assumption.

**Explicitly preserved.** Migrations `0001`–`0033` untouched · migration head `0033` remains
historical · `platform/billing/**` byte-for-byte unchanged · no frozen table altered ·
`core` unchanged in its import budget · `observability/` stays `core` + stdlib ·
`platform/replay/**` untouched · no new top-level directory · no `pyproject.toml` dependency change.

## Alternatives Considered

| # | Alternative | Rejected because |
|---|---|---|
| C1 | **`events/` imports `api.persistence`** | Creates `events → api`, inverting the DAG and breaking AR-14's intent. It would also couple the event spine's storage lifetime to the Control Plane's, forfeiting extractability. |
| C2 | **Widen `events/`'s whole import budget to include `sqlmodel`** | Puts SQL within reach of `bus.py`, the one module that must stay a pure in-process dispatcher. IR1a's insight is that the permission must be *scoped to a sub-package* and asserted, not granted to a plane. |
| C3 | **A new top-level `platform/eventstore/` package** | §A14 already assigns the outbox to `events/`, so a new directory would contradict the folder map, require a CODING_STANDARDS §2 placement ADR, and need a `pyproject.toml` packaging change — all to relocate work that already has a constitutional home. It would also take "event store" as an architectural noun, which `SqlBillingEventStore` already uses. |
| C4 | **Fold this into ADR-0032** | Semantics and structure answer different questions and have different lifetimes; the storage-seam precedent applies to future packages that will never care about at-least-once delivery. Settled as OD-2. |
| C5 | **Start a second migration chain at `0001` for Epic 10** | Two chains make "which schema version is this database at?" a question with two answers. The Epic 9 precedent explicitly refused this. |
| C6 | **In-memory SQLite with `StaticPool` for restart tests** | It cannot express a restart — the database is pinned to one connection for the object's lifetime — so the test would pass without testing anything. This is exactly the failure the mandated restart test exists to prevent. |
| C7 | **Alembic for migrations** | Not a dependency today, and both existing registries note a tool "can adopt this same ordered structure later without reshaping it". Adding one would be a dependency change this ADR is not authorized to make. |

## Compliance Verification

| # | Assertion |
|---|---|
| **SV1** | `platform/events/persistence/**` may import `sqlmodel`/`sqlalchemy`; **no other** `events` module does (per-layer AST assertion). |
| **SV2** | `platform/events/bus.py` and all non-`persistence` `events` modules contain no session, table, or SQL reference. |
| **SV3** | No `events → api` and no `core → api` import exists; the import graph is acyclic. |
| **SV4** | Migrations `0001`–`0033` are byte-for-byte unchanged; Epic 10 migrations are all `≥ 0034`; the global chain is ordered and contiguous. |
| **SV5** | Every Epic 10 table name begins with `evt_`; none begins with `cp_` or `cp_billing_`. |
| **SV6** | No frozen table is altered; no `ALTER` against any pre-existing table. |
| **SV7** | Migrations are declarative and idempotent — applying twice is a no-op. |
| **SV8** | No ambient `time.time()` on a durable-storage path; the clock is injected. |
| **SV9** | Retention deletes nothing when unconfigured, and deletes no un-drained or un-metered event. |
| **SV10** | Restart-durability tests use file-backed SQLite via `tmp_path`, not in-memory `StaticPool`. |
| **SV11** | `platform/pyproject.toml` dependency declarations are unchanged. |
| **SV12** | `observability/` imports `core` + stdlib only — this ADR's exception did not leak. |

## Documentation

Linked from [`docs/INDEX.md`](../INDEX.md) and registered in [`docs/adr/README.md`](README.md).
ADR-0031 is **referenced as precedent, not amended** — its file is not modified.
