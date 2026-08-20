# Billing Platform v1.0 — Epic 9 Freeze Manifest & Repository Release

**Release:** **v4.0.0** · **Milestones:** M9.2–M9.25 (frozen) · released by **M9.26**
**Status:** 🔒 **Epic 9 FROZEN (architecture)** (M9.25) · 📦 **RELEASED** (M9.26) · **Depends on:** Customer Platform v1.0
(Epic 8, frozen), Product Runtime v1.0 (Epic 7, frozen), Execution Platform v1.0 (Epic 6, frozen),
Runtime Platform v1.0 (Epic 5, frozen).

> This is the official architecture freeze of Epic 9, and — as of **M9.26** — its repository
> release. The Billing Platform's public architecture, ownership, dependency direction, and
> extension points are frozen as of this document. Future work **extends** this baseline; it does
> not modify it.
>
> **M9.25** froze the architecture and performed no commit and no tag. **M9.26** discharges the
> condition M9.25 recorded in §12: the frozen tree is committed and tagged **v4.0.0**, which is what
> makes the freeze durable rather than a description of a working directory. §12 now records the
> released state.

**Manifest digest:** `cdafb4ab6cba00bcaed0c9b6a3ae477253ce66e492b5b743c9835072704d1fb7`
**Certification digest:** `c044b3f6a23066df4ed97c8b7d0992d8419490ed68dd1b3633b22717f7cab6e6`

---

## 1. Executive Summary

The **Billing Platform** (`platform/billing/*`) is Epic 9's revenue plane — a deterministic,
layered, additive plane built beside the frozen Customer Platform, never inside it. It owns the
commercial lifecycle end to end: billing accounts, catalog, pricing, subscriptions, entitlements,
metering, rating, ledger, invoicing, tax determination, payments, dunning, revenue recognition,
reconciliation, integrity correlation, investigation, regulatory reporting, and period scope and
consolidation.

Delivered across milestones **M9.2–M9.23** and independently certified in **M9.24** (verdict:
**`CERTIFIED_WITH_ADVISORIES`** — 27 controls, 24 blocking all satisfied, 0 blocking violations, 0
inconclusive, 2 advisories). The architecture is frozen here as **Billing Platform v1.0**.

Evidence at freeze: **4643 repository-wide tests pass**, `ruff check billing/ tests/` clean,
migration chain **0013–0033** valid and additive, and frozen Epics 5/6/7/8 unchanged.

## 2. Frozen Scope

| Layer | Frozen surface |
|---|---|
| Foundation | 78 modules — pure, clock-free, database-free, provider-free, authorization-free |
| Application | 30 modules, 15 engines — CQRS commands/queries over injected seams |
| Persistence | 45 modules, 145 tables, 1193 indexes, migrations 0013–0033 |
| Customer API | 28 modules, 521 routes across 30 prefixes |
| Studio | 18 self-contained pages (ADR-0021 Consumer) |

**Vocabulary:** 252 Billing domain events (all `billing.*`), 29 application events, 29 lifecycles
(all with terminal states), 1381 Foundation exports, 1028 Application exports, 646 OpenAPI schemas.

## 3. Frozen Ownership

ADR-0023 is the authority and is unchanged. Billing is the **sole owner of revenue** and writes only
the 18 Allowed Writes in §6; it writes none of §7. Certification controls `OWN-1..4` prove the
table namespace, the Forbidden-Write boundary, the `metric.*` consumption-only posture, and the
`billing.*` event namespace.

Per-domain ownership (M9.8 accounts, M9.13 ledger, M9.14 invoice, M9.15 tax, M9.16 payments, M9.17
collections, M9.18 recognition, M9.19 reconciliation, M9.20 correlation, M9.21 investigation, M9.22
reporting, M9.23 scope/consolidation, M9.24 certification) is frozen as documented in
`docs/architecture/epic9/*`.

## 4. Frozen Dependency Direction

`billing → core → stdlib`. Nothing else. Certification `PL-1` proves Billing imports no `runtime`,
`actuator`, `api`, `product`, `bootstrap` or `providers`; `PL-2` proves no frozen Epic imports
Billing. `PL-3` proves Foundation purity; `PL-4` proves no vendor SDK appears outside an adapter.

## 5. Frozen Financial Integrity

| Guarantee | Control |
|---|---|
| Revenue history is append-only | `RS-1` — 15 repositories, no edit or delete path |
| Money is integer minor units | `RS-5` — behavioural: `Money(1.5)` raises |
| No currency conversion anywhere | `RS-6` — no FX rate at any altitude |
| No divergence is auto-resolved | `PY-5` — 29 lifecycles, no auto trigger |
| No instrument data on the platform | `PY-6` |
| No tax rate table ships | `TX-1` |

## 6. Frozen Determinism

`DT-1` (29 lifecycles reachable with terminals), `DT-2` (66 `derive_*` identities read no clock,
random or UUID), `DT-3` (exactly one canonicalizer), `DT-4` (migrations additive, ordered,
contiguous).

## 7. Frozen Namespace

`NS-1` (2646 package-namespace names, zero shadowing), `NS-2` (Billing index names unique against
every table in the process), `NS-3` (145 tables, all `cp_billing_*`), `NS-4` (521 routes, none
served twice).

## 8. Extension Policy

Epic 9 extends **additively only**:

* new capability lands **beside** frozen aggregates, never inside them;
* new tables carry `cp_billing_*` and arrive by a migration **after 0033** — never an `ALTER`;
* new API surface takes its own prefix, proven collision-free;
* new Foundation modules extend `__all__` additively;
* **declared limitations are closed by their owning milestones.**

Anything not expressible additively requires an ADR.

## 9. Certification & Freeze Verdicts

**Certification (M9.24): `CERTIFIED_WITH_ADVISORIES`** — catalogue `m9.24.1`, digest
`c044b3f6a23066df…`, 27 controls, 24 blocking all `SATISFIED`, 0 blocking violations, 0
inconclusive.

**Freeze (M9.25): `FREEZE_WITH_ADVISORIES`** — manifest digest `cdafb4ab6cba00bc…`, eleven of twelve
freeze invariants satisfied at freeze time; FZ-12 was the exception and is discharged by this
release.

**Release (M9.26): `RELEASED_WITH_ADVISORIES`** — both digests preserved, migration head 0033, and
neither advisory fixed nor any limitation closed.

## 10. Advisories

| Id | Advisory | Owner |
|---|---|---|
| NS-5 | 29 module-qualified OpenAPI schema names; FastAPI disambiguates, so no client receives an ambiguous contract | M9.5/M9.6/M9.9/M9.10/M9.11 |
| ST-3 | Two Studio pages rescale minor units by a hardcoded 100; correct for all eight supported currencies, wrong the day a non-exponent-2 currency is added | M9.7/M9.8 |

## 11. Declared Limitations

Nine, each owned. LIM-1 (M9.15), LIM-2 (M9.16), LIM-3 (M9.18), LIM-4 and LIM-5 (M9.8), LIM-6
(M9.13/M9.14), LIM-7 (M9.24, self-administered certification), LIM-8 and LIM-9 (as above). **M9.25
closed none of them.**

## 12. Repository State — released (M9.26)

**The Epic 9 implementation is committed and tagged.**

At M9.25 this section recorded the honest opposite: the implementation was present in the working
tree but not committed — roadmap risk **D-11**, *"implementation exists but is uncommitted at
freeze"*, the same condition Epic 8 recorded at M8.20 §22. The freeze/release split exists precisely
so that condition is discharged rather than repeated, and M9.26 discharges it.

* Branch: `feat/epic8-customer-platform` (the branch v3.0.0 was released from)
* Release commit: the commit **`v4.0.0` points at** — a document cannot contain its own hash, so
  the tag is the identity, exactly as it is for v1.0.0, v2.0.0 and v3.0.0
* Release tag: **`v4.0.0`**, annotated — the established convention
* Parent: `a376e27 release(epic8): Customer Platform v3.0.0 — Epic 8 baseline`
* Working tree after release: **clean**
* **Version files: unchanged.** No prior release bumped one — `platform/pyproject.toml` has read
  `version = "0.1.0"` through v1.0.0, v2.0.0 and v3.0.0. The tag is the single release identity,
  and inventing a second one here would create the competing version source §5 forbids.

**FZ-12 is discharged.** The repository can now be reconstructed from the release commit: one commit
preserves the whole frozen Epic 9 tree — every layer, every test suite, both ADRs, and the
architecture and release documents.

## 13. Handover to M9.26 — Repository Release

**Done.** M9.26 committed the frozen tree, created the annotated tag **v4.0.0**, and wrote the
release notes below. It introduced **no financial fact, no table, no migration, no route, no Studio
page and no production module** — repository-release metadata only.

**Neither advisory was fixed and no limitation was closed.** NS-5 and ST-3 remain open advisories
with named owners; all nine limitations remain owned by the milestones that can close them. The
release preserves the certified architecture exactly; it does not improve it.

## 14. Release Notes — Epic 9, Billing Platform v1.0

Epic 9 built VoxVoice's revenue plane as a deterministic, layered, additive plane beside the frozen
Customer Platform. The progression, milestone by milestone:

| Milestone | Capability |
|---|---|
| M9.2–M9.4 | **Foundation, Application, Persistence** — the layered substrate: pure domain, CQRS, SQL |
| M9.5–M9.7 | **Catalog & Pricing** — products, editions, bundles, price books, discounts, coupons |
| M9.8 | **Billing Accounts** — commercial identity, hierarchy and payer graphs, credit profile |
| M9.9 | **Subscriptions** — agreements, plan change, proration, lifecycle |
| M9.10 | **Metering** — usage ingestion, attribution, quarantine, period sealing |
| M9.11 | **Rating** — deterministic charge lines from usage, seats, fees, discounts |
| M9.12 | **Ledger** — append-only revenue entries, credits, accounting periods |
| M9.13–M9.14 | **Invoicing** — billing-run FSM, gapless numbering, immutable issued documents |
| M9.15 | **Tax Determination** — rented-edge determination, owned immutable evidence |
| M9.16 | **Payments** — idempotent intents, verified callbacks, token-only instruments |
| M9.17 | **Dunning & Collections** — bounded, policy-driven recovery ladder |
| M9.18 | **Revenue Recognition** — schedules and entries as a projection over the ledger |
| M9.19 | **Reconciliation & Financial Integrity** — divergence findings, never auto-resolved |
| M9.20 | **Integrity Correlation** — incidents grouping related findings |
| M9.21 | **Investigation & Audit Evidence** — content-addressed evidence, immutable audit packages |
| M9.22 | **Compliance & Regulatory Reporting** — versioned definitions, pinned inputs, filings |
| M9.23 | **Period Scope & Consolidation** — immutable scope snapshots across account groups |
| M9.24 | **Certification** — the architecture review as 27 executable controls |
| M9.25 | **Architecture Freeze** — the deterministic freeze manifest |
| M9.26 | **Repository Release** — this commit and tag |

**What Epic 9 deliberately does not do**, per its own milestone reports rather than optimism: it
converts no currency and ships no FX rate; it hard-codes no tax rate; it stores no payment
instrument; it auto-resolves no reconciliation divergence; it never renders a missing source as
zero; and it writes nothing outside `cp_billing_*`.

### Known release advisories

Two, both recorded rather than hidden, neither a defect in financial behaviour:

* **NS-5** — 29 Pydantic model names collide across customer schema modules, so FastAPI emits
  module-qualified OpenAPI schema names. FastAPI *detects* the collision and disambiguates, so no
  client receives an ambiguous contract; the cost is a generated document that reads badly.
  Owner: M9.5/M9.6/M9.9/M9.10/M9.11.
* **ST-3** — two Studio pages rescale integer minor units by a hardcoded 100. Correct for all eight
  supported currencies (every one has exponent 2) and wrong the day a 0-exponent (JPY) or
  3-exponent (KWD) currency is added, which must therefore be preceded by a Studio formatting fix.
  Owner: M9.7/M9.8.

### Declared limitations at release

All nine remain open and owned. They are the honest edges of the platform, not a backlog M9.26
absorbed: LIM-1 (M9.15), LIM-2 (M9.16), LIM-3 (M9.18), LIM-4/LIM-5 (M9.8), LIM-6 (M9.13/M9.14),
LIM-7 (M9.24 — certification is self-administered), LIM-8 and LIM-9 (as above).
