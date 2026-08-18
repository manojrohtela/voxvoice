# ADR-0023 — The Billing Platform is the sole owner of revenue; it owns nothing else and meters the authoritative `metric.*` stream

| | |
|---|---|
| **Status** | **Accepted 2026-08-01** (ratified at M9.0B). Recommended by [ADR-0021](ADR-0021-studio-consumer-and-management-surface.md) ("a **Billing Runtime** (future) becomes the authoritative owner Studio's Billing module projects… recommended ADR-0023: metering consumes the authoritative `metric.*` stream") and by the [Epic 9 Billing Platform Blueprint](../architecture/epic9/billing-platform-blueprint.md) §26. This milestone ratifies it. |
| **Amends** | Introduces Epic 9 (**Billing & Subscription Platform**) as a new additive plane atop the frozen Runtime Platform v1.0 (Epic 5), Execution Platform v1.0 (Epic 6), Product Runtime v1.0 (Epic 7), and Customer Platform v1.0 (Epic 8). Amends no Constitution Part-A/Part-B section and modifies no frozen Epic. Records the load-bearing ownership boundary for revenue. |
| **Deciders** | Technical Owner |
| **Related** | [Epic 9 Billing Platform Blueprint](../architecture/epic9/billing-platform-blueprint.md) (M9.0A), [ADR-0021](ADR-0021-studio-consumer-and-management-surface.md) (Studio owns no runtime state; Billing Ledger is a Forbidden Write), [ADR-0022](ADR-0022-deployment-actuator-runtime-actuation.md) (Actuator = sole owner of deployment execution), [ADR-0018](ADR-0018-product-runtime-declarative-composition-layer.md) (Product Runtime = declarative layer, one-way dependency), [ADR-0019](ADR-0019-binding-resolution-against-registry-snapshot.md)/[ADR-0020](ADR-0020-registry-nouns-reserved-for-product-registry.md) (resolve against an injected immutable snapshot; reserved nouns), [ADR-0006](ADR-0006-single-system-of-record.md) (single system of record), [ADR-0004](ADR-0004-event-driven-core.md) (event-driven core), [ADR-0009](ADR-0009-workflow-durable-repository-exception.md) (durable-repository precedent), [ADR-0001](ADR-0001-first-party-runtime.md)/[ADR-0002](ADR-0002-substrate-behind-contracts.md)/[ADR-0003](ADR-0003-two-plane-architecture.md) (first-party runtime, substrate behind contracts, two planes), [ADR-0005](ADR-0005-streaming-first-turn-intelligence.md), [ADR-0007](ADR-0007-keep-control-plane-delete-adapters.md), [ADR-0008](ADR-0008-skill-manifest-not-code.md)/[ADR-0010](ADR-0010-hook-framework-cross-cutting-library.md)/[ADR-0012](ADR-0012-tool-contract-manifest-plus-adapter.md)…[ADR-0017](ADR-0017-execution-context-duplication-lineage.md) (Epic 6 ownership categories), [P-ADR-0001](P-ADR-0001-implementation-profile-v1.md); Constitution §A0 (Consumer glossary), §A1 (the runtime is the product), §A2 (own the loop, rent the edges), §A2.4 (deterministic behaviour), §A2.5 (**one writer per fact**), §A2.6 (accepted-or-rejected), §A4 (two-plane), §A5 (event architecture / event log = audit log), §A6 (capability plugins), §A7 (projections), §A9 (deployment stages), §A10 (fault tolerance / graceful degradation), §A11.1 (bounded resources), §A11.2 (org-scoped security), §A11.3 (component specs), §A12 (additive extension); ARCHITECTURE_RULES AR-15 (acyclic graph), AR-30 (producers never know consumers), AR-50 (one responsibility per module); Epic 5/6/7/8 Freeze Manifests; Repository Baseline v3.0.0 |

## Status

**Accepted 2026-08-01**, ratified at milestone **M9.0B**. The Epic 9 Blueprint (M9.0A) is a design
milestone and could not ratify; it *recommended* this ADR, as ADR-0021 had already anticipated it.
This ADR is the ratification act.

It **authorizes no code**. It fixes the ownership boundary every future Epic 9 implementation
milestone (M9.1–M9.19) must honour. Epics 5, 6, 7, and 8 remain frozen and are not modified.
**ADR-0024 remains reserved** for the Coach-autonomy decision recommended by ADR-0021 and is not
claimed here; the remaining Epic 9 decisions are recommended as ADR-0025–ADR-0030 (Blueprint §26) and
are ratified separately.

---

## 1. Executive Summary

VoxVoice has a complete product stack and no owner of money. Epics 5–8 froze the runtime, the
execution platform, the product runtime, and the customer platform; Epic 8 deliberately scoped
Observability's cost view as **"estimated (directional), not billing — no invoices, no ledger"**, and
ADR-0021 listed the **Billing Ledger** among Studio's **Forbidden Writes** while naming a future
"Billing Runtime" as the authoritative owner Studio would project. That owner does not exist. Until
it does, every commercial fact — what a customer is on, what they consumed, what they owe, what they
paid, what they are entitled to — has **no writer**, which under §A2.5 means it is one convenience
away from having *several*.

**This ADR ratifies that owner.**

> **The Billing Platform is the sole owner of revenue. It owns nothing else.**

It is a new, additive plane above the frozen stack. It **consumes authoritative facts and never
produces them**: it meters the `metric.*` stream read-only (it measures nothing itself), references
Customer-Platform identity, Product-Runtime artifacts, and Actuator status read-only, and writes only
commercial truth — plans, prices, price books, commercial catalog, billing accounts, subscriptions,
usage records, credits, entitlements, discounts, coupons, invoices, payments, refunds, taxes,
contracts, licenses, revenue-recognition state, and the ledger.

It is also, by ratified constraint, **never on a critical path**: it never blocks the Runtime, never
blocks Deployment, never becomes synchronous infrastructure, and **never performs a permission
check**. It publishes **immutable entitlement snapshots**; the plane that owns a write enforces them
locally at its own boundary (§A2.6). Authorization stays exactly where §A11.2 and Epic 8 put it.

The result: money gets an owner, the freeze is preserved by construction, the dependency graph stays
acyclic and one-way, and a billing outage can never become a conversation outage.

---

## 2. Context

**What the frozen stack already establishes.**

| Frozen fact | Source |
|---|---|
| The runtime is the product; Consumers configure and observe it | Constitution §A0/§A1, ADR-0001 |
| Every piece of state has exactly one owning component; all others learn of it via events | **§A2.5** |
| The event log is the single source of truth and the audit log; `metric.*` is emitted by runtime producers | §A5/§A7/§A11.2, ADR-0004, ADR-0006 |
| Own the loop, rent the edges — vendors sit behind contracts | §A2, ADR-0002 |
| Behaviour is deterministic and table-driven; non-determinism is confined to the adapter edge | §A2.4, ADR-0022 |
| Product Runtime is declarative and one-way; it never actuates | ADR-0018, Epic 7 Freeze |
| Studio is a Consumer + Management Surface owning no runtime state; the **Billing Ledger is a Forbidden Write**; a future **Billing Runtime** is the authoritative owner it would project | **ADR-0021** |
| Deployment **execution** has exactly one owner, sitting above a frozen declarative layer and consuming its artifact read-only | **ADR-0022** |
| Observability's cost is **estimated, directional, not billing** — no invoices, no ledger | Epic 8 M8.17 scope |
| New planes extend additively; no frozen Epic may be modified or depended upon in reverse | §A12, AR-15, Epic 5/6/7/8 Freeze Manifests |

**What Epic 9 adds.** The Blueprint (M9.0A) designs the Billing & Subscription Platform across 28
artifacts: tiers Free→Enterprise, usage/seat/hybrid pricing, credits, trials, coupons, discounts,
taxes (GST/VAT), invoices, payments, refunds, dunning, subscription lifecycle with proration,
quotas/limits/entitlements, metering of voice minutes / AI tokens / knowledge storage / deployments /
API calls, marketplace commerce, enterprise licensing, and a future reseller/white-label channel. Its
organizing principle is one sentence — *Billing owns revenue and nothing else* — and its correctness
depends entirely on that sentence being **ratified before implementation**, not discovered during it.

**Why this must be settled now.** A billing system is the single strongest magnet for ownership
erosion in a SaaS platform. It is under permanent pressure to (a) keep "its own" usage counters
because the event log feels indirect, (b) keep "its own" customer/org records because joins are
easier, (c) sit synchronously in front of the runtime to "enforce" limits, and (d) become the
platform's de-facto authorization layer because entitlements look like permissions. Each of those is
a §A2.5 violation, a freeze violation, or a §A10 availability violation — and each is far cheaper to
forbid now than to unwind after the first invoice is issued.

---

## 3. Problem Statement

**Where does revenue live, and what is it allowed to touch?**

Concretely, five questions must have one answer each, permanently:

1. **Who writes commercial facts?** Today: nobody. Studio is forbidden (ADR-0021), Observability is
   explicitly out of scope (estimate only), and no frozen Epic claims them.
2. **Who measures usage for billing?** If Billing measures, there are two measurements of one fact
   (the runtime's `metric.*` and Billing's own) — a §A2.5 violation that guarantees eventual
   divergence between what the platform reports and what the customer is charged.
3. **Who decides entitlement, and who enforces it?** If Billing enforces, every enforcing plane gains
   a synchronous dependency on Billing, inverting the dependency direction ADR-0018/0021/0022 fixed
   and putting a revenue system on the conversation and deployment critical paths.
4. **Is entitlement the same thing as authorization?** If it is, Billing becomes a second
   authorization authority beside §A11.2/Epic 8 RBAC — two writers for "may this happen?".
5. **Can a billing failure degrade the product?** If Billing is synchronous anywhere in the runtime
   or deployment path, then a payment-provider outage, a metering backlog, or a billing bug becomes a
   customer-visible conversation outage — violating §A10 and the platform's entire availability
   posture.

Without a ratified answer, the default outcome is a Billing Platform that quietly becomes a second
system of record, a second authorization layer, and a new single point of failure.

---

## 4. Decision

**Ratify: the Billing Platform is the sole owner of revenue and owns nothing else.**

The Billing Platform **SHALL**:

1. **Own revenue facts exclusively** — subscription plans, pricing, price books, the commercial
   catalog, billing accounts, subscriptions, usage records, credits, entitlements, discounts,
   coupons, invoices, payments, refunds, taxes, the ledger, contracts, licenses, and
   revenue-recognition state (§5 matrix).
2. **Consume authoritative facts only.** Every input to billing is a fact another component already
   owns. Billing references; it never copies as truth and never re-asserts.
3. **Never measure usage.** Billing **consumes `metric.*` read-only** (§A5). It does not instrument
   the runtime, does not emit or mutate `metric.*`, and does not derive a competing measurement. Its
   **usage records** are a normalized, deduplicated, attributed *projection* of that stream — the
   billable fact — never an independent measurement.
4. **Never block the Runtime.** No conversation, turn, session start, or provider call may depend on
   a synchronous Billing response, under any circumstance.
5. **Never block Deployment.** No deployment step, rollout, health check, or rollback may depend on a
   synchronous Billing response. Deployment execution ownership stays entirely with the Actuator
   (ADR-0022).
6. **Publish immutable entitlement snapshots** — versioned, org-scoped, cacheable statements of
   features, quotas, and limits.
7. **Never perform permission checks.** Billing evaluates no request, gates no action, and authorizes
   nobody. It states entitlement; it does not apply it.
8. **Require the owning platform to enforce entitlement locally**, at its own write boundary, as an
   accepted-or-rejected decision (§A2.6), against the snapshot.
9. **Never become synchronous infrastructure.** Billing is not a dependency any other plane must call
   to function. It is asynchronous, downstream, and independently failable.
10. **Own commercial truth only.** Where a fact is operational, structural, behavioural, or
    identity-bearing, Billing does not own it — full stop.

The Billing Platform **SHALL NOT** own organizations, workspaces, projects, agents, knowledge,
deployments, runtime, conversation state, messages, sessions, metrics, audit, Product Runtime,
Customer Platform, Observability, infrastructure, provider execution, authentication, authorization,
or RBAC (§5, §7).

**Topology.** Billing is a **new top-level plane**, peer to the Customer Platform and the Deployment
Actuator, depending downward only, reached through the Control Plane. It is not a module inside the
Customer Platform: revenue has a distinct lifecycle, retention obligation, compliance surface, and
failure domain, and AR-50 (one responsibility) applies at the plane level as it does at the module
level. A new top-level package therefore requires this ADR, per CODING_STANDARDS §2 and the
ADR-0018/ADR-0022 precedent.

**Determinism (§A2.4).** Billing's *behaviour* — subscription, invoice, payment, refund, and dunning
lifecycles; rating; proration; discount ordering — is deterministic and table-driven with an injected
clock. Billing's *content* — payment capture, tax determination, document delivery — is
non-deterministic and confined to adapter edges. "Own the loop, rent the edges" (§A2), applied to
money: **VoxVoice owns the revenue loop; payment and tax providers are rented edges behind
contracts.**

---

## 5. Ownership Matrix

No ownership overlap. Each fact has exactly one writer (§A2.5). Nothing in the right-hand column
moves; Billing is added, and nothing is taken away from anyone.

### 5.1 Facts Billing owns (sole writer — new)

| Fact | Owner (new) | Note |
|---|---|---|
| Commercial catalog (plans, tiers, features, meter definitions) | **Billing** | Definitions only; a meter definition names its authoritative source, it does not become one |
| Pricing / price books (currency, shapes, effective dating, versions) | **Billing** | Immutable, versioned; price change ≠ plan change |
| Billing accounts | **Billing** | **References** an organization; is never an identity of its own |
| Subscriptions (state, term, renewal, proration outcome) | **Billing** | Deterministic FSM, injected clock |
| Usage records (normalized, deduplicated, attributed billable units) | **Billing** | A **projection of `metric.*`**, not a measurement |
| Credits (balances and movements) | **Billing** | Append-only |
| Entitlements (grants + immutable snapshots) | **Billing** | Decision only — never enforcement (§11) |
| Discounts and coupons (definitions, redemptions) | **Billing** | Deterministic application order |
| Invoices and credit notes | **Billing** | Immutable once issued; gapless numbering |
| Payments (attempts, outcomes, dunning state) | **Billing** | Reconciled against the provider (§14) |
| Refunds | **Billing** | Append-only; never a ledger edit |
| Taxes (determinations + evidence) | **Billing** | Determination is a rented edge; evidence is an owned fact |
| Ledger (revenue entries) | **Billing** | Immutable, append-only — the revenue system of record |
| Contracts and licenses | **Billing** | Enterprise/channel terms expressed as subscriptions + entitlements |
| Revenue-recognition state | **Billing** | A projection over the ledger, owned by Billing |

### 5.2 Facts Billing must never own (unchanged owners — frozen)

| Fact | Authoritative owner (unchanged) | Billing's relationship |
|---|---|---|
| Organizations, workspaces, projects, members, teams | **Customer Platform** (Epic 8) | reference (scoping/attribution/seat counts) — read-only |
| Agents | **Agent Runtime** (Epic 6) + agent config (§A0) | reference only |
| Knowledge | **Knowledge Runtime** (Epic 5) | never accessed; only its emitted metrics |
| Deployments (execution state) | **Deployment Actuator** (ADR-0022) | reference status via events — read-only |
| Runtime, conversation state, messages, sessions | **Runtime Platform** (Epic 5) | never accessed; only emitted metrics |
| Metrics (`metric.*`) | **Runtime producers** (§A5) | **consume read-only**; never emit, never mutate |
| Audit | **Audit Platform / event log** (§A5/§A11.2, Epic 8) | Billing **emits** `billing.*`; Audit records them |
| Product Runtime artifacts (manifest→definition→compiled→package→plan) | **Product Runtime** (Epic 7, frozen) | reference identity read-only |
| Customer Platform state | **Customer Platform** (Epic 8, frozen) | reference read-only |
| Observability (metrics/logs/traces/health/estimated cost) | **Observability Platform** (Epic 8) | **neither consumes nor replaces** the estimate (§19) |
| Infrastructure | **Infrastructure** (external, via Actuator adapters) | none |
| Provider execution | **Provider Runtime / plugins** (Epic 5, §A6) | none; only emitted metrics |
| Authentication | **Control Plane / §A11.2** | none |
| Authorization, RBAC | **Control Plane + Customer Platform RBAC** (§A11.2) | **none — Billing performs no permission check** (§11.3) |
| Studio UI, workflows, projections | **Studio** (Consumer, ADR-0021) | Billing supplies projections Studio renders |

---

## 6. Allowed Writes

The Billing Platform may write **only** the following, and each is org-scoped (§A11.2) and audited:

1. Commercial catalog entries (plans, tiers, features, meter definitions)
2. Price books and price versions
3. Billing accounts (and the billing-account hierarchy)
4. Subscriptions and subscription lifecycle transitions
5. Usage records (the projection of `metric.*`) and their period aggregates
6. Credit balances and credit movements
7. Entitlement grants and entitlement snapshots
8. Discounts, coupons, and redemption records
9. Charge lines (rated output)
10. Invoices and credit notes
11. Payment attempts, outcomes, and dunning state
12. Refund records and their approval trail
13. Tax determinations and tax evidence
14. Revenue ledger entries
15. Contracts, licenses, and channel/partner grants
16. Revenue-recognition state
17. Marketplace orders and publisher payable entries
18. `billing.*` events on the §A5 envelope (its own domain events, as one more producer)

**Nothing else.** Every allowed write is a commercial fact. Adding a write to this list requires this
ADR to be superseded or extended — never a local exception.

---

## 7. Forbidden Writes

The Billing Platform may **never** write, mutate, delete, or otherwise author:

- Organizations, workspaces, projects, members, teams, invitations
- Agents, prompts, tools, skills, workflows, MCP connections
- Knowledge (documents, chunks, indexes, embeddings)
- Deployment execution state (ADR-0022) — including cancelling or tearing down a deployment
- Runtime state, conversation state, messages, sessions, turns
- **`metric.*` events** — Billing is a consumer of that stream, never a producer on it
- Audit records (it emits domain events; the Audit Platform records them)
- Product Runtime artifacts (manifests, definitions, compilations, packages, deployment plans)
- Customer Platform state of any kind
- Observability state (metrics, logs, traces, health, alert config, estimated cost)
- Infrastructure or provider state; provider execution of any kind
- Authentication state, credentials, sessions, tokens
- Authorization decisions, RBAC roles, scopes, or permission grants
- Studio state

**Consequential rule.** Where a commercial outcome implies an operational change (suspension,
downgrade, entitlement revocation after a refund), Billing **states the outcome**; the owning plane
acts on it. Billing never reaches into an owner's store, and **entitlement contraction never destroys
customer data** — that remains an explicit customer or contract action executed by the owning plane.

---

## 8. Interaction Model

All interaction flows **downward**: facts and requests in, projections and events out.

```
Studio (Consumer + Management Surface — ADR-0021, owns no state)
   │  plan intent · payment-method intent · purchase requests   ▲  projections: plan · usage · invoices ·
   ▼                                                            │  entitlements · credits (read-only)
Control Plane (auth · org scoping · RBAC · routing · audit)
   │
   ├─► BILLING PLATFORM (Epic 9 — new, additive; owns revenue only)
   │        │                                          │
   │        │ read-only consumption                    │ rented edges (adapters, §A6/§A2)
   │        ▼                                          ▼
   │   Event Log / `metric.*`  (authoritative usage)   Payment providers · Tax providers ·
   │   Customer Platform (Epic 8, frozen)              Document delivery · Accounting export
   │   Product Runtime  (Epic 7, frozen)
   │   Deployment Actuator (Epic 8, frozen)
   │
   │        ▲ entitlement snapshots (immutable, versioned, pull/cacheable)
   │        │
   └── enforcing planes (Customer Platform · Product Runtime · Actuator · Control Plane)
            evaluate the snapshot **locally** at their own write boundary (§A2.6)
```

**Rules of interaction.**

| # | Rule |
|---|---|
| **I1** | Billing is reached **only through the Control Plane** — never directly by Studio, never directly by a runtime. Auth, org scoping, RBAC, and audit happen before Billing is involved. |
| **I2** | Billing's consumption of `metric.*` is **read-only, asynchronous, and downstream**. It subscribes; it never asks the runtime for anything. |
| **I3** | Billing **publishes** entitlement snapshots; it never **calls** an enforcing plane. Enforcement is a local read of a snapshot, not a remote request. |
| **I4** | No plane makes a **synchronous** call to Billing on a customer-facing critical path. Snapshot reads are local and cached (§11.4). |
| **I5** | Billing is one more **event producer** on the §A5 envelope (`billing.*`, `metric.billing.*`) — so Replay, the Live Event Gateway, Observability, and Audit consume it with **zero consumer change** (AR-30: producers never know consumers). |
| **I6** | Vendor interaction (payment, tax, delivery, accounting) happens **only** through adapters. Billing never speaks a vendor protocol directly (§A6, ADR-0002/ADR-0022 pattern). |

---

## 9. Dependency Direction

**Frozen: `Billing → Platform`, one-way. Nothing depends on Billing.**

```
Studio ──► Control Plane ──► Billing ──► { Event Log, Customer Platform, Product Runtime, Actuator }  (read-only)
                                  └────► { Payment, Tax, Delivery, Accounting adapters }              (rented edges)
```

| # | Rule |
|---|---|
| **D1** | Billing depends downward on the Event Log and on Epic 5/6/7/8 read-only artifacts. |
| **D2** | **No frozen Epic depends on Billing.** No Epic 5/6/7/8 package, no Control Plane service, and no future runtime may import, call, or require the Billing Platform. |
| **D3** | Enforcing planes depend on an **entitlement snapshot** — data delivered as configuration through the Control Plane — **not on the Billing Platform**. Reading a snapshot creates no dependency edge on Billing, exactly as ADR-0019's injected immutable snapshot creates no edge on a live runtime. |
| **D4** | The graph stays **acyclic** (AR-15), as ADR-0018 fixed `product → …`, ADR-0021 fixed `Studio → Platform`, and ADR-0022 fixed `actuator → product`. Billing is a **leaf, never a hub**. |
| **D5** | Billing is **independently failable and independently deployable**. Its unavailability changes no other plane's behaviour beyond the declared degradation posture (§11.4). |

---

## 10. Revenue State Ownership

**Billing is the system of record for revenue** (§D6/§A7 ordinary durable state, one writer — not the
ADR-0009 closed-runtime exception).

| # | Rule |
|---|---|
| **RS1** | The **ledger is immutable and append-only**. There is no administrative path to edit or delete a revenue entry. Corrections are new entries (adjustment, credit note, reversal) that reference the original. |
| **RS2** | Revenue state is **reproducible**: because usage records are a deterministic projection of the event log and rating is a pure function of (usage, price version, subscription, discounts, credits, period), a period's charges can be re-derived and verified. This is the billing analogue of the Replay Platform's determinism guarantee. |
| **RS3** | **Issued financial documents are immutable** (§13). Sealing a period is a one-way door. |
| **RS4** | Revenue records have their **own retention lifecycle**, independent of and typically longer than customer-data retention. A deleted customer does not delete revenue history; the account identifier is preserved for legal retention. This asymmetry is deliberate and ratified here so it is designed, not discovered. |
| **RS5** | **Money is never approximate in storage**: amounts are integer minor units, always paired with a currency; a currency is fixed per subscription; conversion is presentation, never ledger. |
| **RS6** | Revenue-recognition state is a **projection over the ledger owned by Billing** — not a second bookkeeping system, and not something an external accounting system writes back into. |

---

## 11. Entitlement Ownership

This is the most load-bearing part of the ADR, because entitlement is where billing touches
everything else.

### 11.1 The decision/enforcement split (ratified)

| Role | Owner |
|---|---|
| **Decision** — what is this account entitled to? | **Billing** (sole authority) |
| **Enforcement** — is this specific write allowed right now? | **The plane that owns the write** (Customer Platform, Product Runtime, Actuator, Control Plane) |

### 11.2 Snapshot properties (ratified)

An entitlement snapshot is **immutable, versioned, org-scoped, cacheable, and self-describing** —
features, structural limits, rate limits, consumption quotas, support/SLA level, and retention
windows, plus the version and validity metadata needed to reason about staleness. A new entitlement
produces a **new snapshot version**; snapshots are never mutated in place. This mirrors ADR-0019's
injected-immutable-snapshot pattern and is chosen for the same reason: it removes a live dependency
without weakening authority.

### 11.3 Entitlement is not authorization (ratified)

| Question | Answered by | Owner |
|---|---|---|
| *Who are you?* | Authentication | Control Plane (§A11.2) |
| *May this principal perform this action?* | Authorization / RBAC | Control Plane + Customer Platform (§A11.2) — **unchanged** |
| *Has this account purchased this capability / is it within its limits?* | **Entitlement** | **Billing decides; the owning plane enforces** |

**Billing performs no permission check.** It evaluates no principal, no role, no scope, and no
request. It publishes a commercial statement. Authorization remains exactly where the frozen stack
put it; Billing adds no second authorization authority and no new RBAC concept.

### 11.4 Enforcement discipline (ratified)

| # | Rule |
|---|---|
| **E1** | Enforcement is **local**: the enforcing plane reads a cached snapshot and decides. No network call to Billing occurs on the enforced path. |
| **E2** | Enforcement is **accepted-or-rejected** (§A2.6) — explicit, typed, and audited. A rejection names the entitlement exceeded. |
| **E3** | **Every gate carries a mandatory fail-open / fail-closed / degrade classification.** A gate without a classification is an incomplete design and is non-compliant. |
| **E4** | Anything that could interrupt a **live conversation or an in-flight deployment is fail-open**. Anything that could create unbounded new cost or unearned capability is **fail-closed**. |
| **E5** | Quotas warn before they wall. **Silent overage is an architectural failure**, not a product decision. |
| **E6** | Entitlement contraction (downgrade, suspension, refund-triggered revocation) **never deletes customer data**. It reduces capability, reversibly. |

---

## 12. Metering Ownership

| # | Rule |
|---|---|
| **M1** | **Billing never measures.** Measurement is the runtime producers' responsibility (§A5). Billing subscribes to `metric.*` read-only. |
| **M2** | **A meter has exactly one authoritative source**, named in its definition. Two sources for one meter is a defect, not a configuration. |
| **M3** | **Usage records are a projection, not a measurement** — normalized, attributed, deduplicated billable units derived from the stream. They are the *billable* fact Billing owns; they are never a competing count of what happened. |
| **M4** | Ingestion is **idempotent by source-event identity**, so at-least-once delivery yields exactly-once billing, and reprocessing is safe. |
| **M5** | Ingestion is **fully asynchronous and downstream**. A metering outage delays billing; it never degrades a conversation, a deployment, or an API. |
| **M6** | **Attribution is total.** Every usage record resolves to exactly one billing account; unattributable usage is quarantined and alerted, never silently billed and never silently discarded. |
| **M7** | **Late arrival is an explicit, declared policy** per meter; sealed periods stay sealed, and post-seal corrections become adjustments or credit notes — never retroactive mutation. |
| **M8** | Billing **never emits or mutates `metric.*`**. Its own operational signals are emitted under its own namespace as a producer. |

---

## 13. Invoice Ownership

| # | Rule |
|---|---|
| **IN1** | Billing is the **sole author** of invoices and credit notes. No other component may create, modify, or void a billing document. |
| **IN2** | **An issued invoice is immutable.** No field ever changes after issue. |
| **IN3** | **Corrections are credit notes** — themselves immutable, referencing the original. |
| **IN4** | **Numbering is gapless, sequential, per legal entity/jurisdiction**, allocated exactly once, never reused — including across retries and failures. |
| **IN5** | An invoice is **self-contained evidence**: it carries the plan version, price version, usage summary, discount trail, tax determination, currency, period, and input identities, and must be explainable without querying live state. |
| **IN6** | **Rendering and delivery are rented edges.** A delivery failure never changes invoice state. |
| **IN7** | Zero-amount and fully credit-covered invoices are still **issued** — absence of payment is not absence of a document. |

---

## 14. Payment Ownership

| # | Rule |
|---|---|
| **PY1** | **The payment provider is the authority for money movement; Billing is the authority for revenue state.** Neither overrides the other; they are reconciled deterministically. |
| **PY2** | Payment providers are **rented edges behind adapters** (§A6, ADR-0022 pattern). Billing never speaks a provider protocol directly; multi-provider, per-region routing, and provider migration are configuration. |
| **PY3** | **Every payment operation is idempotent**, keyed by a Billing-generated operation identity. A retry never double-charges. |
| **PY4** | **Provider callbacks are notifications, not truth** — verified, deduplicated, and ordered before state advances through the FSM. Duplicate, out-of-order, and missing callbacks must all converge to the same terminal state. |
| **PY5** | **Reconciliation is scheduled and first-class.** Divergence between provider records and the ledger raises an incident; it is never auto-resolved. |
| **PY6** | **Payment instrument data never enters VoxVoice.** Instruments are provider tokens — a deliberate compliance-surface decision, not an operational one. |
| **PY7** | **Dunning is deterministic and policy-driven**, with a bounded, auditable recovery ladder (§A10 graceful degradation): retry → alternate instrument → notification → grace → reduced entitlement → suspension → cancellation. |
| **PY8** | **Payment failure never destroys anything.** It contracts entitlement, reversibly. Data lifecycle stays with the owning plane. |
| **PY9** | Refunds are **append-only** events referencing the original charge; never a ledger edit. Above a policy threshold they require approval with separation of duties (the ADR-0022 approval posture, applied to money). Disputes and chargebacks are distinct from refunds and are never silently reconciled as such. |

---

## 15. Tax Ownership

| # | Rule |
|---|---|
| **TX1** | **Determination is a rented edge**; **evidence is an owned fact.** Billing hard-codes no rate and no jurisdiction rule. |
| **TX2** | Every determination is recorded **immutably with the invoice** — jurisdiction, rate, rule applied, customer tax status, adapter identity, and timestamp. Rates change; issued invoices must not. |
| **TX3** | The **customer tax profile** (residency, registration numbers such as GSTIN/VAT ID, business-vs-consumer status, exemptions) is a **revenue fact owned by Billing**, referencing — never duplicating — the Customer Platform's organization. |
| **TX4** | **Reporting exports recorded evidence.** Recomputing tax at reporting time is forbidden: it can diverge from what was invoiced. |
| **TX5** | New jurisdictions (GST, VAT, sales tax, and others) are **adapter configuration**, never a structural change (§A12/§A6). |
| **TX6** | Withholding tax is a distinct ledger concept — a remittance obligation, never a discount. |

---

## 16. Relationship with Customer Platform (Epic 8, frozen)

| Direction | Relationship |
|---|---|
| **Billing → Customer Platform** | **Read-only.** Organization, workspace, project, member, and role facts are referenced for scoping, attribution, and seat counts. Billing writes none of them and copies none of them as truth. |
| **Customer Platform → Billing** | **None.** No Epic 8 package imports, calls, or requires Billing. It consumes the **entitlement snapshot** as data delivered through the Control Plane, exactly as it consumes any other configuration (D3). |
| **Studio → Billing** | Only what ADR-0021 already permits: **plan intent**, payment-method intent, purchase requests, and **read-only projections** (plan, usage, invoices, credits, entitlements). The **Billing Ledger remains an ADR-0021 Forbidden Write**, unchanged and unrelaxed. |
| **RBAC** | The **Billing Admin** role already exists in Epic 8's role model. Epic 9 populates its permissions within the existing RBAC system and introduces **no parallel authorization mechanism** (§11.3). |
| **Audit** | Billing emits `billing.*`; the Epic 8 Audit Platform records them. Billing keeps **no private audit log**. |
| **Org lifecycle** | Organization creation implies billing-account creation (Free tier by default). Organization deletion implies billing-account **retention**, not deletion (RS4). |
| **Freeze** | Epic 8 is **not modified**. Customer Platform v1.0 remains byte-for-byte unchanged, and its one-way `Studio → API → Application → Foundation` direction is untouched. |

---

## 17. Relationship with Product Runtime (Epic 7, frozen)

| Concern | Relationship |
|---|---|
| **Artifacts** | Billing **references** the Epic 7 identity chain (manifest → definition → compiled → package → deployment plan) read-only, so a charge or a marketplace listing traces to an exact artifact version. It stores no artifact and extends no chain. |
| **Compilation** | Unaffected. Billing never participates in, gates, or delays the compile chain. |
| **Entitlement at publish/compile** | Enforced **by Product Runtime's own boundary** against the snapshot. Product Runtime makes no call to Billing (D3). |
| **Nouns** | ADR-0020's reserved "Registry" nouns are untouched; Billing introduces no competing registry vocabulary. |
| **Declarative freeze** | Preserved: Billing adds no execution to Product Runtime and reads only its declarative outputs (ADR-0018). |

---

## 18. Relationship with Deployment (Epic 8 Actuator, ADR-0022)

| Concern | Relationship |
|---|---|
| **Ownership** | Deployment **execution** state remains entirely the Actuator's (ADR-0022). Billing observes emitted events and owns nothing about deployment. |
| **Metering** | Deployment applies, active environments, and environment-hours are metered from the Actuator's emitted `metric.deployment.*` — read-only, downstream. |
| **Entitlement gating** | Environment counts, concurrency, frequency, and approval requirements are enforced **at the Actuator's own admission boundary** against the snapshot. **Billing never calls the Actuator and never cancels a deployment.** |
| **In-flight safety** | Deployment gating is **fail-open once a rollout has started** (E4). Billing state must never orphan a deployment mid-rollout; the Actuator's cancellation and terminal-state guarantees are unaffected. |
| **Suspension** | A suspended subscription blocks **new** deployments; it never tears down running ones. Teardown is a customer or contract action executed by the owning plane. |

---

## 19. Relationship with Observability (Epic 8)

| Concern | Relationship |
|---|---|
| **Direction** | Billing **consumes** `metric.*` for metering and **produces** `billing.*` + `metric.billing.*` for observation. Observability observes Billing like any other producer. |
| **The estimate/authority separation (ratified)** | Observability's cost view stays **estimated and directional** (its frozen M8.17 scope excludes billing). Billing's charges are **authoritative**. **Neither is derived from the other**; both are derived from the same events to answer different questions — *"what is this costing us?"* versus *"what do we charge you?"*. Studio must label them distinctly so an estimate is never mistaken for an invoice. |
| **No replacement** | Epic 9 does **not** remove, replace, or rewire Observability's `CostService`. Epic 8 remains frozen. |
| **Reuse** | Billing adds **no new observability mechanism**: its health signals, alerts, logs, traces, and replayability come from the existing Epic 4/8 platforms because Billing is a §A5 producer. |

---

## 20. Consequences

**Benefits.**

- **Money finally has an owner**, and exactly one (§A2.5). The gap ADR-0021 named — "until a Billing
  Runtime exists, Studio must not invent a billing ledger of its own" — is closed by adding the
  owner, not by relaxing the rule.
- **The freeze is preserved by construction.** Billing cannot break Epics 5–8: it writes none of
  their state and none of them can import it (D2).
- **A billing outage cannot become a product outage.** No synchronous dependency exists on any
  critical path; enforcement degrades along a declared, classified ladder (§A10).
- **Charges are explainable and reproducible** — derived from an immutable event log by a
  deterministic function, with immutable documents and evidence. Auditability is structural, not
  procedural.
- **Authorization stays singular.** Entitlement is commercial capability, not permission; §A11.2 RBAC
  gains no rival.
- **Commercial agility without engineering projects.** Tiers, prices, meters, coupons, currencies,
  jurisdictions, marketplace terms, enterprise contracts, reseller and white-label channels are
  catalog, policy, and adapter configuration over one core (§A12).
- **Build-vs-buy stays open.** Because payment, tax, invoicing, and delivery are rented edges, a
  third-party billing/tax engine can be adopted **behind the seams without moving ownership** — the
  decision the roadmap defers to M9.0C.

**Trade-offs.**

- **Indirection.** Billing cannot "just read the sessions table" or "just call the runtime". It must
  consume events and reference owners — more moving parts than a monolithic billing module.
- **Eventual consistency is explicit.** Entitlement enforcement acts on a snapshot with a declared
  staleness bound, which means brief windows where enforcement lags reality. That is the deliberate
  price of never blocking the runtime, and it is bounded and observable rather than hidden.
- **Fail-open gates permit bounded leakage.** A live conversation continues even if entitlement data
  is stale. Revenue leakage is preferred, explicitly, over customer-visible interruption — and is
  measured and reconciled rather than assumed away.
- **Two cost numbers exist** (estimated and authoritative). This must be handled with vocabulary and
  UI discipline (§19), permanently.
- **Duplication of "usage" is apparent, not real.** Usage records look like a second count; they are a
  projection with a named single source (M2/M3). This distinction must be defended in review.

**Future extensibility.**

- New meters, plans, price shapes, currencies, jurisdictions, and channels are additive (§A12).
- Marketplace commerce and enterprise licensing extend the same core (recommended ADR-0029).
- The billing-account hierarchy, separate from org tenancy, makes reseller/OEM/white-label a
  configuration exercise (recommended ADR-0030).
- A future revenue-reporting or accounting integration is an **export** over the ledger, never a
  second bookkeeper.

**Risks.**

- **Erosion** — a future contributor adding a Billing-owned usage counter, org copy, or synchronous
  entitlement check "for convenience". Mitigated by §22 Compliance Verification, the mandatory
  Allowed/Forbidden Writes lists, and this ADR being binding on every M9 milestone.
- **Metering correctness** — the one failure mode that is simultaneously a revenue loss, a trust loss,
  and a legal exposure (Blueprint R-1). Mitigated by idempotent ingestion, total attribution,
  reproducibility, and shadow validation before any customer is charged.
- **Entitlement staleness** — mitigated by declared bounds, mandatory gate classification, and
  observability on snapshot age.

---

## 21. Alternatives Considered

1. **Billing as a module inside the Customer Platform (Epic 8).** **Rejected.** Epic 8 is frozen, so
   this would either break the freeze or bolt revenue onto a plane whose ownership (customers,
   workspaces, projects, agents, audit, observability) is deliberately *not* revenue. It also merges
   two failure domains, two retention regimes, and two compliance surfaces into one, violating AR-50
   at the plane level, and it would put revenue behind the same availability envelope as the customer
   API. Revenue's lifecycle outlives the customer record (RS4); it cannot be a sub-module of it.

2. **Billing measures its own usage** (instrumenting the runtime or maintaining independent
   counters). **Rejected.** It creates a **second writer for one fact** (§A2.5) and guarantees that
   what the platform reports and what the customer is charged eventually diverge — the exact failure
   §A7 exists to prevent ("projections of the same event log cannot diverge"). It would also require
   Billing to reach into Epic 5/6/8 internals, breaking the freeze and inverting the dependency
   direction.

3. **Billing enforces entitlement synchronously** (every enforcing plane calls Billing before acting).
   **Rejected.** It inverts the dependency direction ADR-0018/0021/0022 fixed (frozen Epics would
   depend on Epic 9), puts a revenue system on the conversation and deployment critical paths, and
   makes a payment-provider or metering outage a customer-visible conversation outage — a §A10
   violation and a direct contradiction of §A1 (the runtime is the product). It also makes Billing an
   authorization authority (§11.3), creating a second answer to "may this happen?".

4. **Studio owns billing** (a console-side ledger and plan state). **Rejected, already.** ADR-0021
   lists the Billing Ledger among Studio's Forbidden Writes and explicitly defers to a future Billing
   Runtime. Re-opening it would make a Consumer an owner and reduce "the runtime is the product" to
   "the dashboard is the product".

5. **Observability's cost service is promoted to billing.** **Rejected.** Epic 8 scoped it as
   estimated and directional *by design* and froze it that way; promoting an estimate to an invoice
   would put revenue inside the observation plane (Observability owns observation — M8.17's own
   golden rule), and would make the platform's monitoring view and its financial record the same
   artifact, which is both an ownership violation and an auditing hazard.

6. **Buy a complete third-party billing SaaS and let it own the boundary** (the vendor becomes the
   system of record for subscriptions, entitlements, and the ledger). **Discussed, not adopted as an
   architecture.** Renting the *engine* is a legitimate implementation choice — and is deliberately
   left open, to be decided at M9.0C. But letting the vendor own the *boundary* would put the
   authoritative statement of what a customer may do inside a third party, make provider migration a
   re-architecture, and place a rented system on the platform's dependency path. This ADR fixes
   ownership; the seams (payment, tax, invoicing, rating, delivery) are drawn so a vendor can sit
   **behind** them without changing who owns what.

7. **Current proposal — Billing is a new additive plane that owns revenue and nothing else, consumes
   authoritative facts read-only, publishes immutable entitlement snapshots, and is never
   synchronous.** **Accepted.** It is the only option consistent with §A2.5 (one writer per fact),
   §A1/§A0 (the runtime is the product; Consumers own no runtime state), §A5/§A7 (one event log, many
   projections), §A10 (graceful degradation), §A11.2 (unchanged authorization), §A12 (additive
   extension), AR-15 (acyclic), and the Epic 5/6/7/8 freezes simultaneously.

---

## 22. Compliance Verification

1. **Mandatory on every Epic 9 milestone.** Every M9.x design, implementation, and review must comply
   with this ownership model. A design or change that gives Billing ownership of a Forbidden Write,
   that makes any plane synchronously dependent on Billing, or that adds a `Platform → Billing`
   dependency is **non-compliant**.
2. **Allowed/Forbidden Writes are exhaustive.** Billing writes only §6; it never writes §7. New writes
   require this ADR to be superseded or extended — never a local exception, a comment, or a
   convenience.
3. **One-way dependency is enforced mechanically.** The existing import-boundary/AST tests — which
   already prove `Studio → Platform`, `actuator → product` read-only, *"no Epic 8 layer imports a
   frozen engine"*, and *"no frozen Epic imports Epic 8"* — are extended to Billing when its package
   lands: **no Epic 5/6/7/8 package may import Billing**, and Billing may import only downward.
4. **Frozen Epics remain byte-for-byte unchanged.** Verified the same way Epic 8 verified it (no
   tracked frozen-Epic source modified). Any Epic 9 change that requires editing a frozen Epic is
   non-compliant by definition.
5. **No `metric.*` production by Billing.** Verified by boundary review: Billing subscribes to the
   stream and emits only its own namespace.
6. **No synchronous billing on a critical path.** Verified by review of every enforcement point:
   enforcement reads a local snapshot; no enforcing path performs a call to Billing.
7. **Every entitlement gate is classified** fail-open / fail-closed / degrade (E3). An unclassified
   gate fails review.
8. **Immutability is verified, not asserted** — no update or delete path exists on issued invoices,
   credit notes, or ledger entries (the append-only posture Epic 8 already verifies for audit).
9. **Org scoping and audit** on every billing fact and every billing mutation (§A11.2/§A5), reviewed
   as Epic 8 reviews its own org-scoped queries.
10. **Any violation requires a superseding ADR.** No Epic 9 milestone may deviate by note, comment, or
    convenience. Deviation is a constitutional change and needs a new ADR against this one (authority
    hierarchy, §A11.1.2).

---

## 23. Documentation Updates

Updated in the same change-set:

- **ADR Register** — [adr/README.md](README.md): ADR-0023 added; the ADR-0024 reservation restated.
- **Documentation index** — [INDEX.md](../INDEX.md): ADR table row added.
- **CHANGELOG** — [CHANGELOG.md](../../CHANGELOG.md): Epic 9 (M9.0A blueprint, M9.0B ratification)
  entries.
- **Decision log** — rationale recorded as **DL-0108** (the M9.0A blueprint is **DL-0107**).
- **Cross-references** — the [Epic 9 Billing Platform Blueprint](../architecture/epic9/billing-platform-blueprint.md)
  §4/§9/§26 now reads against a ratified ADR-0023 rather than a recommended one.

No frozen document is modified. ADR-0021 and ADR-0022 are **referenced and reinforced, not amended**:
ADR-0021's Ownership Mapping line *"Billing → Billing Runtime (future), metering the authoritative
`metric.*` stream"* is fulfilled exactly as written, and its Forbidden Write on the Billing Ledger
stands unchanged.

---

## 24. Final Recommendation

**Ratify ADR-0023 as stated, and treat it as binding on every Epic 9 milestone from M9.1 onward.**

Three provisions carry the weight, and any future pressure to relax them should be read as a proposal
to supersede this ADR, not as an implementation detail:

1. **Billing never measures.** The moment Billing counts anything itself, the platform has two truths
   about usage, and the invoice and the dashboard begin to diverge. `metric.*` is the measurement;
   usage records are its billable projection.
2. **Billing never blocks and never authorizes.** Entitlement is a published, immutable snapshot the
   owning plane enforces locally, with a mandatory fail-open/fail-closed classification. This single
   provision is what guarantees a revenue outage can never become a conversation outage, and what
   keeps §A11.2 authorization singular.
3. **Billing owns revenue and nothing else.** Every fact it holds is commercial; every fact it needs
   from elsewhere it references read-only. This is what keeps Epics 5–8 frozen by construction rather
   than by discipline.

The remaining Epic 9 decisions — entitlement mechanics (ADR-0025), money and ledger discipline
(ADR-0026), payment/tax edges (ADR-0027), critical-path prohibition (ADR-0028), commerce and
licensing reuse (ADR-0029), and the billing-account hierarchy (ADR-0030) — are recommended in the
Blueprint §26 and ratified separately. **ADR-0024 remains reserved for Coach.** Implementation may
begin at M9.1 only under this boundary.

Epics 5, 6, 7, and 8 remain frozen and byte-for-byte unchanged. This ADR authorizes no code.
