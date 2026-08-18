# VoxVoice — Billing Foundation (Epic 9, M9.2)

| | |
|---|---|
| **Milestone** | **M9.2** — the first Epic 9 implementation milestone |
| **Status** | 🟢 **Implemented.** Foundation layer only. |
| **Scope** | Pure domain: aggregates, lifecycles, policies, validation, diagnostics, events, repository **interfaces**, stateless services, factories, DTOs. **No application layer, no persistence implementation, no API, no HTTP/FastAPI, no SQL, no migrations, no Studio, no adapters, no payment/tax providers, no payment execution.** |
| **Governed by** | [ADR-0023](../../adr/ADR-0023-billing-sole-owner-of-revenue.md) (ownership), [ADR-0031](../../adr/ADR-0031-billing-plane-repository-placement.md) (placement, imports, public surface), [Epic 9 Blueprint](billing-platform-blueprint.md), [Provider Strategy](billing-provider-strategy.md), [Implementation Plan](../../roadmap/epic9-implementation-plan.md) |
| **Evidence** | **1855/1855 repository-wide tests pass** (1774 prior + 81 new, zero regression); `ruff check` + `ruff format --check` clean; **frozen Epics 5/6/7/8 byte-for-byte unchanged**. |

## Purpose

Give the revenue plane a body: the business rules for the whole commercial lifecycle —
account → catalog → pricing → subscriptions → entitlements → metering → rating inputs →
ledger → invoices → payments → refunds → taxes → contracts → licenses — expressed as pure,
immutable, deterministic domain code that can be reasoned about and tested with no
infrastructure at all.

## Placement (ADR-0031)

```
platform/billing/                 # the plane; imports platform/core + stdlib only
├── __init__.py                   # the public surface (curated re-export)
└── foundation/                   # ← this milestone
    ├── money.py  domain.py  seams.py  errors.py  diagnostics.py  events.py
    ├── accounts.py  catalog.py  pricing.py  subscription.py  entitlement.py
    ├── metering.py  ledger.py  invoicing.py  payments.py  refunds.py
    ├── tax.py  contracts.py
    ├── lifecycle.py              # pure FSM tables built on core.fsm
    ├── validation.py  repositories.py  services.py  factories.py  dto.py
```

**Refinement recorded honestly.** ADR-0031 §7 sketched the 14 bounded contexts as
sub-packages directly under `billing/`. They land one level down, under
`billing/foundation/`, so the plane mirrors the Epic 8 layer idiom
(`foundation → application → persistence → API → Studio`) that M9.3–M9.19 will extend.
This is an **internal grouping change explicitly permitted by ADR-0031 §13** ("everything
else is internal and refactorable without an ADR"): the plane, its dependency direction,
its import rules, its composition root, and its public surface are all unchanged, and no
ADR provision is weakened. The 1:1 context→module mapping (PL1) is preserved.

## Design rules, and how each is enforced

| Rule | Enforcement |
|---|---|
| **Everything immutable** | Every public aggregate is a `frozen=True, slots=True` dataclass; a test iterates the entire public surface and asserts it (≥40 classes). Transitions return **new** aggregates; the input is never mutated. |
| **Everything deterministic** | No wall-clock read outside the `Clock` seam (asserted: `time` is imported by exactly one module); no randomness; no ambient state. Identical inputs produce identical aggregates and identical, canonically-ordered diagnostics. |
| **No side effects, no I/O** | An AST test forbids `socket`, `http(x)`, `requests`, `urllib`, `sqlite3`, `sqlalchemy`, `sqlmodel`, `fastapi`, `starlette`, `pydantic`, `random`, `datetime`, and `os` anywhere in the plane. Event emission is the only effect, and it is an injected seam. |
| **Repository interfaces only** | All 29 repositories are ABCs with abstract methods; a test asserts each is abstract and **cannot be instantiated**. No in-memory or SQL implementation exists in the Foundation. |
| **Validation returns diagnostics** | `BillingValidator` never raises; it returns `BillingDiagnostics`. Factories decide to fail fast (`BillingValidationError`) on error severity (§A2.6). |
| **Services stateless** | Services hold configuration (a policy, a definition, a seam) and no mutable data; every method is a pure function of its arguments. |
| **Factories compose aggregates** | `BillingFactory` validates → fails fast → composes with injected id/clock seams → emits the `billing.*` fact. Nothing is emitted when an input is rejected. |
| **Events typed** | `BillingEventType` (60+ members) + a frozen `BillingEvent` envelope + an `EventEmitter` ABC. |
| **Business rules only** | No transport, no storage, no vendor, no scheduling, no orchestration. |

## Ownership invariants made executable (ADR-0023)

| ADR-0023 provision | Test |
|---|---|
| M8 — Billing emits `billing.*`, **never** `metric.*` | every `BillingEventType` value starts with `billing.`; no `"metric.` literal exists anywhere in the plane |
| M1/M3/M4 — usage records are a **projection** keyed by the source event | `UsageRecord.idempotency_key == (event_id, meter)`; deduplication is order-independent |
| M6 — total attribution | `quarantine_usage` is a first-class factory path; quarantined records are non-billable and emit `USAGE_QUARANTINED` |
| §11.3 — entitlement ≠ authorization | no `EntitlementSnapshot` method may take a principal/role/permission/request; `EntitlementResolver` exposes no `authorize`/`enforce`/`allow`/`deny` |
| E3 — every gate classified | a snapshot with an unclassified entitlement fails validation (`unclassified_gate`) |
| E6/PY8 — contraction never destroys data | `contract_for_state` reduces capability under PAST_DUE/PAUSED/SUSPENDED and touches nothing else |
| RS1/RS5 — append-only ledger, integer minor units | append-only repositories declare no `update`/`delete`/`replace`; `Money` rejects floats and cross-currency arithmetic |
| IN2/IN3/IN4 — issued invoices immutable, gapless numbering | re-issuing raises `ImmutabilityViolation`; numbering is gapless and independent per entity/jurisdiction |
| PY6 — no instrument data | `PaymentInstrument` carries a provider **token reference** only; a test bans PAN/CVV/IBAN-shaped fields |
| PY9 — bounded refunds, separation of duties | refund > captured is an error; self-approval is an error; a missing idempotency key is an error |
| TX1 — determination is rented, evidence is owned | a test asserts `tax.py` encodes **no rate table** |
| §5.2 — references, never copies | `BillingAccount` carries `org_id` and no members/workspaces/projects/agents/roles |
| §A11.2 — org isolation | every org-scoped repository read takes `org_id`; cross-org scope is a validation error |

## Key modelling decisions

- **`Money` is the spine.** Integer minor units + mandatory currency, exact rational
  scaling under a *declared* rounding policy, and largest-remainder `allocate()` so a split
  never leaks a minor unit. Proration, percentages, and tax splits all use it; no float
  exists anywhere in the plane.
- **Catalog and pricing are separate objects**, so a price change is never a plan change,
  and subscriptions pin `plan_version` + `price_book_version` — the mechanism behind
  grandfathering and re-derivable historical invoices.
- **Lifecycles reuse `core.fsm`** rather than five hand-rolled tables: validation, terminal
  immutability, and duplicate-edge rejection come from the shared M5.0 framework.
- **Periods, not "now".** `BillingPeriod` is a half-open window; every charge attaches to
  one. `PeriodService` enumerates deterministically, which is what makes a run reproducible.
- **The entitlement snapshot is the entire Billing↔enforcement interface** — immutable,
  versioned, cacheable, with an explicit staleness bound and a per-gate fail-open /
  fail-closed / degrade classification.
- **`QuotaVerdict` informs; it does not decide.** It reports allowance, consumption, overage
  permission, classification, and staleness, and leaves the accept/reject to the plane that
  owns the write.
- **Offline settlement is first-class** (`PaymentMethodKind.OFFLINE_TRANSFER`, no provider),
  because most enterprise revenue never touches a payment provider.

## Deferred by design (not gaps)

| Deferred | Lands at |
|---|---|
| Application layer (CQRS, unit of work) | M9.3 |
| Persistence implementations + migrations | M9.4 |
| Rating computation over price shapes | M9.11 |
| Adapter contracts + offline fakes, payment/tax execution | M9.14 / M9.15 |
| `billing/refs.py` (the single capped read-only `product` import) | M9.20, when marketplace listings need Epic 7 identity |
| Enforcement integration in the owning planes | M9.17 |
| Control Plane API + Studio | M9.18 / M9.19 |

## Test surface (81 tests)

Money invariants · immutability of the whole public surface · determinism and ordering ·
no-I/O and clock-seam AST assertions · validation-never-raises and factory fail-fast ·
ownership assertions · lifecycle legality and terminal immutability · entitlement
composition precedence, classification, staleness, contraction · quota verdicts · price
shape/tier/currency/effective-dating validation · proration (upgrade, deferred downgrade,
immediate-downgrade credit) · derived ledger and credit balances · invoice totals ordering,
immutability, gapless numbering, self-contained evidence · payment idempotency and
token-only instruments · refund bounds and separation of duties · GST/VAT profile
validation and the no-rate-table assertion · contract true-up and basis-point uplift ·
account hierarchy vs org tenancy and retention-on-close · repository interface/append-only/
org-scoping assertions · **IR1–IR9** import rules · a full end-to-end Definition-of-Done
scenario.
