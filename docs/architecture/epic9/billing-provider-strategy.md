# VoxVoice — M9.0C Billing Build-vs-Buy & Provider Strategy

| | |
|---|---|
| **Milestone** | **M9.0C** — Epic 9 Phase 0C (architecture governance only) |
| **Status** | ✅ **Accepted 2026-08-01** — binding on every Epic 9 milestone. No implementation, no code, no APIs, no schemas, no repositories, no database, no migrations, no UI. |
| **Decision** | **Option C — VoxVoice owns commercial truth; payment and tax providers are rented edges behind adapters.** |
| **Depends on** | [ADR-0023](../../adr/ADR-0023-billing-sole-owner-of-revenue.md) (ratified), [Epic 9 Billing Platform Blueprint](billing-platform-blueprint.md) (M9.0A), Constitution v2, ADR-0001…ADR-0023, Epic 5/6/7/8 Freeze Manifests, Repository Baseline v3.0.0 |
| **Modifies** | **Nothing.** Epics 5–8 remain frozen and byte-for-byte unchanged. This milestone changes no ownership boundary — it selects the *implementation strategy* inside the boundary ADR-0023 already fixed. |
| **Encoding** | This decision is the substance of the recommended **ADR-0027** ("payment and tax providers are rented edges; the provider is the authority for money movement, Billing for revenue state"). It is binding from ratification of this document; ADR-0027 will encode it verbatim when the ADR-0025–0030 batch is ratified. **ADR-0024 remains reserved for Coach.** |

> **Commercial-terms caveat.** Vendor capabilities, fee structures, regional availability, and
> merchant-of-record terms change continuously and vary by entity, country, and negotiated contract.
> Every commercial figure in §4 is a **directional planning input to be re-verified at contract
> time** — it is not a quoted term. The architecture below is deliberately indifferent to which
> vendor wins; that indifference is the point.

---

# 1. Executive Summary

ADR-0023 fixed *who owns revenue*. This milestone fixes *what VoxVoice builds versus rents inside
that ownership* — permanently, before a line of Epic 9 code exists.

Three strategies were evaluated:

- **Option A — own the entire billing stack** (build rating, invoicing, tax, dunning, and payment
  orchestration in-house, integrating only raw card acquiring).
- **Option B — delegate everything to a billing/merchant platform** (Stripe Billing, Paddle, Lemon
  Squeezy, Chargebee, or Recurly owns plans, subscriptions, pricing, entitlements, and invoices;
  VoxVoice reads them back).
- **Option C — own commercial truth, rent payment and tax** (VoxVoice owns catalog, pricing,
  subscriptions, metering, rating, entitlements, invoices, and the ledger; payment movement and tax
  determination sit behind adapters).

**Option C is selected.** Option B is not merely suboptimal — it is **unratifiable**: it makes a
third party the writer of subscriptions, plans, pricing, entitlements, and the commercial catalog,
which ADR-0023 §5–§7 forbids, and §A2.5 (one writer per fact) forbids more generally. Adopting it
would require superseding ADR-0023 in the same quarter it was ratified. Option A is architecturally
permissible but commercially reckless: it puts a solo/small team on the hook for PCI scope, global
tax determination, e-invoicing formats, payment-method breadth, and network compliance — none of
which are VoxVoice's product, and all of which are the industry's most mature rented services.

Option C is the only strategy that satisfies the ratified boundary *and* the delivery reality. Under
it:

| Concern | Owner |
|---|---|
| Commercial catalog, plans, price books, subscriptions, metering, rating, entitlements, credits, invoices, ledger, contracts, licenses | **VoxVoice Billing** — always, in every market, under every channel |
| Money movement (authorize, capture, refund, payout, instrument storage) | **Payment providers**, behind a Payment Adapter |
| Tax determination and registration validation | **Tax providers**, behind a Tax Adapter |
| Document rendering, delivery, e-invoicing formats, accounting export | **Service providers**, behind adapters |

**Provider posture.** A **multi-provider design from day one** — Stripe-class global acquiring as the
primary international provider, a domestic India provider (Razorpay-class) as the primary INR
provider, with routing by market/currency/method. A **merchant-of-record provider (Paddle- or Lemon
Squeezy-class) is retained as an optional distribution *channel*, never as an owner**: if a market's
compliance cost justifies it, the MoR becomes the seller of record for that channel, and VoxVoice
records channel-settled revenue while continuing to own subscriptions, entitlements, and the ledger
exactly as before.

**The test this strategy is designed to pass:** replacing any payment provider, adding a market,
switching tax engines, or adopting a MoR channel must be a **configuration and adapter exercise —
never a re-architecture, never a customer migration, never a change to what VoxVoice owns.**

---

# 2. Problem Statement

ADR-0023 answered the ownership question. It deliberately left one question open, and the Blueprint
named it the largest schedule risk in the epic (**R-12**):

> **Which parts of the billing stack does VoxVoice build, and which does it rent — and can that
> choice be reversed later without a re-architecture or a customer migration?**

Four forces pull in different directions:

1. **The ratified boundary.** ADR-0023 makes Billing the sole writer of plans, pricing, subscriptions,
   entitlements, catalog, and revenue state. Any vendor that writes those facts is a second writer
   (§A2.5) and is out of bounds by construction — regardless of how good it is.
2. **Delivery reality.** Rating, proration, dunning, invoicing, global tax determination, payment-
   method breadth, PCI scope, and e-invoicing are each multi-year problems. A small team that builds
   all of them ships billing instead of shipping VoxVoice.
3. **Commercial breadth.** The Blueprint commits to usage, seat, hybrid, credits, trials, coupons,
   enterprise contracts, offline licensing, marketplace, reseller, and white-label. Most vendor
   billing platforms model a subset well and the rest badly or not at all — and the ones they model
   badly (usage-metered hybrid, offline licensing, reseller hierarchies) are precisely VoxVoice's
   growth paths.
4. **Lock-in asymmetry.** Renting *payment movement* is reversible: instruments are re-tokenizable,
   and the ledger stays home. Renting *commercial truth* is not: subscriptions, entitlements, price
   history, and invoice lineage inside a vendor become that vendor's asset, and leaving means
   migrating live customers, re-creating billing anniversaries, and losing invoice continuity.

The decision must therefore be made **now**, permanently, and in a form where the reversible parts
stay reversible and the irreversible parts are never rented.

---

# 3. Architectural Options

## Option A — Own the entire billing stack

VoxVoice builds catalog, pricing, subscriptions, metering, rating, entitlements, invoicing, tax
determination, dunning, and payment orchestration, integrating only low-level acquiring (or, in the
extreme, direct acquirer/gateway relationships).

- **Owns:** everything, including tax logic and payment orchestration.
- **Rents:** raw card acquiring only.
- **Consequence:** maximum control, maximum surface. VoxVoice inherits PCI scope decisions, tax
  determination correctness across jurisdictions, payment-method breadth, network rule changes,
  e-invoicing mandates, and provider outage handling — permanently, as product work.

## Option B — Delegate everything to a billing platform

A vendor (Stripe Billing, Paddle, Lemon Squeezy, Chargebee, or Recurly) owns the commercial model:
plans, prices, subscriptions, entitlements, invoices, dunning, and often tax and merchant-of-record
status. VoxVoice calls the vendor to create subscriptions and reads state back to render Studio.

- **Owns:** the UI and a projection of the vendor's state.
- **Rents:** the commercial model itself.
- **Consequence:** fastest possible time-to-first-invoice, and a **direct contradiction of ADR-0023**:
  the vendor becomes the writer of plans, pricing, subscriptions, entitlements, and catalog. VoxVoice
  would hold a read-copy of its own commercial truth — the exact "second writer / divergent
  read-copy" failure §A2.5 and §A7 exist to prevent, and the same failure ADR-0021 rejected for
  Studio.

## Option C — Own commercial truth, rent payment and tax *(selected)*

VoxVoice owns the commercial catalog, price books, billing accounts, subscriptions, metering, rating,
entitlements, credits, discounts, invoices, ledger, contracts, and licenses. Payment movement, tax
determination, document delivery, and accounting export are **rented edges behind adapters** (§A6,
the ADR-0002 and ADR-0022 pattern).

- **Owns:** all commercial truth, in every market, under every channel.
- **Rents:** money movement, tax determination, instrument storage, document delivery, accounting
  export — and, optionally per market, **merchant-of-record status as a channel**.
- **Consequence:** VoxVoice keeps everything that is irreversible to rent and rents everything that is
  expensive to build and cheap to replace. "Own the loop, rent the edges" (§A2), applied to money.

---

# 4. Comparative Analysis

## 4.1 Vendor evaluation

Scores are architectural fit **for VoxVoice under ADR-0023**, not general product quality. Each vendor
is evaluated twice where relevant: as an **owner** of the commercial model (Option B), and as a
**rented edge** under Option C.

| Vendor | Class | As an **owner** (Option B) | As a **rented edge** (Option C) | Verdict |
|---|---|---|---|---|
| **Stripe** | PSP + billing suite + tax + MoR option | **Rejected as owner** — Stripe Billing would own plans, prices, subscriptions, and entitlement-adjacent state (ADR-0023 §7 violation). | **Strong.** Best-in-class acquiring breadth, instrument tokenization, idempotency semantics, webhook model, payouts, and a tax service usable purely as a determination edge. | ✅ **Primary global payment adapter**; tax service a candidate tax adapter |
| **Razorpay-class domestic India PSP** | Domestic PSP | Not applicable (not a billing-model owner in our usage) | **Strong for INR**: domestic methods (UPI, netbanking, RuPay, domestic cards), INR settlement, local mandates/autopay, GST-aware documentation. Materially better domestic coverage and economics than routing INR through a global-only acquirer. | ✅ **Primary INR payment adapter** |
| **Paddle** | Merchant of record | **Rejected as owner** — MoR platforms own the subscription, the price, the invoice, and the customer-of-record relationship. | **Conditional.** Valuable as a **channel**: it absorbs global sales-tax/VAT registration, remittance, and consumer-payment breadth. VoxVoice must continue to own subscriptions/entitlements and record channel-settled revenue. | 🟡 **Optional MoR channel**, never an owner |
| **Lemon Squeezy** | Merchant of record (lighter) | **Rejected as owner** — same MoR ownership inversion. | **Conditional, lower ceiling.** Simpler and faster for low-touch self-serve; weaker fit for usage-metered hybrid billing, enterprise terms, and reseller hierarchies. | 🟡 Fallback MoR channel only |
| **Chargebee** | Subscription-management platform | **Rejected as owner** — it exists precisely to own plans, subscriptions, pricing, invoices, and entitlements. Adopting it as owner means adopting its commercial model as VoxVoice's architecture. | **Weak fit.** As a pure rented edge it offers little VoxVoice would not already own; using only its invoicing/tax pieces buys a heavy dependency for a thin slice. | ❌ Not adopted |
| **Recurly** | Subscription-management platform | **Rejected as owner** — same ownership inversion as Chargebee. | **Weak fit**, same reasoning. | ❌ Not adopted |
| **Custom billing (Option A)** | In-house everything | Fully compliant with ADR-0023 by construction. | n/a | ❌ Rejected on delivery risk, tax correctness, and PCI scope (§5.2) |
| **Hybrid (Option C)** | Own truth, rent edges | Fully compliant with ADR-0023. | Rents exactly the reversible, expensive-to-build parts. | ✅ **Selected** |

**Tax and document providers** are evaluated on the same basis and are adapter-level choices, not
architecture: a Stripe-Tax- or Avalara-class engine for international determination, and an
India-specific adapter for GST place-of-supply, GSTIN handling, HSN/SAC classification, and compliant
invoice fields / e-invoicing where mandated. **No tax rule is ever encoded in VoxVoice's core** (§15,
ADR-0023 TX1/TX5).

## 4.2 Dimension-by-dimension comparison

Legend: ✅ strong · 🟡 partial/conditional · ❌ unacceptable for VoxVoice.

| # | Dimension | **A — Own everything** | **B — Delegate everything** | **C — Own truth, rent edges** |
|---|---|---|---|---|
| 1 | **Ownership** | ✅ Total | ❌ **Vendor owns plans, pricing, subscriptions, entitlements, catalog — ADR-0023 §7 violation** | ✅ VoxVoice owns all commercial truth |
| 2 | **Vendor lock-in** | ✅ None | ❌ Deepest possible: the commercial model *is* the vendor's | ✅ Confined to payment/tax edges |
| 3 | **Migration risk** | ✅ None | ❌ Leaving = migrating live subscriptions, anniversaries, invoice lineage, entitlements | ✅ Swap = re-tokenize instruments; ledger, subscriptions, invoices stay home |
| 4 | **Enterprise contracts** | ✅ Fully modellable | ❌ Committed spend, true-up, custom price books, net-N, PO, offline settlement are weak or absent in most vendor models | ✅ Modelled natively; vendor never sees contract terms |
| 5 | **Offline licensing** | ✅ | ❌ Fundamentally outside a payment-platform model | ✅ License = entitlement grant, unrelated to the payment edge |
| 6 | **Marketplace support** | ✅ | 🟡 Vendor marketplace/connect models exist but impose their commercial shape and payout semantics | ✅ Orders → entitlements → ledger; payouts use the provider's payout rails as an edge |
| 7 | **White-label support** | ✅ | ❌ Vendor-branded checkout, invoices, and customer-of-record leak the vendor to the partner's customer | ✅ Documents and entitlements are VoxVoice-owned; branding is an entitlement flag |
| 8 | **Reseller model** | ✅ | ❌ Parent/child billing hierarchies separate from tenant identity are rarely expressible | ✅ Billing-account hierarchy owned by VoxVoice (Blueprint §17, recommended ADR-0030) |
| 9 | **Usage metering** | ✅ | 🟡 Vendors accept usage records, but the source of truth becomes the vendor's counter — a second measurement (ADR-0023 M1–M3 violation) | ✅ Projection of `metric.*`; never leaves VoxVoice as truth |
| 10 | **Credits** | ✅ | 🟡 Vendor credit models are opinionated and often can't express prepaid-credit + overage + contract drawdown together | ✅ Owned, append-only |
| 11 | **Seat billing** | ✅ | ✅ Well supported | ✅ Owned; seat counts sourced from Customer Platform read-only |
| 12 | **Hybrid billing** (recurring + seats + usage + credits on one subscription) | ✅ | 🟡 The most common vendor weak spot; frequently forces multiple subscriptions per customer | ✅ First-class in the rating model |
| 13 | **Multi-provider support** | ✅ | ❌ Effectively single-provider by construction | ✅ Designed in from day one (§13) |
| 14 | **International taxation** | ❌ Building determination for GST/VAT/sales tax and keeping it current is a permanent liability | ✅ Strongest case for B — MoR absorbs registration and remittance | ✅ Determination rented; **evidence owned**; MoR available per-market as a channel |
| 15 | **Payment methods** | ❌ Breadth is a vendor competency, not ours | ✅ Broad | ✅ Broad — inherited from the chosen adapters, and additive per market |
| 16 | **Compliance** (PCI, invoicing mandates) | ❌ Largest self-inflicted surface | ✅ Vendor absorbs most | ✅ PCI scope minimized (tokens only); invoicing mandates handled at the document adapter |
| 17 | **Scalability** | ✅ Controllable | 🟡 Bounded by vendor API limits and object models | ✅ Metering/rating scale in-house; edges are bounded, queued, circuit-broken |
| 18 | **Cost** | 🟡 Low variable cost, very high fixed engineering cost | 🟡 Lowest build cost; highest ongoing take-rate, especially MoR pricing on every transaction | ✅ Acquiring fees only on the rented edge; no platform take-rate on VoxVoice's own commercial logic |
| 19 | **Developer experience** | ❌ Everything is ours to build and debug | ✅ Best initially; degrades sharply at the first requirement the vendor doesn't model | ✅ Good — clean seams, deterministic core, vendor SDK confined to one adapter |
| 20 | **Operational complexity** | ❌ Highest | ✅ Lowest | 🟡 Moderate — reconciliation and multi-provider routing are real, ongoing work |
| 21 | **Failure isolation** | 🟡 Ours to design, ours to get wrong | ❌ Vendor outage = commercial model unavailable; can bleed into product surfaces | ✅ Edge failure degrades money movement only; ledger, entitlements, and product unaffected |
| 22 | **Long-term flexibility** | ✅ Total | ❌ Bounded by the vendor's roadmap | ✅ New markets/methods/channels are additive (§A12) |

**Reading the table.** Option B wins exactly two dimensions outright (build cost, initial developer
experience) and one shared (taxation) — and loses, fatally, on the dimension that cannot be recovered
later: **ownership**. Option A wins ownership and loses on every dimension where the industry has a
mature rented answer. Option C wins or ties on 19 of 22, and its one genuine weakness — operational
complexity from reconciliation and routing — is bounded, budgeted, and observable.

---

# 5. Recommended Strategy

## 5.1 The decision

> **Option C. VoxVoice Billing owns commercial truth permanently and in every market. Payment
> movement, tax determination, document delivery, and accounting export are rented edges behind
> adapters. Merchant-of-record status may be rented per market as a distribution *channel*, never as
> an owner.**

Concretely:

| Layer | Strategy |
|---|---|
| Commercial catalog, plans, price books | **Build** — owned forever |
| Billing accounts, hierarchy, subscriptions, terms, proration | **Build** — owned forever |
| Metering, usage records, aggregation | **Build** — owned forever (ADR-0023 M1–M8) |
| Rating, discounts, coupons, credits | **Build** — owned forever |
| Entitlements and snapshots | **Build** — owned forever (never rentable: it is the platform's own capability statement) |
| Invoices, credit notes, numbering, ledger, revenue recognition | **Build** — owned forever |
| Dunning policy and lifecycle | **Build** (policy) — the provider supplies retry mechanics, not the ladder |
| Payment authorization, capture, refund, payout, instrument storage | **Rent** — Payment Adapter |
| Tax determination, registration validation | **Rent** — Tax Adapter |
| Document rendering/delivery, e-invoicing formats | **Rent** — Document Adapter |
| Accounting/ERP export | **Rent** — Export Adapter |
| Merchant-of-record (per market, optional) | **Rent as a channel** — never as an owner |

## 5.2 Why Option A was rejected

Option A is fully compliant with ADR-0023 and still wrong.

1. **Tax determination is a permanent correctness liability, not a feature.** GST place-of-supply,
   VAT reverse charge, evolving digital-services rules, and e-invoicing mandates change continuously
   across jurisdictions. Getting them wrong is a legal exposure discovered at audit, years later
   (Blueprint R-7). This is the canonical case for renting.
2. **PCI scope is an architectural decision that Option A gets backwards.** Handling instruments
   in-house expands the compliance surface enormously for zero product differentiation. ADR-0023 PY6
   already ratified that payment data never enters VoxVoice; Option A pushes against that.
3. **Payment-method breadth is a vendor competency.** UPI, cards, wallets, netbanking, SEPA, ACH,
   local methods, mandates, 3-D Secure, and network rule changes are a full-time product for someone
   else.
4. **It spends the team's scarcest resource on the least differentiating work.** The Blueprint already
   sizes metering (XL/Critical) and payments (XL/Critical) as the epic's two hardest milestones;
   Option A adds tax determination and acquiring on top.
5. **It buys no ownership Option C lacks.** Everything irreversible — catalog, subscriptions,
   entitlements, invoices, ledger — is owned identically under C. Option A only adds surface.

**Rejected: maximum control over things that are not the product, at the cost of shipping the
product.**

## 5.3 Why Option B was rejected

Option B is rejected on grounds that are constitutional, not preferential.

1. **It violates ADR-0023 §5–§7 directly.** Plans, pricing, subscriptions, entitlements, and the
   commercial catalog are listed as facts **Billing owns as sole writer**. A vendor writing them is a
   second writer for each — a §A2.5 violation, ratified against three weeks ago.
2. **It recreates the exact failure ADR-0021 rejected for Studio.** Studio was forbidden a
   billing ledger and a session store because a read-copy of someone else's truth diverges (§A7).
   Option B makes VoxVoice itself the holder of a read-copy of its own commercial model.
3. **It makes usage metering a second measurement.** ADR-0023 M1–M3 ratify that Billing never
   measures and that usage records are a projection of `metric.*` with one authoritative source.
   Pushing usage into a vendor's counter and reading charges back creates a competing count — and
   guarantees that the platform's dashboards and the customer's invoice eventually disagree.
4. **It cannot express VoxVoice's committed commercial model.** Hybrid subscriptions (recurring +
   seats + usage + credits on one account), enterprise committed spend with true-up, offline
   licensing, reseller hierarchies separate from tenant identity, and white-label documents are
   exactly where vendor billing models are weakest — and they are the Blueprint's stated growth paths
   (§14–§17).
5. **Its lock-in is the irreversible kind.** Leaving means migrating live subscriptions, preserving
   billing anniversaries, re-establishing mandates, and breaking invoice lineage — a customer-visible
   migration. Payment-edge lock-in, by contrast, is instrument re-tokenization.
6. **It couples product availability to a vendor's uptime** in the one place §A10 says we must not.
7. **Take-rate compounds against the margin story.** VoxVoice's provider portability exists to create
   a visible margin advantage (Blueprint §2); a platform take-rate on every transaction taxes exactly
   that advantage, forever.

**Rejected: it would require superseding ADR-0023, and it trades the platform's commercial model for
a shorter first mile.**

### 5.3.1 The one legitimate part of Option B, preserved

Option B's genuine strength is **merchant-of-record tax absorption** (dimension 14). That value is
retained without the ownership cost: an MoR may be adopted **per market as a channel** (§16), where
it becomes seller of record for that channel's transactions while VoxVoice continues to own the
subscription, the entitlement, and the ledger, recording channel-settled revenue and the MoR's fee as
ordinary ledger facts. The MoR sells; VoxVoice still owns what the customer *has*.

---

# 6. Ownership Boundaries

Restating ADR-0023 in provider terms. **This section is not new authority; it is the ratified
boundary applied to vendors.**

| Fact | Owner | A provider may… |
|---|---|---|
| Commercial catalog, plans, features, meter definitions | **VoxVoice Billing** | never write, never define |
| Pricing, price books, versions, effective dates | **VoxVoice Billing** | never own; may receive an amount to charge |
| Billing accounts and hierarchy | **VoxVoice Billing** | hold a customer reference for its own bookkeeping only |
| Subscriptions, terms, renewal, proration | **VoxVoice Billing** | never own; may hold a payment mandate/schedule as an execution detail |
| Usage records and aggregates | **VoxVoice Billing** | never receive as truth, never re-measure |
| Entitlements and snapshots | **VoxVoice Billing** | never see, never compute, never gate |
| Credits, discounts, coupons | **VoxVoice Billing** | never own |
| Invoices, credit notes, numbering | **VoxVoice Billing** | render/deliver a document VoxVoice authored |
| Revenue ledger, revenue recognition | **VoxVoice Billing** | never write |
| Contracts, licenses, channel grants | **VoxVoice Billing** | never see |
| **Money movement** (authorize, capture, refund, payout) | **Payment provider** | **own** — this is the rented fact |
| **Instrument storage / tokenization** | **Payment provider** | **own** |
| **Tax determination** | **Tax provider** | **own the calculation**; VoxVoice owns the recorded evidence |
| **Seller-of-record status** (MoR channel only) | **MoR provider** | **own for that channel's transactions** |

**The invariant:** a provider may know *how much to move and to whom*. It may never know, decide, or
store *what the customer is on, what they are entitled to, or what they owe over time*.

---

# 7. Provider Responsibilities

A payment provider, under this strategy, is responsible for exactly:

1. Storing payment instruments as tokens and managing their lifecycle.
2. Authorizing, capturing, voiding, and refunding a **specific amount** VoxVoice has already computed.
3. Executing mandates/recurring collection **as instructed** (schedule and amount supplied by
   VoxVoice, never derived by the provider from a plan it owns).
4. Applying network-level retry mechanics and returning typed outcomes.
5. Emitting verifiable notifications about money movement.
6. Executing payouts (marketplace publisher payables, partner settlements).
7. Providing settlement reports for reconciliation.
8. Being the **authority for whether money moved** (ADR-0023 PY1).

A tax provider is responsible for exactly:

1. Determining tax treatment for a described transaction (jurisdiction, rate, rule, reverse-charge
   applicability, exemption handling).
2. Validating registration identifiers (GSTIN, VAT ID, equivalents).
3. Returning a determination VoxVoice records as immutable evidence.

**Providers are never asked, and never permitted, to:** define a plan, hold a price book, own a
subscription's lifecycle, compute proration, meter usage, decide entitlement, author an invoice,
maintain the ledger, or gate any product capability.

---

# 8. Billing Responsibilities

VoxVoice Billing remains responsible for, and sole writer of, everything ADR-0023 §6 lists. Against a
provider specifically, Billing:

1. **Computes every amount.** The provider is told what to charge; it never derives it.
2. **Generates the operation identity** for every provider call, making retries idempotent (PY3).
3. **Owns the payment FSM.** Provider callbacks are notifications that are verified, deduplicated, and
   ordered before state advances (PY4).
4. **Owns dunning policy** — the retry schedule, grace period, communication steps, and terminal
   action. Provider retry mechanics are an input, not the ladder.
5. **Reconciles** provider records against the ledger on a schedule; divergence raises an incident and
   is never auto-resolved (PY5).
6. **Records tax evidence** and never recomputes it at reporting time (TX2/TX4).
7. **Authors and numbers invoices** regardless of which provider (or MoR channel) settled them.
8. **Routes** a transaction to a provider by market, currency, method, and health — and records which
   provider handled it.
9. **Remains authoritative when every provider is down**: subscriptions, entitlements, invoices, and
   the ledger are unaffected by an edge outage.

---

# 9. Adapter Architecture

Four adapter families, all following the §A6 capability-plugin pattern established by provider
plugins (Epic 5) and infrastructure adapters (ADR-0022). **Architectural concepts, not code.**

| Adapter family | Rented responsibility | Capability-flagged variation |
|---|---|---|
| **Payment Adapter** | authorize · capture · void · refund · store/remove instrument · execute mandate · payout · fetch settlement | methods supported, currencies, mandates/autopay, 3-DS, partial capture, payout support, dispute API |
| **Tax Adapter** | determine · validate registration | jurisdictions covered, reverse charge, exemption handling, e-invoicing determination |
| **Document Adapter** | render · deliver · format for jurisdictional e-invoicing | formats, delivery channels, mandate compliance |
| **Export Adapter** | export ledger/revenue projections to an accounting system | target system, cadence, granularity |

**Adapter rules (binding).**

| # | Rule |
|---|---|
| **AD1** | **One contract per family; many implementations.** Adding a provider is one adapter plus one registry entry — never a structural change (§A12/§A6). |
| **AD2** | **Capability flags, never assumptions.** An adapter declares what it supports; Billing routes accordingly and **degrades declaredly** (the ADR-0022 `ProductionAdapterUnavailableError` posture — a declared failure, never a silent no-op). |
| **AD3** | **No vendor type crosses the seam.** Vendor identifiers, objects, and enums stop at the adapter; the core speaks only neutral commercial terms. This is what makes provider replacement a configuration exercise (I3 provider-portability, applied to money). |
| **AD4** | **Adapters are stateless with respect to commercial truth.** An adapter may cache vendor handles; it may never hold a subscription, price, entitlement, or ledger fact. |
| **AD5** | **Every adapter call is idempotent**, keyed by a Billing-generated operation identity, and safe to retry. |
| **AD6** | **Adapters are bounded** (§A11.1): timeouts, bounded queues, retry budgets, and circuit breakers (§A10). No unbounded in-memory work, no unbounded retries. |
| **AD7** | **A deterministic offline adapter is the default in every environment except production**, exactly as the Local infrastructure adapter is for the Actuator — so the entire billing test surface is hermetic and no test opens a socket or moves money. |
| **AD8** | **Secrets use the existing credential seam** (Epic 8 `CredentialProvider` posture); provider keys are rotatable without redeploy, and never appear in code, logs, or VCS. |
| **AD9** | **Notifications are verified before use**: signature, timestamp window, deduplication, ordering. An unverified callback never advances state (PY4). |

---

# 10. Failure Model

The point of Option C is that **provider failure degrades money movement only**. Graceful degradation
per §A10, ordered least to most severe:

| Failure | Blast radius | Behaviour |
|---|---|---|
| **Payment provider slow/erroring** | Money movement only | Retry with backoff/budget; circuit-break; queue the attempt; subscription stays `ACTIVE` in grace. **Nothing about the product changes.** |
| **Payment provider hard outage** | Money movement only | Route to a secondary provider where the market/method allows (§13); otherwise queue and enter the dunning grace window. Invoices are still issued; the ledger still records what is owed. |
| **Callback storm / duplicate / out-of-order callbacks** | Payment state | Verification + dedup + FSM ordering converge to one terminal state (PY4). |
| **Provider and ledger disagree** | Revenue accuracy | Scheduled reconciliation raises an **incident**; never auto-resolved (PY5). Provider is authority for movement; Billing for revenue state. |
| **Tax provider unavailable** | Invoice issuance only | Invoicing **holds** rather than guessing — an invoice with an invented tax figure is worse than a late invoice. Determination is retried; the period stays open until determined. |
| **Document delivery fails** | Delivery only | Invoice state is unchanged (IN6); delivery is retried and observable. |
| **Metering backlog** | Billing timeliness + entitlement freshness | Billing lags; the runtime is unaffected (M5). Enforcement uses the last snapshot within its staleness bound, per each gate's classification. |
| **Billing platform entirely down** | **Billing only** | Conversations run, deployments run, Studio renders, enforcement continues on cached snapshots (fail-open on live paths). No customer-visible product outage. Revenue is delayed, never lost — the event log is intact and metering is replayable. |
| **MoR channel unavailable** | That channel's new sales | Existing subscriptions and entitlements are unaffected; new purchases route to another channel or queue. |

**The invariant this model exists to guarantee:** *no provider, and not Billing itself, can take the
product down.* That is ADR-0023's non-blocking constraint made operational.

---

# 11. Migration Strategy

Migration is designed for **before** the first provider is chosen, because the ability to leave is the
whole argument for Option C.

**What makes migration cheap here:** the irreversible assets — customers, subscriptions, prices,
entitlements, invoice lineage, ledger history, billing anniversaries — never left VoxVoice. Migration
touches only the rented edge.

**Provider replacement sequence (architecture, not procedure):**

1. **Add** the new adapter beside the existing one; both registered, capability-flagged.
2. **Route** new transactions to the new provider by market/currency/method, keeping the old provider
   live for existing mandates.
3. **Re-tokenize** instruments — either by provider-to-provider migration where supported, or by
   customer re-authorization for the remainder. This is the only customer-visible step, and it is
   bounded to payment instruments.
4. **Drain** the old provider: existing mandates run to their natural end or are re-established.
5. **Reconcile** both providers against the one ledger throughout; the ledger never forks.
6. **Retire** the old adapter as configuration.

**Never required during a provider migration:** re-creating subscriptions, re-deriving prices,
re-issuing invoices, changing invoice numbering, migrating entitlements, changing billing
anniversaries, or asking a customer to re-select a plan.

**Migration away from Option C itself** is deliberately not designed for, because it would mean
surrendering ownership — a change that requires superseding ADR-0023, not a migration plan.

---

# 12. Provider Independence

Independence is asserted structurally, and each assertion is verifiable:

| # | Assertion | How it holds |
|---|---|---|
| **PI1** | No vendor concept exists in the billing core | AD3 — vendor types stop at the adapter |
| **PI2** | No commercial fact lives in a vendor | §6 ownership table |
| **PI3** | Every provider is replaceable without customer migration | §11 — only instruments move |
| **PI4** | Two providers can run simultaneously | §13 — routing is first-class, not a failover hack |
| **PI5** | The system is fully testable with no provider | AD7 — deterministic offline adapter is the default |
| **PI6** | Vendor absence degrades money movement only | §10 |
| **PI7** | Adding a market is additive | AD1/AD2 + §A12 |
| **PI8** | Pricing power is retained | prices are VoxVoice's; no vendor take-rate on the commercial model, only acquiring fees on the edge |

---

# 13. Multi-provider Strategy

Multi-provider is designed in from day one — not retrofitted as failover — because retrofitting it is
what forces a re-architecture.

**Routing dimensions** (deterministic, policy-driven, recorded per transaction): market/country,
currency, payment method, customer segment (self-serve vs. enterprise vs. partner channel), provider
health, and explicit contract or channel pinning.

**Planned topology:**

| Scope | Primary | Notes |
|---|---|---|
| India / INR | Domestic PSP (Razorpay-class) | Domestic methods, INR settlement, local mandates, GST-aware documents |
| International / multi-currency | Stripe-class global PSP | Breadth of methods, currencies, payouts |
| Low-touch global self-serve (optional) | MoR channel | Only where tax-registration cost justifies it (§16) |
| Enterprise | Often **offline settlement** (bank transfer against an issued invoice) | No PSP involved; the ledger is unchanged (§14) |

**Rules.** Every transaction records which provider handled it. Reconciliation is **per provider,
against one ledger**. Routing policy is deterministic and auditable — never "whichever responded
first". A provider may be added, deprioritized, or removed as configuration.

---

# 14. Enterprise Strategy

Enterprise is the clearest demonstration of why Option B fails and Option C holds: **most enterprise
revenue never touches a payment provider at all.**

- **Offline settlement is first-class.** An enterprise invoice with net-N terms, a PO reference, and a
  bank transfer is a complete revenue path with **no PSP in it**. Under Option B, a platform that
  owns subscriptions but assumes card collection makes this the awkward exception; under Option C it
  is an ordinary payment record with an offline method.
- **Contracts stay private.** Committed spend, custom price books, true-up/true-down, and negotiated
  terms are VoxVoice facts. No provider sees them, and no provider's data model constrains them.
- **Consolidated billing** across a parent and its child accounts is a VoxVoice-owned hierarchy
  (Blueprint §16), independent of any provider's customer object.
- **Approval-gated invoicing and custom retention** are invoice attributes, not vendor features.
- **Offline licensing** — air-gapped or self-hosted grants — is an entitlement with an issuer, scope,
  and validity window. It has no payment edge at all, which is precisely why it could never be rented
  from a payment platform.

---

# 15. Marketplace Strategy

- **VoxVoice owns the order lifecycle and the entitlement it produces.** The Product Registry owns the
  artifact (Epic 7); Billing owns the commercial terms; the buyer receives an entitlement through the
  same Entitlement Engine — no second permission mechanism.
- **Publisher payables are ledger-native.** A completed order writes both a buyer-side revenue entry
  and a publisher-side payable. **Payouts rent the provider's payout rails** as an edge; the payable
  balance is VoxVoice's.
- **Refunds revoke entitlement** as an explicit recorded transition — never by deleting customer data.
- **Seller-of-record for marketplace sales is the one genuinely open commercial question**
  (Blueprint R-9): VoxVoice as merchant of record versus agent for the publisher changes tax and
  payout obligations materially. **The architecture supports both**; the choice must be made
  explicitly before the M9.15 marketplace milestone and recorded. This strategy does not pre-empt it.

---

# 16. White-label Strategy

White-label is the sharpest constraint on provider choice, and it is why MoR is a channel and never an
owner.

- **A white-label partner's customer must never see a vendor's brand** — not in checkout, not on the
  invoice, not in the customer-of-record relationship. An MoR platform, by definition, *is* the seller
  to that customer; adopting one as the owner would leak the vendor into the partner's product and
  make white-label unachievable.
- Under Option C: **invoices are VoxVoice-authored** (and partner-brandable), **entitlements are
  VoxVoice-owned**, and the payment edge can be presented under partner branding to the extent the
  chosen adapter supports it (a capability flag, AD2).
- Branding, domain, and document presentation are **entitlement flags** — the same mechanism as every
  other capability, requiring no new machinery.

---

# 17. Reseller Strategy

- **The billing-account hierarchy is separate from org tenancy** (Blueprint §17, recommended
  ADR-0030): usage attributes down to the end customer; invoicing rolls up to the partner.
- **Partner-scoped price books** carry wholesale rates and margin; entitlement resolution composes
  partner grant ∩ customer subscription with a declared precedence order.
- **VoxVoice invoices the partner; the partner invoices its customers.** VoxVoice's payment edge
  collects from the partner only — a single relationship, regardless of how many end customers exist.
- **This is unreachable under Option B**: vendor customer models are flat, and a parent/child billing
  hierarchy decoupled from tenant identity is rarely expressible. It is ordinary configuration under
  Option C.

---

# 18. Compliance

| Area | Effect of this strategy |
|---|---|
| **PCI DSS** | Scope **minimized by construction** — instruments are provider tokens; payment data never enters VoxVoice (ADR-0023 PY6). This is the single largest compliance benefit of renting the payment edge. |
| **Tax determination** | Rented (TX1); **evidence owned and immutable** (TX2). Reporting exports recorded evidence and never recomputes (TX4). |
| **Invoicing mandates** | Jurisdictional formats and e-invoicing handled at the Document Adapter over the same immutable invoice fact. |
| **Financial retention** | Ledger, invoices, credit notes, and tax evidence retained per jurisdiction, independent of and longer than customer-data retention (RS4) — **and independent of any provider's retention policy**, which is a decisive argument against holding financial history in a vendor. |
| **Erasure vs. retention** | The designed asymmetry (Blueprint §23) is enforceable because the records are ours. |
| **Data residency** | Adapter selection is region-aware; commercial truth resides under VoxVoice's residency model, not a vendor's. |
| **Audit** | Every billing mutation and every provider interaction is an audited event (§A5/§A11.2) — including which provider handled which transaction. |
| **SOC 2 / ISO** | Inherited from the platform's existing controls; provider due diligence becomes ordinary vendor management rather than a control dependency for the commercial model itself. |

---

# 19. Risks

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| **PS-1** | **Reconciliation drift** — provider and ledger disagree and the gap is discovered late | **High** | Scheduled reconciliation as a first-class activity (PY5); divergence raises an incident, never auto-resolves; per-provider reconciliation against one ledger |
| **PS-2** | **Adapter leakage** — a vendor concept (id, enum, object, webhook shape) seeps into the core, silently re-creating lock-in | **High** | AD3 + boundary review; the same discipline that keeps `pipecat` inside `runtime/pipeline/` and vendor types out of `core` |
| **PS-3** | **Under-estimating the payment edge** — dunning, mandates, 3-DS, disputes, and multi-provider routing are more work than "call the API" | **High** | M9.10 is sized XL/Critical in the Blueprint; deterministic offline adapter (AD7) makes the whole path testable without a vendor |
| **PS-4** | **India + international duality** — two providers, two settlement models, two document regimes from day one | Medium | Multi-provider is designed in (§13), not retrofitted; per-market routing is deterministic and recorded |
| **PS-5** | **MoR channel ambiguity** — an MoR is adopted for convenience and quietly starts owning subscriptions | **High** | §16 rule: MoR is a channel, never an owner; adopting one requires recording it as a channel decision, and the ownership table (§6) is unchanged by it |
| **PS-6** | **Tax adapter gaps** — a market the chosen tax engine covers poorly | Medium | Capability flags + declared degradation (AD2); invoicing holds rather than guessing (§10) |
| **PS-7** | **Build cost of the owned core** — catalog, rating, invoicing, and the ledger are still substantial engineering | **High** | Blueprint milestone sequencing (revenue spine, gates G1–G4); metering and payments start first; nothing in the owned core is rentable without violating ADR-0023, so this cost is structural and accepted |
| **PS-8** | **Provider dependency for payouts** — marketplace payouts depend on the provider's payout rails | Medium | Payables are VoxVoice-owned; only the movement is rented; payout rails are per-provider capability-flagged |
| **PS-9** | **Vendor terms change** (fees, availability, MoR policy) | Medium | Multi-provider by design; switching is an adapter + routing change (§11); commercial terms re-verified at contract time |

---

# 20. Trade-offs

**Accepted costs of this decision, stated plainly:**

1. **More engineering than Option B.** Catalog, rating, invoicing, dunning policy, and the ledger are
   built in-house. This is the price of owning the commercial model, and it is structural — ADR-0023
   makes it non-negotiable.
2. **Reconciliation is permanent operational work.** Two authorities (provider for movement, Billing
   for revenue state) must be reconciled forever. Option B has one authority and therefore no
   reconciliation — and no ownership.
3. **Multi-provider adds routing complexity** from day one rather than "later". Deferring it would be
   cheaper now and a re-architecture later; the Blueprint's whole test is that later capabilities
   must not force redesign.
4. **Tax remains partly external.** VoxVoice depends on a tax provider's correctness for determination,
   while owning the evidence. This is the right split, but it is a dependency, and the §10 posture
   (hold rather than guess) makes it visible as delay rather than as silent error.
5. **Slower first invoice than Option B.** Accepted deliberately: the Blueprint's gate G1 (internal
   billing against real usage, no customer charged) exists precisely so correctness precedes revenue.

**What is explicitly not traded away:** ownership of the commercial model, provider replaceability,
the ability to serve enterprise/marketplace/reseller/white-label without redesign, and the guarantee
that no billing or provider failure can degrade the product.

---

# 21. Architecture Decision

**Decision: Option C is adopted as the permanent Epic 9 provider strategy.**

Ratified statements, binding on every Epic 9 milestone:

1. **VoxVoice Billing owns commercial truth in every market, under every channel, forever** —
   catalog, plans, price books, billing accounts, subscriptions, metering, usage records, rating,
   credits, discounts, entitlements, invoices, credit notes, the ledger, revenue recognition,
   contracts, and licenses.
2. **Providers own payment movement only.** A provider is told an amount and a party; it never derives
   either.
3. **Providers never own subscriptions.**
4. **Providers never own plans.**
5. **Providers never own pricing.**
6. **Providers never own entitlements.**
7. **Providers never own the commercial catalog.**
8. **Providers never own revenue state.**
9. **Billing remains authoritative** — including while every provider is unavailable.
10. **Payment, tax, document, and export capabilities are rented behind adapters** following the §A6
    capability-plugin pattern; no vendor type crosses the seam.
11. **Multi-provider is designed in from day one**, with deterministic, recorded routing by market,
    currency, method, segment, and health.
12. **Merchant-of-record status may be rented per market as a distribution channel, never as an
    owner.** Adopting an MoR changes who sells; it never changes who owns.
13. **A deterministic offline adapter is the default outside production**, so the billing surface is
    fully testable without moving money.
14. **Provider replacement must never require a customer migration** of subscriptions, prices,
    entitlements, invoice lineage, or billing anniversaries.

**Capability assurance — the decision requirement, verified.** Each must hold without future
redesign:

| Requirement | How Option C satisfies it permanently |
|---|---|
| Multiple payment providers | §13 routing + AD1 (one contract, many implementations) |
| Provider replacement | §11 — only instruments move; ledger and subscriptions stay home |
| Enterprise contracts | §14 — contracts are VoxVoice facts; offline settlement is a first-class path with no PSP |
| Hybrid pricing | Rating is owned; recurring + seats + usage + credits coexist on one subscription |
| Usage billing | Metering is a projection of `metric.*` (ADR-0023 M1–M8); never a vendor counter |
| Seat billing | Owned; seat counts read from the Customer Platform |
| Credits | Owned, append-only; no vendor credit model imposed |
| Marketplace | §15 — orders → entitlements → ledger; payout rails rented |
| Reseller | §17 — VoxVoice-owned billing-account hierarchy, partner price books |
| White-label | §16 — VoxVoice-authored documents and entitlements; no vendor brand reaches the partner's customer |

**Open commercial decisions this milestone deliberately does not pre-empt** (each must be recorded
before its dependent milestone): final vendor selection per market (contract-time), marketplace
seller-of-record model (before M9.15, Blueprint R-9), and whether an MoR channel is adopted for
low-touch global self-serve (before the market is opened).

---

# 22. Compliance Verification

1. **Binding on every Epic 9 milestone.** Any design that gives a provider ownership of a subscription,
   plan, price, entitlement, catalog entry, or revenue fact is **non-compliant** and requires
   superseding ADR-0023 — not a local exception.
2. **No vendor type in the billing core.** Verified by the same boundary/AST review that keeps
   `pipecat` confined to `runtime/pipeline/` and vendor types out of `core`: vendor identifiers,
   objects, and enums appear **only** inside an adapter.
3. **Every adapter family has a deterministic offline implementation** and it is the default outside
   production (AD7) — verified the way Epic 8 verifies its offline `EchoTransport` default: the test
   suite opens no socket and moves no money.
4. **Every provider call is idempotent** and safe to retry (AD5); verified by review of each adapter
   contract.
5. **Callbacks are verified before use** (AD9): signature, timestamp window, dedup, ordering.
6. **Reconciliation exists per provider against one ledger**, is scheduled, and raises incidents on
   divergence (PS-1/PY5).
7. **Multi-provider routing is deterministic and recorded** — every transaction records its provider
   and routing reason.
8. **No payment instrument data on platform** (PY6) — verified at the adapter boundary.
9. **Provider secrets use the existing credential seam** and are rotatable without redeploy (AD8).
10. **Frozen Epics remain byte-for-byte unchanged**; no Epic 5/6/7/8 package depends on Billing or on
    any billing provider.

---

# 23. Documentation Updates

Updated in the same change-set:

- **This document** — `docs/architecture/epic9/billing-provider-strategy.md`, linked from
  [INDEX.md](../../INDEX.md).
- **Epic 9 Blueprint** — M9.0C's open question (Blueprint §27 "Build-vs-Buy & Provider Strategy
  Decision", risk R-12) is now **answered**; the Blueprint's adapter seams are unchanged and
  confirmed sufficient.
- **CHANGELOG** — Epic 9 governance entry.
- **Decision log** — rationale recorded as **DL-0109**.
- **ADR encoding** — this decision is the substance of recommended **ADR-0027**; it will be encoded
  verbatim when the ADR-0025–0030 batch is ratified. **ADR-0024 remains reserved for Coach.**

No frozen document is modified. ADR-0023 is **applied, not amended**.

---

# 24. Final Recommendation

**Adopt Option C permanently. Build the commercial model; rent the money movement.**

The decision reduces to one asymmetry, and it is worth stating as the reason to hold this line under
future pressure:

> **What VoxVoice rents, it can replace. What VoxVoice rents *ownership of*, it can only migrate away
> from — at the customer's expense.**

Payment providers, tax engines, document services, and even merchant-of-record status are replaceable
edges: swapping them costs an adapter and a re-tokenization. Subscriptions, prices, entitlements,
invoice lineage, and the ledger are not: putting them in a vendor means that leaving requires moving
live customers, and that every commercial capability VoxVoice wants next — enterprise contracts,
offline licensing, marketplace, reseller, white-label — must first be expressible in someone else's
data model.

Option B buys a shorter first mile and forfeits the road. Option A keeps the road and never reaches
the first mile. Option C is the only one that reaches the first mile *and* keeps the road — and it is
the only one compatible with ADR-0023, which was ratified precisely to make this decision
straightforward.

**Three provisions to defend permanently**, because they are where the pressure will come:

1. **No vendor ever writes a subscription, plan, price, entitlement, catalog entry, or ledger fact** —
   however convenient the vendor's SDK makes it look.
2. **Merchant-of-record is a channel, never an owner** — the distinction is what keeps white-label,
   reseller, and enterprise achievable.
3. **Multi-provider and the offline deterministic adapter exist from day one** — both are cheap now
   and are re-architectures later.

Epics 5, 6, 7, and 8 remain frozen and byte-for-byte unchanged. No ownership boundary is moved; no
code is authorized. Implementation proceeds at **M9.1** under ADR-0023 and this strategy, with the
remaining Epic 9 decisions (ADR-0025–0030) ratified separately.
