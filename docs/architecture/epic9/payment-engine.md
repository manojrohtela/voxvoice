# Payment Engine (M9.16)

**Status:** 🟢 Implemented (M9.16) · 🔧 **Corrected at M9.16-R1** · **Date:** 2026-08-10
**Authority:** Engineering Constitution v2.0 · ADR-0019 · ADR-0021 · ADR-0023 · ADR-0031 · M9.0C
**Predecessors:** M9.2–M9.15 (all frozen; none reshaped)

---

## 1. What this milestone is

Ten milestones produced facts inside this process. M9.6 said **what is sold**, M9.7 **what it
costs**, M9.8 **who we bill**, M9.9 **what was agreed**, M9.10 **what may be used**, M9.11 **what
was consumed**, M9.12 **what should be charged**, M9.13 **what is owed**, M9.14 **what the customer
receives**, M9.15 **what a tax authority is owed**. Every one of them could be recomputed from its
inputs.

This one cannot. M9.16 is the first milestone whose effects happen **outside the database**, in a
system Billing does not own, and which no transaction here can roll back. The question it must
answer is not "is the number right?" but:

> *Did we actually move this money, exactly once, through which provider, for which immutable
> invoice, from which payment attempt, what did the provider report, what financial fact did
> Billing record, and what happens if the network, the database, and the provider disagree?*

So the milestone rests on one sentence, ratified before implementation began:

> **The provider moves money. Billing owns the financial fact that the movement occurred.**
> (ADR-0023 PY1/PY4, M9.0C Option C)

Neither side overrides the other and neither is assumed to be right. A provider callback is an
*observation*, recorded verbatim and never trusted as state; a Billing state is a *decision*, made
from observations and never asserted onto the provider. Where the two disagree, the divergence is
recorded and escalated — never auto-resolved (PY5).

**Evidence:** 14 commands / 17 queries; 8 tables via migration **0026**; 30 paths at `/collections`
(297 total); 11 Studio screens; **161 tests** (133 at M9.16 including 25 named demonstrations of the dangerous cases, **28 added
at M9.16-R1**); **3762/3762 repository-wide**; ruff clean.

**Three defects found and fixed during implementation**, one a case where money could move and
Billing would have had no record of it (§12).

**An independent review then rejected the milestone and found three more, one of them S0.** §12A
records the corrections: a successful *void* was being read as a *capture* and posting a ledger
credit for money never collected; a raised provider call stranded an attempt in `started`, blocking
its intent permanently; and the refund ceiling was a read-modify-write that two concurrent refunds
under different request keys could walk past. All three are closed, each at the layer that owns the
rule, each with a regression test that reproduces the rejected scenario.

---

## 2. The mandatory pre-implementation audit

Performed across M9.2–M9.15 before any code was written.

### What already existed

M9.2's thin payment domain, declared and never executed: `PaymentState` (12 members),
`PaymentTrigger`, `PaymentMethodKind`, `PaymentInstrument` (a provider **token reference**, never
instrument data), `OperationIdentity`, `ProviderReport`, `Payment`, `ReconciliationVerdict`,
`ReconciliationFinding`, `DunningPolicy`/`DunningProcess`, and in `refunds.py` `RefundState`,
`RefundReason`, `RefundDestination`, `RefundApproval`, `Refund`, `Dispute`, `RefundPolicy`. Five
tables, five API routes, and one command (`RequestPayment`) that records an intention and executes
nothing. Its own docstring is explicit: *"there is no provider, no SDK, no network, no execution."*

**Every one of those types is reused unchanged.** The audit found no duplicate provider abstraction,
no second reconciliation vocabulary, no competing refund model, and — importantly — **no vendor SDK
anywhere in the repository**, which `test_ir5_no_vendor_sdk_anywhere_in_the_foundation` has asserted
since M9.2.

### The one modelling problem, and the decision

M9.2's `Payment` carries `invoice_id`, `amount`, **and a mutable `attempt: int`**, with
`operation_identity()` returning `(payment_id, attempt)`. That is intent and attempt collapsed into
one row: a retry increments the counter and overwrites `state`, `failure_code`,
`provider_reference`, and the timestamps. "We tried three times and the third worked" and "it
worked" are different histories, and only one of them is true.

M9.16 therefore introduces `PaymentIntent` and `PaymentAttempt` **beside** `Payment`, which stays
exactly as it is — the same call M9.13 made for `JournalEntry` beside `LedgerEntry`, M9.14 for
`CommercialInvoice` beside `Invoice`, and M9.15 for `TaxAssessment` beside `TaxDetermination`
(§A2.5).

### The three amendments, recorded rather than silent

1. **`PostingSource` gains `PAYMENT` and `REFUND`** (M9.13's enum, `commercial_ledger.py`). Payment
   execution has to be a *nameable cause*. The two alternatives were both wrong: reusing `MANUAL`
   would make every captured payment read as operator-entered in the one report where that
   distinction is the question, and letting Payments post under its own vocabulary would put a
   second poster beside M9.13. Additive; no existing member changed.
2. **`PostJournalEntry` gains `source`, `source_id`, and `payment_id`**, all defaulted to exactly
   the previous behaviour, so every existing caller is unchanged. This lets a domain post through
   the Ledger's one path instead of gaining a poster of its own.
3. **`BillingContainer.build` gains `payment_providers`**, defaulting to `None` ⇒ an empty registry.

No domain was reshaped and no existing behaviour changed. Two *test* assertions were sharpened
(§12).

### Inherited findings, classified

| Milestone | Finding | Classification for M9.16 |
|---|---|---|
| M9.12 | charge storage duplication · entitlement effective-dating · concurrent supersede · unsealed-period finalization · CREDIT diagnostic · commitment digest · usage provenance scale | **INHERITED / SAFE** — Payments reads none of them |
| M9.13 | AR-subledger vs GL ratification | **INHERITED / SAFE**, now depended on by a third domain — §14 |
| M9.13 | idempotent posting · accounting periods · per-account sequence · replay proof | **RESOLVED and reused** — payment posting is idempotent *because* of them |
| M9.14 | immutable document content · gapless numbering | **INHERITED / SAFE** — Payments reads a document and writes nothing back |
| M9.14 | billing entity configuration | **INHERITED / SAFE** |
| M9.15 | determination evidence · provider boundary · basis digest · inclusive calculation · failure policy | **INHERITED / SAFE** — the payable amount arrives already taxed |
| M9.8 | **attribution ceiling** | **INHERITED / SAFE for M9.16, and now blocking seven milestones** — §14 |

**The attribution ceiling was re-evaluated rather than repeated.** Payments needs exactly one
billing account, and it *consumes* it from the command — resolved by the caller through M9.8's
existing contract. No hierarchy logic, no attribution rule, and no account resolution was added
here, and a test asserts the module touches no account repository. It does not block this
milestone; it is not solved by it either.

---

## 3. Components

| Layer | Module | Lines | Owns |
|---|---|---|---|
| Foundation | `commercial_payments.py` | 1,186 | Intent, attempt, operation, event record, refund, reconciliation, routing evidence, both FSMs, derived identity |
| Foundation | `payment_rules.py` | 1,522 | The provider port and two implementations, routing, lifecycles, normalization, positions, refund bounds, reconciliation, the ledger request, explanation, inspection, validation, search |
| Foundation | `payment_factories.py` | 922 | The one construction path; 8 repository contracts |
| Application | `payment_engine.py` | 2,437 | 14 commands, 17 queries, the three-phase external boundary |
| Persistence | `payment_models.py` | 299 | 8 tables |
| Persistence | `payment_repositories.py` | 765 | 8 SQL implementations |
| Persistence | `migrations/v0026_billing_payments.py` | 49 | Migration 0026 — creates 8 tables, **seeds nothing** |
| Customer API | `payment_schemas.py` | 753 | Wire DTOs |
| Customer API | `routers/payment_engine.py` | 845 | 30 paths at `/collections` |
| Studio | `studio/payments.html` | 742 | 11 screens |
| Tests | `test_billing_payment_engine.py` | 2,485 | 161 tests: 25 named demonstrations + 28 R1 regressions |

---

## 4. PaymentIntent vs PaymentAttempt

```
PaymentIntent  pin_…   ← what Billing wants to collect
    ├── PaymentAttempt  pat_… seq 1 → failed      (card_declined, 14:02:11)
    ├── PaymentAttempt  pat_… seq 2 → ambiguous   (provider never answered, 14:04:56)
    └── PaymentAttempt  pat_… seq 3 → succeeded   (ref ch_9f2c, 14:11:03, jrn_…)
```

**The intent** holds the objective: the account, the amount, the document being paid, the digest it
was read from, the routing decision *and its reason*, and the caller's request key. It holds **no
collected figure, no refunded figure, and no outstanding figure** — there is no such column
anywhere in `payment_models.py`, and a test asserts it. A payment total in a column is a second
authority on a number the Ledger owns, and it is the one that goes stale.

**The attempt** is one external movement of money. Its id is derived from `(intent, sequence)`, its
state moves only along an FSM with no edge out of a settled state, and its `observations` tuple is
append-only. A retry is attempt *n+1*: attempt *n*'s failure code, provider reference, timestamps,
and evidence survive it untouched.

**The position** — captured, refunded, net, outstanding — is derived from those facts every single
time it is asked, by `PaymentPositionService`. `outstanding` is floored at zero and overpayment is
reported separately, because a negative outstanding reads as a credit and credit is the Ledger's
word.

---

## 5. Provider architecture

```
    Payment Domain (Foundation)
            ↓  depends only on
    PaymentProvider  ← the port: 6 verbs, no vendor concept
            ↓  implemented by
    NullPaymentProvider · OfflinePaymentProvider · (a vendor adapter, outside this package)
            ↓
    External PSP
```

The port is `initiate`, `capture`, `cancel`, `refund`, `retrieve`, `verify_event` — the six this
milestone uses. A port shaped for the union of every PSP's feature list is a port no PSP implements.

An implementation must treat `request.operation_key` as its idempotency key, return
`ProviderStatus.AMBIGUOUS` when it does not know rather than guessing `FAILED`, translate its
vendor's vocabulary into `ProviderOutcome` while keeping the vendor's own word in `raw_status` as
evidence, and never return instrument data.

**`NullPaymentProvider` is the default and returns `UNAVAILABLE`, not `FAILED`.** "No provider is
configured" and "the issuer declined" are different facts; an installation reporting the second when
the first is true looks like a business problem instead of a deployment one.

**`OfflinePaymentProvider`** is deterministic, opens no socket, honours idempotency keys the way a
real PSP does (the same key returns the first answer marked `ALREADY_PROCESSED`), counts the
movements it actually made, and **can lose a response while still knowing internally whether money
moved** — which is what lets a test assert what reconciliation is *supposed* to discover rather than
what it happened to be told.

**No provider is named anywhere.** Routing is operator-declared `PaymentRoutePolicy` rows; a fresh
installation routes nowhere and can collect nothing. Migration 0026 seeds no provider, no route, and
no credential. A test greps all eight modules for thirteen vendor names.

**Routing is deterministic and recorded.** Among matching policies the winner is lowest `priority`,
then highest `specificity`, then lowest `id` — a total order, so two workers routing the same
payment at the same instant reach the same provider. The *reason* travels onto the attempt, because
routing tables change and a payment made in March must stay explainable in November.

---

## 6. The external transaction boundary

A database transaction cannot roll back a charge. The money-moving path is therefore three phases,
and the ordering is load-bearing:

```
┌─ transaction 1 ─────────────────────────────────────────────────────────────┐
│  read the intent · guard the attempt · append the attempt (derived id)      │
│  **claim the operation key** — composite PK, exactly one winner · COMMIT    │
└─────────────────────────────────────────────────────────────────────────────┘
                              ↓   no transaction is open
                  provider.initiate(request)   ← the only external call
                              ↓
┌─ transaction 2 ─────────────────────────────────────────────────────────────┐
│  append the observation · move the attempt FSM · move the intent FSM        │
│  post the capture through the Ledger's own command · close the operation    │
│  COMMIT                                                                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

Every failure mode has a defined resting place:

| Case | What survives | How it resolves |
|---|---|---|
| **A.** DB ok + provider ok | everything | normal |
| **B.** provider ok + transaction 2 fails | the operation row, `sending` | `ReconcilePaymentAttempt` retrieves **the same key** |
| **C.** provider times out | outcome `AMBIGUOUS`; attempt `ambiguous`; operation stays open | reconciliation; **a retry is refused until then** |
| **D.** request accepted, response lost | identical to C | identical to C |
| **E.** provider says "already processed" | recorded, **not treated as success** | `retrieve` decides |
| **F.** DB rolls back after the movement | the operation row committed in phase 1 | the sweep finds it |
| **G.** crash before the call | the operation row, `sending` | retrieve returns "not found" → abandoned; nothing moved |
| **H.** ledger refuses the posting | the capture stands; `UNPOSTED_CAPTURE` divergence raised | `PostPaymentToLedger`, attributed and idempotent |

"Retry the provider call" is never the answer to an ambiguity. `AttemptGuardService` refuses to open
a new attempt while any attempt is unresolved or any operation is still `sending`, and
`AttemptState.AMBIGUOUS` leaves only by `RESOLVE_SUCCESS`/`RESOLVE_FAILURE`, which only
reconciliation calls.

---

## 7. Idempotency, layer by layer

| Layer | Mechanism | What it prevents |
|---|---|---|
| HTTP → command | `request_key` is part of the intent's derived id | a retried checkout becoming two objectives |
| Intent | `pin_` = digest(org, subject, purpose, amount, currency, request_key) + read-before-write + SAVEPOINT | two workers, one objective |
| Attempt | `pat_` = digest(intent, sequence); unique `(org, intent, sequence)` | two workers both opening "attempt 2" |
| **Provider call** | **`(org_id, operation_key)` composite primary key** — the loser of the claim **does not send** | **two external charges for one collection** |
| Provider | the same `operation_key` is the PSP's idempotency key | a re-send becoming a second movement |
| Webhook | `pev_` = digest(org, provider, provider's event id); unique constraint | duplicate delivery becoming a second fact |
| Ledger | M9.13 derives the entry id from `(org, account, source, source_id=attempt_id, type)` | a replayed capture becoming a second financial fact |
| Refund | `prf_` = digest(org, attempt, request_key) | concurrent refund requests refunding twice |

The claim is a **database constraint, not an application check**. An `if not exists` between two
processes is a race with a comfortable name.

---

## 8. The state machines

**Intent** — 9 states. What it refuses is the specification: **no edge out of `PAID`** (money coming
back is a refund, a separate object, never a state here); **no edge out of `CANCELLED` or
`EXPIRED`** (`cancelled → captured` turns an abandoned checkout into a charge nobody expected);
`FAILED → PENDING` exists and `FAILED → PAID` does not.

**Attempt** — 9 states. **No edge out of `SUCCEEDED`, `FAILED`, `CANCELLED`, or `ABANDONED`.** Not
to `pending`, not to each other. Money that moved does not un-move because a late callback
disagreed; a contradiction is a reconciliation finding and the correction is a refund.

Whether an attempt succeeding means the *objective* is met is decided by the **position**, not by
the attempt — reading "an attempt succeeded" as "the invoice is paid" is how a half-paid document
gets reported as settled.

---

## 9. The Ledger boundary

`LedgerPostingIntent` is a **request**, not an entry: an amount, a direction, a cause, and the
references to walk back. It carries no period, no sequence, and no balance, because those are
M9.13's decisions. It is dispatched through **M9.13's own command**, and Payments writes nothing
into ledger persistence — asserted structurally.

- a captured payment → `PAYMENT` / `CREDIT`, source `payment`, `source_id` = the attempt id
- a completed refund → `REFUND` / `DEBIT`, source `refund`, `source_id` = the refund id
- an **authorization** produces no ledger fact at all — reserved money is not taken money
- a **failed** attempt produces no ledger fact, and `PostPaymentToLedger` refuses one
- a **replay** produces a recorded duplicate, never a second fact

---

## 10. The Invoice boundary

Payments reads the immutable document for what is payable and validates that an intent does not
collect more than it owes. It recomputes no total, resolves no price, calls no rating, and
determines no tax — four AST-driven tests assert each separately. It writes nothing back to a
document: there is no call to `documents.save`, `snapshots.append`, or the number allocator anywhere
in the module.

Partial collection is modelled as a second objective against the same document rather than a
mutable remaining-balance field, so no second authority on the figure exists.

---

## 11. Reconciliation

`PaymentReconciliationService` compares what Billing believes with what a provider reports and
**fixes neither**. Eleven outcomes are modelled, of which eight are divergences; the wider
vocabulary projects onto M9.2's four-member `ReconciliationVerdict` through `VERDICT_PROJECTION`, a
table checked by iteration, so `PaymentReconciliation.to_finding()` is the **single** derivation of
the thinner record.

Exactly one case resolves automatically, and it is not a divergence: `AMBIGUOUS`, closed by
retrieving *the same operation key* — the only authority that can answer it. Everything else opens a
finding with both sides recorded and `status=OPEN`, and `guard_manual_resolution` refuses to close
one without a named actor (**PY5**).

---

## 12. Defects found and fixed

1. **A ledger refusal destroyed the record of a capture that actually happened.** Found by the
   end-to-end smoke, collecting against an account with no open accounting period: M9.13 correctly
   declined the posting, the exception unwound transaction 2, and the *capture* went with it —
   money gone, nothing in Billing saying so. This is precisely the "provider succeeded, local write
   failed" case. Fixed by catching the refusal at that one boundary, keeping the capture, raising an
   `UNPOSTED_CAPTURE` divergence, and repairing through the explicit, attributed, idempotent
   `PostPaymentToLedger`.
2. **A duplicate webhook reported itself as `applied`.** `append` is idempotent on the provider's
   own event id, so a redelivery returned the *original* row — whose disposition still read
   `applied`, because it was. That made a redelivery indistinguishable from a first delivery at
   exactly the surface built to distinguish them. Fixed by returning this delivery's computed
   disposition while leaving the stored evidence untouched.
3. **`PaymentRoute.provider_code` had no default**, so the "no provider selected" value could not be
   constructed — the unrouted case would have raised instead of being reportable.

**Two test-side scans sharpened, both strictly strengthened:** the explainer's no-provider check
scanned for the substring `provider`, which appears in the notes the explainer *writes*, and now
walks the AST for calls to port verbs; and the repository-write scan could not distinguish
`self._repos.refunds` from `self._svc.refunds`, which was fixed by renaming the Foundation service
seams (`refund_rules`, `attempt_lifecycle`, `operation_rules`, …) so the two vocabularies cannot
collide. No domain was changed to satisfy a test.

---

## 12A. M9.16-R1 — the independent review's findings, corrected

The review rejected M9.16. Every finding below states the old behaviour, the root cause, the fix,
the proof, and where the regression test lives.

### P-1 (S0) — a void was recorded as a capture

**Old behaviour.** `POST /collections/attempts/{id}/cancel` on an authorized attempt produced
`attempt=succeeded`, `intent=paid`, `captured=100000`, and one `payment`/`credit` ledger entry —
revenue fabricated from a cancellation.

**Root cause.** `_settle_attempt` is shared by every operation, and it interpreted the provider's
answer through a single table. `STATUS_TRIGGERS[SUCCEEDED] → SUCCEED` is right for `INITIATE` and
`CAPTURE` and exactly wrong for `CANCEL`, where `SUCCEEDED` means *the money did not move*. The
defect was not a missing check; it was **a missing input**: the method never knew what had been
asked.

**Fix.** Interpretation is now **action-aware and declared**, in the Foundation:
`CANCEL_STATUS_TRIGGERS` maps `SUCCEEDED`/`ALREADY_PROCESSED` → `CANCEL`;
`REFUND_STATUS_TRIGGERS` is empty, because a refund outcome moves *no* attempt state;
`ACTION_TRIGGERS` selects the table. `action` is a **required keyword argument** on
`_settle_attempt`, so a new call site cannot omit it. A second, independent gate guards the Ledger:
`OutcomeNormalizer.asserts_money_moved(outcome, action)` — a predicate rather than an inference —
so a capture posts only when an action that moves money succeeded. The webhook path reads the
action from the *operation the key belongs to*, so a callback about a void is interpreted as a void.

**Proof.** `attempt=cancelled`, `captured=0`, **zero ledger entries**, no `payment.captured` event.

**Regression tests.** `test_r1_p1_cancelling_an_authorization_posts_no_capture` (the exact rejected
scenario), plus the action table, a failed void, `ALREADY_PROCESSED` on both a void and a capture,
a refund not re-capturing, and a void after ambiguity.

**Also found while writing those tests:** an *ambiguous* attempt was voidable. Asking a provider to
void a movement that may already have captured, and then recording `cancelled` over it, is the same
class of error. `AttemptLifecycleService.guard_cancellable` now refuses it — reconcile first.

**Status: CLOSED.**

### P-2 (S1) — a raised provider call stranded an intent forever

**Old behaviour.** A provider that *raised* (timeout, reset, adapter bug) skipped phase 3. The
attempt stayed `started`, which is open — so retry was refused, `CancelPaymentIntent` was refused,
and `reconcile_attempt` ignored it because it only resolved `AMBIGUOUS`. One connection reset made
an invoice permanently uncollectable with no operator command able to release it.

**Root cause.** Two gaps. The provider edge assumed adapters *return* outcomes, and reconciliation
assumed "unresolved" meant only `AMBIGUOUS`.

**Fix.** Every provider call goes through `PaymentUseCases._call_provider`, and **a raised call
becomes `AMBIGUOUS`** — the honest state when we cannot prove the request never left. The single
exception is `ProviderNotSent`, which an adapter raises to *assert* nothing was transmitted and
which maps to a local `FAILED`. Reconciliation now resolves **any externally-open attempt** via
`PaymentReconciliationService.resolve_open`: `AMBIGUOUS` still takes `RESOLVE_*`, and a merely-open
attempt takes the ordinary trigger. Neither path sends anything new.

**Proof.** Timeout, reset, and a generic exception all produce `ambiguous`; retry stays blocked;
reconciliation closes it to `failed` or `succeeded`; the intent is then retryable or cancellable.

**Regression tests.** `test_r1_p2_a_raised_provider_call_becomes_ambiguous_not_stuck`
(parametrised over three transport failures), `..._an_adapter_that_proves_nothing_was_sent_fails_locally`,
`..._reconciliation_resolves_an_attempt_stranded_open`, `..._a_resolved_intent_can_collect_again`,
`..._duplicate_reconciliation_is_stable`.

**Status: CLOSED.**

### P-3 (S2) — concurrent refunds could exceed the capture

**Old behaviour.** `guard_refundable` read the already-refunded total and decided. Two transactions
reading before either committed each passed, and 1400 could be returned against a 1000 capture.

**Root cause.** A read-modify-write with no arbitrator. Same-key idempotency was covered by the
derived id; *different* keys had nothing.

**Fix.** A refund now carries a `sequence`, and `(org_id, attempt_id, sequence)` is **unique**. The
second concurrent refund is an impossible row until the first commits; the loser gets
`RefundSequenceConflict` (a `BillingConflict` declared in the Foundation, because the Application
must react to it and may not import persistence), re-reads, and re-checks against the winner's
committed ceiling — taking the next slot or being refused. `_claim_refund_slot` bounds the retries;
the loop terminates because each round either consumes a slot or is refused.

**Proof.** Two Applications, two `BillingDatabase` objects, two connections, one file-backed
database: 700 + 700 against a 1000 capture yields exactly one `refunded` and one refusal, total 700.

**Regression tests.** `test_r1_p3_two_sessions_cannot_over_refund_one_capture`,
`..._the_refund_ceiling_holds_for_every_split` (40+60, 60+40, 50+50, 70+70, 100+1),
`..._the_same_request_key_across_two_sessions_is_one_refund`,
`..._the_refund_sequence_is_a_database_constraint`.

**Status: CLOSED.**

### P-4 (S2) — the webhook boundary, decided honestly

**Option B was chosen.** There is **no production provider-webhook endpoint at M9.16**. The route
is renamed `/collections/provider-events/relay/{provider_code}`, its summary reads
`INTERNAL RELAY ONLY`, and its OpenAPI description states that it is not a provider webhook, that a
PSP cannot call it, and that M9.16 must not be read as ADR-0023 PY4 webhook compliance.

Option A was rejected on a security judgement, not effort: an unauthenticated route must resolve
the organization from provider evidence *before* authentication, and building that cross-tenant
lookup to make an endpoint reachable would put a tenancy hole in the one plane that must not have
one. Signature verification is implemented and exercised; the deployment boundary around it is not.

**Regression tests.** `test_r1_p4_the_relay_is_not_presented_as_a_provider_webhook` (no path
contains "webhook"; the summary and description carry the warning; a valid signature still gets
401) and `test_r1_p4_the_limitation_is_documented`.

**Status: RESOLVED — documented limitation, not a claim of compliance.**

### P-5 (S3) — dead vocabulary removed

`PARTIALLY_PAID` and `SETTLE_PARTIAL` were unreachable: `PaymentFactory.attempt` always opens an
attempt for the whole intent amount and no command passes less, so no outcome could produce them.
Both are **removed** from the FSM, the API and the Studio. Partial collection remains what it always
actually was — a second objective against the same document. Under-capture is a recorded limitation,
not a half-built feature. `test_r1_p5_every_published_intent_state_is_reachable` walks the FSM from
`draft` and asserts every published state is reachable, which prevents the next dead state too.

**Status: CLOSED.**

### P-6 (S3) — the exception boundary narrowed

`except Exception` at the ledger boundary turned an `AttributeError` into an `UNPOSTED_CAPTURE`
divergence — a bug hidden as a financial finding, queued for an operator who could do nothing about
it. Narrowed to `BillingError`. `test_r1_p6_a_ledger_refusal_is_a_divergence_but_a_bug_is_not`
injects a programming error and asserts it propagates and produces no divergence;
`..._the_genuine_refusal_path_still_works` asserts the real refusal path is unchanged.

**Status: CLOSED.**

### P-7 (S4) — genuine concurrency coverage

Two independent Applications over two independent connections to one file-backed database, with
explicit interleaving so the database arbitrates and the result is deterministic. Covers the
operation-claim race and the refund race. `test_r1_p7_two_sessions_race_one_operation_claim` asserts
exactly one worker may transmit.

**Status: CLOSED.**

---

## 13. Compliance

| Check | Result |
|---|---|
| **No provider named anywhere** (13 vendors × 8 modules) | ✅ |
| No SDK, socket, or HTTP client in the Foundation | ✅ AST import scan |
| Prices / rates / charges / balances / invoices nothing | ✅ each asserted separately |
| Writes only to its own 8 repositories | ✅ scoped AST scan |
| Ledger reached only through `PostJournalEntry` | ✅ |
| No instrument data in models, payloads, events, DTOs, or Studio | ✅ whole-word scan across 8 surfaces |
| Duplicate operation / attempt / event = impossible row | ✅ primary key + 2 unique constraints |
| Repositories never commit or roll back; hold no business rule | ✅ |
| Append-only contracts declare no update or delete | ✅ |
| Studio computes no money, calls no provider, stores nothing | ✅ |
| Deny-by-default; org isolation on every read | ✅ |
| ADR-0023 PY1–PY9 · ADR-0021 · ADR-0031 IR1/CR2 | ✅ |
| Repository-wide suite | ✅ **3734 / 3734** |

Foundation exports 788→**873** · application 552→**674** · events 192→**211** · diagnostics
194→**211** · API paths 267→**297** · migration chain →**0026**.

---

## 14. Limitations

1. **No vendor adapter ships.** The port and a deterministic offline provider do; a real PSP
   adapter is deployment configuration and belongs outside this package.
2. **There is no production provider-webhook endpoint (P-4).** Signature verification is
   implemented and exercised, but the only route that ingests a provider event sits behind
   `require_org`, so a real PSP — which holds no Customer-Platform token — cannot call it. It is
   named and documented as an internal relay (`/collections/provider-events/relay/{provider}`).
   An unauthenticated route would need to resolve the organization from provider evidence before
   authentication, and building that cross-tenant lookup to make an endpoint reachable was judged
   the wrong trade in the one plane that must not have a tenancy hole. **Do not read M9.16 as
   ADR-0023 PY4 webhook compliance.**
3. **Webhook signature cryptography is the adapter's.** `OfflinePaymentProvider` implements a
   deterministic shared-secret check to prove the *shape* of the trust boundary; real cryptography
   belongs with the vendor that specifies it.
4. **No dunning execution.** M9.2's `DunningPolicy`/`DunningProcess` are unreshaped and unexecuted.
5. **No dispute or chargeback execution.** `Dispute` and `DisputeStage` are unreshaped; a chargeback
   is deliberately not a refund.
6. **Reconciliation is on-demand**, per attempt or per sweep. There is no scheduled worker and no
   provider-side settlement-file import, so an ambiguous attempt is resolved when somebody or
   something asks — which for a production deployment means a scheduled caller must exist.
7. **`MISSING_IN_BILLING` is modelled but unreachable** without a provider-side listing capability,
   which the port does not have.
8. **No under-capture (P-5).** An attempt always collects the whole intent amount; a provider
   taking less is not modelled. Partial collection of a document is two objectives, which means a
   UI wanting "pay the rest" must open the second intent itself.
9. **Multi-provider routing is implemented; provider *failover* is not.** An unavailable provider
   fails the attempt rather than re-routing, because silently moving a payment to another provider
   is a decision an operator should make.
10. **Refund concurrency is arbitrated by a unique constraint plus bounded retry (P-3).** Correct
    and database-enforced, but it resolves contention by rejecting the loser rather than queueing
    it; a caller facing heavy concurrent refunds on one capture will see conflicts it must retry.
11. **The AR-subledger vs GL decision (M9.13) is still unratified**, and is now depended on by a
    document, a determination, and a payment.
12. **The M9.8 attribution ceiling now blocks seven milestones.**

## 15. Recommendation

Proceed to **M9.17**. Three items should be scheduled alongside: ratifying the AR-subledger decision
into ADR-0023; the M9.8 attribution ownership milestone; and a dunning/collections milestone, which
is the natural consumer of a failed payment and the first thing an operator will ask for once
payments are live.
