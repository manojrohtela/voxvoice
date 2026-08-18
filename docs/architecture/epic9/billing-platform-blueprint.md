# VoxVoice — Epic 9.0A Billing & Subscription Platform Blueprint

| | |
|---|---|
| **Milestone** | **M9.0A** — Epic 9 Phase 0A (design only) |
| **Status** | 🟡 **Design only.** No implementation, no code, no APIs, no schema, no migrations, no UI. Architecture only. |
| **Date** | 2026-08-01 |
| **Depends on** | Constitution v2 (ratified), ADR-0001…ADR-0022, Runtime Platform v1.0 (Epic 5, frozen), Execution Platform v1.0 (Epic 6, frozen), Product Runtime v1.0 (Epic 7, frozen), Customer Platform v1.0 (Epic 8, frozen), Repository Baseline v3.0.0 |
| **Modifies** | **Nothing.** Epics 5–8 are frozen and are not touched. Every element of this blueprint is **additive** (§A12). |
| **Authority** | A design milestone cannot ratify. This blueprint **recommends** ADRs; ratification happens in M9.0B/M9.0C. |

---

## The single organizing principle (read this first)

> **The Billing Platform owns revenue. It owns nothing else.**
>
> It never measures usage — Observability and the Runtime already emit `metric.*`, and Billing
> *consumes* that authoritative stream (§A2.5, ADR-0021's recommended ADR-0023). It never owns
> customers — the Customer Platform does (Epic 8). It never owns deployments — the Actuator does
> (ADR-0022). It never owns conversations, knowledge, or provider state — Epic 5 does. It never
> writes into any of them.
>
> Billing is a **downstream consumer of authoritative facts and the sole writer of the facts money
> depends on**: plans, prices, subscriptions, entitlements, billable usage records, credits,
> invoices, payments, refunds, taxes, contracts, licenses, and the revenue ledger.
>
> Every architectural question in this document resolves against that sentence.

---

# 1. Executive Summary

Epic 9 designs the **Billing & Subscription Platform** — the revenue plane of VoxVoice. It converts
what the platform already knows (authoritative usage events, org/workspace/project identity,
deployment and conversation activity) into what a commercial SaaS needs (plans, subscriptions,
entitlements, metered charges, invoices, payments, taxes, refunds, and licensing) **without moving a
single ownership boundary** established by Epics 5–8.

The design rests on six load-bearing decisions:

1. **One writer per fact, applied to money.** Billing is the sole writer of revenue facts and the
   sole reader-of-record for billable usage. It duplicates no platform state; it references it.
2. **Metering consumes, never measures.** The `metric.*` stream (§A5) is the authoritative input.
   Billing's ingestion is idempotent and replay-safe, so the revenue ledger is reproducible from the
   event log — the same property Replay gives conversations.
3. **Entitlement is a decision, enforcement is a boundary.** Billing *decides* what an organization
   is entitled to; the Customer Platform, Product Runtime, and Actuator *enforce* it at their own
   write boundaries via a read-only entitlement snapshot (accepted-or-rejected, §A2.6). Billing never
   reaches into another plane to block anything.
4. **Billing is never in the conversation critical path.** No turn, no session, no deployment step
   may block on a synchronous billing call. Enforcement uses cached snapshots with an explicit
   fail-open / fail-closed classification per gate.
5. **Own the loop, rent the edges (§A2).** Subscription lifecycle, rating, invoicing, dunning, and
   refund routing are deterministic, table-driven VoxVoice logic. Payment processing and tax
   determination are non-deterministic external edges behind adapters — exactly the shape ADR-0022
   gave infrastructure.
6. **One revenue core, many commercial shapes.** Usage-based, seat-based, hybrid, credits, trials,
   coupons, enterprise contracts, marketplace purchases, licensing, reseller, and white-label are all
   expressed as **configuration and composition over the same core** — never as parallel billing
   systems.

The result is a revenue plane that is deterministic, auditable, reproducible, multi-tenant-safe,
provider-portable, and additive on a frozen stack — and that can carry VoxVoice from a Free tier to
enterprise contracts and a reseller/white-label channel without re-architecture.

---

# 2. Vision

**Billing is a product surface, not an afterthought.**

VoxVoice's strategic advantage is provider portability: it can move STT/TTS/LLM vendors underneath a
stable contract. That advantage is only *visible* to a customer through billing — cost transparency,
predictable pricing, honest metering, and a margin story the customer can see. A billing platform
that is opaque, estimated, or reconciled by hand destroys that advantage.

The vision, therefore:

- **Every charge is explainable down to the event that caused it.** A customer can ask "why is this
  line ₹4,120?" and the platform can answer with the exact metered events, the rate applied, and the
  period — because the ledger is derived from an immutable event log, not from a counter.
- **Never a silent overage surprise.** Limits are enforced as accepted-or-rejected decisions at write
  time (§A2.6), with warning thresholds before the wall — the same determinism the runtime gives
  conversations.
- **The commercial model can change without an engineering project.** New tiers, new meters, new
  currencies, new discount shapes, new markets, and new channels are catalog and policy changes over
  a fixed core.
- **Enterprise and channel readiness is inherited, not rebuilt.** Custom contracts, licensing,
  resellers, and white-label all reuse the same subscription/entitlement/ledger spine.

---

# 3. Architecture Overview

The Billing Platform is a **new additive plane** above the frozen stack, peer to the Customer
Platform and the Deployment Actuator, depending downward only.

```
Studio (Consumer + Management Surface — ADR-0021, owns no state)
   │  plan intent · payment method intent · purchase requests      ▲  projections: plan, usage, invoices, entitlements
   ▼                                                                │
Control Plane (auth · org scoping · routing · authorization)
   │
   ├─► BILLING PLATFORM  (Epic 9 — new, additive)
   │        Catalog · Subscription · Metering · Rating · Entitlement ·
   │        Ledger · Invoicing · Payments · Tax · Dunning · Refunds ·
   │        Commerce · Licensing
   │            │                                   │
   │            │ (read-only, downward)             │ rented edges (adapters)
   │            ▼                                   ▼
   │     Customer Platform (Epic 8, frozen)   Payment Providers · Tax Providers ·
   │     Product Runtime  (Epic 7, frozen)    Billing-document delivery · Accounting export
   │     Deployment Actuator (Epic 8, frozen)
   │            │
   └────────────┴─► Event Log / `metric.*` (Epic 4/5 — the authoritative usage truth Billing meters)
```

**Structural rules.**

| Rule | Statement |
|---|---|
| **R-A1** | Billing depends **downward only**: on the Event Log, on Customer Platform identity, on Product Runtime artifacts, on Actuator status — all **read-only**. |
| **R-A2** | **No frozen Epic ever depends on Billing.** No Epic 5/6/7/8 component imports, calls, or requires it. The graph stays acyclic (AR-15). |
| **R-A3** | Billing is reached only through the **Control Plane** (auth, org scoping, RBAC, audit) — never directly by Studio, never directly by a runtime. |
| **R-A4** | Billing is a **new top-level plane**, not a module inside the Customer Platform. Revenue is a distinct ownership domain with a distinct lifecycle, retention, and compliance posture. |
| **R-A5** | Billing is an **event producer** on the §A5 envelope (`billing.*`, `metric.billing.*`) exactly as the Actuator is — so Replay, Gateway, Observability, and Audit get it for free, with no consumer change. |
| **R-A6** | Billing's own behaviour is **deterministic and table-driven** (§A2.4): every lifecycle is an explicit state machine with an injected clock. Non-determinism is confined to adapter edges. |
| **R-A7** | Every Billing fact is **org-scoped** (§A11.2) and every mutation is audited (§A5/§A11.2, Epic 8 Audit Platform). |

**The thirteen conceptual components** (single responsibility each, AR-50; each specified per §A11.3
when built — these are architectural concepts, not code artifacts):

| Component | Single responsibility |
|---|---|
| **Product Catalog** | Owns the commercial catalog: plans, tiers, features, meters, and their versioned definitions. |
| **Price Book** | Owns prices: currency, unit prices, tiering/volume/graduated shapes, effective dating. Separate from the catalog so a price change is never a plan change. |
| **Subscription Engine** | Sole writer of subscription state; drives the subscription FSM (trial → active → past-due → paused → cancelled → expired); owns upgrades, downgrades, proration, term, renewal. |
| **Metering Pipeline** | Ingests `metric.*` idempotently, normalizes to billable units, and writes **usage records**. Measures nothing itself. |
| **Rating Engine** | Deterministically converts usage records + price book + subscription into **charge lines**. Pure function of its inputs; replayable. |
| **Entitlement Engine** | Sole writer of entitlement grants; computes the entitlement snapshot (features, quotas, limits) from subscription + contract + credits. Decides; never enforces. |
| **Credit Ledger** | Sole writer of credit balances (prepaid credits, promotional grants, goodwill), append-only. |
| **Revenue Ledger** | Sole writer of the immutable, append-only double-entry-shaped record of every money-affecting fact. The system of record for revenue. |
| **Invoicing Engine** | Assembles charge lines + taxes + discounts + credits into an invoice; drives the invoice FSM; invoices are immutable once issued. |
| **Payment Engine** | Drives the payment FSM against a payment adapter; reconciles adapter truth into ledger truth; owns dunning/recovery routing. |
| **Tax Engine** | Determines tax treatment (jurisdiction, GST/VAT, reverse charge, exemption) via a tax adapter; records the determination as evidence. |
| **Commerce Engine** | Owns marketplace orders and purchase lifecycle; converts a completed order into entitlement + ledger effects. |
| **Licensing Engine** | Owns enterprise licenses, contract terms, and channel (reseller/white-label) grants; expresses them as subscriptions + entitlements, never as a second system. |

---

# 4. Ownership Model

Ownership is the load-bearing part of this blueprint. **One writer per fact (§A2.5).**

## 4.1 Facts Billing owns (sole writer)

| Fact | Owner |
|---|---|
| Plan / tier / feature / meter **definitions** | Product Catalog |
| Prices, currencies, price shapes, effective dates | Price Book |
| Subscription state, term, renewal, proration outcome | Subscription Engine |
| Billable **usage records** (normalized, deduplicated, attributed) | Metering Pipeline |
| Charge lines (rated usage, seats, fees, discounts) | Rating Engine |
| Entitlement grants and the entitlement snapshot | Entitlement Engine |
| Credit balance and credit movements | Credit Ledger |
| Revenue ledger entries | Revenue Ledger |
| Invoices, invoice numbering, credit notes | Invoicing Engine |
| Payment attempts, outcomes, dunning state | Payment Engine |
| Tax determinations and tax evidence | Tax Engine |
| Refunds and their approval trail | Payment Engine (refund path) |
| Marketplace orders | Commerce Engine |
| Enterprise contracts, licenses, channel grants | Licensing Engine |

## 4.2 Facts Billing consumes but never owns

| Fact | Authoritative owner | Billing's use |
|---|---|---|
| `metric.*` usage events | **Runtime producers** (§A5, Epics 4–8) | metering input — read-only, never re-emitted as truth |
| Organization / workspace / project / member identity | **Customer Platform** (Epic 8) | attribution and scoping keys — referenced, never copied as truth |
| Seat membership and roles | **Customer Platform** | seat-count input for seat-based pricing |
| Conversation / session state | **Runtime Platform** (Epic 5) | never read directly; only its emitted metrics |
| Deployment state | **Deployment Actuator** (ADR-0022) | deployment-billing input via events |
| Knowledge storage footprint | **Knowledge Runtime** (Epic 5) | storage-billing input via metrics |
| Product / package / manifest identity | **Product Runtime** (Epic 7) | marketplace listing reference |
| Estimated cost dashboards | **Observability Platform** (Epic 8) | *not* an input — Observability's cost is directional; Billing's is authoritative |
| Audit records of billing actions | **Audit Platform** (Epic 8) | Billing emits; Audit records |

## 4.3 The two boundaries most at risk of erosion

**(a) Observability's estimated cost vs. Billing's authoritative charge.** Epic 8 deliberately scoped
`CostService` as *estimated, directional, not billing*. Epic 9 must **not** consume that estimate, and
must **not** replace it. They are two different facts derived from the same events: Observability
answers "what is this costing us?"; Billing answers "what do we charge you?". Neither reads the other.

**(b) Studio's billing module.** ADR-0021 already fixed this: Studio renders plan/usage/invoices and
manages *plan intent*; the **Billing Ledger is an explicit Forbidden Write** for Studio. Epic 9
supplies the owner ADR-0021 anticipated; it does not relax Studio's posture by one line.

## 4.4 What Billing may never do

- Never write to any Epic 5/6/7/8 store.
- Never emit or mutate `metric.*` events (it is a consumer; it emits its own `billing.*`).
- Never terminate a conversation, cancel a deployment, or delete knowledge. It emits a decision; the
  owning plane acts on it.
- Never hold a customer identity of its own. A billing account **references** an organization.
- Never be a synchronous dependency of a turn, a session start, or a deployment step.

---

# 5. Revenue Model

VoxVoice monetizes four independent axes. The architecture keeps them independent so any product can
combine them without a new mechanism.

| Axis | What it charges for | Billing shape |
|---|---|---|
| **Platform access** | The right to use a tier | Recurring subscription fee (monthly/yearly) |
| **Seats** | Named humans with platform access | Recurring per-seat fee, proration on change |
| **Consumption** | What the runtime actually did | Metered usage rated per unit |
| **Commerce** | One-off purchases | Marketplace orders, add-ons, licenses |

**Metered dimensions (Phase-1 meter set).** Each is a distinct meter with its own unit, aggregation
rule, and price shape:

| Meter | Unit | Aggregation | Source of truth |
|---|---|---|---|
| Voice minutes | second → billed minute | sum over period, rounding policy per plan | Runtime session metrics |
| AI tokens | token (input/output separated) | sum, per model class | Reasoning/Provider metrics |
| Speech synthesis | character or second | sum | Provider metrics |
| Speech recognition | second | sum | Provider metrics |
| Knowledge storage | byte-month | time-weighted average | Knowledge metrics |
| Knowledge ingestion | document / page / embedding | sum | Knowledge metrics |
| Vector search | query | sum | Knowledge metrics |
| Tool calls | invocation | sum, optionally per tool class | Tool Runtime metrics |
| Workflow runs | run / step | sum | Workflow metrics |
| Deployments | apply / active environment-hour | sum / time-weighted | Actuator metrics |
| API calls | request | sum, per API class | Control Plane metrics |
| Concurrency | peak concurrent sessions | max over period | Runtime metrics |

**Revenue-model invariants.**

- **Every meter has exactly one authoritative source.** Two sources for one meter is a defect, not a
  configuration.
- **A meter is defined once and priced many times.** Adding a tier never adds a meter.
- **Aggregation is declared, not implied.** Sum / max / time-weighted / unique-count is part of the
  meter definition, because it changes the ledger.
- **Rounding is a policy, versioned with the price.** Rounding silently changing is a revenue
  incident.

---

# 6. Subscription Model

## 6.1 Plan structure

Five standard tiers plus a contract shape:

| Tier | Commercial shape |
|---|---|
| **Free** | No fee. Hard-capped meters, single environment, community support, platform branding. Fail-closed at cap. |
| **Starter** | Low recurring fee + modest included allowances, overage billed. Small seat count. |
| **Pro** | Recurring + seats + usage. All environments. Higher limits, standard providers. |
| **Business** | Recurring + seats + usage. Approvals, SSO-lite, higher quotas, cost dashboards, priority support. |
| **Enterprise** | **Contract-driven.** Custom prices, custom quotas, committed spend, negotiated terms, SSO/SCIM, private providers, compliance addenda, SLA, white-label. |

**Architectural point:** Enterprise is *not* a separate code path. It is a subscription whose catalog
plan, price book entries, and entitlement overrides come from a **Contract** rather than the public
catalog. Everything downstream — rating, invoicing, tax, ledger — is identical.

## 6.2 Subscription lifecycle (deterministic FSM)

```
        ┌──────────► TRIALING ──────┐
        │               │           │ trial converts
   DRAFT│               │ trial ends│
        ▼               ▼           ▼
     PENDING ────────► ACTIVE ◄─────┘
                         │  │  │
      payment fails      │  │  └──────────► SCHEDULED_CHANGE (pending downgrade/cancel at period end)
                         │  │                       │
                         ▼  ▼                       ▼
                     PAST_DUE ──recovered──►     ACTIVE
                         │
                 dunning exhausted
                         ▼
                     SUSPENDED ──reactivated──► ACTIVE
                         │
                         ▼
                     CANCELLED ────► EXPIRED (terminal)
                         ▲
                    PAUSED ┘  (explicit pause, entitlements reduced, meters frozen)
```

**Invariants.**

- Every transition is explicit and table-driven; there is no implicit state.
- **Terminal states are immutable** (the `core/fsm` discipline from M5.0).
- The **clock is injected**. Period boundaries, trial expiry, and renewal are computed, never
  wall-clock-sampled ad hoc — this is what makes billing runs reproducible and testable.
- A subscription always has exactly one **current period**; charges attach to a period, never to
  "now".

## 6.3 Upgrades, downgrades, proration

| Change | Timing | Entitlement effect | Financial effect |
|---|---|---|---|
| **Upgrade** | Immediate | Immediate expansion | Prorated charge for the remainder of the period; optionally invoiced immediately |
| **Downgrade** | **End of period** (default) | Deferred contraction | No immediate credit; new price applies next period |
| **Immediate downgrade** (explicit) | Immediate | Immediate contraction | Prorated credit issued as **credit note or credit balance — never a silent refund** |
| **Seat increase** | Immediate | Immediate | Prorated seat charge |
| **Seat decrease** | End of period | Deferred | No mid-period credit by default |
| **Plan switch across billing intervals** (monthly ↔ yearly) | Immediate | Immediate | Remaining value credited, new term charged; term restarts |
| **Cancellation** | End of period (default) or immediate | Contract-dependent | Immediate cancel with refund runs the Refund Lifecycle, never an ad-hoc ledger edit |

**Downgrade safety.** A downgrade that would violate current consumption (e.g. more workspaces than
the target plan allows) must be **rejected or explicitly acknowledged with a remediation plan** at
request time — never silently accepted and enforced later by deleting customer resources. Billing
decides; the Customer Platform is the only thing that could ever remove a resource, and only on an
explicit customer action.

## 6.4 Trials

- A trial is a subscription state, not a plan. Same plan, same meters, `TRIALING` state, an entitlement
  overlay, and an explicit end date.
- **Trial abuse is an identity problem, not a billing problem** — eligibility is evaluated against the
  Customer Platform's org/identity facts; Billing records the verdict.
- Trial → paid conversion is a transition, not a new subscription: history, usage, and entitlements
  are continuous.

---

# 7. Usage Metering Model

The metering pipeline is the most correctness-critical part of Epic 9. It is designed so that **the
revenue ledger is reproducible from the event log**.

```
metric.* (authoritative, at-least-once)
   │
   ▼
[1] Ingestion        — idempotent by event identity; out-of-order tolerant; late-arrival windowed
   │
   ▼
[2] Attribution      — resolve org / workspace / project / agent / subscription / period
   │
   ▼
[3] Normalization    — raw signal → meter unit (declared aggregation + rounding policy)
   │
   ▼
[4] Usage Record     — immutable, append-only, deduplicated  ◄── the billable fact Billing owns
   │
   ├──► [5] Aggregation  — period totals per meter per scope (quota checks, dashboards)
   │
   └──► [6] Rating       — usage × price book × subscription = charge lines
```

**Guarantees and how they are achieved.**

| Guarantee | Mechanism |
|---|---|
| **Exactly-once billing from at-least-once delivery** | Every usage record is keyed by the source event's identity; re-ingesting the same event is a no-op. |
| **Replayable** | Ingestion is a pure function of the event stream + catalog version; reprocessing a period yields identical usage records. |
| **Late events are handled explicitly** | A declared late-arrival window per meter. Inside the window: attach to the original period. Outside: attach to the current period as an explicitly labelled adjustment. Never silently dropped, never silently backdated after invoice issue. |
| **Closed periods stay closed** | Once a period is **sealed**, usage records for it are frozen. Corrections after sealing become adjustments in a later period or a credit note — never a mutation. |
| **Attribution is total** | Every usage record resolves to exactly one billing account. Unattributable usage goes to an explicit quarantine, is alerted on, and is never silently billed or silently discarded. |
| **No double counting across planes** | A meter has one source. If two planes emit an overlapping signal, exactly one is designated authoritative in the meter definition. |
| **Metering never blocks the runtime** | Ingestion is fully asynchronous and downstream of the event log. A metering outage delays billing; it never degrades a conversation. |

**Backpressure and outage posture.** If ingestion falls behind, the event log remains the truth and
metering catches up. Quota enforcement, which depends on aggregates, degrades to the **last known
aggregate + a declared staleness bound**, and each gate's fail-open/fail-closed classification (§9)
decides behaviour. This is stated up front because "quota enforcement under stale data" is the single
most common source of both revenue leakage and customer-visible unfairness.

---

# 8. Pricing Model

Pricing is **catalog + price book + policy**, all versioned, all effective-dated. Nothing about
pricing is a structural change.

## 8.1 Price shapes supported

| Shape | Description |
|---|---|
| **Flat recurring** | Fixed fee per period |
| **Per seat** | Fixed fee × seat count, prorated on change |
| **Per unit** | Linear price × metered units |
| **Included allowance + overage** | N units included in the plan fee, overage priced per unit |
| **Tiered (graduated)** | Each tier's units priced at that tier's rate |
| **Volume** | All units priced at the rate of the tier reached |
| **Package/block** | Units sold in blocks (e.g. per 1,000 tokens) |
| **Credits** | Prepaid balance consumed by rated usage at a declared conversion |
| **Hybrid** | Any combination of the above on one subscription — the default for Pro/Business |
| **Committed/contract** | Negotiated commitment, custom rates, optional true-up/true-down |
| **One-off** | Marketplace purchase, add-on, professional services, license fee |

## 8.2 Pricing invariants

- **Money is never a float.** Every amount is an integer in a currency's minor unit, always paired
  with its currency. No amount exists without a currency.
- **Currency is fixed per subscription**, chosen at creation. Cross-currency conversion is a
  presentation concern, never a ledger concern.
- **Prices are immutable and effective-dated.** Changing a price creates a new price version; existing
  subscriptions reference the version they were rated against. This is what makes an old invoice
  re-derivable years later.
- **Rating is a pure, deterministic function** of (usage records, subscription, price version,
  discounts, credits, period). Given the same inputs it always produces the same charge lines — the
  billing equivalent of the runtime's deterministic-behaviour rule (§A2.4).
- **Grandfathering is expressed as a price-version pin**, not as a special case in code.
- **Discounts and coupons are modifiers on charge lines**, applied in a **declared, deterministic
  order** (line discount → subscription discount → contract discount → credits → tax). Order matters
  financially and is therefore part of the architecture, not an implementation detail.
- **Promotional coupons** carry: applicability scope, redemption limits, validity window, stacking
  rule, and a first-order/recurring/lifetime duration. Redemption is recorded as an immutable fact.

---

# 9. Entitlement Model

Entitlement is where billing touches the rest of the platform — and therefore where the ownership
discipline matters most.

## 9.1 The decision/enforcement split

| Role | Owner |
|---|---|
| **Decision** — what is this org entitled to? | **Billing Entitlement Engine** (sole authority) |
| **Enforcement** — is this specific write allowed? | **The plane that owns the write** (Customer Platform, Product Runtime, Actuator, Control Plane) |

Billing publishes an **entitlement snapshot** per organization: a versioned, immutable, cacheable
statement of features, quotas, and limits. Enforcing planes read the snapshot at their own write
boundary and **accept or reject** (§A2.6). Billing never calls into another plane to block an action,
and no plane calls synchronously into Billing to ask permission.

**Why this shape:** it preserves ownership (each plane still decides its own writes), preserves the
freeze (no frozen Epic gains a Billing dependency — it reads a snapshot delivered through the Control
Plane), and keeps billing off every hot path.

## 9.2 Entitlement dimensions

| Class | Examples |
|---|---|
| **Feature flags** | SSO, SCIM, approvals, private providers, white-label, marketplace publishing, audit export, custom retention |
| **Structural limits** | organizations, workspaces, projects, agents, products, deployments, environments, teams, members/seats, API keys, integrations |
| **Rate limits** | API requests/min, concurrent sessions, concurrent deployments, ingestion throughput |
| **Consumption quotas** | included minutes/tokens/storage; hard caps; overage permission |
| **Support & SLA** | support tier, response targets, uptime commitment (declarative, surfaced not enforced) |
| **Retention** | event log, transcript, and analytics retention windows |

## 9.3 Gate classification (mandatory)

Every entitlement gate is classified at design time. This is a non-negotiable part of the model:

| Class | Behaviour when entitlement data is unavailable or stale | Examples |
|---|---|---|
| **Fail-closed** | Reject the action | creating a new organization; publishing to the marketplace; enabling a paid feature |
| **Fail-open** | Allow, record, reconcile later | an in-flight conversation continuing; a deployment already mid-rollout |
| **Degrade** | Allow at a reduced level | non-critical background jobs, best-effort ingestion |

**Rule:** anything that could interrupt a live customer conversation is **fail-open**. Anything that
could create unbounded new cost or unearned capability is **fail-closed**. A gate without a
classification is an incomplete design.

## 9.4 Quota behaviour

- **Warn → soft limit → hard limit.** Customers receive threshold notifications before any wall.
- **Overage is a plan property**, not an accident: a plan either permits overage (billed) or caps
  (rejected). Free caps; paid tiers permit by default.
- **Rejection is explicit and typed** — the customer and the audit log both learn exactly which
  entitlement was exceeded. "Silent overage surprise" is an architectural failure (Epic 8 §15).

---

# 10. Billing Lifecycle

The period-level lifecycle that produces money owed. Deterministic, idempotent, replayable, and
resumable — a billing run interrupted midway must be safely re-runnable.

```
PERIOD_OPEN
   │  usage accrues; entitlements enforced live
   ▼
PERIOD_CLOSING     — cutoff reached; late-arrival window open
   ▼
USAGE_SEALED       — usage records for the period frozen
   ▼
RATED              — charge lines produced (pure function; re-runnable)
   ▼
ADJUSTED           — discounts, coupons, credits, contract true-ups applied in declared order
   ▼
TAXED              — tax determination recorded with evidence
   ▼
INVOICE_ISSUED     — invoice becomes immutable; hands off to Payment Lifecycle
   ▼
PERIOD_CLOSED      — terminal; corrections only via credit note / next-period adjustment
```

**Invariants.**

- **Idempotent by period.** Re-running any stage for the same period and inputs produces the same
  result; partial completion is resumable, never duplicative.
- **No stage may mutate a previous stage's output.** Each stage appends.
- **A billing run never blocks the platform.** It is scheduled, bounded, and observable; failure
  raises an incident, it does not degrade the runtime.
- **Every stage emits `billing.*` events**, so a billing run is replayable and inspectable with the
  same tooling as a conversation.
- **Sealing is a one-way door.** After `USAGE_SEALED`, correction has exactly one path: adjustment or
  credit note.

---

# 11. Payment Lifecycle

Payment is where VoxVoice's truth meets an external system's truth. The boundary must be stated
precisely.

> **The payment provider is the authority for money movement. Billing is the authority for revenue
> state. Neither overrides the other; they are reconciled deterministically.**

```
PAYMENT_REQUIRED
   ▼
AUTHORIZING ──► AUTHORIZED ──► CAPTURING ──► CAPTURED ──► SETTLED (terminal)
   │                 │                            │
   │                 └──► AUTHORIZATION_EXPIRED    └──► DISPUTED ──► DISPUTE_RESOLVED / CHARGEBACK
   ▼
FAILED ──► RETRY_SCHEDULED ──► (retry) ──► ...
   │
   └──► DUNNING ──► RECOVERED (→ CAPTURED) | ABANDONED (→ subscription SUSPENDED)
```

**Architecture rules.**

| Rule | Statement |
|---|---|
| **P1** | Payment providers are **rented edges behind an adapter** (§A6/ADR-0022 pattern). Billing never speaks a provider protocol directly. Multiple providers, per-region routing, and provider migration are configuration. |
| **P2** | **Every payment operation is idempotent**, keyed by an operation identity Billing generates. A retried request never double-charges. |
| **P3** | **Provider callbacks are notifications, not truth.** They are verified, deduplicated, ordered, and reconciled; state advances only through the FSM. A callback that arrives twice, out of order, or never must all converge to the same final state. |
| **P4** | **Reconciliation is a first-class scheduled activity**, not an exception path: provider records are compared against the ledger, and divergence raises an incident rather than being auto-resolved. |
| **P5** | **Card/bank data never enters VoxVoice.** Payment instruments are provider-tokenized references. Scope-limiting the compliance surface is an architectural decision, not an operational one (§23). |
| **P6** | **Dunning is deterministic and policy-driven**: a declared retry schedule, declared communication steps, declared grace period, and a declared terminal action. Recovery attempts are bounded and auditable. |
| **P7** | **Payment failure never deletes anything.** It moves a subscription to `PAST_DUE`, then `SUSPENDED` — entitlement contraction, never data destruction. Data lifecycle stays with the owning plane. |

**Failed-payment recovery ladder** (mirrors §A10 graceful degradation): retry with backoff → alternate
payment instrument → customer notification with self-serve fix → grace period with full entitlements
→ reduced entitlements → suspension → cancellation. Every rung is a recorded, reversible state.

---

# 12. Invoice Lifecycle

```
DRAFT ──► PENDING_APPROVAL (contract/enterprise only) ──► ISSUED ──► PAID (terminal)
   │                                                        │  │
   └──► VOIDED (pre-issue only)                             │  └──► OVERDUE ──► IN_COLLECTION
                                                            │
                                                            └──► CREDITED (partially/fully, via credit note)
```

**Invariants.**

- **An issued invoice is immutable.** No field of an issued invoice ever changes. This is the
  foundation of auditability and legal defensibility.
- **Corrections are credit notes**, which are themselves immutable documents referencing the original.
- **Invoice numbering is gapless, sequential, and per legal entity/jurisdiction**, allocated exactly
  once, and never reused — including across retries and failures.
- **An invoice is self-contained evidence**: it carries the plan version, price version, usage
  summary, discount trail, tax determination, currency, period, and the identity of every input. An
  invoice must be explainable without querying live state.
- **Document rendering and delivery are edges** (adapters), never core logic. A delivery failure never
  changes invoice state.
- **Zero-amount and credit-covered invoices are still issued.** Absence of payment is not absence of a
  billing document.
- **Enterprise invoicing supports PO references, custom terms (net-N), approval gates, consolidated
  billing across child accounts, and offline/manual settlement** — all as invoice attributes, not
  parallel flows.

---

# 13. Refund Lifecycle

```
REFUND_REQUESTED ──► PENDING_APPROVAL ──► APPROVED ──► PROCESSING ──► REFUNDED (terminal)
        │                    │                              │
        │                    └──► REJECTED (terminal)        └──► REFUND_FAILED ──► (retry | manual)
        └──► CANCELLED (pre-approval)
```

**Rules.**

- **A refund is never a ledger edit.** It is an independent, append-only event that references the
  original charge and produces a credit note.
- **Refunds are bounded**: never more than the captured amount, never against a voided invoice, never
  duplicated for the same request identity.
- **Approval is required above a policy threshold**, with separation of duties and full audit — the
  same posture the Actuator gives deployments.
- **Refund vs. credit is an explicit choice**: money returned to the instrument, or credit added to
  the balance. Both are first-class; neither is a workaround for the other.
- **Tax is refunded proportionally**, with the tax reversal recorded as evidence.
- **Chargebacks and disputes are distinct** from refunds: they are provider-initiated, have their own
  evidence and deadline handling, and must never be silently reconciled as refunds.

---

# 14. Tax Model

> **Tax determination is a rented edge. Tax evidence is an owned fact.**

| Element | Design |
|---|---|
| **Determination** | Performed by a **tax adapter** (external tax service or a configured rule set). Billing never hard-codes a rate or a jurisdiction rule. |
| **Evidence** | The determination — jurisdiction, rate, rule applied, customer tax status, timestamp, adapter version — is recorded immutably with the invoice. Rates change; issued invoices must not. |
| **Customer tax profile** | Tax residency, registration numbers (GSTIN / VAT ID / equivalents), business-vs-consumer status, exemption certificates. Owned by Billing (it is a revenue fact), referencing the Customer Platform's organization. |
| **Validation** | Registration-number validation is an adapter call whose result is recorded with a validity timestamp; validation failure is surfaced, never assumed valid. |
| **India / GST** | Place-of-supply determination, intra-state (CGST+SGST) vs inter-state (IGST) split, GSTIN capture, HSN/SAC classification, and compliant invoice fields — all as adapter output + recorded evidence. |
| **EU / VAT** | Destination-based determination for digital services, B2B reverse charge on valid VAT ID, B2C rate by customer location, evidence-of-location retention. |
| **Other jurisdictions** | US sales tax/nexus, UK VAT, and further regimes are additional adapter configurations — never a structural change (§A12). |
| **Inclusive vs exclusive** | A declared per-market property of the price, not an invoice-time improvisation. |
| **Withholding tax** | Recorded as a distinct ledger concept: the customer's remittance obligation, not a discount. |
| **Reporting** | Tax reporting is an **export** of recorded evidence, never a recomputation. Recomputing tax at reporting time is forbidden — it can diverge from what was invoiced. |

---

# 15. Marketplace Model

The marketplace monetizes Epic 7 packages (products, tools, skills, templates, voices, knowledge
packs) without a second commerce system.

```
LISTING (Product Registry artifact + Billing listing terms)
   ▼
ORDER_CREATED ──► PAYMENT (Payment Lifecycle) ──► ORDER_COMPLETED
   ▼                                                    │
ORDER_FAILED / CANCELLED                                ▼
                                          ENTITLEMENT GRANT (Entitlement Engine)
                                                        │
                                                        ▼
                                          REVENUE LEDGER (buyer charge + publisher payable)
```

**Rules.**

- **The Product Registry owns the artifact; Billing owns the commercial terms.** A listing references
  a registry package; Billing never stores a copy of the package.
- **Purchase outcomes are entitlements**, delivered through the same Entitlement Engine. There is no
  second permission mechanism for purchased content.
- **Revenue sharing is ledger-native**: a completed order produces both a buyer-side revenue entry and
  a publisher-side payable entry. Payouts are a separate, later lifecycle against the payable balance
  — designed for, not implemented in, Phase 1.
- **Pricing models supported per listing**: free, one-off, subscription add-on, usage-metered,
  seat-based — the same shapes as §8, reused.
- **Refunds on purchased content revoke the entitlement** as an explicit, recorded transition — never
  by deleting the customer's data.
- **Tax on marketplace sales depends on the seller-of-record model** (VoxVoice as merchant of record
  vs. agent). This is a commercial decision with architectural consequences and is flagged as a Phase-1
  decision requirement (§25 R-9).

---

# 16. Enterprise Licensing

Enterprise is a **contract-driven configuration** of the same core.

| Element | Design |
|---|---|
| **Contract** | The authoritative commercial agreement: term, committed spend, custom price book, custom entitlement overrides, SLA, invoicing terms, renewal and true-up rules. Owned by the Licensing Engine. |
| **Subscription from contract** | A contract materializes a subscription whose catalog/price references point at contract-scoped versions. Downstream behaviour is identical to self-serve. |
| **Committed spend** | Commitment is tracked against actual consumption; drawdown, overage, and true-up/true-down are ledger facts, not spreadsheets. |
| **Custom quotas** | Entitlement overrides on the snapshot — the same mechanism as any other entitlement, with a contract as the grant source. |
| **License grants** | For deployed/self-hosted or air-gapped scenarios: a license is an entitlement grant with an issuer, scope, validity window, and verification method. Offline verification is a declared capability; the platform still treats the license as an entitlement, not a new permission system. |
| **Multi-entity / consolidated billing** | A parent billing account with child accounts: usage attributed to children, invoiced at the parent, entitlements resolvable at either level. This hierarchy is **separate from org tenancy** — it is a billing relationship, not an identity one. |
| **Procurement realities** | POs, net-N terms, offline settlement, invoice approval gates, security/compliance addenda, and custom retention — all invoice/contract attributes. |
| **Renewal** | Deterministic: notice windows, auto-renew flags, price-uplift rules, and non-renewal all as contract terms driving the subscription FSM. |

---

# 17. Future White-label Strategy

Designed for now, built later. The requirement is that **no re-architecture is needed when it lands**.

| Model | Shape | What it needs from this blueprint |
|---|---|---|
| **Reseller** | A partner sells VoxVoice to end customers; VoxVoice invoices the partner; the partner invoices its customers. | The billing-account **hierarchy** (§16), partner-scoped price books, margin/discount at the partner level, consolidated invoicing, and partner-scoped entitlement resolution. |
| **White-label** | A partner presents VoxVoice as its own product. | Entitlement flags for branding/domain, partner-scoped catalogs, and partner-scoped billing documents — all existing mechanisms with a partner scope. |
| **OEM / embedded** | VoxVoice is embedded in a partner's product, billed wholesale. | Wholesale price books, high-volume metering aggregation at the partner level, and API-level entitlement. |

**The three capabilities that make all three possible** — and which this blueprint therefore fixes now:

1. **Billing account hierarchy independent of org tenancy** (parent/child, attribution down,
   invoicing up).
2. **Scoped catalogs and price books** (a price book has an audience: public, partner, contract).
3. **Entitlement resolution that composes** (partner grant ∩ customer subscription), with a declared
   precedence order.

Nothing else about the core changes. This is the test of the design: if a reseller model would force a
change to the ledger, the rating engine, or the entitlement mechanism, the design is wrong.

---

# 18. Integration with Epic 8 (Customer Platform)

Epic 8 is frozen. Every integration below is additive and one-way.

| Direction | Interaction |
|---|---|
| **Billing → Customer Platform** | **Read-only.** Organization, workspace, project, member, and role facts are referenced for attribution, seat counts, and scoping. Billing never writes them. |
| **Customer Platform → Billing** | **None, directly.** The Customer Platform gains no import of, and no dependency on, Billing. It consumes the **entitlement snapshot** delivered through the Control Plane, exactly as it consumes any other configuration. |
| **Studio → Billing** | Through the Control Plane only, and only for what ADR-0021 already permits: **plan intent**, payment-method intent, purchase requests, and **read-only projections** (plan, usage, invoices, entitlements, credits). The Billing Ledger remains an ADR-0021 **Forbidden Write**. |
| **Audit Platform** | Billing emits `billing.*` events; the Audit Platform records them. Billing keeps no private audit log. |
| **RBAC** | The **Billing Admin** role already exists in Epic 8's role model. Epic 9 populates its permissions; it introduces no parallel authorization system. |
| **Org lifecycle events** | Organization creation → billing account creation (Free tier by default). Organization deletion → billing account **retention**, not deletion: revenue records outlive the customer for legal retention, referenced by a preserved identifier. This asymmetry is deliberate and must be stated in the ADR. |
| **Seat changes** | Member add/remove events drive seat-count changes on subscriptions with a declared proration policy. |

**Freeze-compliance test (an M9 acceptance criterion):** the existing import-boundary/AST test must
show **no Epic 5/6/7/8 package depending on Billing**, and Billing depending only downward. Frozen
Epics must remain byte-for-byte unchanged.

---

# 19. Integration with Product Runtime (Epic 7)

| Concern | Design |
|---|---|
| **Products as billable objects** | A product/package identity is referenced by marketplace listings and by product-scoped entitlements. Billing stores the reference, never the artifact. |
| **Compilation and packaging** | Unaffected. Billing observes; it never participates in the compile chain. |
| **Entitlement at compile/publish time** | Publishing to the marketplace, exceeding product counts, or using entitlement-gated capabilities is enforced **by Product Runtime's own boundary** against the entitlement snapshot — Product Runtime does not call Billing. |
| **Identity chain** | Billing references the Epic 7 identity chain (manifest → definition → compiled → package) so a charge can be traced to an exact artifact version. It extends nothing in that chain (ADR-0020 nouns untouched). |
| **Declarative freeze preserved** | Billing adds no execution to Product Runtime and reads only its declarative outputs. |

---

# 20. Integration with Deployment (Epic 8 Actuator, ADR-0022)

| Concern | Design |
|---|---|
| **Deployment as a billable dimension** | Deployment applies, active environments, and environment-hours are metered from the Actuator's `metric.deployment.*` stream. |
| **Entitlement gating** | Environment count, concurrent deployments, deployment frequency, and approval requirements are entitlement dimensions enforced **at the Actuator's own admission boundary** using the snapshot — never a synchronous Billing call, and never a Billing-initiated cancellation. |
| **In-flight safety** | Deployment gating is **fail-open once a rollout has started** (§9.3). Billing state must never orphan a deployment mid-rollout; the Actuator's cancellation and terminal-state guarantees are unaffected. |
| **Suspension behaviour** | A suspended subscription blocks **new** deployments; it never tears down running ones. Teardown is a customer or contract action executed by the owning plane. |
| **Ownership** | Deployment execution state stays entirely with the Actuator (ADR-0022). Billing observes events and emits its own. |

---

# 21. Integration with Observability (Epic 8)

| Concern | Design |
|---|---|
| **Direction** | Billing **consumes** `metric.*` for metering, and **produces** `billing.*` + `metric.billing.*` for observation. Observability observes Billing like any other producer. |
| **The estimated/authoritative separation** | Observability's `CostService` stays **estimated and directional** (its Epic 8 scope explicitly excludes billing). Billing's charges are authoritative. **Neither is derived from the other.** Both are derived from the same events, for different questions. Studio must label them distinctly so a customer never confuses an estimate with an invoice. |
| **Billing's own health metrics** | Ingestion lag, unattributed-usage rate, rating duration, billing-run success rate, invoice issuance latency, payment success rate, dunning recovery rate, reconciliation divergence count, entitlement-snapshot staleness. |
| **Alerting** | Revenue-critical alerts (ingestion lag beyond the staleness bound, reconciliation divergence, invoice numbering anomaly, payment success-rate drop) use Epic 8's existing alerting; Billing adds no alerting mechanism. |
| **Replay** | Because Billing is a §A5 event producer, a billing run is replayable and inspectable with the existing Replay Platform — a deliberate reuse, not a new tool. |

---

# 22. Security

| Area | Design |
|---|---|
| **Tenancy** | Every billing fact is org-scoped (§A11.2). Cross-org read or write is structurally impossible, and is verified by the same org-scoped-query review Epic 8 uses. |
| **Authorization** | Billing actions are gated by the existing RBAC model. Financially significant actions (plan change, payment method, refund, contract change) require the Billing Admin (or Owner) role and are separated from build/deploy roles. |
| **Separation of duties** | Refunds and credits above a threshold require approval by a different principal than the requester. |
| **Payment data** | **Never stored.** Instruments are provider tokens. This deliberately keeps the cardholder-data compliance surface off the platform. |
| **Sensitive revenue data** | Contract terms, prices, and invoices are access-controlled and audit-logged on read as well as write. |
| **Idempotency and replay attacks** | Every mutating billing operation carries a caller-supplied or platform-generated idempotency identity; replayed requests are no-ops. |
| **Provider callback verification** | Signature verification, timestamp/window validation, and deduplication before any state transition. An unverified callback never advances state. |
| **Fraud posture** | Trial abuse, stolen-instrument signals, and abnormal-consumption detection are surfaced as signals and human-reviewable holds — never automated destructive action. |
| **Secrets** | Provider credentials use the existing credential seam; no new secret mechanism. |
| **Audit** | Every billing state transition is an audited event with actor, scope, before/after state, and correlation identity. The event log is the audit log (§A11.2). |

---

# 23. Compliance

| Area | Posture |
|---|---|
| **Financial record retention** | Invoices, credit notes, ledger entries, and tax evidence are immutable and retained per jurisdictional requirement — independent of, and longer than, customer data retention. |
| **Right to erasure vs. financial retention** | A customer deletion request removes personal data from the customer plane; **financial records are retained** with the minimum identifiers required by law, referencing a preserved account identifier. This tension is explicit, designed, and must be documented in the ADR — not discovered at deletion time. |
| **Tax compliance** | Determination and evidence per §14; reporting exports recorded evidence, never recomputation. |
| **Invoice compliance** | Jurisdiction-specific mandatory fields, gapless numbering, and (where required) e-invoicing formats are adapter/format concerns over the same immutable invoice fact. |
| **Data residency** | Billing data residency follows the platform's residency model; adapter selection is region-aware. |
| **PCI scope** | Minimized by construction (§22 — no payment data on platform). |
| **SOC 2 / ISO alignment** | Inherited from the platform's existing audit, access-control, and change-management posture; Billing adds evidence, not a parallel control set. |
| **Revenue recognition** | The ledger is designed to support recognition (deferred vs. recognized revenue for annual terms, credits, and commitments) as a **reporting projection over the ledger**, not as a separate bookkeeping system. Accounting-system export is an adapter. |
| **Anti-corruption of records** | Nothing in Billing supports mutation or deletion of issued financial documents. There is no administrative path to edit an invoice. |

---

# 24. Scalability

| Dimension | Design response |
|---|---|
| **Metering volume** | The highest-volume path by orders of magnitude. Fully asynchronous, horizontally partitionable by billing account, and bounded by the event log's own throughput. Aggregation is incremental — period totals are maintained continuously, not computed at period end. |
| **Billing runs** | Partitioned by billing account and by period; accounts are independent, so runs parallelize linearly. Staggered cycle dates avoid a global month-end spike. |
| **Rating cost** | Rating is a pure function over sealed usage; it is cacheable and re-runnable, and can be computed incrementally through the period so period-end work is small. |
| **Hot accounts** | A very large enterprise or reseller account is the natural hotspot; hierarchy-aware partitioning keeps children independently processable with roll-up at the parent. |
| **Entitlement reads** | Extremely high read volume, near-zero write volume — solved by an immutable, versioned, cacheable snapshot with a declared staleness bound. This is why the snapshot model was chosen over synchronous checks. |
| **Provider rate limits** | Payment and tax adapters are bounded, queued, retried with backoff, and circuit-broken (§A10). |
| **Storage growth** | Usage records dominate. Raw records are retained for the dispute window at full fidelity, then compacted to period aggregates + evidence — with the event log remaining the ultimate re-derivation source. |
| **Deployment topology** | Billing is a separately deployable plane; it can be scaled, isolated, and its incidents contained without touching the runtime (the §A9 staging model). |

---

# 25. Risks

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| **R-1** | **Metering inaccuracy** — under-billing (leakage) or over-billing (trust loss) from lost, duplicated, or misattributed events. | **Critical** | Idempotent ingestion keyed by event identity; total attribution with an alerting quarantine; continuous reconciliation of usage records against the event log; ledger reproducible by replay. |
| **R-2** | **Estimated vs. authoritative cost confusion** — Observability's estimate and Billing's charge diverge and a customer sees both. | High | Architecturally separate, never derived from each other; distinct vocabulary and labelling mandated in Studio; divergence beyond a threshold is an alert, not a customer discovery. |
| **R-3** | **Billing becomes a runtime dependency** — a synchronous entitlement check slips into a hot path and a billing outage degrades conversations. | **Critical** | Snapshot-based entitlement with fail-open classification for live paths; an explicit ADR forbidding synchronous billing in the conversation/deployment critical path; enforced by boundary review. |
| **R-4** | **Ownership erosion** — a "convenient" copy of usage, org, or deployment state inside Billing, or a Studio-side ledger. | High | ADR-0021 Forbidden Writes remain in force; §4 ownership table is mandatory on every M9 milestone; import-boundary tests; review checklist item. |
| **R-5** | **Money correctness defects** — floats, currency mixing, rounding drift, discount-order ambiguity. | **Critical** | Integer minor units with mandatory currency pairing; declared discount order; property-based and golden-case testing of the rating engine; immutability of issued documents. |
| **R-6** | **Provider reconciliation divergence** — platform and payment provider disagree about what was captured. | High | Provider as authority for movement, Billing for revenue state; scheduled reconciliation; divergence raises an incident and is never auto-resolved. |
| **R-7** | **Tax exposure** — wrong jurisdiction, rate, or evidence, discovered at audit. | High | Determination delegated to a tax adapter; evidence recorded immutably; reporting exports recorded evidence; jurisdiction expansion is adapter configuration with a review gate. |
| **R-8** | **Compliance/erasure conflict** — deletion requests versus financial retention. | Medium | Explicit designed asymmetry (§23), documented in the ADR and surfaced in the product's data policy. |
| **R-9** | **Marketplace seller-of-record ambiguity** — VoxVoice as merchant of record vs. agent changes tax and payout obligations materially. | Medium | Flagged as a **required commercial decision before M9 marketplace milestones**; architecture supports both, but the choice must be made explicitly and recorded. |
| **R-10** | **Scope explosion** — attempting all pricing shapes, all jurisdictions, and marketplace payouts in one epic. | High | Strict milestone sequencing (§27) with a minimal revenue spine first; every later shape must land as configuration over the same core or the design is rejected. |
| **R-11** | **Downgrade/limit enforcement harming customers** — a plan change silently destroying resources. | High | Downgrades reject-or-acknowledge at request time; entitlement contraction never deletes data; suspension is reversible. |
| **R-12** | **Build-vs-buy misjudgment** — reimplementing a mature commercial billing engine (rating, tax, dunning, invoicing) with a solo/small team. | High | The adapter seams are drawn so that a third-party billing/tax/payment system can be adopted behind them **without changing ownership or the ledger's role**. This is an explicit design goal, evaluated at M9.0C. |

---

# 26. Recommended ADRs

Design-only. These are **recommendations**; ratification occurs at M9.0B/M9.0C. **ADR-0024 remains
reserved** for the Coach autonomy decision recommended by ADR-0021 and is not claimed here.

| ADR | Title | Decision it fixes |
|---|---|---|
| **ADR-0023** | **The Billing Platform is the sole owner of revenue facts; metering consumes the authoritative `metric.*` stream** | Fulfils the ADR-0021 recommendation. Establishes Billing as a new additive plane, its owned/consumed fact boundary, one-way downward dependency, and the rule that Billing measures nothing itself. |
| **ADR-0025** | **Entitlements are decided by Billing and enforced at the consuming plane's write boundary** | The decision/enforcement split, the versioned immutable entitlement snapshot, and mandatory fail-open/fail-closed classification per gate. |
| **ADR-0026** | **Money is integer minor units; the revenue ledger is immutable and append-only** | No floats, mandatory currency pairing, issued documents immutable, corrections only via credit note, gapless invoice numbering. |
| **ADR-0027** | **Payment and tax providers are rented edges; the provider is the authority for money movement, Billing for revenue state** | Adapter seam, idempotent operations, callbacks-as-notifications, deterministic reconciliation, no payment data on platform. |
| **ADR-0028** | **Billing is never in the conversation, session, or deployment critical path** | Forbids synchronous billing calls on hot paths; defines degradation posture under metering/entitlement staleness. |
| **ADR-0029** | **Marketplace commerce and enterprise licensing extend the same revenue core** | Order → entitlement → ledger; no parallel commerce or licensing system; publisher payables are ledger-native. |
| **ADR-0030** | **The billing account hierarchy is separate from organization tenancy** | Enables consolidated billing, reseller, OEM, and white-label without changing org identity or the ledger. |

---

# 27. Milestone Breakdown (Epic 9 roadmap)

Sequenced on a **revenue spine**: *account → plan → entitlement → metering → rating → invoice →
payment*. Nothing later may force a change to anything earlier; if it does, the design is wrong.

| Milestone | Name | Purpose | Depends on | Size | Risk |
|---|---|---|---|---|---|
| **M9.0A** | **Billing Platform Blueprint** *(this document)* | Architecture, ownership, and boundaries for the whole epic. Design only. | Epics 5–8 frozen | M | Low |
| **M9.0B** | **ADR Ratification** | Ratify ADR-0023, 0025–0030. No code. | M9.0A | S | Low |
| **M9.0C** | **Build-vs-Buy & Provider Strategy Decision** | Decide, per seam, what VoxVoice owns vs. rents (billing engine, payments, tax, invoicing/e-invoicing). Records the decision; changes no boundary. | M9.0B | S | **High value** |
| **M9.1** | **Billing Foundation** | Billing account, org linkage, tenancy, RBAC wiring, event vocabulary (`billing.*`), audit integration. The plane exists and owns nothing else yet. | M9.0B | M | Low |
| **M9.2** | **Catalog & Price Book** | Plans, tiers, features, meters, versioned effective-dated prices, all price shapes. Static, unconsumed. | M9.1 | M | Low |
| **M9.3** | **Subscription Engine** | Subscription FSM, trials, terms, renewal, upgrade/downgrade, proration. The spine's core. | M9.2 | **L** | Medium |
| **M9.4** | **Entitlement Engine + Snapshot** | Entitlement computation, snapshot publication, gate classification. **Pivotal unlock** — every enforcing plane depends on this shape. | M9.3 | **L** | **High** |
| **M9.5** | **Metering Pipeline** | Idempotent ingestion, attribution, normalization, usage records, incremental aggregation, late-arrival policy. **The correctness-critical milestone.** | M9.1 (parallelizable with M9.2–M9.4) | **XL** | **Critical** |
| **M9.6** | **Rating Engine** | Deterministic usage + seats + fees → charge lines; discounts, coupons, credits in declared order. | M9.2, M9.5 | **L** | High |
| **M9.7** | **Revenue Ledger & Credits** | Append-only ledger, credit balances, movements, reproducibility guarantees. | M9.6 | M | High |
| **M9.8** | **Invoicing Engine** | Billing-run lifecycle, sealing, invoice issuance, immutability, numbering, credit notes, document rendering seam. | M9.7 | **L** | High |
| **M9.9** | **Tax Engine** | Tax adapter seam, determination, evidence recording, GST/VAT first. | M9.8 | M | High |
| **M9.10** | **Payment Engine + Dunning** | Payment adapter seam, payment FSM, idempotency, callback verification, retries, dunning ladder, reconciliation. | M9.8 | **XL** | **Critical** |
| **M9.11** | **Refunds & Credit Notes** | Refund lifecycle, approvals, separation of duties, proportional tax reversal, dispute/chargeback handling. | M9.10 | M | High |
| **M9.12** | **Enforcement Integration** | Consuming planes enforce the snapshot at their own boundaries; quota warnings and rejections; overage policy. No frozen Epic modified. | M9.4 | **L** | High |
| **M9.13** | **Studio Billing Surface** | Plan/usage/invoice/credit/entitlement projections; plan-change, payment-method, and purchase **intents**. Owns nothing (ADR-0021). | M9.8, M9.10, M9.12 | **L** | Medium |
| **M9.14** | **Enterprise Contracts & Licensing** | Contracts, custom price books, committed spend, true-up, consolidated billing, license grants. | M9.7, M9.8 | **L** | Medium |
| **M9.15** | **Marketplace Commerce** | Listings, order lifecycle, purchase entitlements, publisher payables. Requires the R-9 seller-of-record decision. | M9.10, M9.14 | **L** | Medium |
| **M9.16** | **Revenue Observability & Reconciliation** | Billing health metrics, alerts, reconciliation runs, revenue reporting projections, accounting export seam. | M9.10 | M | Medium |
| **M9.17** | **Hardening & Compliance** | Retention, erasure asymmetry, residency, evidence exports, failure-mode drills, load validation of metering. | all | **L** | High |
| **M9.18** | **Epic 9 Certification** | Independent certification against this blueprint and the ratified ADRs. | M9.17 | M | Low |
| **M9.19** | **Billing Platform v1.0 Freeze Manifest** | Architecture freeze, ownership boundaries, extension policy, baseline v4.0.0 recommendation. | M9.18 | S | Low |

**Critical path:** M9.1 → M9.3 → M9.4 → M9.6 → M9.7 → M9.8 → M9.10.
**Parallelizable from day one:** M9.5 (metering) and M9.2 (catalog) are independent of the
subscription chain and should start immediately — metering is the longest-lead, highest-risk item.
**MVP (first billable revenue):** M9.1, M9.2, M9.3, M9.4, M9.5, M9.6, M9.7, M9.8, M9.10, M9.13.

**Gates.** G1 *Internal billing* (billing runs correct against real usage, no customer charged) →
G2 *Design-partner billing* (real charges, manual oversight, small cohort) → G3 *Self-serve GA*
(Free/Starter/Pro live) → G4 *Enterprise & marketplace*.

---

# 28. Final Recommendation

**Proceed with Epic 9 as designed, under the ownership model in §4, and in the milestone order in
§27.**

Three points carry disproportionate weight, and this blueprint should be judged on them:

1. **Metering correctness is the epic.** Everything else in billing is recoverable; systematically
   wrong metering is not — it is simultaneously a revenue loss, a trust loss, and a legal exposure.
   M9.5 must start first, get the most scrutiny, and be validated against real production traffic in
   shadow mode (Gate G1) before a single customer is charged.

2. **Billing must never be able to hurt a conversation.** The snapshot-based entitlement model and
   ADR-0028 exist for exactly one reason: a revenue system's outage must never become a runtime
   outage. This is the strongest reason to reject any future "just check the balance synchronously"
   proposal, however small it looks.

3. **Decide build-vs-buy explicitly at M9.0C, before M9.6.** The adapter seams in this blueprint are
   deliberately drawn so a third-party billing, tax, or payment engine can be adopted **behind** them
   without changing who owns what. A solo/small team building rating, dunning, invoicing, tax, and
   e-invoicing from scratch is the largest schedule risk in the epic (R-12). The architecture is
   indifferent to the answer; the roadmap is not.

Everything in this blueprint is additive. Epics 5–8 remain frozen, byte-for-byte. No ownership moves,
no dependency inverts, no redesign is proposed. The Billing Platform is a new plane that consumes
authoritative facts and owns revenue — and nothing else.

---

**Rationale to be recorded as DL-0107.** Ratification of the recommended ADRs occurs at M9.0B; the
build-vs-buy decision at M9.0C. This document authorizes no code.
