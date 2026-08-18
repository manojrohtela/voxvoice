# M9.22 — Billing Compliance & Regulatory Reporting Engine

**Status:** implemented · **Milestone:** M9.22 · **Decision log:** DL-0133
**Amends:** nothing. ADR-0023 **PY5** is unchanged and now holds at a fourth altitude.

---

## The question this milestone exists to answer

Nine milestones own financial facts. **None of them can tell a regulator what happened**, because a
filing is not any one domain's view: it spans the Ledger, the Invoice, Tax, Payments, Revenue
Recognition and the integrity record over all of them, for a period somebody outside Billing
defined, under a rule that changes on its own schedule.

> **What financial and operational facts must be reported, for which reporting period, under which
> reporting definition, from which authoritative sources, and what evidence supports the report?**

Under one constraint that shapes everything else:

> **A report is derived evidence. It is not financial truth.**

M9.22 may produce a `RegulatoryReport`. It may never produce a `LedgerEntry`, a `TaxDetermination`,
a `CommercialInvoice`, a `Payment` or a `RecognitionEntry` — and it imports no repository that could
write one.

---

## The audit

No reporting, filing or submission aggregate existed. `ProviderReport` (M9.2 payments) and
`StalenessReport` (M9.10 entitlements) are unrelated domain-local shapes; `GetUsageReconciliationReport`
(M9.11) is a metering query. None conflict.

**Four period concepts already exist and are genuinely different**, which §9 anticipated:

| Concept | Owner | Meaning |
|---|---|---|
| `BillingPeriod` | M9.2 | a half-open `[start, end)` window of seconds |
| `AccountingPeriod` | M9.13 | one **account's** ledger close, with a `close_digest` |
| `PeriodGranularity` | M9.18 | the calendar step a recognition schedule is cut on |
| tax `period_key` | M9.15 | the period a jurisdiction assesses over |

M9.22 adds a fifth — `ReportingPeriod` — that **names which of these a definition consumes**
(`PeriodBasis`) rather than redefining any of them. `BillingPeriod` is *reused* as the window value
type (§A2.5); a fifth "range of seconds" would be the duplication §2 forbids.

**What is reused rather than re-declared:** `Money`/`Currency` (M9.2), `EvidenceDomain` and
`EvidenceSourceType` (M9.21), and — most importantly — **M9.21's single canonicalizer**. A second
serialization here would mean the same filing had two digests, which is the failure M9.21 wrote its
own rule against.

**One earlier domain was extended, and it is reported rather than silent (§2):** M9.21's
`EvidenceDomain` gained `INVESTIGATION`, and `EvidenceSourceType` gained `INVESTIGATION_CASE` and
`AUDIT_PACKAGE`, so a filing can *reference* the investigation that examined its period (§20). Purely
additive: no existing member changed, and M9.21 emits neither value itself.

---

## Ownership

| Question | Owner |
|---|---|
| what is a balance, an invoice total, a tax, a payment, recognized revenue? | **M9.13–M9.18** |
| do two financial facts agree? | **M9.19** |
| which disagreements are the same problem? | **M9.20** |
| what did a human find out, on what evidence? | **M9.21** |
| what must be reported, for which period, from which sources? | **M9.22** |

**M9.22 has no outward command at all.** M9.20 dispatched M9.19's repair request; M9.21 and M9.22
dispatch nothing, and `dispatch` is not injected. A report is derived evidence — it cannot ask a
domain to change so the filing comes out better.

---

## Source-of-truth map

Every field names its authoritative source. **There is no `source = "computed"` escape**:
`ReportFieldSpec` binds each field to a domain, a source type and a transformation drawn from a
closed set of seven, so *where did this number come from* is answerable from the definition alone.

| Domain | Read through | Period-scoped? |
|---|---|---|
| Ledger (M9.13) | `entries.for_period(org, account, period)` | ✅ |
| Invoice (M9.14) | `documents.for_period(org, account, period)`, issued only | ✅ |
| Tax (M9.15) | `assessments.for_account(org, account)` | ⚠️ account-bounded |
| Payment (M9.16) | `intents.for_account(org, account)` | ⚠️ account-bounded |
| Revenue (M9.18) | — | ❌ reports the gap |
| Reconciliation (M9.19) | `findings.open_findings(org)` | operational queue read |
| Integrity (M9.20) | `incidents.live(org)` | operational queue read |

**The headline architectural finding**: *every* period-scoped read Billing publishes is keyed by
`(org, account, period)`, because an accounting period is a property of an **account**. An
organization-wide filing therefore has no authoritative period read to consume. M9.22 does not
aggregate across accounts on a regulator's behalf and does not scan the organization in Python
(§34) — it **reports the gap**, and `ReportScope` makes the distinction explicit on every filing.

M9.18 publishes no period-scoped read at all, so a recognition field resolves `UNAVAILABLE` with a
reason naming the missing capability. That is §2's instruction taken literally: *if a required
source cannot provide the required fact, record the limitation; do not invent a value.*

---

## Definitions are versioned and immutable

`ReportDefinition` is `(code, version)`, identity-derived, and immutable once published.
Re-publishing a version with **different content** is refused — every filing already bound to it
would silently change meaning. `definition_digest` lets a re-publication be *proven* identical
rather than assumed.

A filing stores `definition_version`, and nothing reads `latest()` to interpret one. The single
`latest_version()` call in the codebase lives in `_resolve_definition`, is reachable only when
opening a new run, and a test asserts that no other function can reach it.

---

## Inputs are pinned, and identity follows them

```
report id = f(org, definition, period, scope, manifest_digest, corrects)
```

`ReportInputManifest` records every source **at the revision it was read**, and `source_digest` is a
canonical digest over all of them. Three consequences, which are §7's guarantees stated as an
identity:

* the same definition over the same period with the same inputs is **one** report — a retry files
  nothing twice;
* any input moving produces a **different** id, so corrected figures land *beside* what was filed;
* two organizations can never collide, because the org is inside the key.

A finalized `ReportSnapshot` is content-addressed on a representation built from **pinned state
only** — the version the run used, the period, the manifest digest, the values as computed. A test
demonstrates that a filing's digest and representation are byte-identical after the sources move.

---

## Missing evidence never becomes zero

The rule this milestone exists to protect, enforced in four places independently:

1. `ReportingTransformationService` returns `ValueStatus.SOURCE_UNAVAILABLE` with **no amount, no
   number and no text** — not `0`, not `""`;
2. `ReportCompletenessService` returns `BLOCKED`, distinct from `INCOMPLETE`;
3. `ReportExceptionService` raises a **named** exception (`MISSING_TAX_EVIDENCE`,
   `MISSING_PAYMENT_EVIDENCE`, `SOURCE_UNAVAILABLE`…) — there is no generic "report failed", and a
   test asserts no `ReportExceptionKind` member is called `FAILED`, `ERROR`, `UNKNOWN` or `OTHER`;
4. the wire and the Studio carry `amount_minor: null` and render the status, never a figure.

`SourceStatus` distinguishes `RESOLVED`, `EMPTY` and `UNAVAILABLE`. **`EMPTY` is a legitimate
zero** — the domain answered and had nothing, which is a filing. `UNAVAILABLE` means we could not
look, which is a gap. A regulator cannot tell those apart from a `0`, so the system keeps them
apart.

---

## Currency is never combined implicitly

`ReportTransformation` has seven members and **exactly one touches money**: `SUM_SAME_CURRENCY`,
which adds integer minor units of facts that already share a currency and **refuses a mixed-currency
input rather than converting it**. There is no `AVERAGE`, no `ALLOCATE`, no `APPLY_RATE`, no
`NET_OF` and no `SUM_CONVERTED`.

`CurrencyBasis` has no `CONVERTED` member. There is no FX rate anywhere in this milestone — not from
a provider, not from a table, not from a constant — because currency conversion is a rule nobody has
approved here, and inventing one would put a number in front of a regulator that no domain owns.

An AST scan asserts exactly **one** `sum(` call in the whole Foundation, and none of
`Money.times`/`allocate`/`percent`/`apply_rate`.

---

## Finalization cannot skip its checks

```
DRAFT → PREPARING → VALIDATING ─pass→ READY → FINALIZED
                          └─fail→ EXCEPTIONS_RAISED ─┐
                                       ┌─────────────┘
                                  (re-prepare)
```

**`FINALIZED` is reachable only from `READY`, and `READY` only from a validation that passed.** No
operator confidence, override flag or retry reaches the terminal state around validation — a test
enumerates every edge to prove it.

`ReportGuardService.guard_finalizable` re-reads the verdict, the exceptions and the completeness
inside the transaction and refuses on any of three grounds. **Acknowledging an exception does not
unblock a filing**: it records that somebody saw the problem, which is a different fact from the
problem being gone.

`FINALIZED` has no outgoing edge. A correction opens a **new report beside the original**; the
original keeps every figure, its digest, its snapshot and its finalized state, gaining only a
back-reference. A correction whose inputs have not moved and whose definition version has not
changed is refused — it would compute the identity of a report that already exists.

---

## Validation is not reconciliation

Every check asks about the **report**: is the definition version pinned, does the period basis
match, is the period closed, is the manifest the one this report's identity derives from, did every
required source resolve, is every mandatory field present, is the currency basis satisfied, is the
integrity record settled, are there blocking exceptions.

None compares two financial domains against each other. The integrity check **reads M9.19/M9.20's
own verdict** — and `INDETERMINATE` is a real answer, so *no report data* is never inferred to mean
*no integrity issue*.

---

## Layers

| Layer | Artifact |
|---|---|
| Foundation | `commercial_reporting.py`, `reporting_rules.py`, `reporting_factories.py` |
| Application | `reporting_engine.py` — 11 commands, 14 queries |
| Persistence | `reporting_models.py`, `reporting_repositories.py`, migration **0032** (10 tables) |
| Customer API | `reporting_schemas.py`, `routers/reporting_engine.py` — **21 paths** at `/reports` |
| Studio | `reports.html` — 12 screens, self-contained (ADR-0021 Consumer) |

`cp_billing_report_source` is split from the manifest deliberately: §34 requires the source list to
paginate, and three hundred pinned sources inside one JSON blob cannot. No DELETE, PUT or PATCH
exists anywhere on the surface.

**Submission packages are prepared, never sent.** `submitted_at` and `submission_reference` are
declared and always empty; there is no network client, regulator endpoint or credential in this
milestone, and a test asserts every assignment to those fields is a passthrough.

---

## Limitations

1. **I implemented and self-reviewed this milestone.** A reporting engine is a poor candidate for
   that: a filing that is subtly wrong looks exactly like one that is right until a regulator says
   otherwise.
2. **Organization-wide filings cannot resolve any source.** Every period-scoped read Billing
   publishes is account-keyed, so an org-wide report comes back `BLOCKED` with named gaps. That is
   honest, and it is also the single biggest functional gap in the milestone: the common regulatory
   case of a per-entity filing works; a consolidated one does not. Closing it requires org-wide
   period reads from M9.13/M9.14, which belong to those milestones.
3. **Revenue recognition contributes nothing.** M9.18 publishes no period-scoped read, so every
   recognition field resolves `UNAVAILABLE`.
4. **Tax and Payment are account-bounded, not period-bounded.** Their readers return every
   assessment or intent for the account, and the transformation applies over all of them — a
   period-scoped read would be more precise, and its absence is why the period filter is not
   applied at the database for those two domains.
5. **`SUM_SAME_CURRENCY` is the only aggregation.** A definition needing a net-of, a proportion or
   a weighted figure cannot express it, and adding one means adding a versioned transformation
   here rather than a formula in a service — by design, but it does mean the closed set will need
   to grow.
6. **No report scheduler.** Whoever decides that a filing is due lives outside this plane.
7. **Submission is not implemented.** §24 defers it, and the package is where a future adapter
   would start.

---

## What this does not change

Every earlier milestone keeps its ownership. ADR-0023 is **not amended** — PY5 is unchanged and now
holds at a fourth altitude: M9.19 made *a divergence is never auto-resolved* structural for one
finding, M9.20 for a group, M9.21 for the investigation of one, and M9.22 for the **filing** — a
report reaches `finalized` only from a validation that passed, and missing evidence is never filed
as zero.

Every M9.2–M9.21 table is untouched.
