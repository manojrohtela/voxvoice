# Revenue Recognition & Deferred Revenue Engine (M9.18)

**Status:** 🟢 Implemented (M9.18) · **Date:** 2026-08-12
**Authority:** Engineering Constitution v2.0 · ADR-0019 · ADR-0021 · ADR-0023 (RS5, **RS6**) · ADR-0031
**Predecessors:** M9.2–M9.17 (all frozen; none reshaped)

---

## 1. What this milestone is

M9.13 records financial facts. M9.14 states what was billed. M9.16 moves money. M9.17 decides
whether to chase it. This one answers the question a statutory filing reports:

> *Was this amount earned when we said it was, on evidence that has not changed since, under rules
> that were in force at the time — and can we prove it after the price book, the tax rates, the
> subscription and the policy have all been rewritten?*

Stated as a boundary:

> **Invoice states what was billed. Recognition decides when it is earned. Ledger records the fact.**

**Evidence:** 10 commands / 16 queries; 5 tables via migration **0028**; 24 paths at `/revenue`
(346 total); 10 Studio screens; **110 tests**; **3969/3969 repository-wide**; ruff clean.

**Two defects were found and fixed during implementation** (§17), one of which reported deferred
revenue *greater than the invoice*.

---

## 2. Mandatory audit

| Artifact | Owner | Current meaning | M9.18 action | Reason |
|---|---|---|---|---|
| `RecognitionState` (deferred/recognized/not_applicable) | M9.2 `ledger.py` | A **posture tag on a ledger entry** — ADR-0023 RS6's projection | **Reused unchanged** | Correct vocabulary. A second enum would be a second answer. `commercial_revenue.py` imports it and declares none. |
| `PostingOutcome.DEFERRED` | M9.13 | **Period deferral** — the period closed, the entry landed in the next open one | **Never conflated** | Two meanings of one English word already existed. Recognition stores M9.13's *disposition*, not its outcome. |
| `LateEntryDisposition` | M9.13 | Where a late fact lands | **Reused; no second close semantics** | The mandate's rule, and M9.13 already had the answer. |
| `AccountingPeriod` / `AccountingPeriodState` | M9.13 | One-way close, no edge out of `CLOSED` | **Consumed; no second period model** | Asserted by a scan over all eight files. |
| `PostingSource` | M9.13 | Closed cause vocabulary | **Additively extended: `REVENUE_RECOGNITION`** | `MANUAL` would make every recognized period read as operator-entered in the one report where that is the question. Same precedent as M9.16 adding `PAYMENT`/`REFUND` (DL-0126). |
| `PostJournalEntry` | M9.13 | The posting command | **Additively extended with `recognition`** | Defaults to previous behaviour; every existing caller unchanged. |
| `InvoiceSnapshot` + digest | M9.14 | Immutable, self-contained, content-addressed | **Consumed as the recognition basis** | The authoritative commercial document (ADR-0023 IN5). |
| `Money.allocate` | M9.2 | Largest-remainder exact split | **Reused — the only division of money** | Conservation becomes a property of one function every path goes through. |
| `BillingPeriod` | M9.2 `domain.py` | Half-open `[start, end)` | **Reused — interval semantics** | |
| `unearned` (entitlement), "revenue recognition" (subscriptions) | M9.10 / M9.9 | Unrelated prose | **Untouched** | |

**Conclusion:** M9.2's `RecognitionState` *is* revenue-recognition vocabulary and is correct for
what it is — a posture. What it cannot express is *when* the posture changes, on what basis, in how
many pieces. The schedule lands beside it, as M9.13–M9.17's aggregates did beside theirs (§A12).

## 3–4. Ownership and the accounting model decision

**Option 2 — recognition scheduling plus posting. Explicitly not option 3.**

There is **no deferred-revenue subledger and no stored balance**. Deferred revenue is
`basis amount − recognized − adjusted`, derived from the entries on every read. M9.13 remains the
sole owner of financial recording; recognition reaches it only through `PostJournalEntry`, and an
architecture test scans every persistence call in the Application to prove it writes to five tables,
all its own.

That choice keeps ADR-0023 **RS6** literally true: *recognition state is a projection over the
ledger, not a second bookkeeping system*. ADR-0023 is **not** amended; the `PostingSource` member is
an additive extension recorded in DL-0129, exactly as M9.16's was in DL-0126.

## 5. Source of recognition

The basis is copied from an **issued** `InvoiceSnapshot`, resolved `by_digest` — the digest the
document pinned at issue, not whichever revision is newest. A draft document is refused: only an
issued one states an amount that can be recognized.

The recognizable figure is `total − tax_total`. **Not `subtotal`** — that figure is pre-discount, and
recognizing it would earn revenue the customer was never charged. Tax is a liability collected for
an authority and is never revenue; M9.15 determined it and M9.14 presented it, and that one
subtraction between two figures they already agreed on is the only arithmetic in the module.

Nothing here asks Pricing to reprice, Rating to rerate, or Tax to recompute — asserted by an
identifier scan across all eight files.

## 6. Domain model

`RecognitionPolicy` (immutable version) · `RecognitionBasis` (the evidence) · `RevenueSchedule` ·
`RecognitionEntry` (one dated slice) · `RecognitionAdjustment` (a correction as a new fact).

### The basis digest — the reproducibility contract

Hashed over **every** input that can change the answer: org, document, **revision**, snapshot
digest, account, amount, currency, service window, method, policy version, remainder rule,
granularity. The schedule id *is* that digest. Three consequences:

* two workers deriving the same recognition compute one id and one loses the insert;
* re-deriving with unchanged inputs writes nothing;
* **changing any input produces a different schedule rather than a mutated one.**

No timestamp, no run id, no actor — a volatile value would make every re-derivation a new schedule
and destroy the idempotency the digest exists to provide. Asserted by a source scan.

## 7. Lifecycle

`DRAFT · SCHEDULED · PARTIALLY_RECOGNIZED · FULLY_RECOGNIZED · CANCELLED · SUPERSEDED`, every state
asserted reachable by walking the machine. Refusals:

* **no edge out of `FULLY_RECOGNIZED`** — the transition that would let a policy edit restate a
  filed number;
* **`PARTIALLY_RECOGNIZED` cannot be cancelled** — once revenue is earned the way back is a
  reversal, a new fact with provenance, never an erasure;
* **`PARTIALLY_RECOGNIZED → SUPERSEDED` exists** — a document revised mid-term does not undo what
  was earned;
* entries: **no edge out of `REVERSED`**, none out of `POSTED` back to `RECOGNIZED`, and
  `UNPOSTED → POSTED` exists because that is the repair path.

## 8. Schedule and policy versioning

`RecognitionPolicyRepository` has no `update` and no `delete`. `latest()` appears **exactly once** in
the Application — on the path that binds a *new* basis. A historical schedule reads the version
pinned in its own basis. The mandated regression passes: build under v1, publish v2, re-derive — v1
is byte-identical and the two are different rows.

## 9. Deferred revenue

Not a state, not a column, not a stored balance. `RecognitionPositionService` computes
`recognized + adjusted + deferred == total` from the facts, every time. The schedule row holds
`entries_recognized` — a **count**.

## 10. Corrections and reversals

A recognized entry is never edited. A reversal is a new `RecognitionAdjustment` carrying the entry,
the reason, the document **and revision**, the policy version, the effective instant, the signed
amount and the ledger entry that recorded it. Its id is derived from
`(org, entry, kind, request key)`, so two concurrent requests under one key are one correction.

## 11. Ledger boundary and closed periods

Recognition posts through `PostJournalEntry` with `source=REVENUE_RECOGNITION` and
`recognition=RECOGNIZED`. The Ledger derives the entry id from `(org, account, source, source_id,
entry_type)`, so a replayed posting is a recorded duplicate rather than a second financial fact.

**Closed periods are M9.13's rule, reused.** Recognition posts; the Ledger applies
`LateEntryDisposition` and answers `in_period`, `next_open_period` or `rejected_no_open_period`;
whichever it was is recorded on the entry.

When the Ledger *refuses*, the recognition decision survives as `UNPOSTED` and
`billing.revenue.unposted` is published. `PostRecognitionToLedger` is the repair. Only
`BillingError` — a considered refusal — is caught; **a bug propagates**. That is the M9.16-R1 P-6
correction, applied here from the start.

## 12–13. Persistence and concurrency

Five tables, migration 0028, nothing seeded. Two race-critical writes:
`SqlRevenueScheduleRepository.create` (derived PK) and **`SqlRecognitionEntryRepository.recognize`**
— `UPDATE ... WHERE id = ? AND state = 'pending'`, the double-recognition defence. The loser learns
*before* it posts anything, because two ledger entries for one month of revenue cannot be undone by
noticing later.

Proven with **two independent Applications over two independent connections** to one file-backed
database: duplicate schedule creation, duplicate period recognition, stale-worker advance against a
superseded schedule, recognition against a superseded schedule, and restart durability.

`ScheduleStale` lives in `foundation/errors.py`-adjacent `revenue_rules.py`, not persistence — the
Application must react to it and may not import persistence. Same reason as `RefundSequenceConflict`
(M9.16-R1) and `CaseStale` (M9.17).

## 14–15. API and Studio

24 paths at `/revenue`. **No DELETE anywhere** — financial history is corrected by a new fact.
`ScheduleRevenueBody` takes a document id and carries no amount field, asserted against the OpenAPI
schema. `deferred` appears only on the *position* and *summary* responses, never on a stored shape.

`revenue.html` is a Consumer (ADR-0021): no storage, no allocation, no subtraction, no vendor, and
buttons from `allowed_triggers`. It reports `recognized` and `posted` **separately**, because
reporting both as success would show clean books while the repair queue grew.

## 16. Testing

110 tests across fifteen sections, including a parametrised conservation sweep over nine schedule
shapes and three remainder rules, leap-year and month-boundary tests, the policy-restatement
regression, both directions of paid-is-not-earned, and the real-connection concurrency set.

## 17. Defects found and fixed

**D-1 — deferred revenue exceeded the invoice.**
*OLD:* reversing a recognized period reported `recognized=0, adjusted=−7500, deferred=97500` against
a `total=90000`. The correction was subtracted **twice**: the entry left `RECOGNIZED_ENTRY_STATES`
*and* the adjustment carried its own negative amount.
*ROOT CAUSE:* `REVERSED` was excluded from the recognized set. The revenue *was* recognized — a
ledger credit exists for it and a period may have closed over it — so the state change is not itself
the correction.
*FIX:* `RECOGNIZED_ENTRY_STATES` is **gross of corrections** and includes `REVERSED`; the adjustment
is the only correction. `recognized + adjusted + deferred == total` now holds by construction.
*PROOF:* the position after a reversal is `7500 / −7500 / 90000`, summing to 90000.
*REGRESSION:* `test_a_reversal_conserves_the_position`. **CLOSED.**

**D-2 — the reversal guard refused with the wrong reason.**
*OLD:* reversing an already-reversed entry reported "only a recognized amount can be reversed",
sending an operator to look for a recognition sitting right there.
*ROOT CAUSE:* guard order — the recognizability check ran before the already-reversed check, and
after D-1 a reversed entry is still a recognized one.
*FIX:* already-reversed is checked first.
*REGRESSION:* `test_reversing_twice_is_refused`. **CLOSED.**

### One architectural decision worth recording

`period_boundaries` initially used `datetime`, which two frozen assertions correctly rejected —
the Foundation bans the module outright so it cannot read an ambient clock. **The assertions were
not weakened.** The calendar is now pure integer arithmetic (`civil_from_days` / `days_from_civil`,
Hinnant), which needs no days-in-month table and no leap-year branch, and it is **proven against the
standard library across 33,000 days in both directions** in the test suite, where `datetime` is
importable. A calendar that is checked beats a ban that was relaxed.

## 18–19. Documentation and compliance

This document, `docs/INDEX.md`, `CHANGELOG.md`, **DL-0129**. ADR-0023 is not amended; the
`PostingSource` extension is recorded explicitly.

Foundation is pure — no SQL, no clock, no `datetime`, no transport, no provider, no scheduler — and
imports no payment or collection module at all. Integer-only money. Deny-by-default on every new
message. Billing absent from the runtime composition root.

## 20. Limitations

1. **No scheduler ships.** `/revenue/entries/due` is a read; production needs a caller outside this
   plane, so an unattended deployment recognizes nothing.
2. **One basis per document.** Line-level recognition (different service windows per invoice line)
   is modelled by `source_line_id` on the basis but no command derives per-line schedules yet.
3. **No multi-currency deferred summary.** Mixed currencies are skipped rather than converted —
   RS5 says conversion is presentation, never ledger.
4. `CORRECTION` and `WRITE_DOWN` adjustment kinds exist and are posted, but only `REVERSAL` has a
   derived amount; the other two take a caller-supplied figure.
5. Contract-level allocation across multiple performance obligations (ASC 606 step 4) is **not**
   implemented — one basis is one obligation.
6. The repair queue is a read; nothing escalates a long-unposted recognition.
7. Recognition does not automatically supersede when a credit note is issued against a document; an
   operator issues the supersession.

## 21. Final recommendation

M9.18 is complete against its gate.
