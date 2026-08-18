# Billing Persistence Layer — Component Specification (M9.4)

**Status:** 🟢 Implemented · **Date:** 2026-08-03 · **Milestone:** M9.4
**Package:** `platform/billing/persistence/`
**Authority:** Engineering Constitution v2 §A2.5, §A5, §A7, §A11.2, §A11.3, §A12 · ADR-0023 · ADR-0031
**Rationale:** DL-0114 · **Supersedes nothing** · **Amends** ADR-0031 §10 and IR1 (see §11)

---

## 1. Purpose

The Billing Persistence layer is the **only production implementation** of the Foundation's 29
repository contracts (M9.2) and the Application's `UnitOfWork` abstraction (M9.3).

It owns **storage and nothing else**: no business rule, no orchestration, no transport, no
provider call, no authorization decision. The layers beneath it do not know it exists — the
Foundation and the Application remain byte-for-byte unchanged from M9.2/M9.3, contain no
mention of SQLModel, a session, or a table, and gained nothing but durability.

```
persistence ──► application ──► foundation ──► core
```

One way. Enforced by AST assertions, not convention.

---

## 2. What This Milestone Changes

| Before (M9.3) | After (M9.4) |
|---|---|
| Repository *interfaces* only; the Application ran on in-test fakes | 29 SQL-backed implementations |
| `StagingUnitOfWork` — boundary + event outbox, no durable rollback | `SqlBillingUnitOfWork` — real transaction; data and events move together |
| Billing facts emitted to a recorder | Billing facts appended to `cp_billing_event` inside the caller's transaction |
| No tables | 30 `cp_billing_*` tables via migrations `0013–0015` |
| No composition root | `build_persistent_billing_application(...)` |

**Not one use case, handler, command, query, or Foundation service changed.** That is the payoff
for having written the boundary interface-first at M9.2/M9.3.

---

## 3. Package Structure

```
platform/billing/persistence/
├── __init__.py          # public surface: composition root, database, UoW, repositories
├── models.py            # 30 SQLModel tables (cp_billing_*)          [internal]
├── codec.py             # type-driven aggregate ↔ JSON codec         [internal]
├── mapping.py           # row ↔ aggregate boundary translation       [internal]
├── database.py          # BillingDatabase — engine + session lifetime
├── repositories.py      # 29 Sql* repository implementations
├── unit_of_work.py      # SqlBillingUnitOfWork + BillingUnitOfWorkFactory
├── event_store.py       # SqlBillingEventStore — durable billing.* fact stream
├── composition.py       # build_persistent_billing_application(...)
└── migrations/
    ├── __init__.py                  # ordered registry + upgrade_all
    ├── v0013_billing_commercial.py  # accounts, catalog, pricing, subscriptions, entitlements
    ├── v0014_billing_revenue.py     # usage, ledger, credits, invoices, runs
    └── v0015_billing_settlement.py  # payments, refunds, tax, contracts, licenses, events
```

`models`, `mapping`, and `codec` are **internal** — no SQLModel row type appears in `__all__`, and
none crosses the package boundary in either direction (ADR-0031 §13).

---

## 4. Repository Implementations

All 29 Foundation contracts, implemented exactly, with **no interface change**:

| Domain | Implementations |
|---|---|
| Accounts | `SqlBillingAccountRepository` |
| Catalog & pricing | `SqlPlanRepository`, `SqlFeatureRepository`, `SqlMeterRepository`, `SqlCatalogSnapshotRepository`, `SqlPriceBookRepository` |
| Subscriptions & entitlements | `SqlSubscriptionRepository`, `SqlEntitlementGrantRepository`, `SqlEntitlementSnapshotRepository` |
| Metering | `SqlUsageRecordRepository`, `SqlUsageAggregateRepository`, `SqlUsageQuarantineRepository` |
| Ledger & credits | `SqlLedgerRepository`, `SqlCreditRepository` |
| Invoicing | `SqlInvoiceRepository`, `SqlInvoiceNumberAllocator`, `SqlCreditNoteRepository`, `SqlBillingRunRepository` |
| Payments | `SqlPaymentRepository`, `SqlPaymentInstrumentRepository`, `SqlDunningRepository`, `SqlReconciliationRepository` |
| Refunds | `SqlRefundRepository`, `SqlDisputeRepository` |
| Tax | `SqlTaxProfileRepository`, `SqlTaxDeterminationRepository` |
| Contracts & licensing | `SqlContractRepository`, `SqlLicenseRepository` |
| Events | `SqlBillingEventRepository` |

Every implementation:

* reads and writes the shared `BillingDatabase.session` and **never commits or rolls back**
  (source-asserted) — it may `flush()` only;
* **maps only** — row ↔ frozen aggregate at the boundary, no derivation the Foundation owns;
* is **org-scoped by construction** (§A11.2): every org-owned query filters on `org_id`, so a
  cross-tenant read is not expressible, not merely disallowed.

`SQL_REPOSITORIES` pairs each contract with its implementation; a test asserts the pairing covers
the Foundation's declared contracts **exactly**, so a contract added later cannot ship unimplemented.

### 4.1 Storage-level invariants enforced here

Three invariants are enforced at this layer rather than trusted from above, because a database is
uniquely able to guarantee them:

| Invariant | Mechanism | Authority |
|---|---|---|
| **Append-only** (ledger, credits, usage, credit notes, tax determinations, findings, events) | The classes expose `append`/`add` + reads and have **no update or delete method to call**; asserted at both surface and source level | ADR-0023 RS1 |
| **Issued documents are immutable** | `replace_draft` raises `ImmutabilityViolation` on a non-draft invoice; snapshot and catalog versions are unique-constrained | IN2, E4 |
| **Exactly-once usage** | `append` is idempotent on `(source event id, meter)` **and** the table carries the matching unique constraint | M4 |

The third is deliberate defence in depth: double-billing must not be one lost race away from a
caller who bypasses the check.

Two further one-way doors: sealing a usage period cannot be undone (`ImmutabilityViolation` on an
attempt to unseal — M7), and revoking an entitlement grant is a **recorded transition**, never a
delete, because *why* an entitlement went away is itself a fact worth keeping.

---

## 5. Unit of Work

`SqlBillingUnitOfWork` implements the existing `billing.application.UnitOfWork` with no interface
change, backing the boundary with a real transaction over the shared session:

```
commit    → commit the DB transaction, THEN publish the staged application events
rollback  → roll the DB transaction back AND discard the staged events
```

The ordering is deliberate and asymmetric: **events publish only after durable success**, because
an event announcing a charge that no longer exists is worse than a late one. `__exit__` never
suppresses an exception, so the dispatcher's rejection audit and the caller both still see it.

This closes the limitation M9.3 named honestly. There, rollback discarded events but there were no
repository writes to undo; here both move together — proven across several repositories in one
transaction (invoice + ledger + tax evidence + fact stream all land or none do).

**Transaction ownership lives in exactly one place.** Repositories participate by writing into the
session; a repository that committed would silently make a half-finished orchestration durable.

---

## 6. Database Schema

30 tables, all prefixed `cp_billing_*`. `cp_` keeps Billing inside the one Control-Plane database
namespace; `_billing_` guarantees it never reuses, extends, or collides with a legacy table. **No
Epic 5–8 table is read, written, or altered.** A test asserts the two table-name sets are disjoint
and that no migration contains `ALTER TABLE` or a legacy table name.

### 6.1 Row shape — projection + payload

Each row carries:

1. **Projected columns** — the keys the 29 contracts actually query on (identity, `org_id`,
   `account_id`, lifecycle state, period bounds, idempotency keys), typed and indexed;
2. **A JSON `payload`** holding the canonically encoded aggregate.

Aggregates are reconstructed from the payload alone. Projections are an *index over* the payload,
derived from the same aggregate at write time — never an independent copy that could disagree with
it (§A2.5, applied inside a row).

This is a considered trade, and the honest statement of it: a **column-per-field** schema across 30
aggregates and ~400 fields would be several hundred lines of hand-maintained mapping that drifts the
moment the domain gains a field, and every Foundation addition would require a migration. The hybrid
keeps every contract query on an indexed column while letting additive domain change land without
one. **The cost:** a field that is *not* projected cannot be filtered or aggregated in SQL. Every
query the contracts expose today is projected; a future reporting need (revenue by tier, usage by
workspace) adds a projection column or a read-model projection (§A7) — additively, and by then it
will be a query with a known shape rather than a guess.

### 6.2 Migration chain

Billing **continues the platform's single chain** rather than starting a second one: Epic 8 ends at
`0012`, so Billing begins at `0013`. One global sequence keeps "which schema version is this
database at?" a question with one answer.

| Version | Scope |
|---|---|
| `0013` | Commercial structure — accounts, catalog, pricing, subscriptions, entitlements |
| `0014` | Revenue records — usage, ledger, credits, invoices, invoice sequence, credit notes, runs |
| `0015` | Settlement — payments, instruments, dunning, reconciliation, refunds, disputes, tax, contracts, licenses, event stream |

Ordered, contiguous, idempotent (create-if-absent), declarative-only — a test asserts each module
exports exactly `upgrade` and `downgrade` and nothing else. **Every `downgrade` is a deliberate
no-op**: the ledger and issued invoices are legal records under a retention obligation, and a schema
rollback is never a licence to delete them.

---

## 7. Mapping Strategy

```
Foundation aggregate ──encode──► JSON payload ──► row ──► JSON payload ──decode──► Foundation aggregate
```

`codec.py` is a single type-driven encoder/decoder. Every Billing aggregate is a frozen, slotted
dataclass built from a closed set of value kinds — primitives, `StrEnum`s, `Money`, nested frozen
dataclasses, homogeneous tuples, and one plain dict — and that regularity is what makes one codec
correct for all thirty, instead of thirty field lists that drift.

**Round-trip fidelity is the contract**: `decode(type(a), encode(a)) == a`, asserted aggregate by
aggregate for all 30 types, because a mapping layer that is merely *plausible* is how revenue
records quietly lose a field.

Decoding is **tolerant forward, strict backward**: an unknown key from a newer writer is ignored; an
absent key falls back to the dataclass default; a **required** field with no default raises rather
than being invented. Payloads are asserted JSON-portable (SQLite *and* Postgres).

---

## 8. Composition Root

```python
build_persistent_billing_application(
    *, url="sqlite://", ids=None, clock=None, create_all=True,
    authorizer=None, emitter=None, database=None,
) -> PersistentBillingApplication   # .application, .database, .events
```

Wires SQL repositories, the durable fact stream, the deterministic seams, and
`BillingUnitOfWorkFactory` into the existing `BillingApplicationFactory`. Two properties are ADR
obligations rather than implementation taste:

* **Deny-by-default survives production wiring.** `authorizer` defaults to nothing, so the
  Application's `DenyAllAuthorizer` stands. Billing owns no RBAC (ADR-0023 §5.3); the Control Plane
  injects the §A11.2 evaluator at M9.18. A build that forgets to is **inert, not open** — asserted.
* **Not wired into any runtime composition root.** ADR-0031 CR2: Billing is absent from
  `platform/bootstrap.py`, so a total Billing outage cannot affect a conversation or a deployment.
  Wiring is an explicit, control-plane-only act.

Passing an existing `database` opens a **second** Application over the same storage — which is how
the cross-instance durability tests prove records are in the database and not in an object's memory.

`build_repositories` supplies the 19 seams `BillingRepositories` declares today;
`build_auxiliary_repositories` returns the remaining 10 as a mapping, so the milestones that need
them (M9.10–M9.16) wire one without this function gaining a positional parameter.

---

## 9. Testing

**105 integration tests**, all against a real database (SQLite in-memory over the same
SQLModel/SQLAlchemy stack as Postgres/Supabase) — no repository fakes, because the point of this
milestone is precisely what a fake cannot prove.

| Group | Coverage |
|---|---|
| Round-trip & mapping | Lossless codec round-trip for all 30 aggregates; JSON portability; field add/remove tolerance; required-field strictness; 29-contract implementation coverage |
| Organization isolation | Every org-scoped read across 12 repositories returns nothing for a foreign org; identical ids in two orgs stay separate |
| Append-only & immutability | No mutation path (surface *and* source); ledger correction is a new entry; issued invoice refuses replacement; snapshot versions immutable and unique-constrained; revocation recorded not deleted; usage idempotent at both layers; sealed period cannot reopen; invoice numbers gapless, per-jurisdiction, and surviving a new allocator instance |
| Transactions | Commit durability; rollback undoes writes; all-or-nothing across four repositories; events publish only after commit; rollback discards events; never suppresses an exception; closed UoW unusable; billing facts commit with the records they describe |
| Cross-instance durability | A second Application reads the first's writes; full journey end-to-end; rejected command leaves no durable trace; composition root defaults to denying |
| Migration chain | Ordered, contiguous, continues `0012` → `0013`; builds every Billing table and only those; idempotent; reuses no legacy table; no application logic |
| Architecture compliance | Import allow-list and deny-list; one-way dependency direction; Foundation and Application unaware of persistence; no row type leaks; repositories never commit; no business rule in a repository; frozen layer surfaces unchanged; Billing absent from `bootstrap.py` |

**Repository-wide: 2012/2012 pass** (1907 prior + 105, zero regression). `ruff check` and
`ruff format --check` clean.

---

## 10. Architecture Compliance

| Rule | Status |
|---|---|
| Persistence imports `billing.foundation`, `billing.application`, `core`, `sqlmodel`, stdlib only | ✅ AST-verified |
| Never imports FastAPI, Customer API, Studio, Runtime, provider adapters, Deployment, Knowledge, Observability, Product | ✅ AST-verified |
| Dependency direction one-way | ✅ |
| Foundation unchanged and unaware of persistence | ✅ byte-for-byte; source-asserted |
| Application unchanged and unaware of persistence | ✅ byte-for-byte; source-asserted |
| Repository contracts unchanged | ✅ 29/29 satisfied as declared |
| No business rule in a repository | ✅ source-asserted |
| No transaction ownership outside the Unit of Work | ✅ source-asserted |
| Namespace isolation; no legacy table reuse | ✅ disjoint sets asserted |
| Migration chain ordered, contiguous, idempotent | ✅ |
| Frozen Epics 5/6/7/8 untouched | ✅ |

---

## 11. Amendments to ADR-0031

Two, both recorded here and in DL-0114 rather than left as silent drift:

**A. Persistence placement — `platform/billing/persistence/`, not `platform/api/persistence/`.**
ADR-0031 §10 sketched billing persistence as an additive package inside the Epic 8 persistence
layer. This milestone places it inside the Billing plane instead. Rationale: ADR-0031 **IR3 forbids
`billing` importing `api`**, and putting Billing's storage inside `api/` would either invert that
edge or split the plane across two top-level packages — while §17 names *"extract Billing into its
own service"* as a deployment decision the topology must preserve, which a plane whose storage lives
in another plane's package does not. Placement of the plane itself is unchanged; this is the
already-permitted §13 latitude over internal structure, made explicit because §10 named a location.

**B. IR1a — `billing/persistence/**` may import `sqlmodel`/`sqlalchemy`.**
IR1 confined the whole plane to `core` + `billing` + stdlib. Storage cannot be implemented under
that rule. The exception is **scoped to one sub-package**, is checked by its own assertion, and
leaves the original rule at full strength for `billing/foundation` and `billing/application` — the
M9.2 boundary test was **tightened into per-layer assertions**, not relaxed. No vendor SDK is
involved, so IR5 is untouched.

---

## 12. Known Limitations (stated, not hidden)

1. **Invoice-number gaplessness under concurrency.** The counter is a row, so allocation survives
   restarts and retries. Under the single-writer Control Plane (§A9 Stage 1–2) the shared session
   serialises it; multi-worker deployment needs `SELECT … FOR UPDATE` on that one row — a one-line
   change confined to `SqlInvoiceNumberAllocator`, which is why the counter is a row and not a
   column on something else. A rolled-back run releases its number back; gaplessness holds for
   *committed* invoices, which is what the tax authority requires (IN4).
2. **One shared session** (the M8.4 shape). Correct for the single-worker Control Plane; per-request
   `scoped_session` is the documented Stage-3+ upgrade.
3. **Non-projected fields are not SQL-queryable** — see §6.1. Additive to fix, by design.
4. **10 of 29 repositories are implemented but not yet Application seams** — features, catalog
   snapshots, quarantine, credit notes, runs, instruments, dunning, reconciliation, disputes, and
   the event repository (the last is wired through `SqlBillingEventStore`). They join
   `BillingRepositories` with the use cases that need them (M9.10–M9.16), additively.
5. **No DB-level foreign keys** — referential integrity is enforced by Foundation validators, the
   same decoupled, order-free choice Epic 8 made. FK constraints can be added in a later migration.

---

## 13. Next

**M9.5+ per the Epic 9 implementation plan**, with **M9.10 Metering** in parallel — the epic's only
Critical-risk correctness milestone and the one gating G1. Every subsequent domain milestone now
writes into a durable, atomic, org-isolated, append-only store rather than a fake.
