# Credit & Revenue Ledger (M9.13)

**Status:** 🟢 Implemented · **Epic:** 9 (Billing & Subscription Platform) · **Date:** 2026-08-07
**Authority:** Engineering Constitution v2 → ADR-0023 (Billing Sole Owner of Revenue) → ADR-0031
(Billing Plane Placement) → ADR-0019 / ADR-0021 → this document.

---

## 1. What this milestone is

Seven milestones produced facts. M9.6 said **what is sold**, M9.7 **what it costs**, M9.8 **who we
bill**, M9.9 **what was agreed**, M9.10 **what may be used**, M9.11 **what was consumed**, M9.12
**what should be charged**. None of them recorded money as owed.

This one does, and it is the first Billing domain whose errors cannot be corrected by re-running
anything. A duplicated usage record is corrected; a duplicated rating run is superseded. **A
duplicated financial posting has already changed a balance**, may already have reached a statement,
and is remedied only by a reversal, a credit note, and a conversation with a customer who has been
charged twice. Every invariant here is therefore made *structural* — the absence of a method, the
absence of an FSM edge, or a digest comparison — rather than procedural.

### Owns

Journal entries, account balances, accounting periods and their close, credits, credit
applications, adjustments, reversals, balance checkpoints, reconciliation, replay, inspection,
diagnostics, and append-only ledger history.

### Owns nothing else

It calculates no price, resolves no pricing, meters no usage, evaluates no entitlement, authorizes
no request, creates no invoice, executes no payment, and determines no tax. Six AST-asserted tests
enforce each of those separately.

---

## 2. What kind of ledger this is — and why that is declared

An **accounts-receivable subledger**, deliberately and explicitly. A `JournalEntry` is single-sided:
one account, one direction, one amount. It is not a general ledger and does not enforce ΣDr = ΣCr
across a balanced journal.

This is recorded here rather than left to be discovered, because a pre-implementation review of the
M9.2 substrate raised exactly this question. A general ledger requires a chart of accounts, contra
accounts, and a fiscal structure belonging to an accounting system VoxVoice does not own and should
not reimplement — ADR-0023 §5 says Billing owns *revenue*, not bookkeeping.

`TrialBalance` is the control report that makes this subledger reconcilable against whatever GL
consumes it: totals by entry type, **debits and credits kept separate** rather than netted, per
period, with `opening + movement == closing` asserted. Mapping those totals onto GL accounts is an
integration concern at the boundary, and keeping it at the boundary is what stops a chart of
accounts leaking into this plane.

---

## 3. Components

| Layer | Module | Lines |
|---|---|---|
| Foundation | `billing/foundation/commercial_ledger.py` | 921 |
| Foundation | `billing/foundation/ledger_rules.py` | 1,258 |
| Foundation | `billing/foundation/ledger_factories.py` | 517 |
| Application | `billing/application/ledger_engine.py` | 1,757 |
| Persistence | `billing/persistence/ledger_models.py` | 261 |
| Persistence | `billing/persistence/ledger_repositories.py` | 594 |
| Persistence | `billing/persistence/migrations/v0023_billing_ledger.py` | 47 |
| API | `billing/customer/ledger_schemas.py` | 495 |
| API | `billing/customer/routers/ledger_engine.py` | 876 |
| Studio | `billing/customer/studio/ledger.html` | 597 |
| Tests | `tests/test_billing_ledger_engine.py` | 1,934 |

Additive throughout. M9.2's `ledger.py` is unreshaped, `LedgerEntry` / `LedgerBalance` /
`CreditGrant` / `CreditMovement` / `CreditBalance` stay exactly as they are, and
`cp_billing_ledger_entry`, `cp_billing_credit_grant`, and `cp_billing_credit_movement` are
untouched. **`LedgerEntryType`, `LedgerDirection`, `RecognitionState`, and `CreditKind` are reused
unchanged** — two answers to "which way does this move the balance?" is the drift §A2.5 prevents.

---

## 4. The four structural invariants

### 4.1 Balances are derived, never stored

`AccountBalance.of()` is the only way to obtain one. **No stored aggregate in this domain carries a
balance field**, and a test enumerates them to prove it — `CommercialCredit` has no `remaining`,
`AccountingPeriod` has no live balance, and `CreditPosition` is computed from applications.

`BalanceCheckpoint` exists for scale and is a **cache with a proof**: it carries a digest, and
`verify()` recomputes from the entries it claims to summarise. Drift is therefore *detectable*,
which is the only reason having one at all is acceptable. The moment a checkpoint becomes
authoritative, the ledger stops being verifiable.

### 4.2 Posting is exactly-once by derived identity

An entry's id is a hash of `(org, account, source, source id, entry type)` — the cause, not a
counter. Two things follow structurally:

* a retry, a replay, or two concurrent workers compute the **same** id and collide;
* a correction derives a **different** id, so it is structurally incapable of overwriting the entry
  it corrects.

Two exclusions from the derivation are deliberate and separately tested. The **target period** is
excluded, so a charge deferred into February is the same fact as the same charge posted in January —
otherwise re-running the January job after a close would double-post. The **amount** is excluded, so
a corrected figure collides with the original rather than posting beside it.

Defended three deep in persistence: derived primary key, read-before-write, and an insert inside a
**`SAVEPOINT`** whose `IntegrityError` becomes a read of the winner. The savepoint is the part
usually missing — without it a caught integrity error poisons the session and the surrounding
transaction dies with the activity entry and the staged event.

### 4.3 History is never mutated

No update path on an entry, no delete state anywhere, no edge out of a closed period. `ReversalService`
refuses to reverse a reversal (that is a re-posting, and calling it a reversal makes the chain
unreadable) and refuses to reverse twice (which would credit the amount twice over).

A **reversal** says "this should not have been recorded"; an **adjustment** says "the amount has
moved by this much". An auditor needs to tell them apart, so they are separate fields on the entry
rather than one `correction`.

### 4.4 Entries are totally ordered per account

A monotonic sequence from a **durable counter**, not `max + 1` and not a timestamp. A derived
maximum hands a number back after a rollback, and two entries that have both occupied position 47
make "what was the balance immediately after entry 47?" a question with two answers.

A gap is possible and preferable to reuse: `BalanceService.sequence_gaps` surfaces it, and an
unexplained gap is something an audit can ask about. A silently reused number is not.

---

## 5. Lifecycles

```
Accounting period:   open ──begin_close──▶ closing ──close──▶ closed
                       └────────────────── close ───────────────▶

Credit:              active ──consume──▶ consumed
                            ──expire───▶ expired
                            ──revoke───▶ revoked
```

**No edge out of `closed`.** A closed period is what a statement was issued from, what an auditor
examined, and what a statutory filing reported. Reopening it is not a recovery procedure; it is how
a filed number silently changes. A late fact lands in the next open period and the entry records
that it did — `LateEntryDisposition.NEXT_OPEN_PERIOD` — so two months reconcile against each other
rather than appearing to disagree.

**No edge out of any terminal credit state.** Re-activating a spent, lapsed, or withdrawn credit
would make "was this credit ever honoured?" unanswerable — precisely the question a disputed balance
turns on. Extending a credit is a *new* grant. Revoking does **not** un-apply what was already spent.

Expiry is a **resolution-time property** as well as a state: `is_live_at(ts)` excludes a lapsed
credit immediately, rather than when a job gets round to closing it.

---

## 6. Posting: four outcomes, three of them successes

`PostingService.decide` returns a verdict and writes nothing. The Application acts on it. That
separation is what lets the *same* pure function serve the live path and replay.

| Outcome | Meaning |
|---|---|
| `POSTED` | a new financial fact |
| `DUPLICATE` | the cause was already recorded — **not an error**; the activity entry is the evidence exactly-once worked |
| `DEFERRED` | the period it belongs to is closed, so it lands in the next open one as an adjustment |
| `REJECTED` | no open period exists, or the request is malformed. The one failure |

Credits are applied **earliest-expiring first, then by id**. Declared rather than emergent: a
customer whose oldest credit lapsed unspent while a newer one was consumed has a legitimate
complaint, and an insertion-ordered answer would depend on how a repository sorted its rows.

---

## 7. Replay

`AccountingPeriodService.close_digest` hashes the sorted entry digests — order-independent by
construction, and excluding posting timestamps, which legitimately differ between an original
posting and a replay of it. Closing seals it; `LedgerReplayService` recomputes it using **the same
function the close used**, not a second implementation that could drift.

That is what makes reconstruction a *proof*. A test closes a period, replays it, and then posts a
later correction — and asserts the closed period's digest is byte-identical afterwards.

---

## 8. Persistence

Seven additive tables via migration **0023**; three append-only (entries, credit applications,
activity). `cp_billing_journal_entry.id` is the primary key **and** the derived identity — there is
no separate idempotency column, because a separate column can disagree with the key.

Every constraint arrives with its table, and for the ledger that ordering matters in a way it does
not elsewhere: a journal-entry table that existed for even one deployment without its exactly-once
key could accumulate two postings for one cause, and once one has reached a statement no later
migration can safely choose between them.

---

## 9. API and Studio

**24 paths at `/accounting`** — a separate prefix, so M9.5's `/ledger` and `/credits` still answer.
**No DELETE, no PUT, no PATCH anywhere**, and no route that reopens a period. Posting returns 201
for a new entry, 200 for a duplicate.

Eleven Studio screens. It never computes a balance — a test forbids `reduce(`, `+=`, `toFixed(`, and
arithmetic on `minor`. In a ledger, a client that adds up its own column will eventually disagree
with the books and be believed. Lifecycle buttons come from `allowed_triggers`, so a closed period
offers none.

---

## 10. Testing — 135 tests

Repository-wide: **3293 passing** (3158 prior), zero regressions. `ruff check` and `ruff format`
clean.

### 10.1 Defects found and fixed

Both were found by the end-to-end smoke, not by reasoning about the code.

**1. The deferral path was unreachable.** A `PostingRequest` never named the period it *belonged*
to, so the Application always handed `PostingService` the account's open period as the target and
nothing was ever deferred. The Foundation logic was correct and unit-tested; **nothing fed it**. A
late correction against a closed period would have posted silently into the open one with
`disposition=IN_PERIOD` — losing exactly the audit signal the disposition exists to carry. Fixed by
adding `target_period_key` to the request, set from the rating run's period for a charge and from
the corrected entry's period for a reversal or adjustment.

**2. Corrections shadowed their originals during reconciliation.** A reversal and an adjustment both
carry the original's `charge_id` — that lineage is the point — so keying the posted map on it
without filtering let a correction shadow the entry it corrected. **Every corrected charge read as
an `AMOUNT_MISMATCH`**, which would have made reconciliation useless on exactly the accounts most
worth reconciling. Fixed by mapping original postings only and explaining corrections by the entry
they correct.

**3. A schema name collided with M9.5's.** `GrantCreditBody` was declared in both
`customer/schemas.py` and `customer/ledger_schemas.py`, so FastAPI emitted two fully qualified names
into the **public** OpenAPI document. Renamed to `GrantCommercialCreditBody`. This is the same defect
class M9.12 hit with `MoneyResponse`, so a general assertion now exists — see §10.2.

**4. A `StrEnum` alias in the event vocabulary.** `COMMERCIAL_CREDIT_GRANTED` and
`COMMERCIAL_CREDIT_EXPIRED` were declared with values M9.2 already used. In a `StrEnum` that
silently creates an *alias*, not a member — two names for one fact and a miscounted vocabulary.
Caught during authoring; M9.2's `CREDIT_GRANTED`, `CREDIT_CONSUMED`, and `CREDIT_EXPIRED` are now
reused (§A2.5), and `test_every_event_value_is_unique` holds the line.

### 10.2 A new general assertion

`test_no_schema_name_is_qualified` checks that no OpenAPI schema name contains `__`. It is **scoped
to M9.13's own schemas**, and the reason is a finding rather than a convenience: the document already
carries **29 qualified names** inherited from M9.5/M9.7–M9.11 (entitlement_schemas 8, schemas 8,
metering_schemas 4, subscription_schemas 3, account_schemas 2, catalog_schemas 2, pricing_schemas 2).
Failing this suite on that debt would make M9.13 unmergeable for a problem it did not create;
ignoring it would let the count grow. It is recorded in §12 and the assertion holds at zero *new*
ones.

### 10.3 Frozen assertions extended additively

`test_billing_package_layout_is_the_declared_one` (+3 modules) ·
`test_rating_tables_are_created_by_the_migration_chain` (0022 pinned by position, not by being the
tail — the same extension M9.12 made to M9.11's).

---

## 11. The accounting demonstrations

| # | Invariant | Test |
|---|---|---|
| 1 | **A provisional rating run cannot become a financial fact** | `test_demo_1_a_provisional_rating_run_cannot_become_a_financial_fact` |
| 2 | Posting a finalized run records every charge | `test_demo_2_posting_a_finalized_run_records_every_charge` |
| 3 | The balance is derived and equals what was rated | `test_demo_3_the_balance_is_derived_and_equals_what_was_rated` |
| 4 | **Posting twice records nothing the second time** | `test_demo_4_posting_the_same_run_twice_records_nothing…` |
| 5 | A duplicate leaves evidence in the history | `test_demo_5_a_duplicate_leaves_evidence_in_the_history` |
| 6 | A reversal leaves the original exactly as it was | `test_demo_6_a_reversal_leaves_the_original_exactly_as_it_was` |
| 7 | An entry cannot be reversed twice | `test_demo_7_an_entry_cannot_be_reversed_twice` |
| 8 | **Closing seals a digest and a replay proves it** | `test_demo_8_closing_seals_a_digest_and_a_replay_proves_it` |
| 9 | A closed period accepts no further move | `test_demo_9_a_closed_period_accepts_no_further_lifecycle_move` |
| 10 | A late correction lands in the next open period | `test_demo_10_a_late_correction_lands_in_the_next_open_period` |
| 11 | **A closed period's digest survives a later posting** | `test_demo_11_a_closed_period_digest_survives_a_later_posting` |
| 12 | Determinism survives a restart with new seams | `test_demo_12_determinism_survives_a_restart_with_new_seams` |
| 13 | Credits are derived and spent earliest-expiring first | `test_demo_13_credits_are_derived_and_spent_earliest_expiring_first` |
| 14 | The trial balance reconciles end to end | `test_demo_14_the_trial_balance_reconciles_end_to_end` |
| 15 | Reconciliation against the rating run is clean | `test_demo_15_reconciliation_against_the_rating_run_is_clean` |
| 16 | An unposted charge is detected | `test_demo_16_an_unposted_charge_is_detected` |
| 17 | Entries are totally ordered by a durable counter | `test_demo_17_entries_are_totally_ordered_by_a_durable_counter` |
| 18 | A rebuilt checkpoint replaces rather than accumulates | `test_demo_18_a_rebuilt_checkpoint_replaces_rather_than_accumulates` |
| 19 | **The Ledger writes to no other domain** | `test_demo_19_the_ledger_writes_to_no_other_domain` |

Demonstration 11 is the strongest: a period is closed, its digest recorded, a later correction is
posted against one of its entries, and the closed period's replay digest is asserted **byte-identical
afterwards**. That is "corrections never mutate history", proven rather than described.

---

## 12. Compliance

| Check | Result |
|---|---|
| The Ledger calculates no price | ✅ AST-asserted, 8 modules |
| The Ledger rates nothing | ✅ AST-asserted |
| The Ledger meters nothing | ✅ AST-asserted |
| The Ledger evaluates no entitlement | ✅ AST-asserted |
| The Ledger authorizes nothing | ✅ AST-asserted |
| The Ledger creates no invoice, moves no money, determines no tax | ✅ AST-asserted |
| **The Ledger writes to no other domain** | ✅ AST write-target scan + before/after equality |
| Balances derived, never stored | ✅ no balance field on any aggregate; checkpoint verifiable |
| Append-only enforced by contract | ✅ no update/delete method exists to call |
| Corrections by new entry | ✅ reversal + adjustment, both refusing double-application |
| No deletion path anywhere | ✅ no state, no method, no route |
| Exactly-once posting | ✅ derived PK + read-before-write + SAVEPOINT + database demo |
| Deterministic replay | ✅ close digest, same function, proven after a later posting |
| Closed periods immutable | ✅ FSM has no edge out |
| Total ordering | ✅ durable counter, gaps surfaced |
| No import of any frozen Epic | ✅ import-graph assertion |
| Foundation pure and clock-free | ✅ asserted |
| Deny-by-default across all 26 messages | ✅ |
| Migration extended, alters nothing | ✅ M9.2 ledger tables untouched |
| M9.6–M9.12 boundaries hold | ✅ re-asserted |
| Billing absent from runtime composition root | ✅ ADR-0031 CR2 |
| Repository-wide suite | ✅ 3293/3293 |

**ADR compliance.** ADR-0023 RS1 (append-only), RS4 (revenue outlives the customer record), RS5
(integer minor units), RS6 (recognition is a Billing-owned projection) — followed. ADR-0031
IR1/IR3/D3/CR2 — followed. ADR-0021 (Studio = Consumer) — followed. **ADR-0023 RS2 (the ledger is
reproducible) is now demonstrated rather than asserted**, by the close-digest replay.

**One ADR amendment is recommended, not bent:** ADR-0023 does not state whether Billing's ledger is
an AR subledger or a general ledger. This milestone chose the subledger, implemented `TrialBalance`
as the reconciliation surface, and documented the reasoning in §2. That decision should be ratified
into ADR-0023 rather than left as an implementation choice — see §14.

---

## 13. Surface changes

11 new `billing.ledger.*` + 1 new `billing.credit.*` event type (168 total; M9.2's `credit.granted`,
`credit.consumed`, `credit.expired` reused) · 16 new diagnostic codes (162 total) · foundation
exports 574 → 642 · application exports 433 → 492 · 12 commands + 14 queries · 7 tables via
migration 0023 · 24 API paths · 217 paths total, no collisions.

---

## 14. Limitations

Recorded honestly rather than deferred silently.

1. **The M9.8 attribution ceiling is still unresolved and now blocks four milestones.** Usage
   attribution requires exactly one active billing account per organization. The Ledger does not
   worsen it — it posts per *account*, named directly by the rating run — but it inherits the
   ceiling on who can be billed at all. **Not fixable inside this boundary**; needs a milestone with
   a mandate to amend M9.8.
2. **This is an AR subledger, not a general ledger.** Declared in §2 and surfaced through
   `TrialBalance`. External accounting integration is a mapping exercise at the boundary and is not
   implemented. **Should be ratified into ADR-0023.**
3. **`for_account` folds every entry on a balance read.** `BalanceCheckpoint` and
   `since_sequence` exist so a roll-forward read is possible, but the balance query does not yet use
   them — it folds the full history bounded by `limit`. Correct today; the first real scaling
   bottleneck. **Owner: a query/indexing milestone.**
4. **Search filters in Python over a bounded fetch** (2000 entries). Consistent with M9.7/M9.11/M9.12,
   and journal entries grow faster than any of them.
5. **29 pre-existing qualified OpenAPI schema names** from M9.5/M9.7–M9.11 (§10.2). Cosmetic on the
   wire, but it makes generated clients awkward and the count is growing. **Owner: an API-surface
   cleanup milestone.**
6. **A period's currency is fixed per account.** `AccountBalance.of` raises on a mixed fold, and
   ADR-0023 RS5 fixes currency per account — so a multi-currency enterprise needs multiple accounts,
   which interacts with M9.8's payer hierarchy. Correct today; named because the first multi-currency
   enterprise deal will surface it.
7. **Revenue recognition is a declared state, not a scheduler.** `RecognitionState` is carried on
   every entry and defaults to `RECOGNIZED` for a rated charge. Deferred-revenue scheduling —
   recognising a monthly fee across the period it covers — is not implemented. **Owner: a revenue
   recognition milestone.**
8. **`PostRatingRun` is sequential** over one run's charges, in one transaction. Correct and
   restartable; parallelism is safe (derived ids make overlapping posts converge) and not
   implemented.

---

## 15. Recommendation

Proceed to **M9.14 (Invoice Engine)**, which consumes a closed accounting period and its journal
entries.

Three items should be scheduled alongside it: **limitation 1** (the M9.8 ceiling, now blocking four
milestones — this is a dependency, not a limitation), **limitation 2** (ratify the subledger decision
into ADR-0023 before an invoice document depends on it), and **limitation 7** (M9.14 will want
deferred revenue the moment it issues an invoice covering a future period).
