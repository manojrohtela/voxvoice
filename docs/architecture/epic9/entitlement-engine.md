# Entitlement Engine (Epic 9 · M9.10)

**Status:** 🟢 Implemented · **Date:** 2026-08-06 · **Constitution:** v2 (§A2.4, §A2.5, §A2.6, §A5, §A11.2, §A11.3, §A12)
**ADRs:** ADR-0019, ADR-0021, ADR-0023 (§5.3, §11, E3–E6, RS4), ADR-0031 (D3, IR1, IR3, CR2)
**Depends on:** M9.2 Foundation · M9.3 Application · M9.4 Persistence · M9.5 Control Plane API · M9.6 Commercial Catalog · M9.7 Pricing Engine · M9.8 Billing Accounts · M9.9 Subscription Engine

---

## 1. What this component is

The Entitlement Engine answers the question every other plane actually asks at runtime:

> **What is this billing account entitled to, right now — and what should I do if I cannot see fresh data?**

It owns entitlement decisions and nothing else.

Four milestones built the vocabulary it consumes. M9.6 said *what is sold*, M9.7 said *what it
costs*, M9.8 said *who we sell to*, M9.9 said *what was agreed*. This one turns all of that into
the single artefact an enforcing plane can act on without ever calling Billing: an **immutable,
versioned, self-contained entitlement snapshot**.

### The load-bearing invariant

```
Billing decides.
Billing publishes immutable entitlement snapshots.
The owning platform enforces locally.
Billing never performs authorization.
```

Each line is a code-shape property here, not a convention:

| Line | How it is enforced |
|---|---|
| Billing decides | Precedence, bundle expansion, window coverage, and composition are all Foundation behaviour; the Application calls, never re-decides (AST-asserted) |
| Billing publishes immutable snapshots | `EntitlementVersion` is append-only at the contract, the repository, *and* the table's unique constraint |
| The platform enforces locally | Every gate carries a `GateClassification`; `EntitlementSet` contains no principal and creates no dependency on Billing (ADR-0031 D3) |
| Billing never authorizes | No module in this domain contains `principal`, `Permission`, `scope`, `has_role`, `check_permission`, `is_authorized`, `RoleAssignment`, or `RBAC` — a test greps for all of them |

---

## 2. Components implemented

| Layer | Module | Contents |
|---|---|---|
| Foundation | `billing/foundation/commercial_entitlements.py` | Grant FSM, windows, values, definitions, bundles, grants, scheduled changes, profile, resolution types, `EntitlementSet`, staleness types, versions, history, search |
| Foundation | `billing/foundation/entitlement_rules.py` | `EntitlementValidator`, `GrantLifecycleService`, `EntitlementBundleExpander`, `EntitlementPrecedenceResolver`, `EntitlementAssembler`, `EntitlementStalenessService`, `EntitlementScheduleService`, `EntitlementSearchService`, `EntitlementInspector` |
| Foundation | `billing/foundation/entitlement_factories.py` | `EntitlementFactory` + 5 repository ABCs |
| Application | `billing/application/entitlements_engine.py` | 19 commands / 12 queries, use cases, view DTOs, wiring table |
| Persistence | `billing/persistence/entitlement_models.py` | 5 tables |
| Persistence | `billing/persistence/entitlement_repositories.py` | 5 SQL repositories |
| Persistence | `billing/persistence/migrations/v0020_billing_entitlements.py` | Migration **0020** |
| API | `billing/customer/entitlement_schemas.py` | Pydantic v2 wire DTOs |
| API | `billing/customer/routers/entitlement_engine.py` | 28 paths at `/capabilities` |
| Studio | `billing/customer/studio/entitlements.html` | 11 screens |
| Tests | `tests/test_billing_entitlements_engine.py` | 199 tests |

**Additive throughout.** M9.2's `entitlement.py` is unreshaped and M9.5's `/entitlements` routes
are untouched. Foundation exports 371 → 429; application exports 261 → 320; 18 new events and 22
new diagnostic codes, none removed or changed.

---

## 3. Architecture

`HTTP → command/query → Application → Foundation → Persistence`, unchanged from M9.3.

### 3.1 M9.2's vocabulary is reused, not duplicated

`GateClassification`, `GrantSource`, `GRANT_PRECEDENCE`, `Entitlement`, `EntitlementGate`, and
`EntitlementSnapshot` are **imported from `entitlement.py`**, not re-declared. Two answers to
"which source wins?" is exactly the drift §A2.5 exists to prevent — the lesson M9.9 learned the
expensive way when it briefly defined its own `INTERVAL_SECONDS` beside M9.2's.

### 3.2 The published contract is M9.2's shape

`EntitlementSet.to_snapshot()` projects onto the frozen `EntitlementSnapshot`, and
`ResolvedEntitlement.as_entitlement()` onto the frozen `Entitlement`. **An enforcing plane
written against the M9.5 contract consumes an M9.10 decision without changing a line.** This is
the constructive answer to the coexistence problem the M9.9 report flagged: rather than two
rival entitlement surfaces, M9.10 is the governed producer of the shape M9.5 already publishes.

### 3.3 Grants are the aggregate, not rows

An account's grants live inside the profile aggregate and travel with it. Precedence resolution
is only correct when it sees **all** of an account's grants at once; a row-per-grant table
invites a partial read that would resolve to the wrong answer silently. A test asserts no table
name contains `grant`.

---

## 4. Domain model

### 4.1 Gate definitions — the registry

An `EntitlementDefinition` declares a capability **and how an enforcing plane must behave when it
cannot reach Billing**. ADR-0023 E3 makes an unclassified gate an incomplete design, so
validation reports it as an **error, not a warning**. A quota with no meter is likewise an error:
a quota is an allowance *of something measured*, and without a meter the gate is undecidable
rather than generous.

Gates are published for **every declared definition**, not only for codes that resolved — an
enforcing plane asking about an ungranted capability still needs to know how to behave.

### 4.2 Values — unlimited and none are opposite answers

`allowance=None` means unlimited; `0` means explicitly none. A nullable integer that collapsed
them would make an enterprise contract indistinguishable from a suspension.

### 4.3 Effective dating — half-open, and never mutating

`EntitlementWindow` is **half-open `[starts_at, ends_at)`**, the same convention M9.7 uses. With
closed intervals, one grant's end and the next grant's start are both in force for one instant,
and a snapshot composed exactly then would be ambiguous.

**Effective dating is a resolution-time property, not a state change.** A grant whose window has
closed simply stops being composed; nothing mutates. The `EXPIRED` state records an
*administrative* fact. Keeping the two separate is what lets this domain contain **no scheduler
at all**.

### 4.4 Precedence — declared, fully ordered, order-independent

1. **declared source precedence** (`GRANT_PRECEDENCE`, or an in-band override) — highest wins;
2. **generosity** — a denial loses to any grant; unlimited beats any number;
3. **grant id**, lexicographically — arbitrary but *stable*.

Insertion order is never consulted; a test rotates the grant list through every offset and
asserts an identical result. `precedence_override` exists for the negotiated term declared
precedence cannot express, and validation keeps it inside the declared band so one grant can
never silently invert the whole ordering.

### 4.5 Bundles — expanded into grants, not stored as a reference

Bundles may nest, because that is how real packaging works. Cycles are detected and reported
**with the path that closes them**, never recursed. Granting a bundle mints **one grant per
expanded entry**: a grant is the unit that gets revoked, suspended, and explained, and a bundle
reference would make "revoke just the SSO part" impossible without inventing an exception.

A nearer definition overrides a deeper one — an outer bundle that restates a capability is
overriding the pack it includes, which is exactly why it restated it.

### 4.6 Inspection — the losers are kept

`EntitlementExplanation` carries the winner *and* every grant weighed, each with the reason it
took no part. **"Why does this account *not* have X?" is asked at least as often as the
opposite, and it is unanswerable from the winner alone.**

Every `ResolvedEntitlement` also carries a `resolution_path` — the same idea M9.7 gave
`ResolvedPrice`. An entitlement a customer disputes is worth nothing if nobody can say *why* the
system decided it.

### 4.7 No money, no usage

An allowance says how much is *included*, never what it costs (pricing's) or how much has been
consumed (metering's, M9.11). `Money`, `minor`, `.times(`, `.allocate(`, `.scaled(`, `PriceTier`,
`UsageRecord`, `LedgerEntry`, `Invoice`, `Payment`, `Refund` appear nowhere across the eight
modules, and no amount reaches the wire.

---

## 5. Lifecycle

```
draft ──activate──▶ active ──suspend──▶ suspended ──resume──▶ active
  │                   │  │                  │
  │                   │  ├──expire──▶ expired ──archive──▶ archived
  └──revoke──▶ revoked ◀─┴──revoke──────────┘
                  └──archive──▶ archived
```

Four absences, each a decision:

* **no edge reaches a deletion state, because there is no deletion state.** What a customer was
  given, and when it was taken away, is evidence — of a support promise, a contract term, a
  partner arrangement. ADR-0023 RS4 makes the record permanent;
* **no `revoked → active`.** Re-granting is a *new* grant; collapsing the two makes "was this
  ever revoked?" unanswerable, and that is precisely the question a disputed entitlement turns on;
* **no `draft → suspend`.** Suspending something never active is a distinction without a
  difference, and offering it would produce two ways to say "not yet";
* **no edge out of `archived`**, which is what makes it terminal.

A test asserts the machine contains **no self-loops at all**, so a computation cannot creep into
the state machine.

Two lifecycle behaviours worth stating:

* **Activation anchors an unbounded window to now.** A capability that quietly applied to last
  month is a different promise from the one that was made.
* **Revocation closes the window as well as the state.** Otherwise a snapshot composed for a
  later instant would re-admit the grant through effective dating — the state saying *gone*, the
  window saying *present*.

---

## 6. Versioning and publishing

* A version **embeds both the decision and the profile that produced it**. Embedding the grants
  as well as the answer is what makes an old decision *auditable* rather than merely readable:
  the composition can be redone and compared.
* **Immutability is defended three deep** — no update/delete on the contract, an
  `ImmutabilityViolation` in the repository, and a unique `(org_id, account_id, version)`.
* **A snapshot is returned as published, never recomputed.** A decision an enforcing plane acted
  on must read back exactly as it was; recomputing on read would quietly answer a different
  question. `PreviewEntitlementSnapshot` exists for "what *would* be published" — composition is
  pure, so previewing costs nothing and publishes nothing.
* **Rollback publishes forward** with recorded provenance; the versions in between remain
  readable forever, because an enforcing plane may have acted on any of them.
* **Republishing an unchanged decision is refused** (`allow_identical` overrides it for a
  deliberate audit boundary). A hundred identical versions would bury the ones that meant
  something. The comparison is a deterministic `fingerprint` that ignores *when* the decision was
  made.

---

## 7. Staleness handling

`STALE_DIRECTIVES` maps classification → directive as **one table**, so every enforcing plane
gets the same answer instead of each doing its own subtraction:

| Classification | Directive when stale | Rationale (ADR-0023 E4) |
|---|---|---|
| `fail_open` | `allow` | Anything that could interrupt a live conversation |
| `fail_closed` | `deny` | Anything that could create unbounded new cost |
| `degrade` | `degrade` | Reduce rather than refuse |

Bounded, measured revenue leakage is preferred — **explicitly** — to a customer-visible
interruption. `GET /capabilities/profiles/{id}/staleness` answers the operational question
directly rather than handing over a timestamp and a budget and hoping.

---

## 8. Persistence

Five additive tables via **migration 0020**, two append-only. `ALTER`s nothing; M9.2's
`cp_billing_entitlement_grant` and `cp_billing_entitlement_snapshot` are untouched. Round-trip
fidelity is asserted for the full profile aggregate (grants, windows, overrides, intentions) and
for a published version. No repository resolves precedence, expands a bundle, decides a lifecycle
move, or composes a snapshot.

---

## 9. API

**28 paths at `/capabilities`** (148 total, no collisions). The prefix is ADR-0023's own word for
this domain — entitlement is *commercial capability*, not authorization (§11.3) — and a separate
prefix is a **correctness** property: `/entitlements/grants/{grant_id}` and
`/entitlements/snapshots/{account_id}` are existing M9.5 wildcards, and siblings under them would
be shadowed depending on router registration order.

**There is exactly one DELETE**, and it withdraws a *pending intention* that was never acted on.
No grant, profile, gate declaration, bundle, or published snapshot is deletable at any path.

**Authorization split.** Declaring a gate is a platform act (`BILLING_CATALOG_MANAGE`, as the
catalog uses); granting to an account is a commercial act (`BILLING_MANAGE`); reads take
`BILLING_READ`. Requiring a permission to *change* entitlement data is not the same as evaluating
entitlement as permission — Billing still does none of the latter.

---

## 10. Studio

Eleven screens in one self-contained file: Gate Registry, Bundles, Profile, Grants, Snapshot,
Version History, **Why?**, Diagnostics, Staleness, Scheduled Changes, Timeline.

ADR-0021: it owns no state and decides nothing — including, especially, precedence. The Why?
screen *draws* the resolution the server returned, losing grants included. Lifecycle buttons come
from the server's `allowed_triggers`. No `localStorage`, no money formatter, and no field in
which to express an authorization question.

---

## 11. Testing — 199 tests

Grant lifecycle · effective dating · values · definitions and gates · precedence · inspection and
diagnostics · bundles · snapshots and versions · staleness · scheduled changes · search · factory
discipline · the Application over a real database · cross-org isolation, deny-by-default on all
31 messages, restart durability · migrations and codec round-trip · HTTP · Studio · architecture
compliance.

### 11.1 Defects found and fixed

1. **`build_account_repositories` and `build_subscription_repositories` were unimportable.** Both
   were declared in `billing/persistence/__init__.__all__` at M9.8/M9.9 but never imported from
   `composition`, so `from billing.persistence import build_subscription_repositories` raised
   `ImportError`. Found while wiring M9.10's equivalent; fixed additively — the two names now
   resolve, as they always claimed to. *(A pre-existing defect in frozen milestones, surfaced and
   corrected without changing any behaviour.)*
2. **`_subscription_state` treated every `source_ref` as a subscription id.** A contract, partner,
   or marketplace grant names a document this domain knows nothing about; the check now applies
   only to `PLAN`-sourced grants. Caught before the suite by reasoning about the reference kinds,
   and covered by two tests.
3. **A private emitter reach-through.** The Application briefly emitted a profile-level fact via
   `self._factory._emitter`, which would have put a second emission path in the domain. Replaced
   with a public `EntitlementFactory.emit_profile_fact`, so "exactly one event per business fact"
   stays checkable in one place.

### 11.2 Assertions sharpened (none weakened)

* **The DELETE allow-list** gained the new pending-intention deletion with its reason, plus a new
  rule statement: no path containing `/grants/`, `/profiles/`, `/definitions`, `/bundles`, or
  `/snapshot` under `/capabilities` may ever be deletable.
* **The Foundation package-layout test** gained the three new modules, with the reason recorded
  inline: extension is addition (§A12), never a reshape of `entitlement.py`.
* **Controller transport-only detection** identifies routes **structurally** by their
  `@router.<verb>` decorator (the M9.9 sharpening), and additionally asserts a minimum route
  count so a shrinking surface cannot pass by having nothing to check.

---

## 12. Documentation updated

`docs/INDEX.md` · `CHANGELOG.md` · `.voxvoice/DECISION_LOG.md` (DL-0120) · this spec.

---

## 13. Architecture compliance

| Check | Result |
|---|---|
| Owns only entitlement decisions | ✅ AST-asserted, 8 modules |
| **Billing never evaluates a permission** | ✅ AST-asserted (9 terms, Foundation + all layers) |
| Billing never blocks Runtime (no outbound call anywhere) | ✅ AST-asserted |
| Publishes immutable, versioned snapshots | ✅ contract + repository + unique constraint |
| Every gate carries a fail-open / fail-closed / degrade classification | ✅ error-severity, not warning (ADR-0023 E3) |
| Deterministic resolution, independent of insertion order | ✅ rotation test |
| No money, no usage, no ledger, no invoice, no payment, no tax | ✅ AST-asserted |
| No runtime / product / deployment / knowledge / observability import | ✅ import-graph assertion |
| No scheduler; intent kept separable from execution | ✅ AST-asserted + behavioural |
| Foundation pure and clock-free (`at` always an argument) | ✅ asserted |
| M9.2 vocabulary reused rather than duplicated | ✅ asserted |
| Exactly one event per fact; none for a rejected command | ✅ asserted both ways |
| Deny-by-default; all 31 messages authorized | ✅ |
| Migration chain extended, idempotent, alters nothing | ✅ M9.2's entitlement tables untouched |
| M9.6–M9.9 boundaries still hold | ✅ re-asserted here |
| Epics 5–8 frozen; Billing absent from `bootstrap.py` | ✅ |
| Repository-wide suite green | ✅ 2827/2827 |

---

## 14. Limitations (recorded, not hidden)

1. **Two entitlement write surfaces still coexist.** M9.5's `GrantEntitlement` /
   `PublishEntitlementSnapshot` write the minimal M9.2 records; M9.10's governed engine writes its
   own. They are *reconcilable* — M9.10 publishes the M9.2 snapshot shape — but M9.5's commands
   are not yet re-pointed at the engine. Doing so touches a frozen surface and belongs in a
   milestone with a mandate for it.
2. **Grants are not derived from a subscription's plan features automatically.** A `PLAN`-sourced
   grant references its subscription and is validated against it, but reading the pinned catalog
   version's `PlanFeature` set and minting grants from it is not implemented. `EntitlementResolver.
   from_plan` (M9.2) already does that composition; wiring it here would add a catalog read to
   this domain and was left as an explicit decision rather than a silent coupling.
3. **Scheduled changes are never applied by anything.** By design (§4.3, §5), but it means an
   operator must dispatch the real command. No milestone yet owns the surface that would.
4. **Quota consumption is not evaluated here.** `QuotaService` (M9.2) derives verdicts from a
   snapshot plus observed consumption; this engine publishes the snapshot and owns no usage.
5. **`precedence_override` is per-grant, not per-tenant policy.** A customer wanting a different
   global ordering would need a policy object this milestone does not define.
6. **Bundle expansion is eager at grant time.** Amending a bundle does not retroactively change
   grants already minted from it — correct for immutability, but it means "re-sync this account
   to the current bundle" is an operator action with no command yet.

---

## 15. Recommendation

Proceed to **M9.11 (Metering Engine)** or the roadmap's next milestone. The Entitlement Engine is
the platform unlock the blueprint identified: every other plane can now read one cached, immutable
artefact and enforce locally, with a declared answer for what to do when Billing cannot be
reached.

The highest-value follow-up inside Billing is limitation 1 — re-pointing the M9.5 entitlement
commands at this engine so there is exactly one writer of entitlement facts (§A2.5). That should
be an explicit milestone, because it changes a published surface.
