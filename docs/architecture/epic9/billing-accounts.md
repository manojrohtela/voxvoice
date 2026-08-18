# Billing Accounts (Epic 9 · M9.8)

**Status:** 🟢 Implemented · **Date:** 2026-08-04 · **Owner:** Technical Owner
**Authority:** Level 4 (component spec, §A11.3). Subordinate to the Engineering Constitution,
ADR-0023 (Billing owns revenue and nothing else) and ADR-0031 (billing plane placement).

**Scope:** `platform/billing/foundation/{commercial_accounts,account_rules,account_factories}.py`,
`platform/billing/application/billing_accounts.py`,
`platform/billing/persistence/{account_models,account_repositories}.py` + migration `0018`,
`platform/billing/customer/{account_schemas.py,routers/billing_accounts.py,studio/accounts.html}`.
**Tests:** `platform/tests/test_billing_accounts.py` (130).

---

## 1. What this domain owns

M9.6 said what is sold. M9.7 said what it costs. This domain says **who we sell to**.

**Billing Accounts own** commercial identity, billing hierarchy, payer relationships, contacts,
addresses, legal entity, billing and invoice preferences, a tax-profile *reference*, credit
profile and limit, commercial status, notes, activity, and history.

**They own no** subscription, price, catalog entry, usage record, payment, runtime object, or
deployment. Not one type here holds a `Subscription`, an `Invoice`, a `UsageRecord`, a
`LedgerEntry`, a `Payment`, a `PricingBook`, or a `CatalogVersion` — asserted by AST across all
eight modules.

### The sharper boundary: an account holds no balance

A credit limit is **what we are willing to be owed**. What a customer *currently* owes is a
Ledger fact, and this domain does not know it. `CreditProfile.permits(exposure)` therefore takes
the exposure as an *argument* and compares two `Money` values in the same currency; a currency
mismatch is a definite "no" rather than a silent conversion.

Enforced, not described: `Money.times`, `Money.allocate`, and `Money.scaled` — the operations
that would compute a balance or a charge — appear nowhere in the domain
(`test_accounts_compute_no_balance_and_no_charge`).

---

## 2. Architecture

Every Billing layer is reused exactly as M9.2–M9.7 built it. No layer moved, no dependency
changed, no signature broke.

```
HTTP  →  command / query  →  Application  →  Foundation  →  Persistence
```

| Layer | What M9.8 added | What it reused |
|---|---|---|
| Foundation | `commercial_accounts.py` (aggregates + FSM), `account_rules.py` (validation, hierarchy, payers, lifecycle, search, profile), `account_factories.py` (factory + 2 repository ABCs + a number allocator seam) | `BillingContact`, `BillingAddress`, `AccountKind` (M9.2); `Money`/`Currency`; `core.fsm`; the `Clock`/`IdGenerator` seams |
| Application | `billing_accounts.py` — 14 commands, 5 queries, 2 use-case classes, 13 view DTOs | the CQRS buses, `UnitOfWork`, `AuthorizationOrchestrator`, `ApplicationEvent`, `MoneyView` |
| Persistence | `account_models.py` (3 tables), `account_repositories.py` (3 impls), migration `0018` | `_Repo`, the codec, `from_row`/`update_row`, `SqlBillingUnitOfWork` |
| API | `account_schemas.py`, `routers/billing_accounts.py` (17 paths) | `create_billing_app`, the container, `require_org`, centralized error translation, `MoneyBody`/`MoneyResponse` |
| Studio | `studio/accounts.html` (9 screens) | the `/studio` static mount |

**Additive and optional.** `BillingRepositories.commercial_accounts` and
`BillingContext.commercial_accounts` default to `None`; without them the Application is exactly
what M9.7 assembled.

**M9.2's `BillingAccount` is untouched.** It remains the minimal account the revenue spine
attributes to; `CommercialAccount` is the full commercial record built around the same idea, in
its own module — the call M9.6 made for `Product` beside `Plan` and M9.7 for `PricingBook`
beside `PriceBook`.

---

## 3. Commercial identity

`account_number` is the human commercial identifier — it appears on invoices, in support
conversations, and in a customer's own accounts-payable system. It is therefore:

* **allocated from a stored per-organization sequence**, not `max(number) + 1`. Deleting a draft
  account must not hand its number back, and a derived maximum would do exactly that
  (`test_account_numbers_are_allocated_and_never_reused`);
* **unique per organization** at the database level;
* **format-checked** — letters, digits, `-`, `_` only. It has to be typeable and readable aloud.

`LegalEntity` is separate from `display_name` because they routinely differ: a customer known as
"Acme" contracts as "Acme Technologies Private Limited", and an invoice, a contract, and a tax
filing all need the second. Registration numbers and tax identifiers are unique across an
organization's accounts — the same registered company twice is usually a duplicate customer, not
a second one.

`tax_profile_id` is a reference. This domain holds the pointer and computes no tax.

---

## 4. Hierarchy — and the payer graph beside it

**Two graphs, resolved independently.**

`AccountHierarchyResolver` walks `parent_account_id`: ancestors, descendants, depth, subtree,
and cycles. `PayerResolver` walks `payer_account_id`: chain, ultimate payer, and loops.

Modelling them separately is the load-bearing decision of this milestone. "Who consolidates this
account's invoices" and "who actually pays" are different questions in every reseller and
enterprise arrangement, and collapsing them breaks the first time a customer pays for a
subsidiary it does not own. A test constructs exactly that case — a parent whose payer is its own
child — and asserts **neither graph is malformed**
(`test_the_payer_graph_is_not_the_hierarchy`).

### Separate from Epic 8 tenancy

The billing hierarchy is **not** the organization, workspace, or project hierarchy (ADR-0023
§5.2, Blueprint §17). `org_id` is a string this domain scopes on and never dereferences. A test
asserts that no module here imports `api`, or mentions `Organization`, `Workspace`, `Project`,
`workspace_id`, or `project_id` — so a reseller hierarchy provably requires no change to Epic 8
tenancy.

### Guarantees

| Property | How |
|---|---|
| No cycles | `find_cycle` returns the **path** (`a → b → c → a`), not a boolean — it names the edge to cut |
| No payer loops | same shape, on the payer graph |
| Bounded depth | `MAX_HIERARCHY_DEPTH = 6`; distributor → reseller → sub-reseller → customer is four |
| Traversal always terminates | `tree()` carries a visited-set guard: validation rejects cycles, but a renderer must not be what discovers one the hard way |
| Single currency per hierarchy | a parent in another currency is rejected — a consolidated invoice cannot mix currencies |
| Same organization | a cross-org parent or payer is rejected |
| Live payer | only an `active` account can be made to pay |

Attribution flows **down** (a child accrues its own usage); invoicing rolls **up**
(`BillingTree.invoiced_account_id`). Both directions are explicit so neither is inferred later.

---

## 5. Lifecycle

```
draft ──activate──▶ active ──suspend──▶ suspended ──resume──▶ active
  │                   │                      │
  │                   └────close──────▶ closed ◀───close──────┘
  │                                        │
  │                                   archive ⇄ restore
  └──delete──▶ deleted                  archived
```

Four absences, each a decision:

* **Exactly one edge reaches `DELETED`, and it starts at `DRAFT`.** A draft has billed nobody;
  everything past it has revenue attached, and ADR-0023 RS4 says revenue outlives the customer
  record. There is no way to delete an account that was ever active — not even after closing and
  archiving it. `test_only_a_draft_may_ever_be_deleted` enumerates the whole FSM to prove it.
* **No `closed → active`.** Closing is terminal for commercial activity; resuming a closed
  relationship is a new account, not a state change on an old one.
* **`restore` returns to `closed`, not `active`.** Un-filing a record and reopening a commercial
  relationship are separate decisions, and conflating them lets the second happen by accident.
* **`suspended → closed` exists directly.** A customer who stops paying should not have to be
  reactivated before being let go.

`activate` is one command over two triggers: a draft is *activated*, a suspended account is
*resumed*. `AccountLifecycleService.activation_trigger` picks — callers should not need to know
which applies, but the history must record which happened, so the FSM keeps both edges.

**Retention makes the record read-only.** `guard_mutable` rejects preference, contact, address,
credit, parent, and payer changes on a closed or archived account: its commercial details are
evidence of what was agreed. A **note** is the deliberate exception — a note is a record *about*
the relationship, not a change to it, so a final statement can still be logged against a closed
account.

Commercial status (`good_standing`, `watch`, `credit_hold`, `delinquent`, `write_off`) is
**independent of lifecycle state** — an account can be active and delinquent, and that
combination is the point. Only incoherent pairings are rejected: a draft that has never traded
cannot be delinquent.

---

## 6. Persistence

Three additive `cp_billing_*` tables via migration **0018**, continuing the single platform
chain (0001–0012 Customer Platform, 0013–0018 Billing).

| Table | Notes |
|---|---|
| `cp_billing_commercial_account` | unique `(org, account_number)`; `parent_account_id` and `payer_account_id` indexed **separately**, because they are separate graphs |
| `cp_billing_account_activity` | **append-only** |
| `cp_billing_account_number` | one monotonic sequence per organization; never rewound |

M9.2's `cp_billing_account` is untouched and still present — asserted.

**Repositories make no commercial decision.** No `AccountHierarchyResolver`, no `PayerResolver`,
no `ancestors`, `descendants`, `find_cycle`, or `ultimate_payer` anywhere in the storage layer.
`children_of` is one indexed row selection; walking the tree is the Foundation's.

The migration `ALTER`s nothing and its `downgrade()` is a deliberate no-op: a schema rollback is
never a licence to drop the commercial record a retained invoice was raised against.

---

## 7. API

17 paths under `/v1/billing/billing-accounts`, tagged `accounts`.

**The prefix is deliberate, not cosmetic.** M9.5's `/accounts` already owns
`GET /accounts/{account_id}`; any sibling path under it would be shadowed by that wildcard
depending on router registration order. A separate prefix makes the two surfaces independent of
each other's ordering — a correctness property. The M9.5 routes are untouched and asserted still
present.

| Group | Paths |
|---|---|
| Account | `POST/GET /billing-accounts`, `GET/DELETE /billing-accounts/{identifier}` |
| Lifecycle | `POST /{id}/{activate,suspend,close,archive,restore}` |
| Detail | `PATCH /{id}/{preferences,contacts,addresses,credit}`, `POST /{id}/notes` |
| Relationships | `POST /{id}/{parent,payer}` |
| Reads | `GET /{id}/{hierarchy,profile,history}` |

An account resolves by **id or account number** on every path — a support engineer has the
number, not the id.

Controllers are AST-asserted transport-only. `_account_response` re-reads through
`GetBillingAccount` after a command so `allowed_triggers` is computed by the Foundation rather
than by importing the state machine into a controller.

**The one DELETE is a draft deletion**, rejected by the FSM in every other state. The
plane-wide "no DELETE on revenue" assertion was sharpened at this milestone from *"there is
exactly one DELETE verb"* to the rule it actually protects: an allow-list of provably
non-destructive deletions, plus an assertion that **no** DELETE exists on any path touching
invoices, ledger, payments, refunds, credits, metering, subscriptions, taxes, contracts,
licenses, or a published price/catalog version.

---

## 8. Studio

`/studio/accounts.html` — one file, no build step, no CDN, no external font. Nine screens:
Account List · Account Details · Hierarchy Viewer · Commercial Profile · Contacts · Addresses ·
Preferences · History · Search.

ADR-0021 taken literally: it owns no state (no `localStorage`), holds no rule, and **walks no
graph** — the Hierarchy Viewer renders the tree the server resolved, including
`invoiced_account_id`, rather than following parent pointers itself. Asserted. Lifecycle buttons
are rendered from the server's `allowed_triggers`.

---

## 9. Testing (130)

Lifecycle FSM (legal/illegal edges, the four absent edges, the single deletion edge enumerated
across the whole machine, non-mutation, retention read-only) · hierarchy (ancestors, descendants,
trees, invoiced account, self- and transitive cycles with paths, bounded depth, cross-currency
and closed-parent rejection, terminating traversal on malformed data) · payers (independence from
the hierarchy, loops, chains, ultimate payer, live-payer rule) · validation (account numbers,
duplicate identifiers, addresses, contacts, preferences, credit limits, commercial status) ·
search and profile · the Application over a real database · cross-org isolation, per-org number
sequences, authorization on all 19 messages, durability across a restart · migrations · HTTP ·
Studio · architecture compliance.

Repository-wide: **2463 passed**, zero regressions.

---

## 10. Known limitations

1. **`CloseBillingAccount` (M9.3) and `CloseCommercialAccount` (M9.8) coexist.** The M9.3 command
   closes the M9.2 `BillingAccount`; the M9.8 one drives this domain. Two clear names beat one
   name that quietly changed meaning. Unifying the two account aggregates belongs to the
   milestone that first needs a single path — most likely M9.9 (Subscriptions), which must attach
   a subscription to exactly one of them.
2. **Four commands are additive** beyond the ten the brief listed: `UpdateAddresses`,
   `UpdateCreditProfile`, `AddAccountNote`, and `DeleteDraftAccount`. Without them the Address
   Book, Credit Limit, Account Notes, and lifecycle-completeness responsibilities would have no
   reachable command.
3. **Price overrides and credit exposure are not connected.** `CreditProfile.permits` takes an
   exposure it cannot compute; wiring it to a real balance is the Ledger's (M9.12).
4. **Search and hierarchy resolution are in-memory** — both load the organization's accounts and
   filter or walk them. Deterministic and correct; they will need an indexed path when an
   organization holds thousands of accounts.
5. **Two Applications over one database must not share a deterministic id seam.** The restart
   test uses `DefaultIdGenerator` for the second instance because a second `SequentialIdGenerator`
   would re-mint `bev_1` and collide with the durable fact stream — which is an append-only event
   store behaving correctly, not a defect. Production always uses `DefaultIdGenerator`.
6. **The Studio has no authentication flow.** A bearer token is pasted into a header field. Real
   sign-in is a Control-Plane concern (M9.18).

---

## 11. Compliance

| Obligation | Status |
|---|---|
| ADR-0023 RS4 — revenue outlives the customer record | ✅ one deletion edge, from `draft` only; FSM-enumerated |
| ADR-0023 §5.2 — billing hierarchy separate from org tenancy | ✅ AST-asserted: no Epic 8 import, no org/workspace/project field |
| ADR-0023 RS5 — money is integer minor units + currency | ✅ credit limits only; no arithmetic anywhere |
| ADR-0023 §5.3 — Billing owns no RBAC; deny-by-default | ✅ all 19 messages authorized through the seam |
| ADR-0031 IR1/IR3 — Foundation imports only `core` + `billing` + stdlib | ✅ asserted |
| §A2.4 — deterministic behaviour | ✅ resolution, search, and validation are pure functions |
| §A2.6 — accepted or rejected, never degraded | ✅ validation fails fast; never raises from the validator itself |
| §A5 — history append-only, written in the caller's transaction | ✅ no mutation path; a failed command leaves no history |
| §A11.2 — org-scoped by construction | ✅ every read filters on `org_id`; numbers sequence per org |
| §A12 — additive extension | ✅ no signature broken; every earlier export still present |
| ADR-0031 CR2 — Billing absent from the runtime composition root | ✅ asserted |
| Epics 5–8 frozen; M9.2–M9.7 unchanged | ✅ catalog and pricing boundaries re-asserted here |
