# Rating Engine (M9.12)

**Status:** 🟢 Implemented · **Epic:** 9 (Billing & Subscription Platform) · **Date:** 2026-08-07
**Authority:** Engineering Constitution v2 → ADR-0023 (Billing Sole Owner of Revenue) → ADR-0031
(Billing Plane Placement) → this document.

---

## 1. What this milestone is

Six milestones built a vocabulary. M9.6 said **what is sold**, M9.7 said **what one unit costs**,
M9.8 said **who we bill**, M9.9 said **what was agreed**, M9.10 said **what is included**, M9.11
said **what was consumed**. None of them produced money.

Rating is the arithmetic that joins them:

> *this agreement, on this pricing version, over this period, against this usage → these charges.*

It is the first Billing milestone whose output a customer pays, and therefore the first whose
errors cost money in both directions. The design consequence is that **every claim about
determinism is made checkable rather than asserted** — the same discipline M9.11 applied to
metering, applied to money.

### Owns

Charge calculation for every supported commercial shape; charge breakdown; charge provenance;
included-quantity resolution; overage; charge-level reductions; minimum commitments; rating runs;
rerating; adjustment against finalized runs; rating snapshots; comparison; explanation; inspection;
diagnostics; search; and append-only rating history.

### Owns nothing else

No usage, price, catalog, subscription, entitlement, ledger, credit, invoice, payment, refund, tax,
or authorization. It **measures nothing** and **prices nothing**.

---

## 2. Components

| Layer | Module | Lines |
|---|---|---|
| Foundation | `billing/foundation/commercial_rating.py` | 1,017 |
| Foundation | `billing/foundation/rating_rules.py` | 1,578 |
| Foundation | `billing/foundation/rating_factories.py` | 465 |
| Application | `billing/application/rating_engine.py` | 1,782 |
| Persistence | `billing/persistence/rating_models.py` | 207 |
| Persistence | `billing/persistence/rating_repositories.py` | 518 |
| Persistence | `billing/persistence/migrations/v0022_billing_rating.py` | 39 |
| API | `billing/customer/rating_schemas.py` | 487 |
| API | `billing/customer/routers/rating_engine.py` | 733 |
| Studio | `billing/customer/studio/rating.html` | 593 |
| Tests | `tests/test_billing_rating_engine.py` | 2,198 |

Additive throughout. M9.2's `pricing.py` and `invoicing.py` are unreshaped, `ChargeLine` and
`ChargeLineKind` stay exactly as they are, and `cp_billing_charge_line` is untouched.

---

## 3. Architecture

### 3.1 The two decisions that carry the milestone

**The run id is derived from the basis digest, not minted.** Every other Billing aggregate draws
its id from the `IdGenerator` seam. A rating run derives it from
`(org, subscription, period, basis digest)`, and three consequences follow *structurally* rather
than by convention:

* rerating unchanged inputs is a **no-op at the database** — same id, unique constraint fires,
  repository returns the stored run;
* replay is **byte-comparable** — equal ids, equal charge ids (derived from the run id), equal
  digests, checked with `==`;
* a changed input **cannot overwrite** the figure it revises, because the id of a revised run is
  never the id of the run it revises.

**Rating consumes a `ResolvedPrice`; it never resolves one.** That single field is the whole
boundary between M9.7 and M9.12. Book selection, override precedence, and discount eligibility stay
in the Pricing Engine; the Application loads the *pinned* pricing version and calls M9.7's
`PriceResolver`. A test asserts the rating Foundation never names `PriceResolver`, `select_book`,
`PriceResolutionRequest`, or `eligible(`.

### 3.2 Reading six domains without owning any of them

Rating is the first milestone that must read almost every other one. It does so through their
repository interfaces, read-only, at the orchestration altitude. Three rules keep that from becoming
coupling, and all three are tested:

1. **Nothing is written outside rating's own five tables.** `_RatingInputs` — the class that reads
   the other domains — contains no `.add(`, `.append(`, `.replace(`, `.publish(`, `.upsert(`, or
   `.seal(` at all. A separate test walks the Application's AST and asserts every write verb on an
   injected collaborator targets `self._repos.*`.
2. **Every read is of a published, immutable artefact.** A pricing version, an entitlement version,
   recorded usage. Rating never reads a draft, so nothing it consumes can move under a rerun.
3. **Price resolution is delegated, never re-implemented.**

### 3.3 Purity

`rating_rules.py` reads no clock, opens no transaction, and consults nothing outside its arguments.
Given equal arguments it returns an equal result. That is what makes "identical inputs always
produce identical charges" a property a test can check rather than a claim a reviewer must accept.

---

## 4. Domain model

**All money arithmetic is integer arithmetic.** Extension is `Money.times` (exact), proportional
reduction is `Money.scaled` with a declared rounding policy, and splitting a total across claimants
is `Money.allocate` (largest-remainder, so parts always sum to the whole). Package blocks use
integer ceiling division `-(-a // b)`, never `math.ceil` on a float quotient — a test uses a
quantity of 10¹⁸ + 1, where the float route silently rounds down.

| Type | What it is |
|---|---|
| `RatedCharge` | One immutable, fully explained, fully attributed amount. Beside M9.2's `ChargeLine`, never instead of it |
| `ChargeComponent` | One step of the arithmetic. The components sum **exactly** to the charge |
| `ChargeProvenance` | Everything needed to re-derive the charge — every field names a frozen artefact |
| `RatingBasis` | The complete immutable input, and its digest |
| `RatingResult` | Charges plus derived totals plus a result digest |
| `RatingRun` | One attempt and its verdict, with `supersedes` / `adjusts` links |
| `RatingSnapshot` | A self-contained record of what a run decided |
| `RatingDelta` | Why two runs differ, charge by charge |
| `MinimumCommitment` | A spend floor and what counts toward it |

**A charge carries its own breakdown and provenance, not a reference to them.** A customer disputing
₹4,120 is answered from the charge itself — which rate, from which book at which version, times which
quantity, from which usage records, less which reductions — without re-running anything.

### 4.1 Included quantities — declared precedence

Two authorities can declare an allowance: the price (`included_units`) and the published entitlement
snapshot. **The entitlement wins when it declares one at all**, and which one won is recorded on
every charge.

Declared rather than inferred, because the alternatives are worse. Taking the larger would let a
promotional entitlement permanently raise a plan's allowance; taking the smaller would let a stale
snapshot bill a customer for units their plan includes. A contract grant that raises an allowance is
the *whole point* of M9.10's precedence, so the entitlement is the more specific statement.

An entitlement allowance of `0` beats a price allowance of 100 — treating explicit-none as absent
would silently restore an allowance a contract removed.

### 4.2 Minimum commitments

Applied **after** every other charge, never before one. A shortfall computed against a partial charge
set would depend on the order charges happened to be calculated in, and a true-up that changes when
you reorder a list is not a commitment. A met commitment produces **no charge at all** rather than a
zero line, and overspending is never a credit.

---

## 5. Lifecycle

```
draft ──rate──▶ rated ──finalize──▶ finalized
  │               │
  │               ├──supersede──▶ superseded
  └──discard──────┴──discard────▶ discarded
```

Four absences, each a decision:

* **no edge out of FINALIZED.** A finalized run is what an invoice was raised from. Revising it is a
  *new* run plus a credit note, never a mutation — and the absence of the edge is what makes that
  structural rather than procedural;
* no edge out of SUPERSEDED — it answers "what did we think this period cost, before we knew
  better?";
* no edge out of DISCARDED and no path back to DRAFT;
* **no deletion state at all** (ADR-0023 RS1).

`RerateService.links` encodes the distinction with teeth: a **provisional** predecessor is
*superseded*; a **finalized** one is *adjusted* and left exactly as it was billed.

---

## 6. Persistence

Five additive tables via migration **0022**; three append-only.

The load-bearing object is `uq_cp_billing_rating_run_basis` on
`(org_id, subscription_id, period_start, period_end, basis_digest)`. Because the primary key is
derived from the same tuple, the constraint and the key agree by construction rather than by
coincidence — two defences for the invariant that cannot be recovered from.

`append` defends three deep: a derived key, a read-before-write, and — for the genuine race between a
nightly job and a manual rerate — an insert inside a **`SAVEPOINT`** whose `IntegrityError` is caught
and turned into a read of the winner. The savepoint is the part usually missing: without it a caught
integrity error poisons the session and the surrounding transaction dies with the activity entry and
staged event.

---

## 7. API

**19 paths at `/charges`** — a separate prefix for the same reason M9.8, M9.9, M9.10, and M9.11 each
took one: siblings under an existing wildcard would be shadowed depending on router registration
order.

Rating returns **201** for a new run and **200** for an unchanged one. Both are successes: a nightly
job that finds nothing to do has still done its job.

**No DELETE, no PUT, no PATCH anywhere in the surface.** Money on the wire is always
`{minor, currency}` — M9.5's `MoneyResponse`, reused rather than redeclared.

---

## 8. Studio

Eleven screens, one self-contained file. It *displays* money — that is what rating produces — but
derives none: no total is summed, no difference taken, no percentage applied, and a test scans for
each. The Verdict panel displays the server's answer rather than reaching one; the Compare screen
renders the server's delta rather than subtracting two totals.

---

## 9. Testing — 141 tests

Repository-wide: **3158 passing** (3011 prior + 147 collected here, including parametrised cases),
zero regressions. `ruff check` and `ruff format --check` clean.

### 9.1 Defects found and fixed

**1. A fixed-sum discount on a metered price leaked revenue — a defect in frozen M9.7, fixed
additively here.**

M9.7's `PriceResolver` folds **every** eligible discount into the *unit* rate, including
`FIXED_AMOUNT`, `BUNDLE`, and `COMMITMENT` reductions. For a flat or one-off price that is exactly
right. For a per-seat, per-unit, tiered, volume, or package price it is not: rating multiplies the
rate by a quantity, so a one-time "₹500 off this line" becomes "₹500 off *each* unit".

Worked example, from the test: a ₹600/minute price with a ₹500 fixed discount, 10 minutes consumed.

| Route | Result |
|---|---|
| Naive (M9.7's net rate × quantity) | ₹1,000 — a ₹5,000 reduction nobody authorised |
| Correct (list × quantity − ₹500 once) | **₹5,500** |

M9.7 is frozen and correct at its own altitude: it answers "what does one unit cost?", and a
per-unit answer is the only answer it can give without a quantity. The quantity is rating's, and so
is the fix. `DiscountRebasingPolicy` re-bases the rate for extended shapes, subtracts each fixed sum
once at charge level, records both moves as visible `REBASE` and `DISCOUNT` components, and raises a
`FIXED_DISCOUNT_ON_METERED_RATE` **warning**. **Nothing in M9.7 changes**, and for every shape rating
does not multiply the re-based result is arithmetically identical to M9.7's — the correction is
invisible exactly where M9.7 was already right.

**2. A second `MoneyResponse` on the wire.** M9.12 declared its own wire type for
`{minor, currency}`, identical to M9.5's. FastAPI resolved the collision by emitting two qualified
schema names — `billing__customer__schemas__MoneyResponse` and
`billing__customer__rating_schemas__MoneyResponse` — into the public OpenAPI document. Fixed by
reusing M9.5's (§A2.5); a test now asserts exactly one money schema exists.

**3. `RatingLifecycleService` raised the wrong error type**, referencing a diagnostic code
(`ILLEGAL_TRANSITION`) that does not exist in `BillingDiagnosticCode`. Aligned with the frozen
vocabulary M9.9 and M9.11 use: `InvalidBillingTransition` for an illegal edge, `BillingConflict` for
an attempt to edit a settled run.

**4. `_RatingInputs` read the commitment repository off the context by attribute name** rather than
through injection, which would have failed silently if the attribute were ever renamed. Now injected.

### 9.2 Assertions sharpened, none weakened

* **Network scanning** moved from a substring search to an *import* scan. `requests` is also the
  name of this domain's own `Sequence[ChargeRequest]` parameter, and a domain word that collides with
  a library word must not cost the domain its vocabulary — the M9.6 lesson.
* **"Rating writes nowhere else"** moved from a target allow-list to a rule: a write verb on an
  *injected collaborator* (`self._*`) must target `self._repos.*`. A local list's `.append` is not a
  repository write, and `dataclasses.replace` merely shares a name with one; flagging either would
  teach a reader to ignore the test.
* **Migration and boundary scans** switched to code-only (docstrings stripped), because prose
  explaining that a rollback must never drop a record is not a `DROP`.
* **The M9.11 migration-chain assertion** moved from "0021 is last" to "0021 is applied, in position,
  and the chain stays ordered" — the same extension M9.11 made to M9.10's.

### 9.3 Frozen assertions extended additively

`test_billing_package_layout_is_the_declared_one` (+3 modules) ·
`test_every_bounded_context_is_tagged` (+`rating`) ·
`test_metering_tables_are_created_by_the_migration_chain` (position, not tail).

---

## 10. The correctness demonstrations

Each corresponds to one claim in the milestone brief and is a separate, named test.

| # | Claim | Test |
|---|---|---|
| 1 | Identical inputs always produce identical charges | `test_demo_1_identical_inputs_always_produce_identical_charges` |
| 2 | Replay produces byte-identical results, **ids included** | `test_demo_2_replay_produces_byte_identical_results` |
| 3 | Charge order does not depend on the caller | `test_demo_3_the_charge_order_does_not_depend_on_the_caller` |
| 4 | A rerate appends and never overwrites | `test_demo_4_a_rerate_appends_and_never_overwrites` |
| 5 | Charge provenance exists for every billed amount | `test_demo_5_charge_provenance_exists_for_every_billed_amount` |
| 6 | Hybrid subscriptions rate as one result | `test_demo_6_hybrid_subscriptions_rate_as_one_result` |
| 7 | Totals are derived from the charges, never stored beside them | `test_demo_7_totals_are_derived_from_the_charges…` |
| 8 | No float participates anywhere | `test_demo_8_no_float_participates_anywhere…` |
| 9 | Rating reads no clock | `test_demo_9_rating_reads_no_clock` |
| 10 | No dependency on Runtime or a provider | `test_demo_10_the_engine_needs_no_runtime_and_no_provider` |
| 11 | Preview and authoritative are distinguishable | `test_demo_11_a_preview_and_an_authoritative_run…` |
| 12 | **Idempotent rerating appends no run** (against the database) | `test_demo_12_idempotent_rerating_appends_no_run` |
| 13 | A preview writes nothing at all | `test_demo_13_a_preview_writes_nothing_at_all` |
| 14 | Rerating never changes the historical figure | `test_demo_14_rerating_never_changes_the_historical_figure` |
| 15 | **A finalized run is adjusted, never superseded** | `test_demo_15_a_finalized_run_is_adjusted_never_superseded` |
| 16 | Determinism survives a restart with new seams | `test_demo_16_determinism_survives_a_restart_with_new_seams` |
| 17 | Rating history is append-only | `test_demo_17_rating_history_is_append_only` |
| 18 | A commitment true-up appears end to end | `test_demo_18_a_commitment_true_up_appears_end_to_end` |
| 19 | **Rating writes to no other domain** | `test_demo_19_rating_writes_to_no_other_domain` |

Demonstration 16 is the strongest: a second Application over the same database, with a **different
id generator and a different clock**, rates the same period and gets the same run back — because a
run's identity comes from what it rated, not from when or by whom.

---

## 11. Compliance

| Check | Result |
|---|---|
| Billing never measures | ✅ AST-asserted, 8 modules |
| **Billing never prices** | ✅ the Foundation names no resolver, no book selection, no eligibility |
| Billing never authorizes | ✅ no principal, permission, or role anywhere |
| Rating owns no ledger, invoice, payment, or tax | ✅ AST-asserted |
| Rating mutates no other domain | ✅ read-only input class + AST write-target scan + before/after equality |
| No import of any frozen Epic | ✅ import-graph assertion |
| Deterministic rating | ✅ 3 demonstrations incl. across restart |
| Idempotent rerating | ✅ constraint + savepoint + database demonstration |
| Rating never edits history | ✅ FSM has no edge out of `finalized` |
| Breakdown sums to the charge | ✅ parametrised over all 7 shapes |
| Money is integer minor units | ✅ no float in the domain; wire shape asserted |
| Foundation pure and clock-free | ✅ asserted |
| Deny-by-default across all 21 messages | ✅ |
| Migration chain extended, alters nothing | ✅ `cp_billing_charge_line` untouched |
| M9.6–M9.11 boundaries still hold | ✅ re-asserted |
| Billing absent from the runtime composition root | ✅ ADR-0031 CR2 |
| Repository-wide suite green | ✅ 3158/3158 |

**No ADR amendment is required.** The M9.7 discount defect is a *fix within M9.12's own arithmetic*,
not a change to a ratified decision: ADR-0023 §5.1 says a price is what one unit costs and rating is
what someone owes, and re-basing is rating obeying that boundary rather than bending it.

---

## 12. Surface changes

10 new `billing.rating.*` event types (157 total) · 16 new diagnostic codes (146 total) ·
foundation exports 510 → 574 · application exports 380 → 433 · persistence exports 85 ·
9 commands + 12 queries · 5 tables via migration 0022 · 19 API paths · 193 paths total, no collisions.

---

## 13. Limitations

Recorded honestly rather than deferred silently.

1. **The M9.11 attribution ceiling is unresolved and cannot be resolved here.** Usage attribution
   still requires exactly one active billing account per organization, because M9.8 models no
   scope→account attachment. Rating does not make this worse — it rates per *subscription*, and a
   subscription names its account directly — but it does not fix it either, and fixing it means
   touching M9.8. Introducing a scope→account map inside Rating would be exactly the cross-domain
   coupling ADR-0023 forbids. **Recommended for a milestone with a mandate to amend M9.8.**
2. **Proration is not implemented.** A mid-period upgrade currently rates the full period at the
   agreement's items. M9.2 ships a `ProrationService` and M9.9 declares proration *behaviours*, so
   the pieces exist; wiring them is additive and was outside this brief's deliverable list.
3. **One pricing version per run.** An agreement whose pricing version changes mid-period rates the
   whole period at the pinned version. Splitting a period at a version boundary is a rating-period
   decision that belongs with proration.
4. **Credit prices produce a zero usage charge.** `PriceShape.CREDIT` has no `ChargeKind`, because a
   credit is not a charge. Credit drawdown is M9.13's.
5. **`RateAccountPeriod` is sequential.** One transaction per agreement, iterated. Correct and
   restartable, but a very large account rates serially; parallelism is safe (the derived id makes
   overlapping runs converge) and simply not implemented.
6. **A tiered price's reported per-discount reduction is approximate in one edge case.** M9.7 reports
   only the first band's reduction for a tiered price. Rating allocates the *exact* aggregate across
   the declared policies by that weight, so the trail always sums to the charge; when every reported
   weight is zero it attributes the whole reduction to the first policy. The total is always exact;
   only the attribution between two simultaneous tier discounts can be imprecise.
7. **Rating does not know a usage period is sealed.** It reads recorded usage regardless. In practice
   a sealed period's usage cannot change, so this is safe today — but rating a period that is still
   open produces a provisional figure, and nothing currently prevents finalizing one.
   `RATING_PERIOD_NOT_SEALED` is declared as a diagnostic code and not yet raised.

---

## 14. Recommendation

Proceed to **M9.13 (Credit & Ledger)**, which consumes a finalized rating run as its input.

Two items should be scheduled alongside it: resolving limitation 1 (the M9.8 attribution ceiling,
which now blocks two milestones) and limitation 7 (refusing to finalize a rating run over an unsealed
usage period — a one-line guard whose absence is the only path by which a provisional figure could
reach an invoice).
