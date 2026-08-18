# VoxVoice — Epic 9 (Billing & Subscription Platform) Implementation Plan (M9.1)

| | |
|---|---|
| **Milestone** | **M9.1** — Epic 9 implementation planning (the last planning milestone before code) |
| **Status** | 🟡 **Planning.** No implementation, no code, no APIs, no database, no schemas, no repositories, no migrations, no routers, no UI implementation. Planning artifacts only. |
| **Date** | 2026-08-01 |
| **Authoritative inputs** | Constitution v2 (Part A + Part B/P-ADR-0001), ADR-0001…**ADR-0023** (ratified), Epic 5 Freeze (Runtime Platform v1.0), Epic 6 Freeze (Execution Platform v1.0), Epic 7 Freeze (Product Runtime v1.0), Epic 8 Freeze (Customer Platform v1.0 + GA v1.0), [Epic 9 Blueprint](../architecture/epic9/billing-platform-blueprint.md) (M9.0A), [Billing Provider Strategy](../architecture/epic9/billing-provider-strategy.md) (M9.0C) |
| **Introduces** | **No new architecture and no new ADR.** This plan *realizes* ADR-0023 + the Blueprint + the Provider Strategy as an executable roadmap. It identifies **one structural placement ADR** as an M9.2 entry criterion (below); it does not mint it. |

## Standing constraints (bind every milestone)

1. **Epics 5/6/7/8 are frozen.** No milestone modifies them. Billing reaches the platform only
   through the **event log / `metric.*`**, **read-only references** to Customer-Platform identity,
   Product-Runtime artifacts, and Actuator status, and the **Control Plane** for ingress.
2. **ADR-0023 is the acceptance criterion of every milestone.** Its **Allowed Writes** (§6) and
   **Forbidden Writes** (§7) lists are exhaustive and are checked per milestone, not once at the end.
3. **Billing owns revenue and nothing else.** Billing never measures usage, never blocks Runtime or
   Deployment, never performs a permission check, never becomes synchronous infrastructure.
4. **Entitlement = decision (Billing) / enforcement (the owning plane).** Every gate carries a
   **fail-open / fail-closed / degrade** classification. An unclassified gate fails review.
5. **Provider Strategy (M9.0C) is binding.** Providers own payment movement only. No vendor type
   crosses an adapter seam. A **deterministic offline adapter is the default outside production** —
   the test suite opens no socket and moves no money.
6. **One writer per fact (§A2.5); accepted configuration must act (§A2.6); money is integer minor
   units on an append-only ledger.**
7. **Dependency direction is one-way and acyclic (AR-15):** `Billing → {event log, Epic 5/6/7/8
   read-only artifacts, adapters}`. **No frozen Epic may import Billing.** Enforced by the existing
   import-boundary/AST tests, extended per milestone.
8. **Every milestone is build-green, independently testable, and additive.** No milestone leaves the
   repository red, and none requires a later milestone to become correct.
9. **New top-level directory ⇒ structural placement ADR** (CODING_STANDARDS §2, the ADR-0018/0022
   precedent). This plan identifies **one** (`platform/billing/`) as an **M9.2 kickoff entry
   criterion**. It is a placement record, not an architectural change — ADR-0023 already ratified
   that Billing is a new top-level plane.

## Milestone renumbering (recorded honestly)

The Blueprint (§27) sketched a 20-milestone roadmap numbered **M9.0A–M9.19** while planning was still
notional. This plan supersedes that numbering with the **M9.1–M9.23** sequence below, which separates
the platform layers (Foundation / Application / Persistence / API / Studio) from the commercial
domains, as Epic 8 did. **Scope is unchanged; only the numbering and the layer/domain split changed.**

| Blueprint §27 | This plan | Note |
|---|---|---|
| M9.0A / M9.0B / M9.0C | unchanged | Blueprint · ADR-0023 · Provider Strategy (all complete) |
| M9.1 Billing Foundation | **M9.2 Foundation** + **M9.3 Application** + **M9.4 Persistence** | split into the Epic-8 layer pattern |
| M9.2 Catalog & Price Book | **M9.6 Commercial Catalog** + **M9.7 Pricing Engine** | catalog and prices separated (a price change is never a plan change) |
| M9.3 Subscription Engine | **M9.8 Subscription Engine** | + **M9.5 Billing Accounts** extracted ahead of it |
| M9.4 Entitlement | **M9.9 Entitlement Engine** | unchanged in substance |
| M9.5 Metering | **M9.10 Metering Pipeline** | unchanged in substance |
| M9.6 Rating | **M9.11 Rating Engine** | unchanged in substance |
| M9.7 Ledger & Credits | **M9.12 Revenue Ledger & Credits** | unchanged |
| M9.8 Invoicing | **M9.13 Invoice Engine** | unchanged |
| M9.9 Tax | **M9.15 Tax Engine** | moved after payments (invoicing holds on tax, §Failure Model) |
| M9.10 Payments | **M9.14 Payment Engine & Dunning** | unchanged |
| M9.11 Refunds | **M9.16 Refunds & Credit Notes** | unchanged |
| M9.12 Enforcement Integration | **M9.17 Entitlement Enforcement Integration** | unchanged |
| M9.13 Studio | **M9.18 Control Plane API** + **M9.19 Studio Billing Surface** | API and UI separated |
| M9.14 Contracts & Licensing | **M9.21 Enterprise Contracts & Licensing** | |
| M9.15 Marketplace | **M9.20 Marketplace Commerce** | |
| M9.16 Revenue Observability | **M9.22 Revenue Observability & Reconciliation** | |
| M9.17/M9.18/M9.19 | **M9.23 Hardening** + **M9.24 Certification** + **M9.25 Freeze** + **M9.26 Release** | freeze and release separated (the M8.20 governance lesson) |

---

# 1. Executive Summary

Epic 9 converts a ratified ownership boundary into a working revenue plane, in **25 milestones across
6 phases**, executed by **7 engineering streams** over **14 bounded contexts**, sequenced on a single
**commercial lifecycle spine**:

```
Billing Account → Commercial Catalog → Pricing → Subscriptions → Entitlements →
Metering → Rating → Ledger → Invoices → Payments → Refunds → Taxes →
Marketplace → Enterprise Licensing
```

The plan is organized around five engineering judgments:

1. **Layers first, domains second.** Foundation → Application → Persistence → API → Studio is the
   proven Epic 8 shape (M8.2–M8.5 built exactly this way, and the freeze verified it). Doing it
   again means every commercial domain lands as an additive slice through stable layers rather than
   as a bespoke vertical, which is what keeps 14 domains from colliding.
2. **Metering starts on day one, in parallel.** It is the epic's only *Critical*-risk correctness
   milestone, its longest lead, and it depends on almost nothing in the subscription chain. Starting
   it late is the single most likely way to miss the epic.
3. **The adapter seams are built before the engines that use them.** The Payment and Tax adapter
   contracts + their deterministic offline implementations land ahead of the Payment and Tax engines,
   so those engines are developed and tested against a fake from their first commit and never grow a
   vendor-shaped dependency.
4. **Entitlement is the pivotal unlock.** M9.9 publishes the snapshot contract; M9.17 integrates
   enforcement into the owning planes. Everything about "can the customer do X" waits on the first
   and nothing about it blocks the runtime, per ADR-0023 §11.
5. **Correctness precedes revenue.** Gate **G1** runs the entire billing pipeline against real
   production usage in **shadow mode with no customer charged**. No customer money moves until the
   ledger has been reproduced and reconciled against the event log.

**MVP (first billable revenue):** M9.2–M9.14 + M9.18/M9.19 — accounts, catalog, pricing,
subscriptions, entitlements, metering, rating, ledger, invoices, payments, plus the API and Studio
surface. Taxes (M9.15) join before any invoice reaches a customer in a tax-bearing jurisdiction.
Marketplace, licensing, and reseller/white-label are post-MVP and additive by construction.

**Critical path:** `M9.2 → M9.3 → M9.4 → M9.5 → M9.6 → M9.7 → M9.8 → M9.9 → M9.11 → M9.12 → M9.13 →
M9.14`, with **M9.10 (metering)** running fully in parallel from M9.4 and joining at M9.11.

---

# 2. Implementation Strategy

| # | Strategy | Why |
|---|---|---|
| **S-1** | **Additive-only, layer-by-layer.** Every milestone adds to `platform/billing/` and additively extends the Epic 8 layers; nothing frozen is edited. | The Epic 8 freeze held for 17 milestones this way; the mechanism is proven in this repository. |
| **S-2** | **Contract-first between streams.** Each milestone publishes its neutral contracts — event vocabulary, entitlement-snapshot shape, adapter capability contracts, projection shapes — *before* implementation, so dependent streams build against fixtures rather than each other's code. | This is the anti-merge-conflict mechanism; it is what let Epic 8 run 7 streams. |
| **S-3** | **Deterministic core, faked edges, always.** Every engine is a pure/table-driven component with an injected clock; every provider is a deterministic offline adapter by default. | Makes 14 domains independently testable offline, and satisfies M9.0C AD7. |
| **S-4** | **Reproducibility as a first-class feature, not a test.** Usage records and rating are re-derivable from the event log; the ledger is verified by re-derivation, not by inspection. | This is the only defense that scales against metering error (R-1) — the epic's top risk. |
| **S-5** | **Shadow before live.** The whole pipeline runs against real usage producing no charges (G1), then a small cohort with oversight (G2), then self-serve (G3). | Billing defects are discovered by customers or not at all; shadow mode is how we discover them first. |
| **S-6** | **Enforcement last, decision early.** The entitlement *contract* lands at M9.9; *enforcement integration* into other planes lands at M9.17, after the snapshot shape has been exercised for several milestones. | Changing a contract that four planes already enforce is the expensive mistake; exercising it first is cheap. |
| **S-7** | **Money invariants are enforced by tests from the first commit.** Integer minor units, mandatory currency pairing, append-only, immutability, gapless numbering, declared discount order. | These cannot be retrofitted; a float that reaches the ledger is a data-migration incident. |
| **S-8** | **No milestone ends red.** Every milestone leaves the repository green, ruff-clean, boundary-test-green, with frozen Epics byte-for-byte unchanged, and is independently demonstrable. | Standing constraint 8; also the Epic 8 DoD discipline. |

---

# 3. Epic 9 Phase Plan

| Phase | Milestones | Theme | Exit condition |
|---|---|---|---|
| **P0 — Governance** *(complete)* | M9.0A, M9.0B, M9.0C | Blueprint · ADR-0023 · Provider Strategy | Ownership and provider strategy ratified |
| **P1 — Platform Substrate** | M9.1 *(this doc)*, M9.2, M9.3, M9.4 | The billing plane exists: layers, boundaries, event vocabulary, tenancy, persistence seam | A billing fact can be written, read, audited, and org-scoped — with no commercial meaning yet |
| **P2 — Commercial Core** | M9.5, M9.6, M9.7, M9.8, M9.9 | Accounts → catalog → pricing → subscriptions → entitlements | An org has an account, a plan, a subscription, and a published entitlement snapshot |
| **P3 — Consumption & Money** | M9.10, M9.11, M9.12, M9.13, M9.14, M9.15, M9.16 | Metering → rating → ledger → invoices → payments → taxes → refunds | A period closes into a taxed invoice that can be paid, dunned, and refunded |
| **P4 — Surfaces & Enforcement** | M9.17, M9.18, M9.19 | Enforcement integration, Control Plane API, Studio | A customer can see their plan/usage/invoices, change plan intent, pay, and hit an enforced limit |
| **P5 — Commercial Breadth** | M9.20, M9.21, M9.22 | Marketplace, enterprise contracts/licensing, revenue observability + reconciliation | Enterprise and marketplace revenue flow through the same core |
| **P6 — Production & Governance** | M9.23, M9.24, M9.25, M9.26 | Hardening, certification, architecture freeze, repository release | Billing Platform v1.0 frozen; baseline v4.0.0 recommended |

---

# 4. Engineering Streams

Seven streams, each progressing against **published contracts** rather than each other's code. Stream
ownership is the anti-merge-conflict mechanism: two streams rarely touch the same module.

| Stream | Owns | Primary surface | Parallelism key |
|---|---|---|---|
| **B1 — Billing Core** | Accounts, catalog, price book, subscriptions, entitlements, credits, contracts, licenses | `platform/billing/` domain | Publishes domain contracts + event vocabulary first |
| **B2 — Metering & Rating** | Ingestion, attribution, normalization, usage records, aggregation, rating, charge lines | `platform/billing/` metering + rating | **Independent of the subscription chain**; consumes the event log directly from M9.4 |
| **B3 — Money & Documents** | Ledger, invoicing, numbering, credit notes, revenue recognition, document/export adapters | `platform/billing/` ledger + invoicing | Depends on rating output shape only; can build against synthetic charge lines |
| **B4 — Provider Adapters** | Payment adapter contract + offline fake + first providers; tax adapter contract + offline fake; payout rails | `platform/billing/adapters/` | **Fully isolated package**; the offline fake unblocks B3/B5 from day one (the Epic 8 `EchoTransport` / ADR-0022 Local-adapter pattern) |
| **B5 — Control Plane & Enforcement** | Additive billing routers/services in the Epic 8 layers; entitlement-snapshot delivery; enforcement integration in the owning planes | `platform/api/` (additive) | Publishes projection + snapshot contracts; enforcement lands as small additive checks in each owning plane |
| **B6 — Studio Billing Surface** | Plan/usage/invoice/credit/entitlement screens; plan-change, payment-method, purchase **intents** | `studio/` (additive) | Builds against B5 contract fixtures; owns no state (ADR-0021) |
| **B7 — QA, Governance & Docs** | Golden rating cases, property-based money tests, reproducibility/shadow harness, boundary tests, component specs (§A11.3), INDEX/CHANGELOG/DECISION_LOG upkeep | `platform/tests/` + `docs/` | Continuous; gates every DoD |

**Coordination contract.** Each milestone publishes, before implementation: its **event vocabulary
additions**, its **neutral domain contracts**, its **projection shapes**, and (for B4) its **adapter
capability contract**. B2 and B4 are deliberately structured so they never block and are never
blocked: B2 consumes only the event log; B4 exposes only adapter contracts.

---

# 5. Bounded Contexts

Fourteen contexts, one per commercial-lifecycle stage. Ownership is already fixed by **ADR-0023 §5**;
this table restates it as engineering ownership.

| # | Bounded context | Owns (authoritative) | Consumes (read-only) | Stream | Milestone |
|---|---|---|---|---|---|
| 1 | **Billing Account** | account identity, hierarchy, currency, billing profile | Customer-Platform org identity | B1 | M9.5 |
| 2 | **Commercial Catalog** | plans, tiers, features, meter *definitions* | — | B1 | M9.6 |
| 3 | **Pricing** | price books, price versions, shapes, effective dates | catalog | B1 | M9.7 |
| 4 | **Subscription** | subscription state, term, renewal, proration outcome | account, catalog, pricing, seat counts | B1 | M9.8 |
| 5 | **Entitlement** | grants, snapshots | subscription, contract, credits | B1 | M9.9 |
| 6 | **Metering** | usage records, period aggregates | **`metric.*` (read-only)**, account attribution | B2 | M9.10 |
| 7 | **Rating** | charge lines, discount/coupon application | usage records, price versions, subscription, credits | B2 | M9.11 |
| 8 | **Ledger & Credits** | ledger entries, credit balances/movements, recognition state | charge lines, payments, refunds | B3 | M9.12 |
| 9 | **Invoicing** | invoices, credit notes, numbering, billing-run lifecycle | charge lines, tax determinations, credits | B3 | M9.13 |
| 10 | **Payments & Dunning** | payment attempts, outcomes, dunning state, reconciliation | invoices; payment adapter | B3 + B4 | M9.14 |
| 11 | **Tax** | tax profiles, determinations, evidence | invoices; tax adapter | B3 + B4 | M9.15 |
| 12 | **Refunds** | refunds, approvals, disputes/chargebacks | payments, invoices | B3 | M9.16 |
| 13 | **Marketplace Commerce** | orders, listings' commercial terms, publisher payables | Product Registry artifacts (ref), entitlement, ledger | B1 + B3 | M9.20 |
| 14 | **Enterprise Licensing** | contracts, committed spend, licenses, channel grants | subscription, pricing, entitlement | B1 | M9.21 |

**Non-contexts (explicitly not owned).** Organizations, workspaces, projects, agents, knowledge,
deployments, runtime/conversation/session state, `metric.*`, audit, Product Runtime, Customer
Platform, Observability, infrastructure, provider execution, authentication, authorization, RBAC —
each keeps its frozen owner (ADR-0023 §5.2/§7).

---

# 6. Dependency Graph

```
                          ┌──────────────────────── P1 SUBSTRATE ────────────────────────┐
        M9.1 (plan) ─► M9.2 Foundation ─► M9.3 Application ─► M9.4 Persistence
                                                                    │
        ┌───────────────────────────────────────────────────────────┼──────────────────────────┐
        │                                                           │                          │
        ▼ (B1 — commercial chain)                                   ▼ (B2 — independent)        ▼ (B4 — isolated)
   M9.5 Billing Accounts                                    M9.10 Metering Pipeline      M9.14a Payment Adapter
        ▼                                                           │                    M9.15a Tax Adapter
   M9.6 Commercial Catalog                                          │                    (contracts + offline fakes,
        ▼                                                           │                     buildable from M9.2)
   M9.7 Pricing Engine ──────────────────┐                          │
        ▼                                │                          │
   M9.8 Subscription Engine              └──────────┬───────────────┘
        ▼                                           ▼
   M9.9 Entitlement Engine  ──────────────►  M9.11 Rating Engine
        │                                           ▼
        │                                   M9.12 Revenue Ledger & Credits
        │                                           ▼
        │                                   M9.13 Invoice Engine
        │                                    ▼            ▼
        │                    M9.14 Payments & Dunning   M9.15 Tax Engine
        │                                    ▼
        │                            M9.16 Refunds & Credit Notes
        ▼                                    │
   M9.17 Enforcement Integration ────────────┤
                                             ▼
                                     M9.18 Control Plane API
                                             ▼
                                     M9.19 Studio Billing Surface
                                             ▼
              ┌──────────────────────────────┼──────────────────────────────┐
              ▼                              ▼                              ▼
     M9.20 Marketplace           M9.21 Enterprise Licensing      M9.22 Revenue Observability
              └──────────────────────────────┼──────────────────────────────┘
                                             ▼
                    M9.23 Hardening ─► M9.24 Certification ─► M9.25 Freeze ─► M9.26 Release
```

**Reading the graph.** Three chains run concurrently after M9.4: the **commercial chain** (B1), the
**consumption chain** (B2), and the **adapter chain** (B4). They converge at M9.11 (rating needs both
usage records and prices) and M9.14/M9.15 (money needs adapters). Everything after M9.19 fans out
again.

---

# 7. Critical Path

**`M9.2 → M9.3 → M9.4 → M9.5 → M9.6 → M9.7 → M9.8 → M9.9 → M9.11 → M9.12 → M9.13 → M9.14`**

| Node | Why it is on the path | Slip risk |
|---|---|---|
| **M9.4 Persistence** | Every context needs the durable seam; nothing commercial persists before it | Low — the Epic 8 pattern is proven |
| **M9.8 Subscription Engine** | Entitlement, rating, and invoicing all resolve against a subscription and its period | **Medium-High** — FSM + proration + term changes are the densest domain logic in P2 |
| **M9.9 Entitlement Engine** | The pivotal unlock; four planes will later enforce its snapshot | **High** — contract changes after M9.17 are expensive |
| **M9.11 Rating Engine** | Converges the two chains; everything financial is downstream | **High** — determinism and discount ordering must be right first time |
| **M9.13 Invoice Engine** | Immutability + gapless numbering + billing-run lifecycle; nothing can be re-issued to fix a mistake | **High** |
| **M9.14 Payments & Dunning** | XL; provider FSM, idempotency, callback verification, reconciliation, dunning ladder | **Critical** |

**Off the critical path but Critical-risk: M9.10 Metering.** It does not gate the chain (rating can be
developed against synthetic usage records) but it gates **G1 shadow validation** and therefore *all
revenue*. Treat it as a parallel critical path with its own schedule and its own reviewer.

---

# 8. Parallelizable Work

| Can start | Work | Depends only on |
|---|---|---|
| **Immediately (from M9.2)** | **B4 adapter contracts + deterministic offline fakes** (payment, tax, document, export) | The §A6 capability-plugin pattern; no billing domain at all |
| **Immediately (from M9.2)** | **B7 test harnesses**: golden-case corpus format, property-based money invariants, reproducibility harness, boundary/AST test extensions | The standing constraints |
| **From M9.4** | **B2 metering pipeline** — ingestion, attribution, normalization, aggregation | The event log + the persistence seam. **Independent of catalog/pricing/subscriptions.** |
| **From M9.6** | **Catalog authoring** content work (tier definitions, meter definitions) | Catalog contract |
| **From M9.9 contract publication** | **B5 enforcement scaffolding** in the owning planes (gate identification + classification) — landing at M9.17 | The snapshot contract, not its implementation |
| **From M9.13 contract publication** | **B6 Studio screens** against projection fixtures | Projection shapes |
| **From M9.12** | **M9.22 revenue observability** metric/alert definitions | Ledger + billing event vocabulary |
| **Continuous** | **B7 documentation** (component specs §A11.3, INDEX, CHANGELOG, DECISION_LOG) | Each milestone's outputs |

**Deliberately serialized** (parallelizing these creates rework, not speed): pricing before
subscriptions; subscriptions before entitlements; rating before ledger; ledger before invoicing;
invoicing before payments; payments before refunds; entitlement contract before enforcement
integration.

---

# 9. Risk Analysis

Blueprint §25 holds; this section adds the **delivery** risks that only appear at implementation
planning.

| # | Risk | Sev | Trigger | Mitigation |
|---|---|---|---|---|
| **D-1** | **Metering correctness** — lost, duplicated, or misattributed events silently mis-bill | **Critical** | M9.10 | Idempotent ingestion by source-event identity; total attribution with alerting quarantine; **reproducibility harness**; **G1 shadow mode against real traffic before any charge** |
| **D-2** | **Entitlement contract churn** after enforcement lands in four planes | **High** | M9.9 → M9.17 | Publish and exercise the snapshot for 6+ milestones before integration; versioned snapshots; additive-only contract changes (§A11.4) |
| **D-3** | **Payment edge under-estimation** — dunning, mandates, 3-DS, disputes, multi-provider routing | **High** | M9.14 | Sized XL; adapter + offline fake built at M9.2 (B4); reconciliation designed in, not bolted on |
| **D-4** | **Money-invariant defects** — floats, currency mixing, rounding drift, discount-order ambiguity | **Critical** | M9.11–M9.13 | Property-based invariants + a golden-case corpus from the first commit; declared discount order; immutability enforced by absence of any update/delete path |
| **D-5** | **Adapter leakage** — a vendor concept reaches the core, silently re-creating lock-in | **High** | M9.14/M9.15 | AD3 + boundary review; the same discipline that confines `pipecat` to `runtime/pipeline/` |
| **D-6** | **Billing accidentally on a hot path** — a synchronous check slips into a runtime or deployment flow | **Critical** | M9.17 | ADR-0023 §Decision 4/5/9; enforcement reads a **local cached snapshot**; reviewed at every enforcement point; no enforcing path may call Billing |
| **D-7** | **Frozen-Epic drift** — an "obvious tiny change" to Epic 8 to make integration easier | **High** | M9.17/M9.18 | Boundary test + byte-for-byte verification per milestone; any need to edit a frozen Epic is non-compliant by definition |
| **D-8** | **Scope explosion** — all pricing shapes, all jurisdictions, marketplace payouts in one pass | **High** | P3/P5 | Phase gating; every later shape must land as configuration over the same core or be rejected |
| **D-9** | **Ownership erosion** — a Billing-owned usage counter or org copy "for convenience" | **High** | any | ADR-0023 §6/§7 as a per-milestone acceptance criterion; review checklist item |
| **D-10** | **Reconciliation debt** — divergence discovered late because reconciliation shipped last | **High** | M9.14 | Reconciliation is **in-scope at M9.14**, not deferred to M9.22; M9.22 adds observability, not the mechanism |
| **D-11** | **Governance repeat of the M8.19 condition** — implementation exists but is uncommitted at freeze | **Medium** | M9.25/M9.26 | Freeze and release split into **M9.25** (architecture) and **M9.26** (repository release); commit state is a stated DoD line, not a footnote |
| **D-12** | **Solo-team throughput** — 25 milestones with 7 nominal streams | **High** | whole epic | Streams are *work partitions*, not headcount; the phase gates and the MVP definition are the descope levers, and P5 is explicitly post-MVP |

---

# 10. Milestone Breakdown

Per-milestone: **Purpose · Inputs · Outputs · Deps · Complexity (S/M/L/XL) · Risk · Test strategy ·
Milestone-specific DoD.** The **universal DoD** in §14 additionally applies to every row.

## P1 — Platform Substrate

| M | Purpose | Inputs | Outputs | Deps | Cx | Risk | Test strategy | Milestone DoD |
|---|---|---|---|---|---|---|---|---|
| **M9.2 Foundation** | The billing plane exists: domain services skeleton, tenancy/org scoping, `billing.*` event vocabulary, audit integration, RBAC wiring to the existing Billing Admin role, adapter-seam declarations | ADR-0023, Blueprint, Provider Strategy | Billing plane package; event vocabulary; org-scoping + audit path; **B4 adapter contracts + deterministic offline fakes**; **placement ADR accepted** | frozen Epics 5–8 | **L** | Med | Boundary/AST tests (no frozen Epic imports Billing; Billing imports downward only); org-scoping tests; audit-emission tests; offline-fake contract tests | A billing fact can be written, org-scoped, and audited; **placement ADR for `platform/billing/` accepted**; zero frozen-Epic change |
| **M9.3 Application** | Command/query separation, use cases, unit-of-work boundary for billing mutations | M9.2 | Application layer mirroring the Epic 8 CQRS shape | M9.2 | **M** | Low | One-unit-of-work-per-mutation tests; command/query isolation | Every billing mutation goes through exactly one transactional boundary |
| **M9.4 Persistence** | Durable seam for billing aggregates; composition root; **append-only enforcement** | M9.3, aggregate identification (§16) | Persistence layer + composition wiring; append-only ledger/document storage posture | M9.3 | **L** | Med | Repository contract suite; **append-only proof (no update/delete path on ledger, invoices, credit notes, usage records)**; idempotent-write tests | A billing aggregate persists and reloads; append-only is verified, not asserted |

## P2 — Commercial Core

| M | Purpose | Inputs | Outputs | Deps | Cx | Risk | Test strategy | Milestone DoD |
|---|---|---|---|---|---|---|---|---|
| **M9.5 Billing Accounts** | Account identity + hierarchy + currency + billing profile; org linkage; retention asymmetry | M9.4, Customer-Platform org facts (read-only) | Account lifecycle; **parent/child hierarchy separate from org tenancy**; org-created → Free account | M9.4 | **M** | Med | Org-linkage read-only tests; hierarchy resolution; **deletion-asymmetry test** (org deleted → billing account retained) | An org has exactly one billing account; hierarchy resolves; Billing writes no Customer-Platform fact |
| **M9.6 Commercial Catalog** | Plans, tiers, features, **meter definitions** (unit, aggregation, rounding, **named authoritative source**) | M9.5 | Versioned catalog; the 5 standard tiers defined | M9.5 | **M** | Low | Catalog versioning; **one-authoritative-source-per-meter test**; declared-aggregation test | The five tiers exist as data; every meter names exactly one source |
| **M9.7 Pricing Engine** | Price books, price versions, effective dating, all price shapes | M9.6 | Flat/seat/per-unit/allowance+overage/tiered/volume/package/credit/hybrid/committed/one-off shapes; **price-version pinning** | M9.6 | **L** | Med | Shape-by-shape golden cases; effective-dating boundary cases; **immutability of a published price version** | A price change creates a new version; existing subscriptions stay pinned |
| **M9.8 Subscription Engine** | Subscription FSM, trials, terms, renewal, upgrade/downgrade, proration, seat changes | M9.7 | Deterministic FSM with **injected clock**; period model; proration outcomes; downgrade reject-or-acknowledge | M9.7 | **XL** | **High** | FSM transition table (legal/illegal/terminal-immutability); **deterministic clock tests** for periods/trials/renewal; proration golden cases; downgrade-safety tests | Full lifecycle drives deterministically; a downgrade that would violate current consumption is rejected or explicitly acknowledged — never silently destructive |
| **M9.9 Entitlement Engine** | Entitlement computation + **immutable versioned snapshot** + gate taxonomy | M9.8 | Snapshot contract (published early); grant sources (plan, contract, credits); **gate classification taxonomy** | M9.8 | **L** | **High** | Snapshot immutability/versioning; composition precedence tests; **classification-completeness test** | A snapshot is published, immutable, versioned, cacheable; **every declared gate carries a fail-open/fail-closed/degrade classification**; Billing evaluates no principal and no request |

## P3 — Consumption & Money

| M | Purpose | Inputs | Outputs | Deps | Cx | Risk | Test strategy | Milestone DoD |
|---|---|---|---|---|---|---|---|---|
| **M9.10 Metering Pipeline** *(parallel from M9.4)* | Ingest `metric.*` read-only → usage records; attribution, normalization, incremental aggregation, late-arrival policy, sealing | M9.4, event log | Idempotent ingestion; total attribution + quarantine; period aggregates; **reproducibility harness** | M9.4 | **XL** | **Critical** | **Exactly-once-from-at-least-once** (replay the same events → identical records); out-of-order and late-arrival cases; **unattributable → quarantine + alert, never billed, never dropped**; sealed-period immutability; **no `metric.*` emitted by Billing**; throughput/backlog test | Usage records are reproducible from the event log; Billing measures nothing; a metering outage provably does not affect a conversation |
| **M9.11 Rating Engine** | Usage + seats + fees + discounts + coupons + credits → charge lines | M9.7, M9.10 | Pure deterministic rating; **declared discount-application order** | M9.7, M9.10 | **L** | **High** | **Golden-case corpus** per price shape; property-based money invariants (integer minor units, currency pairing, no drift); **same inputs → identical charge lines**; discount-order fixtures | Rating is a pure function of its inputs and is re-runnable with identical output |
| **M9.12 Revenue Ledger & Credits** | Append-only ledger; credit balances/movements; recognition state | M9.11 | Ledger entries; credit ledger; recognition projection | M9.11 | **L** | **High** | Append-only proof; **balance = sum of movements** invariants; re-derivation of a period from the event log; concurrency isolation | The ledger is append-only with no edit path; a period's ledger is re-derivable and verified |
| **M9.13 Invoice Engine** | Billing-run lifecycle (open→closing→sealed→rated→adjusted→taxed→issued→closed); invoices, credit notes, **gapless numbering**; document adapter | M9.12 | Billing-run FSM (idempotent, resumable); immutable issued invoices; credit notes; self-contained invoice evidence | M9.12 | **L** | **High** | **Idempotent, resumable billing run** (interrupt at each stage → re-run → identical result); **gapless numbering under retry/failure/concurrency**; issued-invoice immutability; zero-amount issuance | A period closes into an immutable, self-explaining invoice; re-running a billing run never duplicates or renumbers |
| **M9.14 Payments & Dunning** | Payment FSM, idempotent provider ops, verified callbacks, **reconciliation**, dunning ladder, multi-provider routing | M9.13, B4 adapters | Payment lifecycle; routing by market/currency/method/health; dunning policy; scheduled reconciliation | M9.13, M9.2(B4) | **XL** | **Critical** | Duplicate/out-of-order/missing-callback convergence; **idempotency under retry (no double charge)**; provider-outage failover; reconciliation-divergence → incident; dunning ladder determinism; **all offline** | Payment lifecycle converges deterministically against a fake provider; reconciliation exists and raises incidents; no instrument data on platform |
| **M9.15 Tax Engine** | Tax profiles, determination via adapter, **immutable evidence**, GST/VAT first | M9.13, B4 tax adapter | Determination + evidence recording; place-of-supply / reverse-charge handling; registration validation | M9.13, M9.2(B4) | **L** | **High** | Determination-evidence immutability; **reporting exports evidence, never recomputes**; adapter-unavailable → **invoicing holds, never guesses**; jurisdiction fixtures | Tax is determined at the edge and recorded as owned evidence; no tax rule exists in the core |
| **M9.16 Refunds & Credit Notes** | Refund lifecycle, approvals + separation of duties, proportional tax reversal, disputes/chargebacks | M9.14, M9.15 | Refund FSM; credit-note path; dispute handling distinct from refunds | M9.14, M9.15 | **M** | **High** | Bounded-refund invariants (never > captured, never duplicated, never on a voided invoice); self-approval rejection; proportional tax reversal; dispute-vs-refund separation | A refund is an append-only event producing a credit note; no ledger edit path exists |

## P4 — Surfaces & Enforcement

| M | Purpose | Inputs | Outputs | Deps | Cx | Risk | Test strategy | Milestone DoD |
|---|---|---|---|---|---|---|---|---|
| **M9.17 Entitlement Enforcement Integration** | Owning planes enforce the snapshot at their own write boundaries; quota warnings; overage policy | M9.9 (+ its exercise through P3) | Additive local gates in Customer Platform / Product Runtime / Actuator / Control Plane; warn→soft→hard ladder | M9.9 | **L** | **High** | **No enforcing path calls Billing** (verified); classification-honoured tests (live conversation continues under stale/absent data); typed rejection + audit; **frozen Epics unchanged** | Limits are enforced as accepted-or-rejected against a cached snapshot; **a Billing outage provably does not interrupt a conversation or an in-flight rollout**; no frozen Epic modified |
| **M9.18 Control Plane API** | Additive org-scoped billing surface: projections + intents | M9.13, M9.14, M9.17 | Read projections (plan, usage, invoices, credits, entitlements) + intent submission (plan change, payment method, purchase) | M9.14, M9.17 | **L** | Med | Org-scoping/RBAC matrix; projection-correctness vs. ledger; **ADR-0021 Allowed/Forbidden Writes assertions** | A customer's plan/usage/invoices are readable and an intent is submittable; **the Billing Ledger remains a Studio Forbidden Write** |
| **M9.19 Studio Billing Surface** | Plan, usage, invoices, credits, entitlements; plan-change/payment-method/purchase intents | M9.18 | Studio screens; **estimated-vs-authoritative labelling** | M9.18 | **L** | Med | Projection-rendering tests; **no Studio-owned billing state** assertion; estimate/invoice labelling test | Studio renders billing projections and submits intents while owning nothing (ADR-0021) |

## P5 — Commercial Breadth

| M | Purpose | Inputs | Outputs | Deps | Cx | Risk | Test strategy | Milestone DoD |
|---|---|---|---|---|---|---|---|---|
| **M9.20 Marketplace Commerce** | Listings' commercial terms, order lifecycle, purchase entitlements, publisher payables | M9.14, M9.19, **seller-of-record decision** | Order FSM; entitlement on completion; payable entries; payout via provider rails | M9.14, M9.19 | **L** | Med | Order→entitlement→ledger integration; refund-revokes-entitlement; payable arithmetic | A purchase grants an entitlement through the **same** Entitlement Engine; **entry criterion: the seller-of-record decision is recorded** (Blueprint R-9) |
| **M9.21 Enterprise Contracts & Licensing** | Contracts, custom price books, committed spend/true-up, consolidated billing, licenses | M9.12, M9.13 | Contract-sourced subscriptions; drawdown/true-up; offline settlement; license grants | M9.13 | **L** | Med | Contract-materialization identity (downstream behaviour identical to self-serve); commitment drawdown; **offline settlement path with no provider involved** | An enterprise contract bills through the same core; a license is an entitlement grant with no payment edge |
| **M9.22 Revenue Observability & Reconciliation** | Billing health metrics/alerts, reconciliation runs, revenue reporting projections, accounting export | M9.14, M9.12 | Ingestion lag, unattributed rate, rating duration, billing-run success, payment success, dunning recovery, reconciliation divergence, snapshot staleness; export adapter | M9.14 | **M** | Med | Metric-derivation correctness; alert firing on real thresholds; **no new observability mechanism introduced** | Billing is observable through the **existing** Epic 4/8 platforms because it is a §A5 producer |

## P6 — Production & Governance

| M | Purpose | Inputs | Outputs | Deps | Cx | Risk | Test strategy | Milestone DoD |
|---|---|---|---|---|---|---|---|---|
| **M9.23 Production Hardening** | Retention, erasure asymmetry, residency, evidence exports, failure drills, metering load validation, secret rotation | all | Hardened billing surface behind existing seams | M9.22 | **L** | **High** | Metering load/soak; provider-outage and callback-storm drills; **total-Billing-outage drill (product unaffected)**; retention/erasure tests; credential rotation | Production-grade under the documented failure model; no new domains, no new public surface |
| **M9.24 Certification** | Independent certification against ADR-0023, the Blueprint, and the Provider Strategy | M9.23 | Certification report with an honest verdict | M9.23 | **M** | Low | Executable certification suite (boundaries, append-only, determinism, reproducibility, no-vendor-types, no-synchronous-billing, gate classification completeness) | Certification is **reproducible as tests**, not a one-off document |
| **M9.25 Architecture Freeze** | Billing Platform v1.0 Freeze Manifest: frozen interfaces, ownership, dependency direction, extension policy | M9.24 | Freeze manifest | M9.24 | **S** | Low | n/a (governance) | Architecture frozen; extension policy stated; **repository/commit state recorded honestly** |
| **M9.26 Repository Release** | Commit, tag, baseline **v4.0.0** recommendation, release notes | M9.25 | Tagged release | M9.25 | **S** | Low | Release checks | The release is actually committed and tagged — **closing the M8.19 governance condition rather than repeating it** |

---

# 11. Complexity Estimate

| Complexity | Milestones | Count |
|---|---|---|
| **XL** | M9.8 Subscriptions · M9.10 Metering · M9.14 Payments | 3 |
| **L** | M9.2 · M9.4 · M9.7 · M9.9 · M9.11 · M9.12 · M9.13 · M9.15 · M9.17 · M9.18 · M9.19 · M9.20 · M9.21 · M9.23 | 14 |
| **M** | M9.3 · M9.5 · M9.6 · M9.16 · M9.22 · M9.24 | 6 |
| **S** | M9.25 · M9.26 | 2 |

**Risk-weighted concentration.** Four milestones carry **Critical** risk or sit at the top of the
critical path — **M9.8, M9.10, M9.11, M9.14** — and together they represent the majority of the
epic's true difficulty. Sequencing, reviewer attention, and schedule buffer belong there. Everything
in P5 is *breadth*, not *depth*: each reuses the core and is a descope candidate under pressure.

**Relative sizing note.** These are complexity classes, not calendar estimates. For calibration
against this repository: Epic 8 delivered 17 implementation milestones of comparable class
distribution and finished with ~218 capability tests on a 1774-test repository-wide suite. Epic 9 is
smaller in surface but denser in correctness obligation.

---

# 12. Testing Strategy

**Testing posture:** every billing test is **deterministic, offline, and hermetic** — no socket, no
vendor call, no wall-clock dependence, no money moved (M9.0C AD7 + the Epic 8 `EchoTransport`
precedent).

| Layer | What it proves | Where |
|---|---|---|
| **Money invariants (property-based)** | Integer minor units; currency always paired; no float ever reaches a money field; sums are exact; rounding is policy-driven and stable | From M9.2, applied at every money-touching milestone |
| **Golden-case corpus** | Rating, proration, discounts, credits, and tax application produce exactly the expected charge lines for a curated set of scenarios per price shape | M9.7, M9.11, M9.15, M9.16 |
| **FSM suites** | Legal/illegal transitions, terminal immutability, duplicate rejection, guard behaviour — for subscription, billing-run, invoice, payment, refund, dunning, order | Every FSM milestone (the `core/fsm` discipline) |
| **Determinism & reproducibility** | Same inputs → identical outputs; **a period's usage records and ledger are re-derivable from the event log** | M9.10, M9.11, M9.12 |
| **Idempotency** | Re-ingesting an event, re-running a billing run, retrying a provider call, replaying a callback — all no-ops | M9.10, M9.13, M9.14 |
| **Append-only proofs** | No update/delete path exists on ledger entries, issued invoices, credit notes, or sealed usage records | M9.4 and re-verified at M9.12/M9.13 |
| **Boundary / AST tests** | No frozen Epic imports Billing; Billing imports only downward; **no vendor type outside an adapter**; Billing emits no `metric.*` | Every milestone |
| **Non-blocking proofs** | **No enforcing path calls Billing**; a total Billing outage leaves conversations, deployments, and Studio functioning; enforcement honours its gate classification under stale/absent data | M9.17, re-run at M9.23 |
| **Adapter contract suites** | Every adapter implementation satisfies one contract; capability flags are honoured; unsupported operations fail **declaredly**, never silently | M9.2 (fakes), M9.14, M9.15 |
| **Failure drills** | Provider outage, callback storm, duplicate/out-of-order callbacks, tax-adapter unavailability, metering backlog, reconciliation divergence | M9.23 |
| **Scale/soak** | Metering throughput and backlog recovery; large-account billing run; hot (reseller/enterprise) account partitioning | M9.23 |
| **Shadow validation (G1)** | The full pipeline against **real production usage**, producing no charges, reconciled against the event log | Gate G1, before any customer charge |
| **Certification suite** | The architecture review rendered as repository-wide assertions | M9.24 |

**Non-negotiable test rule:** a milestone that touches money ships its golden cases and property
invariants **in the same change-set** as the logic. Retrofitted money tests are not accepted.

---

# 13. Release Strategy

Four gates. Each is a **capability gate**, not a date.

| Gate | Name | Entry criteria | What is live | Exit criteria |
|---|---|---|---|---|
| **G1** | **Internal / shadow billing** | M9.2–M9.13 complete | The full pipeline runs against real usage; invoices are generated internally; **no customer is charged** | Usage records reproduce from the event log; ledger re-derivation matches; unattributed usage ≈ 0; golden cases green |
| **G2** | **Design-partner billing** | G1 + M9.14, M9.15, M9.17, M9.18, M9.19 | Real charges for a small, consented cohort with manual oversight before each invoice run | Reconciliation clean for consecutive cycles; no disputed invoice caused by platform error; dunning exercised end-to-end |
| **G3** | **Self-serve GA** | G2 + M9.16, M9.22, M9.23 | Free / Starter / Pro live; self-serve upgrade, downgrade, cancel, payment recovery | Payment success and dunning recovery within targets; alerting proven; support runbook published |
| **G4** | **Enterprise & marketplace** | G3 + M9.20, M9.21 (+ recorded seller-of-record decision) | Contracts, committed spend, offline settlement, licensing, marketplace commerce | An enterprise contract and a marketplace order each bill correctly through the same core |

**Release discipline.** No customer money moves before G1 has *passed*, not merely been *attempted*.
Business/Enterprise tiers do not open before G4. Reseller and white-label are post-Epic-9 and reuse
the same core by construction (Blueprint §17).

---

# 14. Definition of Done (per milestone)

**Universal DoD — every milestone, without exception:**

1. **ADR-0023 compliance:** only §6 Allowed Writes were written; no §7 Forbidden Write was touched.
2. **Frozen Epics 5/6/7/8 byte-for-byte unchanged** (no tracked frozen-Epic source modified).
3. **Boundary tests green:** no frozen Epic imports Billing; Billing imports only downward; no vendor
   type outside an adapter; Billing emits no `metric.*`.
4. **Determinism:** any new lifecycle is table-driven with an injected clock; any new computation is a
   pure function of declared inputs.
5. **Money invariants hold** where money is touched (integer minor units, currency pairing,
   append-only, immutability, declared ordering).
6. **Org-scoped (§A11.2) and audited (§A5)** — every new fact and every mutation.
7. **Offline and hermetic:** tests open no socket, call no vendor, and move no money; the deterministic
   adapter is the default.
8. **Tests in the same change-set**, with the milestone-specific suites from §10/§12.
9. **Non-blocking preserved:** nothing added to a runtime, session, or deployment critical path.
10. **Component specs (§A11.3)** written for every new component; **INDEX.md**, **CHANGELOG.md**, and
    **DECISION_LOG** updated in the same change-set (documentation mandate).
11. **Lint/format clean**; repository-wide suite green (**no regression**); build-green at the
    milestone boundary.
12. **Independently demonstrable:** the milestone's purpose can be shown end-to-end without a later
    milestone existing.

Milestone-specific DoD lines are in the §10 tables and are additional, never substitutes.

---

# 15. Recommended Development Order

```
1.  M9.2  Foundation                    ── with B4 adapter contracts + offline fakes, and B7 harnesses, in the SAME milestone
2.  M9.3  Application
3.  M9.4  Persistence                   ── from here, M9.10 (metering) runs continuously in parallel
4.  M9.5  Billing Accounts              ║  ┌─ M9.10 Metering Pipeline (B2, parallel, own reviewer)
5.  M9.6  Commercial Catalog            ║  │
6.  M9.7  Pricing Engine                ║  │
7.  M9.8  Subscription Engine   (XL)    ║  │
8.  M9.9  Entitlement Engine            ║  └─ snapshot contract PUBLISHED here; enforcement scaffolding may begin
9.  M9.11 Rating Engine                 ◄── converges the two chains
10. M9.12 Revenue Ledger & Credits
11. M9.13 Invoice Engine                ══► GATE G1 (shadow billing, no customer charged)
12. M9.14 Payments & Dunning    (XL)
13. M9.15 Tax Engine
14. M9.17 Enforcement Integration
15. M9.18 Control Plane API
16. M9.19 Studio Billing Surface        ══► GATE G2 (design-partner billing)
17. M9.16 Refunds & Credit Notes
18. M9.22 Revenue Observability & Reconciliation
19. M9.23 Production Hardening          ══► GATE G3 (self-serve GA)
20. M9.20 Marketplace Commerce
21. M9.21 Enterprise Contracts & Licensing  ══► GATE G4
22. M9.24 Certification
23. M9.25 Architecture Freeze
24. M9.26 Repository Release
```

**Three ordering judgments worth stating explicitly:**

- **M9.10 is scheduled first and finished last.** It starts at M9.4 and remains under active
  validation through G1. It is the only milestone deliberately given a parallel schedule and a
  dedicated reviewer, because it is the only one whose defects are simultaneously a revenue loss, a
  trust loss, and a legal exposure.
- **Refunds (M9.16) move after the first customer surface.** Refund pressure only becomes real once
  customers are charged (G2); building it before the payment path is exercised risks designing for
  imagined cases.
- **Tax (M9.15) follows payments.** Invoicing *holds* rather than guessing when tax is undetermined
  (§Failure Model), so the payment path can be completed and exercised first — and G2 does not open
  in a tax-bearing jurisdiction until M9.15 is done.

---

# 16. Repository Impact

**Planning-level identification only — no schema, no tables, no migrations, no routers, no code.**

| Area | Impact | Governance |
|---|---|---|
| **New top-level package** (the Billing plane, peer of `product/`, `actuator/`, `integration/`, `bridge/`) | **One new top-level directory** | **Structural placement ADR required as an M9.2 entry criterion** (CODING_STANDARDS §2; ADR-0018/ADR-0022 precedent). ADR-0023 already ratified that Billing is a new plane; this records *where*. |
| **Adapter sub-package** (payment / tax / document / export) | Inside the billing plane | Follows the Epic 5 provider-plugin and ADR-0022 infrastructure-adapter patterns |
| **Epic 8 layers** (foundation / application / persistence / control-plane API) | **Additive extensions only** | Mirrors how M8.16 added the Actuator surface without touching prior services |
| **Studio** | **Additive screens** | ADR-0021 posture unchanged: no Studio-owned billing state |
| **Persistence namespace** | A distinct billing table-name prefix, aggregates identified per §5 | **Aggregates identified, no tables designed**; schema and migrations are milestone work (M9.4+), not planning work |
| **Migration chain** | Continues the existing chain additively from `0012` | Chain validity is a DoD line at every persistence-touching milestone |
| **Tests** | New billing suites + extensions to the existing boundary/certification suites | Repository-wide suite must stay green with no regression |
| **Docs** | `docs/architecture/epic9/*` component specs (§A11.3); INDEX/CHANGELOG/DECISION_LOG per milestone; `docs/releases/epic9-billing-platform-v1.0.md` at M9.25 | Documentation mandate |
| **Frozen packages** | **Zero change** | Verified per milestone |

---

# 17. Future Extension Points

Declared now so later work is additive by construction (§A12) and never a redesign:

| Extension | Seam it uses | Status |
|---|---|---|
| **Additional payment providers / markets** | Payment Adapter + routing policy | Designed in (M9.0C §13) |
| **Merchant-of-record channel** per market | Channel-scoped settlement + ordinary ledger facts | Designed in (M9.0C §16) — **never an owner** |
| **Additional tax jurisdictions / e-invoicing mandates** | Tax Adapter + Document Adapter | Designed in |
| **Reseller / OEM / white-label** | Billing-account hierarchy + scoped price books + composing entitlement resolution | Designed in (Blueprint §17; recommended ADR-0030) |
| **Marketplace payouts at scale** | Payable balance + provider payout rails | Payables owned; payout lifecycle is post-Epic-9 |
| **Accounting / ERP integration** | Export Adapter over the ledger | Export only — never a second bookkeeper |
| **New meters and price shapes** | Catalog + price book (configuration) | Additive by design |
| **Third-party rating/invoicing engine adoption** | The same adapter seams | Left open by M9.0C — adoptable **behind** the seam without moving ownership |
| **Usage-record compaction / tiered retention** | Storage policy behind the persistence seam | Named in Blueprint §24 |
| **Revenue-recognition reporting depth** | Projection over the ledger | Owned; depth is additive |

---

# 18. Final Recommendation

**Adopt this plan and begin implementation at M9.2.**

Four things decide whether Epic 9 succeeds, and they are all scheduling decisions made here rather
than engineering decisions made later:

1. **Start metering (M9.10) on day one and never let it lag.** It is the only Critical-risk
   correctness milestone, it is off the critical path so it will *feel* deferrable, and deferring it
   is the most likely way to reach G1 late or — far worse — to reach G2 with quietly wrong numbers.
2. **Build the adapter contracts and their deterministic fakes at M9.2, before the engines.** This is
   what keeps the payment and tax engines vendor-shaped-free, keeps the entire suite hermetic, and
   makes the provider strategy real rather than aspirational.
3. **Publish the entitlement snapshot contract at M9.9 and enforce it at M9.17 — in that order, with
   distance between them.** Enforcement touches four planes; the contract must be boring by the time
   it gets there.
4. **Do not move customer money before G1 passes.** Shadow the entire pipeline against real usage,
   reproduce the ledger from the event log, and reconcile. Billing defects are found by customers or
   by us; the gate is how we make it us.

The plan introduces **no new architecture and no new ADR**. It realizes ADR-0023, the Blueprint, and
the Provider Strategy as 25 additive, independently testable, build-green milestones. Epics 5, 6, 7,
and 8 remain frozen and byte-for-byte unchanged throughout.

Two governance items carry forward and are tracked as DoD lines rather than footnotes: the
**structural placement ADR** for the billing plane (M9.2 entry criterion), and the **repository
release** at M9.26 — split from the architecture freeze precisely so Epic 9 closes the M8.19
governance condition instead of repeating it.
