# Tax Determination Engine (M9.15)

**Status:** 🟢 Implemented · **Milestone:** M9.15 · **Date:** 2026-08-09
**Authority:** Engineering Constitution v2.0 · ADR-0019 · ADR-0021 · ADR-0023 · ADR-0031
**Predecessors:** M9.2–M9.14 (all frozen; none reshaped)

---

## 1. What this milestone is

Nine milestones produced facts. M9.6 said **what is sold**, M9.7 **what it costs**, M9.8 **who we
bill**, M9.9 **what was agreed**, M9.10 **what may be used**, M9.11 **what was consumed**, M9.12
**what should be charged**, M9.13 **what is owed**, M9.14 **what the customer receives**. None of
them said what a tax authority is owed, or why.

The failure mode changes again. M9.13 was the first domain whose errors could not be corrected by
re-running anything. M9.14 was the first whose output left the building. M9.15 is the first whose
**correctness is judged by someone outside the company** — a tax authority, on a date years after
the invoice was issued, against rules that have since changed. The question is never "what is the
tax?" It is *"why was this amount applied, and on whose authority?"*

So the milestone rests on one sentence, ratified two years before it:

> **Determination is a rented edge. Evidence is an owned fact.** (ADR-0023 TX1)

Billing therefore declares **no rate**. Not a constant, not a default, not a fallback "18 if India".
A fresh installation determines no tax at all. Rates arrive through `TaxRateProvider`, an adapter
port; what is *stored and owned* is the evidence of which rate applied, from where, under which
version, effective when, and against which registration.

**Evidence:** 13 commands / 16 queries; 7 tables via migration **0025**; 24 paths at `/taxation`
(267 total); 11 Studio screens; **152 tests including 19 named determination demonstrations**;
**3601/3601 repository-wide** (3449 prior, zero regressions); ruff clean.

**Five defects found and fixed** — four by the end-to-end smoke, one of them financially material in
both directions — reported in §10.

---

## 2. The mandatory audit

Performed before any code, across M9.2–M9.14. Findings:

| Inspected | Finding |
|---|---|
| Tax types in M9.2 | `TaxRegime`, `TaxpayerKind`, `TaxTreatment`, `GstSplit`, `TaxRegistration`, `TaxProfile`, `TaxComponent`, `TaxDetermination` |
| Tax repository contracts | `TaxProfileRepository` (implemented), `TaxDeterminationRepository` (implemented, `for_invoice` only) |
| Tax commands / queries | `UpsertTaxProfile`, `GetTaxProfile`, `GetTaxDetermination` |
| Tax API routes | 3, at `/taxes` |
| Determination evidence | Present but **thin** — no rate provenance, no effective period, no registration used, no rounding policy, no version, no digest |
| Invoice tax fields | `tax_components`, `tax_treatment`, `tax_determination_id`, `tax_total`, `tax_rate_basis_points` — all **consumed read-only** by M9.14 |
| Diagnostics / events | `missing_tax_profile`, `invalid_tax_registration`, `missing_tax_evidence`; `tax.profile_updated`, `tax.determined` |
| Jurisdiction / registration models | `TaxProfile.place_of_supply` (a bare string), `LegalEntity.tax_identifier`, M9.14's `PartyRegistration` (printed, captured at issue) |
| Currency handling | `Money(minor, currency)`; `Money.scaled(n, d, rounding)` is exactly the tax primitive |
| **Any existing GST/VAT logic** | **None.** `GstSplit` is a *recorded outcome*, never a decision |
| **Any existing rate table** | **None anywhere in the repository.** Verified by scan |

**Ownership overlap:** one, and resolved by the same call M9.13 made for `JournalEntry` beside
`LedgerEntry` and M9.14 for `CommercialInvoice` beside `Invoice` — `TaxAssessment` sits **beside**
`TaxDetermination`, and every M9.2 enum and value object is **reused unchanged**.

**Duplicated logic:** none found. No jurisdiction rule, no registration logic, no tax calculation
exists elsewhere to duplicate.

**Missing source-of-truth boundary — and how it is closed.** M9.14 reads
`tax_determinations.for_invoice(...)`. If M9.15 had produced only a richer record, M9.14 would never
have seen it; if M9.15 had written both independently, there would be two copies of one number.
Instead `TaxAssessment.to_determination()` is the **single** derivation, written only by
`FinalizeTaxAssessment`, so the thin record M9.5 and M9.14 already read is a *projection* of one
truth (§A2.5). **M9.14 needed no change to start presenting tax** — it re-reads the determination on
its next revision.

**Unresolved M9.14 limitations:** limitation 3 ("tax determination is consumed, never triggered —
owner: M9.15") is **resolved by this milestone existing**, without touching M9.14. Limitations 1
(M9.8 attribution ceiling) and 2 (no supplier registry) are **inherited unchanged**: the supplier's
origin arrives on the command for exactly the reason M9.14's supplier party does.

**Amendments to previous milestones:** none to any domain. Two **test** assertions were sharpened,
both strictly strengthened, both recorded in §10.

---

## 3. Components

| Layer | Module | Lines |
|---|---|---|
| Foundation | `commercial_tax.py` | 1 129 |
| Foundation | `tax_rules.py` | 1 175 |
| Foundation | `tax_factories.py` | 629 |
| Application | `tax_engine.py` | 1 940 |
| Persistence | `tax_models.py` · `tax_repositories.py` · `v0025_billing_tax.py` | 234 · 575 · 47 |
| Customer API | `tax_schemas.py` · `routers/tax_engine.py` | 616 · 832 |
| Studio | `studio/taxation.html` | 623 |
| Tests | `test_billing_tax_engine.py` | 1 962 |

---

## 4. The four structural invariants

### 4.1 No rate is written in Python — anywhere

Asserted by an AST scan across all eight modules: **no integer literal may be passed to
`rate_basis_points=`**, and no rate-shaped constant name may exist. The scan excludes
`model_config = ConfigDict(json_schema_extra=…)` *by structure*, because an OpenAPI example
documents the wire shape and is never read as a rate — precise rather than merely strict, so a
literal anywhere the code could reach still fails.

Migration 0025 **seeds nothing**. A fresh installation has no jurisdiction, no classification, and
no rate, and determines no tax at all until an operator declares one. A migration that inserted a
percentage would make this plane the owner of a number it has no authority over — and that number
would still be there when the jurisdiction changed it.

`NullTaxRateProvider` is the default and offers **nothing**, deliberately not "0%": a silent zero is
indistinguishable from a correctly zero-rated export, and those two are the most expensive pair in
the domain to confuse.

### 4.2 A determination is self-explaining

`TaxAssessment` carries the basis it was made from, the place-of-supply rule applied, the nexus and
registration relied on, the exemption or override that changed it, the **provenance of every rate**
(source, version, reference, effective window, retrieval instant), the rounding policy *and scope*,
the version, and a digest over the lot.

`TaxExplainer` answers "why was this applied?" **from the assessment alone** — it holds no provider
and takes none, so the guarantee is structural rather than a promise. A test scans its source for
`provider`, `quotes(`, and `TaxRateProvider` and finds none. That matters because the answer must
still be available in five years, when a provider's answer would be different and the invoice's
would not.

`is_sourced` and `is_explainable` are honest about the gap: a standard-rated supply for which no
source offered a rate is **not** sourced, and cannot be finalized.

### 4.3 A determination is content-addressed and never edited

The id hashes the **basis** — the immutable inputs — so re-determining identical facts is one
assessment and the second call emits `billing.tax.assessment_unchanged`. A *changed* input derives a
different id and is structurally incapable of overwriting what it revises.

The lifecycle has **no edge out of `final`**. A correction is a *new* assessment carrying
`supersedes_assessment_id`, and the superseded one stays readable — because the question an audit
asks is not "what is the tax?" but "what did you charge, and what did you change it to?" An
override is therefore not an edit either: it travels into a new determination and keeps the
conclusion it replaced.

### 4.4 Rates are effective-dated evidence, never current state

`DeclaredTaxRate` rows are append-only with an effective window. `TaxRateRepository` has no `update`;
`retire` sets a timestamp and amends nothing. Superseding a rate writes a **new row beside** the one
it replaces, because an issued invoice must stay explainable against the rate that was in force when
it was issued (TX2). `for_jurisdiction` returns retired rows too — an audit asks what was in force on
a date, not what is in force now.

---

## 5. Determination, step by step

```
basis  →  place of supply  →  nexus  →  treatment  →  rates (provider)  →  calculate  →  round
```

**Place of supply** is resolved from the *shape* of the supply — same region, same country, or
across a border — plus declared configuration. There is no `if country == "IN"` anywhere: adding a
market is a jurisdiction row (TX5), asserted by scanning the resolver for country literals. The
India GST split is the one regime-specific output and it is *recorded* as evidence a return depends
on.

**Treatment precedence is declared, not emergent**, and every adjacent pair was chosen:

1. an attributed **override** — a human decision beats every rule;
2. **cross-border** → zero-rated export, or reverse charge for a registered B2B customer. *Ahead of
   nexus*, because an export is zero-rated by the **supplier's** regime;
3. **no nexus** (domestic) → out of scope — "not our tax", not "our tax at nothing";
4. a live **exemption** → its kind's treatment;
5. the **classification's** default;
6. standard.

**Calculation is integer throughout.** `Money.scaled(rate, 10_000, rounding)` is the whole
primitive. `BASIS_POINT_SCALE` is the only number the package names, and it is a unit rather than a
rate.

**Rounding scope is recorded**, because a hundred lines rounded individually do not sum to their
total rounded once, and different authorities require different ones. `PER_DOCUMENT` redistributes
with `Money.allocate`, so the parts still sum exactly to the whole.

---

## 6. Persistence

Seven `cp_billing_*` tables via migration **0025**. Three are append-only by construction: declared
rates, assessments, and the audit trail. `retire` and `mark_state` are the two narrow exceptions and
neither amends a figure.

Idempotent `append` is defended three deep as in M9.11–M9.14: a derived primary key, a
read-before-write, and a `SAVEPOINT` whose `IntegrityError` becomes a read of the winner.

`current_for_subject` carries the one query-level rule, declared rather than emergent: **a finalized
determination stands over any provisional one.** Ordering by recency alone would let a stale draft
shadow the figure a customer actually paid.

---

## 7. API and Studio

24 paths at `/taxation`. **No DELETE, PUT, or PATCH anywhere.** M9.5's `/taxes` profile and
determination surfaces are untouched and still answer — the determination being the record this
milestone projects onto.

The **Studio** (11 screens) contains **no rate** and computes **no tax** — both asserted by scanning
the script body. `rate()` prints basis points as the server expressed them; dividing by 100 there
would be the client doing tax arithmetic.

---

## 8. Testing — 152 tests

18 sections plus 19 named determination demonstrations.

### Defects found and fixed

**1. CGST and SGST collided on the derived rate id** — the canonical Indian case. `derive_rate_id`
omitted the rate's *name*, so two separately-named components of one `tax_type`, in one
jurisdiction, over one window derived the same id; the second declaration was swallowed as a
duplicate and **every intra-state invoice would have been taxed at 9% instead of 18%.** Found by the
smoke, which declared both and got one back.

**2. Inclusive pricing extracted per component instead of against the combined rate.** ₹1,000 at
CGST 9% + SGST 9% extracted ₹82.56 twice — ₹165.12 against a correct ₹152.54 — and the parts stopped
summing back to the gross. **Every tax-inclusive sale would have over-collected.** Fixed by
extracting once against the combined rate and allocating by rate weight with `Money.allocate`.

**3. Every export was reported as `out_of_scope`.** The nexus test ran before the cross-border test,
so a missing registration in the buyer's country made an export out of scope rather than zero-rated
— losing the zero-rating from the evidence a return is filed from. Precedence reordered and the
reasoning recorded on the resolver.

**4. `is_sourced` returned `True` when no rate was found at all.** `all()` over an empty tuple is
`True`, so a standard-rated supply for which no provider offered a rate reported itself perfectly
sourced while charging nothing — precisely the indistinguishability `NullTaxRateProvider` exists to
prevent. Fixed with `expects_rates`; such a determination now cannot be finalized.

**5. A factory `_emit` kwarg collided with its own positional `subject_id`.**

### Assertions sharpened — two, both strengthened

* `test_controllers_never_catch_exceptions` (M9.5) scanned for the substring `try:`. A parameter
  named `country` contains it. Moved to an AST walk for `ast.Try`/`ast.ExceptHandler` — structure
  can tell a handler from a word; a substring cannot.
* `test_every_invoice_path_is_documented_and_tagged` and the invoice surface-size assertion (M9.14)
  scanned for `"/documents" in path`. M9.15 mounts a *tax view* of a document at
  `/taxation/documents/…`. Scoped to the exact prefix, which is what they always meant.

No domain was changed to satisfy a test.

---

## 9. The determination demonstrations

| # | Demonstration |
|---|---|
| 1 | A fresh installation determines no tax |
| 2 | A determination with no rate source is loud, not zero |
| 3 | An unsourced determination cannot be finalized |
| 4 | Declared rates carry the authority that set them |
| 5 | Two components of one tax type are two rates |
| 6 | An intra-state supply splits CGST and SGST |
| 7 | Determining the same facts twice is one assessment |
| 8 | Every component names where its rate came from |
| 9 | The explanation answers *why* from stored evidence |
| 10 | Finalizing publishes the projection M9.14 already reads |
| 11 | The issued invoice carries the tax |
| 12 | A finalized determination is never re-determined |
| 13 | An override is a new determination, not an edit |
| 14 | A finalized determination stands over a provisional one |
| 15 | A preview persists nothing |
| 16 | A retired rate stays readable and stops applying |
| 17 | An exemption zeroes the tax and records why |
| 18 | The determination reconciles against the document |
| 19 | Tax writes to no other domain |

---

## 10. Compliance

| Check | Result |
|---|---|
| **No rate written in Python; nothing seeded** | ✅ AST scan + migration scan |
| Prices nothing / rates nothing / meters nothing | ✅ AST-asserted separately |
| Posts no ledger entry; computes no balance | ✅ AST-asserted |
| Issues no invoice; moves no money | ✅ AST-asserted |
| Evaluates no entitlement; authorizes nothing | ✅ AST-asserted |
| **Writes to no other domain** | ✅ write-target scan + before/after equality across six domains |
| Determination reproducible; finalized never edited | ✅ derived id + FSM |
| Evidence self-explaining without a provider | ✅ structural (the explainer holds none) |
| Rates effective-dated and append-only | ✅ repository surface |
| Foundation pure; controllers transport-only; Studio computes nothing | ✅ |
| Migration additive; M9.2 tax tables untouched; bootstrap clean | ✅ |
| Repository-wide suite | ✅ **3601 / 3601** |

**ADR obligations:** TX1 ✅ (adapter port, no owned rate) · TX2 ✅ (immutable determination with
jurisdiction, rate, rule, customer status, adapter identity, timestamp) · TX3 ✅ (profile referenced,
organization never written) · TX4 ✅ (reporting reads stored evidence; there is no recompute method)
· TX5 ✅ (a new jurisdiction is a row) · TX6 ✅ (withholding carried and explained as an obligation).

---

## 11. Surface changes

| Surface | Before | After |
|---|---|---|
| Foundation exports | 714 | **788** |
| Application exports | 552 | **620** |
| `billing.*` events | 178 | **192** (14 new; `tax.profile_updated` and `tax.determined` reused) |
| Diagnostic codes | 177 | **194** (17 new; 3 M9.2 tax codes reused) |
| Migration chain | …0024 | **…0025** |
| API paths | 243 | **267** |
| Repository tests | 3 449 | **3 601** |

---

## 12. Limitations

1. **No catalog → classification mapping.** The classification arrives on the command, per document
   or per line. M9.6 models no tax class on a product, and adding one here would be the cross-domain
   coupling ADR-0023 forbids. **Owner: a catalog milestone.**
2. **No supplier registry** (inherited from M9.14 §13.2). Origin and registration arrive on the
   command, because who VoxVoice contracts as is published organization information.
3. **The M9.8 attribution ceiling now blocks six milestones.** A dependency, not a limitation.
4. **The shipped provider serves declared rates only.** An external tax engine is a different
   `TaxRateProvider`; no such adapter ships. Deliberate — TX1 — and named so it is not mistaken for a
   working integration.
5. **Rate resolution is per jurisdiction, not compounded across levels.** A US state + county + city
   supply needs three jurisdictions and three declarations; the resolver does not walk
   `parent_code` to accumulate them. `JurisdictionLevel` and `parent_code` exist for it.
6. **Residency corroboration is recorded, not enforced.** `is_corroborated` reports whether two
   independent items of evidence agree; nothing refuses a B2C EU digital supply that has only one.
   **Owner: an EU VAT compliance milestone.**
7. **Withholding is carried, never computed.** `withholding_minor` is on the assessment and in the
   explanation; no rule derives it.
8. **Search filters in Python over a bounded fetch** (2 000 assessments).
9. **No periodic tax return or filing export.** The evidence is complete enough to build one; the
   report itself is out of scope.
10. **29 pre-existing qualified OpenAPI schema names** inherited from M9.5/M9.7–M9.11. M9.15 adds
    none.

---

## 13. Recommendation

**Proceed to M9.16 (Payments)** — the next consumer of an issued, taxed invoice.

Three items should be scheduled alongside: **the M9.8 attribution ceiling**, now blocking six
milestones; **ratify the AR-subledger decision into ADR-0023** (inherited from M9.13, now depended
on by both a document and a determination); and **catalog → tax classification**, the one input this
milestone must be told rather than able to look up.
