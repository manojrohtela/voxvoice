# Pricing Engine (Epic 9 · M9.7)

**Status:** 🟢 Implemented · **Date:** 2026-08-04 · **Owner:** Technical Owner
**Authority:** Level 4 (component spec, §A11.3). Subordinate to the Engineering Constitution,
ADR-0023 (Billing owns revenue and nothing else) and ADR-0031 (billing plane placement).

**Scope:** `platform/billing/foundation/{price_books,pricing_rules,pricing_factories}.py`,
`platform/billing/application/pricing.py`,
`platform/billing/persistence/{pricing_models,pricing_repositories}.py` + migration `0017`,
`platform/billing/customer/{pricing_schemas.py,routers/pricing_engine.py,studio/pricing.html}`.
**Tests:** `platform/tests/test_billing_pricing.py` (144).

---

## 1. What this domain owns

M9.6 drew a boundary and asserted it with a test: the Catalog says *what a customer gets*.
The Pricing Engine is the other side of that line.

**Pricing owns** price books, price-book versions, currencies, regional pricing, effective
dating, price resolution, every supported price shape, price overrides, discounts,
promotions, coupons, pricing versions, and pricing activity.

**Pricing owns no** product, plan definition, subscription, invoice, entitlement, usage
record, payment, or tax. Not one type in this domain holds a `Subscription`, an `Invoice`, a
`UsageRecord`, a `LedgerEntry`, a `Payment`, or a `TaxDetermination` — asserted by AST across
all eight modules (`test_pricing_owns_no_subscription_invoice_usage_or_payment`).

### The sharper boundary: pricing is not rating

A price is *what something costs*. A charge is *what someone owes*. The second requires a
quantity, a period, and a usage figure, and it is **rating** — M9.11, a different engine.

This is enforced, not described. `Money.times()` and `Money.allocate()` are the operations
that would turn a price into a charge; neither appears anywhere in this domain
(`test_pricing_never_rates`). `quantity` and `commitment` *do* appear on a resolution request
— a volume discount needs to know it qualifies — but only as **eligibility gates**. A test
resolves the same price at `quantity=1` and `quantity=1000` and asserts the answer is
identical (`test_quantity_is_an_eligibility_input_and_never_a_multiplier`).

`ResolvedPrice` carries no field whose name contains `quantity`, `total`, `tax`, `period`,
`subtotal`, `charge`, or `invoice` — also asserted.

---

## 2. Architecture

Every Billing layer is reused exactly as M9.2–M9.6 built it. No layer moved, no dependency
changed, no signature broke.

```
HTTP  →  command / query  →  Application  →  Foundation  →  Persistence
```

| Layer | What M9.7 added | What it reused |
|---|---|---|
| Foundation | `price_books.py` (aggregates + FSM), `pricing_rules.py` (validation, dating, discounts, resolution, assembly), `pricing_factories.py` (factory + 7 repository ABCs) | `Money`, `Currency`, `Rounding`, `Price`, `PriceTier`, `EffectiveRange`, `PriceShape`, `PriceBookAudience` (M9.2); `BillingValidator.validate_price` (M9.2); `core.fsm`; the `Clock`/`IdGenerator` seams |
| Application | `pricing.py` — 12 commands, 10 queries, 2 use-case classes, 15 view DTOs | the CQRS buses, `UnitOfWork`, `AuthorizationOrchestrator`, `ApplicationEvent`, `MoneyView` |
| Persistence | `pricing_models.py` (7 tables), `pricing_repositories.py` (7 impls), migration `0017` | `_Repo`, the codec, `from_row`/`update_row`, `SqlBillingUnitOfWork`, `BillingDatabase` |
| API | `pricing_schemas.py`, `routers/pricing_engine.py` (18 paths) | `create_billing_app`, the container, `require_org`, centralized error translation, `MoneyBody`/`MoneyResponse` |
| Studio | `studio/pricing.html` (9 screens) | the `/studio` static mount |

**Additive and optional.** `BillingRepositories.pricing` and `BillingContext.pricing` default
to `None`; without them the Application is exactly what M9.6 assembled. Pricing registers onto
the *same* buses when wired, never a second dispatch path.

---

## 3. The catalog relationship

A price book pins to a **published catalog version** by number
(`PricingBook.catalog_version`). It never holds a product, an edition, or a plan object.

Consequences, all of them intentional:

* **Changing a price never republishes the catalog.** `PublishPricing` writes a
  `PricingVersion`; the catalog version it names is untouched
  (`test_publishing_pricing_pins_a_catalog_version_without_republishing_it`).
* **Changing the catalog never republishes prices.** The two version lines are independent
  stores with independent monotonic counters.
* Together, `(catalog_version, pricing_version)` answer *what was on sale, and what did it
  cost, on the day this subscription started* from two frozen snapshots rather than from live
  tables that have since moved on.

Validation distinguishes `known_catalog_versions=None` ("not checked here") from an **empty
set** ("this organization has published no catalog"), which must reject the pin rather than
skip the check — the distinction an empty-collection falsiness test silently loses. That was
a real defect at M9.6, caught by a test, and the same shape is asserted here.

---

## 4. Immutability

Three levels, each defended structurally rather than by convention:

| Thing | How it is made immutable |
|---|---|
| A published `PriceBookVersion` | No update method on the contract; `UniqueConstraint(org, code, version)`; `ImmutabilityViolation` raised above the constraint |
| A published `PricingVersion` | Same three |
| A published `PricingBook` | `is_editable` is `False`; `guard_editable` raises `BillingConflict`; the FSM has **no** `published → draft` edge |

**Repricing publishes a new revision.** `DuplicatePriceBook` into the same code creates
revision *n+1* as a draft; editing and publishing it produces version *n+1*. Revision *n* is
still there, byte-for-byte, which is what makes the invoice it priced re-derivable
(`test_repricing_creates_a_new_revision_and_keeps_the_old_one`).

**Rollback publishes forward.** `RollbackPricing` writes a *new* version carrying an older
one's prices and recording `rolled_back_from`. The versions in between remain readable; a
rollback that rewound a version number is rejected. With money, "never rewrite history" is not
an academic preference.

---

## 5. Price resolution

`PriceResolver.resolve(request, books, discounts, promotions, coupons) -> ResolvedPrice | None`.

A pure function. It reads no clock (the instant is in the request), touches no persistence, and
returns `None` when nothing prices the request — an unpriced code is a fact the caller handles,
never a zero this layer invents.

**Order of operations**, each step recorded in `resolution_path`:

1. **Select the book.** Filter by currency, requested book code, effective window, whether it
   prices the code at that instant, scope match, and catalog-version compatibility. Rank by
   `(scope.specificity, version, code)` — the most specific scope wins, ties broken by the
   newest version, then alphabetically. Adding an India-only enterprise book therefore never
   silently changes anyone else's price (`test_the_most_specific_scope_wins`).
2. **Select the price.** Among covering versions, the highest `version` wins, then the latest
   start. Never insertion order.
3. **Apply an override.** A request-supplied override (a negotiated contract price) outranks
   anything authored in the book; among book overrides, account-scoped beats book-wide, then
   latest `effective_from`. Pricing *applies* an override; the Contract domain owns the
   negotiation, and `contract_id` is provenance only.
4. **Collect eligible discounts** — book policies, then presented promotions, then a presented
   coupon. Each embeds its own `DiscountPolicy`, so one engine handles all three.
5. **Apply them** in `(priority, code)` order.

### Scope matching

| Dimension | "Any" value |
|---|---|
| `region` | `Region.GLOBAL` |
| `channels` | empty tuple |
| `customer_tiers` | empty tuple |

Explicit "any" values rather than magic sentinels. `specificity` weights region (4) above
channel (2) above tier (1) — the order a commercial team reasons in.

### Price shapes

All ten M9.2 shapes resolve. For shapes carrying a single amount, discounts reduce
`list_amount` → `net_amount`. For tiered and volume shapes the money lives in the bands, so
discounts apply **per band**, producing `net_tiers` alongside the original `tiers`. The
reported reduction is the first band's, because a per-band figure is meaningless without a
quantity — and quantity is rating's.

---

## 6. Effective dating

`EffectiveRange` is half-open `[from, until)`. A price ending at midnight and its successor
starting at midnight must not both apply for one instant, and closed intervals would guarantee
they do (`test_half_open_windows_do_not_double_cover_the_boundary`).

`EffectiveDatingService` answers the four questions a pricing team actually asks —
`is_scheduled`, `is_live`, `is_expired`, `is_valid` — plus `resolve` (the price in force at an
instant) and `collisions`.

A **collision** is two prices with the same code *and the same version* whose windows overlap.
Same code with *different* versions is not a collision: that is exactly how a superseding
price is expressed. Collisions are reported by `validate_book` as
`overlapping_effective_range`.

Historical lookup has two forms and both work: resolving at a past instant against live books,
and resolving against a frozen `pricing_version`. A test reprices after a snapshot and asserts
the snapshot still says the old price.

---

## 7. Discounts

`DiscountPolicy` carries one reduction rule and the conditions under which it is *eligible*.

* **Percentages are integer basis points** (2500 = 25%), applied through `Money.scaled` with a
  declared rounding policy. No float participates anywhere — asserted by an AST check that no
  `*`/`/` expression in the Foundation touches `minor`.
* **`kind` says why; the shape says how much.** `is_percentage` is decided by whether basis
  points were supplied, not by the kind. Tying the shape to the reason would force volume
  discounts (usually percentages) and bundle discounts (usually fixed sums) into the wrong
  form. Validation requires exactly one of the two.
* **Ordering is `(priority, code)`** — never insertion, never size. A pricing team decides
  which discount applies first; the engine does not decide for them.
* **A non-stackable discount ends the chain.** If several are eligible, the highest-priority
  one wins, so "which of my three offers did the customer get?" has one answer.
* **Each reduction comes off the list price**, not the running balance. Two 25% discounts are
  worth 50%, not 43.75% — so the order they are listed in never silently changes the total.
* **The result is floored at zero.** A discount that could invert a price would be a credit,
  and credits are a different domain.

**Promotions** embed their reduction rather than referencing one: a campaign whose discount
could be edited from elsewhere would not be reproducible after the fact. `exclusive` means
this offer *is* the offer.

**Coupons are eligibility only.** `single_use` and `max_redemptions` are *declared* limits.
Nothing here counts a redemption, stores one, or decides whether one already happened —
Pricing answers "is this coupon valid for this price, right now?" and stops.

---

## 8. Lifecycle

One FSM on `core.fsm`:

```
draft ──publish──▶ published ──archive──▶ archived ──restore──▶ published
  │                                           │
  └──delete──▶ deleted ◀──────delete──────────┘
```

Three absences are each a decision:

* **No `published → deleted`.** A book a customer may have been priced from must be archived
  first, and archival keeps it readable forever.
* **No `published → draft`.** "Edit the live price book" is precisely the operation that makes
  an old invoice unexplainable.
* **No edge out of `deleted`**, which is what makes it terminal. Deletion is a recorded state,
  never a row removal — a book code that once priced a sale must not become silently reusable.

`allowed(state)` is returned on every book response, so a UI renders only buttons that cannot
produce a 409.

---

## 9. Persistence

Seven additive `cp_billing_*` tables via migration **0017**, continuing the single platform
chain (0001–0012 Customer Platform, 0013–0016 Billing, 0017 pricing). Same
projection-plus-payload row shape, same codec, same org scoping, same never-commit rule.

| Table | Notes |
|---|---|
| `cp_billing_pricing_book` | unique `(org, code, revision)` |
| `cp_billing_price_book_version` | **append-only**, unique `(org, code, version)` |
| `cp_billing_discount_policy` | unique `(org, code)` |
| `cp_billing_promotion` | unique `(org, code)` |
| `cp_billing_coupon` | unique `(org, code)` |
| `cp_billing_pricing_version` | **append-only**, unique `(org, version)` |
| `cp_billing_pricing_activity` | **append-only** |

**No money in a projection column.** Amounts live only inside the encoded payload, as integer
minor units paired with their currency — a bare numeric column is exactly how a currency gets
separated from its amount. Asserted (`test_no_money_is_stored_in_a_projection_column`).

**Repositories make no pricing decision.** No `DiscountEngine`, no `PriceResolver`, no
`select_book`, no `specificity` anywhere in the storage layer — asserted. `PriceBookVersionRepository.all`
returns *every* published version and lets resolution decide; filtering there would move a
pricing decision into storage, where nobody would look for it. The one temporal predicate a
repository may express is `PromotionRepository.live`, a row selection, and even it re-checks
the window through the aggregate.

The migration `ALTER`s nothing and its `downgrade()` is a deliberate no-op: a schema rollback is
never a licence to make a two-year-old charge unexplainable.

---

## 10. API

18 paths under `/v1/billing/pricing`, tagged `pricing`. **No collision** with M9.5's
`/pricing/price-books`, which is reused unchanged rather than duplicated.

| Group | Paths |
|---|---|
| Books | `POST/GET /books`, `GET /books/{code}`, `POST /books/{code}/{publish,archive,restore,duplicate}`, `GET /books/{code}/versions` |
| Prices | `POST /books/{code}/prices`, `PATCH /books/{code}/prices/{price_code}` |
| Discounts | `POST/GET /discounts`, `POST/GET /promotions`, `POST/GET /coupons` |
| Resolution | `POST /resolve` |
| Versions | `POST/GET /versions`, `POST /versions/rollback`, `GET /versions/latest`, `GET /versions/{version}` |
| History | `GET /activity` |

Controllers are transport-only — asserted by AST: every handler dispatches, and none contains
`repositories`, `session`, `.commit()`, `uow.`, `validate`, or `reduction`. `_book_response`
re-reads through `GetPricingBook` after a command so `allowed_triggers` is computed by the
Foundation rather than by importing the state machine into a controller.

`POST /resolve` is a read expressed as a POST because the request is structured. It has no
side effect.

Money crosses the wire as `{"minor": int, "currency": str}`, never a float and never a
formatted string. Percentages cross as integer basis points.

**No mutation verbs on any version resource** — asserted against the OpenAPI document, not only
stated in prose.

---

## 11. Studio

`/studio/pricing.html` — one file, no build step, no CDN, no external font. Nine screens:
Price Books · Price Editor · Currency Matrix · Regional Pricing · Discount Builder ·
Promotion Builder · Coupon Manager · Version History · Pricing Resolver.

ADR-0021 taken literally: it owns no state (no `localStorage`), holds no rule, and computes no
price. The Resolver screen posts a request and displays the server's answer **including the
resolution path** — a disputed price should be explainable without a debugger. Lifecycle
buttons are rendered from the server's `allowed_triggers`.

---

## 12. Testing (144)

Lifecycle FSM (legal/illegal edges, terminality, the two absent edges, non-mutation) ·
effective dating (half-open boundaries, overlap, scheduled/live/expired, historical lookup,
collisions) · validation (currency, duplicates, negative money, tier ordering, discount shape,
promotion/coupon conflicts, dangling references, completeness, the empty-set distinction) ·
discounts (basis-point arithmetic, ordering, list-price basis, non-stackable chain break,
zero floor, eligibility gates) · resolution (determinism, currency, scope specificity,
instant, overrides, coupons, exclusive promotions, tiered bands, resolution path, the
quantity boundary) · snapshots and rollback · the Application over a real database ·
cross-org isolation, authorization on every one of the 22 messages, durability across a
restart · migrations · HTTP · Studio · architecture compliance.

Repository-wide: **2333 passed**, zero regressions.

---

## 13. Known limitations

1. **`PublishPriceBook` (M9.3) and `PublishPricingBook` (M9.7) coexist**, as do `GetPriceBook`
   and `GetPricingBook`. The M9.3 pair writes and reads the M9.2 `PriceBook` aggregate; the
   M9.7 pair drives the Pricing Engine. Two clear names beat one name that quietly changed
   meaning. Unifying them belongs to the milestone that first needs a single path — likely
   M9.8 (Subscriptions), which will consume resolved prices.
2. **`CreateDiscountPolicy` is a 12th command**, beyond the eleven the brief listed. Without
   it the "Discount Policies" responsibility would have no way to create a standalone volume,
   commitment, or bundle discount — `CreatePromotion` and `CreateCoupon` cover only their own
   forms. Likewise `GetPricingVersion`, `PricingVersionHistory`, `ListDiscountPolicies`,
   `ListPromotions`, and `ListCoupons` are additive queries the Studio screens require.
3. **Price overrides have no dedicated command.** They are authored on a book and supplied on a
   resolution request; the brief listed overrides as a responsibility but no command. When the
   Contract domain (M9.20) owns negotiated pricing, the command belongs there.
4. **Search is in-memory** — `PricingSearchService` filters the org's books after loading them.
   Deterministic and correct; it will need an indexed path when an organization holds
   thousands of books.
5. **The Studio has no authentication flow.** A bearer token is pasted into a header field.
   Real sign-in is a Control-Plane concern (M9.18).

---

## 14. Compliance

| Obligation | Status |
|---|---|
| ADR-0023 RS5 — money is integer minor units + currency, never float | ✅ AST-asserted, including basis points |
| ADR-0023 — published prices immutable (RS1/IN2 shape) | ✅ contract + constraint + typed error |
| ADR-0023 §5.3 — Billing owns no RBAC; deny-by-default | ✅ every message authorized through the seam |
| ADR-0031 IR1/IR3 — Foundation imports only `core` + `billing` + stdlib | ✅ asserted |
| §A2.4 — deterministic behaviour | ✅ resolution is a pure function; assembly is byte-identical |
| §A2.6 — accepted or rejected, never degraded | ✅ validation fails fast before a version exists |
| §A5 — history append-only | ✅ three append-only stores, no mutation path |
| §A11.2 — org-scoped by construction | ✅ every read filters on `org_id` |
| §A12 — additive extension | ✅ no signature broken; every earlier export still present |
| ADR-0031 CR2 — Billing absent from the runtime composition root | ✅ asserted |
| Epics 5–8 frozen | ✅ untouched |
