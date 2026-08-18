# Subscription Engine (Epic 9 · M9.9)

**Status:** 🟢 Implemented · **Date:** 2026-08-04 · **Owner:** Technical Owner
**Authority:** Level 4 (component spec, §A11.3). Subordinate to the Engineering Constitution,
ADR-0023 (Billing owns revenue and nothing else) and ADR-0031 (billing plane placement).

**Scope:** `platform/billing/foundation/{commercial_subscriptions,subscription_rules,subscription_factories}.py`,
`platform/billing/application/subscriptions_engine.py`,
`platform/billing/persistence/{subscription_models,subscription_repositories}.py` + migration `0019`,
`platform/billing/customer/{subscription_schemas.py,routers/subscription_engine.py,studio/subscriptions.html}`.
**Tests:** `platform/tests/test_billing_subscriptions_engine.py` (165).

> **Milestone numbering.** `docs/roadmap/epic9-implementation-plan.md` labels M9.9 the
> *Entitlement Engine* and puts subscriptions at M9.8. The delivered sequence has run
> …M9.7 Pricing → M9.8 Billing Accounts → M9.9 Subscription Engine. The dependency order the
> plan protects is unchanged — accounts, catalog, and pricing all precede subscriptions — only
> the labels differ. Recorded here rather than silently reconciled.

---

## 1. What this domain owns

Three milestones built the vocabulary this one binds together. M9.6 said **what is sold**, M9.7
said **what it costs**, M9.8 said **who we sell to**. A subscription is the fact that joins them:
*this account, on this catalog version, at this pricing version, from this date.*

**Subscriptions own** the lifecycle, items, account/catalog/pricing references, billing interval
and cycle, renewal / trial / pause / cancellation / upgrade / downgrade policies, scheduled
changes, versions, history, and activity.

**They own no** catalog, pricing, rating, metering, usage, ledger, credit, invoice, payment,
refund, tax, revenue recognition, runtime, deployment, knowledge, or authorization.

### The sharper boundary: an agreement carries no money

A subscription records **what was agreed**. Turning that into an amount is rating (M9.11).

Enforced, not described: `Money`, `minor`, `PriceTier`, `.times(`, `.allocate(`, and `.scaled(`
appear **nowhere** across all eight modules — asserted by AST
(`test_subscriptions_own_no_money_and_compute_no_charge`) — and an HTTP test asserts no amount
reaches the wire either. The Studio has no money formatter at all.

---

## 2. Architecture

Every Billing layer is reused exactly as M9.2–M9.8 built it. No layer moved, no dependency
changed, no signature broke.

```
HTTP  →  command / query  →  Application  →  Foundation  →  Persistence
```

| Layer | What M9.9 added | What it reused |
|---|---|---|
| Foundation | `commercial_subscriptions.py` (aggregates + FSM), `subscription_rules.py` (validation, cycles, trials, scheduling, versioning, search), `subscription_factories.py` (factory + 3 repository ABCs + a number allocator) | `BillingInterval`, `BillingPeriod`, `ChangeTiming`, `ProrationBehaviour` (M9.2); **`PeriodService`** for period arithmetic; `core.fsm`; the `Clock`/`IdGenerator` seams |
| Application | `subscriptions_engine.py` — 23 commands, 6 queries, 2 use-case classes, 16 view DTOs | the CQRS buses, `UnitOfWork`, `AuthorizationOrchestrator`, `ApplicationEvent` |
| Persistence | `subscription_models.py` (4 tables), `subscription_repositories.py` (4 impls), migration `0019` | `_Repo`, the codec, `from_row`/`update_row`, `SqlBillingUnitOfWork` |
| API | `subscription_schemas.py`, `routers/subscription_engine.py` (25 paths) | `create_billing_app`, the container, `require_org`, centralized error translation |
| Studio | `studio/subscriptions.html` (10 screens) | the `/studio` static mount |

**Additive and optional.** `BillingRepositories.subscriptions_engine` and
`BillingContext.subscriptions_engine` default to `None`; without them the Application is exactly
what M9.8 assembled.

**M9.2's `Subscription` is untouched.** It remains the aggregate the revenue spine rates
against; `CommercialSubscription` is the full commercial agreement built around the same idea,
in its own module — the call M9.6 made for `Product` beside `Plan`, M9.7 for `PricingBook`, and
M9.8 for `CommercialAccount`.

**Period arithmetic is not duplicated.** An early draft of this milestone defined its own
`INTERVAL_SECONDS`; M9.2's `services.py` already owned that table, and two answers to "how long
is a month" is precisely the drift §A2.5 exists to prevent. `BillingCycleService` delegates to
`PeriodService`.

---

## 3. Domain model

| Aggregate / value | Purpose |
|---|---|
| `CommercialSubscription` | The agreement. Three immutable references, items, cycle, five policies, scheduled changes, version |
| `AgreementItem` | One subscribed component, pinned to `price_code@price_version`. Six kinds: flat, seat, usage, tiered, volume, hybrid |
| `BillingCycle` | Interval, anchor day (1–28), cycle number, current period |
| `TrialPolicy` · `RenewalPolicy` · `PausePolicy` · `CancellationPolicy` · `UpgradePolicy` · `DowngradePolicy` | Declared terms — never mechanisms |
| `ScheduledSubscriptionChange` | An accepted intention, with a status. **Nothing fires it** |
| `SubscriptionVersion` | An immutable snapshot embedding the whole agreement |
| `SubscriptionSnapshot` | The derived "what existed at instant *T*" read model |
| `SubscriptionHistory` | Activity + versions: *what happened* beside *what the terms were* |
| `SubscriptionActivity` | Append-only timeline entry |

**Hybrid commerce is one agreement, not several.** A platform fee plus seats plus usage is three
items on one subscription; `is_hybrid` is derived from the distinct item kinds present.

**`ItemKind` is deliberately not `PriceShape`.** A price shape says how a price is *structured*;
an item kind says what the customer *subscribed to*. They usually agree, and where they do not
it is because a hybrid line resolves per component.

---

## 4. The three references

`account_id` · `catalog_version` · `pricing_version` — all fixed at creation, all naming frozen
things, all validated against what is actually published.

| Property | How it is guaranteed |
|---|---|
| Pins are integers into snapshots, never live objects | `test_subscriptions_reference_snapshots_by_version_number_only` inspects the field types |
| A dangling pin is rejected | validated against the real catalog/pricing version stores |
| An **empty** known-version set rejects rather than skips | the M9.6 falsiness defect, re-asserted for *both* pins |
| Publishing a new catalog never mutates an existing subscription | asserted end-to-end |
| Publishing new pricing never mutates an existing subscription | asserted end-to-end |
| An unknown or inactive account is rejected | the account's state is passed **as a string**, so this domain never imports the account enum |

Together `(catalog_version, pricing_version)` make an agreement re-priceable years later —
which is what M9.6's and M9.7's immutability was *for*.

At the database level the pins are indexed integers and deliberately **not foreign keys**: a
foreign key would make the two version lines a coupling the database enforces, when the whole
point is that they version independently.

---

## 5. Lifecycle

```
draft ──activate──▶ active ⇄ past_due ──suspend──▶ suspended
  │                  │  │                              │
  │            pause │  └──────────┐                   │ reactivate
  │                  ▼             │                   ▼
  │               paused ──resume──┴────────────────▶ active
  │
  └─start_trial─▶ trialing ──convert_trial──▶ active
                     │
                     └──expire_trial──▶ expired ──archive──▶ archived
       (any of draft/trialing/active/past_due/suspended/paused) ──cancel──▶ cancelled ──expire──▶ expired
```

Five absences, each a decision:

* **There is no deletion state at all** — stronger than the catalog, pricing, and account
  lifecycles, and deliberately so. A subscription *is* the commercial agreement; ADR-0023 RS4
  makes its record permanent, and `archived` is as far out of the working set as one can go.
  `test_a_subscription_can_never_be_deleted` asserts the enums themselves.
* **No `cancelled → active`.** Winning a customer back is a *new* agreement; conflating the two
  makes churn unmeasurable.
* **No path between `paused` and `suspended`.** Pause is the customer's choice, suspension is
  ours, and an edge between them would blur which actually happened.
* **No edge out of `archived`**, which makes it terminal.
* **Renewal is absent entirely** — it advances the cycle of an active subscription rather than
  changing its state. A test asserts the FSM contains **no self-loops at all**, so the shortcut
  cannot creep back in.

`allowed_triggers` is on every response, so a UI renders only buttons that cannot 409.

**Two kinds of refusal, both Foundation decisions.** `guard_editable` refuses changes to a
closed agreement (its terms are a record). `guard_pausable` refuses a pause the customer has no
allowance left for — structurally legal, commercially spent.

---

## 6. Versioning

Publishing freezes a `SubscriptionVersion` that **embeds the whole agreement**, so reading
version 3 never depends on what version 7 did. Immutability is defended three deep: no update
method on the contract, `UniqueConstraint(org, subscription, version)`, and an
`ImmutabilityViolation` above it.

**Rollback publishes forward** — a new version carrying earlier terms, recording
`rolled_back_from`. The versions in between remain readable; rewinding a version number is
rejected. With a commercial agreement that is not an academic preference: it is the difference
between a contract and a wiki page.

Freezing is deterministic (items and changes sorted on the way in), so two freezes of unchanged
terms are byte-identical and "did anything change between v3 and v4?" is a diff.

`SubscriptionHistory.version_at(t)` answers *which terms were in force* at an instant — the
question a re-priced invoice actually asks.

---

## 7. Scheduled changes — intent without a scheduler

Five kinds: upgrade, downgrade, cancellation, renewal, pricing switch.

**Nothing in this plane fires them.** There is no timer, no worker, and no auto-apply.
`ScheduleService.due(at)` answers a question; a caller then dispatches the ordinary command, and
the FSM validates it *at that moment* exactly as if a human had asked. That separation is what
keeps "what we agreed" auditable independently of "what the system managed to run" — and a test
reads due changes at a far-future instant and asserts the agreement is untouched.

Conflicts are Foundation decisions with readable reasons: at most one pending cancellation; no
change scheduled at or after a cancellation (the subscription will not exist to receive it); at
most `MAX_PENDING_CHANGES = 10` pending, beyond which a customer cannot know what they agreed
to. An overdue pending change **warns** rather than erroring — nothing here applies it, so it
is information.

---

## 8. Trials, pause, cancellation, renewal

**Trial** — one deterministic policy: a length, a computed end, and a conversion.
`requires_payment_method` is a *declared* requirement a consuming surface enforces; this domain
talks to no payment provider. `conversion_blocked_reason` returns a sentence rather than
raising, so a UI can show it before the customer clicks.

**Pause** — `entitlements_retained` defaults to `True` and is a **statement, not a mechanism**:
pausing does not by itself alter entitlements. The Entitlement domain decides; this records what
was agreed so that decision has an input rather than an assumption.

**Cancellation** — immediate, period-end (the default: a customer who paid for a period keeps
it), or scheduled. Reason and reason code are recorded. `refund_eligible` is a declared flag the
Refund domain reads; no refund is computed here.

**Renewal** — auto, manual, or none, with an optional fixed term count. Renewing anchors the new
period to the **previous period's end**, not to now, so a late renewal does not shift the
customer's billing date. Renewing beyond the agreed terms raises: a commercial decision, not a
computation.

---

## 9. Persistence

Four additive `cp_billing_*` tables via migration **0019**, continuing the single platform chain
(0001–0012 Customer Platform, 0013–0019 Billing).

| Table | Notes |
|---|---|
| `cp_billing_commercial_subscription` | unique `(org, subscription_number)`; pins indexed as integers |
| `cp_billing_subscription_version` | **append-only**, unique `(org, subscription, version)` |
| `cp_billing_subscription_activity` | **append-only** |
| `cp_billing_subscription_number` | one monotonic sequence per organization |

M9.2's `cp_billing_subscription` is untouched — asserted. Round-trip fidelity
(`decode(type(a), encode(a)) == a`) is asserted for the full aggregate including nested items,
policies, and scheduled changes.

**Repositories decide nothing.** No lifecycle service, cycle service, trial service, or schedule
service anywhere in the storage layer — asserted.

---

## 10. API

25 paths under `/v1/billing/agreements`, tagged `subscriptions`.

**The prefix is deliberate.** M9.5's `/subscriptions/{subscription_id}` is an existing wildcard;
any sibling under it would be shadowed depending on router registration order. A separate prefix
makes the two surfaces independent of each other's ordering — the same correctness argument
M9.8 made for `/billing-accounts`. The M9.5 routes are untouched and asserted still present.

| Group | Paths |
|---|---|
| Agreement | `POST/GET /agreements`, `GET /agreements/{identifier}` |
| Lifecycle | `POST /{id}/{activate,recover,suspend,reactivate,pause,resume,cancel,expire,archive,past-due}` |
| Trial | `POST /{id}/trial/{start,convert,expire}` |
| Items | `POST /{id}/items`, `PATCH`/`DELETE /{id}/items/{item_id}` |
| Scheduling | `POST`/`GET /{id}/scheduled-changes`, `DELETE /{id}/scheduled-changes/{change_id}` |
| Renewal | `POST /{id}/renew`, `PATCH /{id}/renewal` |
| Versions | `POST`/`GET /{id}/versions`, `POST /{id}/versions/rollback` |
| Reads | `GET /{id}/timeline`, `GET /{id}/snapshot` |

Agreements resolve by **id or subscription number** on every path.

**There is no DELETE on a subscription, in any state.** The only deletions on this surface are a
subscription *item* (while the agreement is editable) and a *pending intention* — both asserted
in the plane-wide DELETE scan, which M9.9 sharpened further (§12).

---

## 11. Studio

`/studio/subscriptions.html` — one file, no build step, no CDN. Ten screens: Dashboard · Details
· Items · Timeline · Version History · Schedule Changes · Trial · Renewal · Pause/Cancel ·
Activity.

ADR-0021 taken literally: no `localStorage`, no rule, and **no money formatter at all** —
asserted, because the absence of an amount is the boundary this domain rests on. Lifecycle
buttons are generated from the server's `allowed_triggers` through a trigger→endpoint table.

---

## 12. Testing (165)

Lifecycle FSM (legal/illegal edges, the five absences, no-self-loops, cancellation timing,
non-mutation, both guards) · billing cycle (idempotent opening, end-anchored renewal, term
exhaustion, due-is-a-read) · trials · scheduled changes (intent never applied, ordering,
conflicts, bounds, overdue warns) · validation · versions and snapshots · search · the
Application over a real database including **cross-domain pins built from real catalog and
pricing versions** · cross-org isolation, authorization on all 29 messages, restart durability ·
migrations and codec round-trip · HTTP · Studio · architecture compliance.

Repository-wide: **2628 passed**, zero regressions.

### Defects found and fixed

1. **Items supplied at creation had no id.** The router passed `id=""`, expecting the factory to
   mint one; `create_subscription` stored them verbatim, leaving every such line un-addressable
   by `/items/{item_id}` (a 405, found by an HTTP test). Fixed in the factory —
   `_with_ids` — because construction is the factory's job, not a controller's.
2. **A pricing-only upgrade/downgrade was rejected.** Validation required items, a plan, or a
   catalog version to say "what changes"; moving to a new *pricing* version says it too.
3. **Duplicated period arithmetic** (§2), removed in favour of M9.2's `PeriodService`.

---

## 13. Known limitations

1. **`BillingInterval` has only monthly and yearly.** Weekly, quarterly, and daily terms need a
   new member on a frozen M9.2 enum that pricing, invoicing, and proration all switch on —
   a change this milestone has no mandate to make. A test asserts the cycle table matches the
   enum exactly rather than quietly widening it. *(A related M9.7 Studio defect — a five-option
   interval dropdown that would have produced a 422 — was fixed here.)*
2. **M9.3's subscription surface coexists.** `StartSubscription`, `TransitionSubscription`,
   `ChangeSubscriptionPlan`, `GetSubscription`, and `ListSubscriptions` drive the M9.2
   aggregate; everything here is named distinctly. Unifying the two belongs to the milestone
   that first needs a single path — most likely M9.11 (Rating), which must rate exactly one.
3. **Applying a due scheduled change is the caller's command**, by design. No orchestration
   exists that walks due changes and dispatches them; that belongs to a control-plane job, not
   to this domain.
4. **Proration is declared, not computed.** `UpgradePolicy`/`DowngradePolicy` carry M9.2's
   `ProrationBehaviour`; the arithmetic is M9.2's `ProrationService` and belongs to rating.
5. **Search is in-memory** — deterministic and correct; it will need an indexed path at scale.
6. **The Studio has no authentication flow** (M9.18).

---

## 14. Compliance

| Obligation | Status |
|---|---|
| Subscription owns only the commercial agreement | ✅ AST-asserted, 8 modules |
| No pricing calculation, rating, metering, invoice, ledger, payment, or tax | ✅ AST-asserted |
| No runtime, deployment, or knowledge dependency | ✅ import-graph assertion (sharpened from a substring scan) |
| Catalog and Pricing remain independently versioned | ✅ asserted end-to-end, both directions |
| Subscriptions reference immutable snapshots only | ✅ field-type assertion + dangling-pin rejection |
| ADR-0023 RS4 — the record is permanent | ✅ no deletion state exists at all |
| ADR-0023 §5.3 — Billing owns no RBAC; deny-by-default | ✅ all 29 messages authorized |
| ADR-0031 IR1/IR3 — Foundation imports only `core` + `billing` + stdlib | ✅ asserted |
| §A2.4 — deterministic; the Foundation reads no clock | ✅ `at` is always an argument, asserted |
| §A2.6 — accepted or rejected, never degraded | ✅ fail-fast before any emission |
| §A5 — exactly one event per business fact; none for a rejected command | ✅ asserted both ways |
| §A11.2 — org-scoped by construction | ✅ every read filters on `org_id` |
| §A12 — additive extension | ✅ no signature broken; every earlier export still present |
| ADR-0031 CR2 — Billing absent from the runtime composition root | ✅ asserted |
| Epics 5–8 frozen; M9.2–M9.8 unchanged | ✅ earlier boundaries re-asserted here |
