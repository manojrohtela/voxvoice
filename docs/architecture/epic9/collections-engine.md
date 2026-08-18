# Dunning & Collections Engine (M9.17)

**Status:** 🟢 Implemented (M9.17) · **Date:** 2026-08-10
**Authority:** Engineering Constitution v2.0 · ADR-0019 · ADR-0021 · ADR-0023 · ADR-0031 · M9.0C
**Predecessors:** M9.2–M9.16 (all frozen; none reshaped). Builds directly on **M9.16-R1**.

---

## 1. What this milestone is

M9.16 answered *did the money move?* This one answers the question that follows it:

> *What should Billing do when an amount remains collectible, a payment attempt failed or became
> unresolved, and the commercial agreement still has an outstanding obligation?*

Collections is a **policy engine, not a payment engine**. It decides that a customer should be
chased, a payment should be retried, a case should wait, or a human should take over — and it
records that the decision was acted on. It moves no money, holds no provider, computes no figure,
and posts no ledger entry.

Stated as a boundary:

> **Collections decides whether to ask. Payments moves the money. Ledger records the fact.
> Invoice owns the document. Subscription owns the agreement.**

It is also the first Billing domain that is mostly *about other domains*, so its risk is not
arithmetic — there is none — it is **ownership drift** and **races**:

* a policy engine that reads "not succeeded" as "failed" turns every ambiguous payment into a
  second charge. That is the M9.16-R1 invariant, one milestone downstream;
* a scheduler that runs twice opens two recoveries, sends two notices, or requests two retries;
* a worker that decided from a stale read undoes a recovery that happened while it was thinking;
* a domain that quietly starts computing what is owed becomes a second authority on a figure the
  Ledger owns.

**Evidence:** 12 commands / 15 queries; 6 tables via migration **0027**; 25 paths at `/recovery`
(322 total); 10 Studio screens; **97 tests**; **3859/3859 repository-wide**; ruff clean.

**Four defects were found and fixed during implementation** (§17), one of them an unbounded dunning
loop that would have chased a customer indefinitely.

---

## 2. The mandatory pre-implementation audit

Performed across M9.2–M9.16 before any code was written. Searched for `dunning`, `collection`,
`retry`, `grace`, `overdue`, `past_due`, `delinquent`, `reminder`, `escalation`, `suspension`,
`recovery`, `write-off`, `communication`, `stale` and `policy_version`.

### The overlap table

| Existing artifact | Owner | Used by | M9.17 action | Reason |
|---|---|---|---|---|
| `DunningState` / `DunningTrigger` / `DUNNING_LIFECYCLE` | M9.2 | `lifecycle.py` | **Left untouched; new FSM beside it** | Six states with no notion of an *ambiguous* payment, because M9.2 had none. After M9.16-R1 that is the single most dangerous gap. |
| `DunningPolicy` | M9.2 | domain only | **Left untouched; `CollectionPolicy` beside it** | A bare value with no identity and no version — unusable as the authority behind a historical decision. |
| `DunningProcess` / `DunningRepository` / `cp_billing_dunning` | M9.2 / M9.4 | `SqlDunningRepository`, migration 0015 | **Left untouched** | One mutable row *per payment*. A recovery that outlived three attempts is three unrelated rows and no readable history. |
| `billing.dunning.started/recovered/abandoned` | M9.2 | event stream | **Reused, not renamed** | A case opening *is* dunning starting. A second type would give one business fact two names (AR-2). |
| `BillingAccount.dunning_exempt` | M9.8 | account schemas | **Read-only consideration; unchanged** | An account flag, not a collections concern. |
| `InvoiceState.IN_COLLECTION` / `SEND_TO_COLLECTION` | M9.14 | invoice engine | **Consumed as a fact; never written** | The Invoice decides its own state; Collections is told. |
| `subscription.past_due` / `EXHAUST_DUNNING` | M9.2/M9.9 | subscription engine | **Untouched** | Suspension is the Subscription's decision, not Collections'. |
| `AttemptState` (9) / `IntentState` (8) / `FailureCategory` | M9.16 | payment engine | **Read and translated once** | Mapped into `PaymentSignal` (6) by `SignalReader`. Not duplicated — see §19. |
| `/collections` prefix | **M9.16** | payment router | **Not reused — `/recovery` taken instead** | Two routers under one prefix shadow each other by registration order, silently. |

### The conclusion

M9.2's dunning surface is **too small for this engine and correct for what it is**, so the richer
aggregates land *beside* it — exactly as `JournalEntry` landed beside `LedgerEntry` (M9.13),
`CommercialInvoice` beside `Invoice` (M9.14), `TaxAssessment` beside `TaxDetermination` (M9.15) and
`PaymentIntent` beside `Payment` (M9.16). Extension is addition (§A12). Nothing frozen was
reshaped, and `test_m9_2_dunning_is_untouched` asserts it.

---

## 3. Ownership model

```
    Invoice ─────read────▶ ┐
    Payments ────read────▶ ├── Collections ──command──▶ Payments  (StartPaymentAttempt)
    Ledger ──(via above)─▶ ┘         │
                                     └──port──▶ CollectionNotifier  (provider-neutral)
```

Every arrow **into** Collections is a read. The only arrow out that changes anything is a *command*
into the Payment Engine's own use cases — the same shape M9.16 uses to reach the Ledger. Collections
writes to six tables, all its own, and `test_collections_never_writes_to_another_domains_repositories`
scans every persistence call in the Application to prove it.

There is **no arrow to the Ledger at all**. Collections never posts an entry, never computes a
balance, and never learns what is owed except by asking Payments, which asks the document, which got
the figure from the Ledger. Three hops, one authority.

## 4. Domain model

Five things, deliberately not collapsed into two:

| Aggregate | What it is | Lifetime |
|---|---|---|
| `CollectionPolicy` | An **immutable, versioned** strategy: ordered stages with waits and graces | Forever; superseded, never edited |
| `CollectionCase` | The long-lived recovery of **one obligation** | Outlives every attempt |
| `CollectionAction` | One decision taken inside a case, **durable before anything leaves** | Claimed by exactly one worker |
| `CollectionAttempt` | Evidence that an action was executed, and what came back | Append-only |
| `CollectionDecision` | **Why** an action was selected, with every input beside it | Append-only, replayable |

A reminder is not a payment attempt. A reconciliation request is not a payment attempt. An operator
escalation is not a payment attempt. Only `REQUEST_PAYMENT_RETRY` causes money to be asked for, and
even that one only *asks*.

### Identity — derived, never minted

* **case** = `(org, obligation kind, obligation id)`. Every way a second recovery could be opened —
  a duplicated payment-failure event, a scheduler that ran twice, an invoice re-read, a restart
  mid-command, two workers sweeping the same document — collapses onto one primary key. No
  timestamp, no cycle, no policy: a debt does not become a different debt because the strategy
  changed;
* **action** = `(case, cycle, sequence)`, so two workers that both conclude "the next action is
  number 3" collide on a primary key instead of both sending a notice;
* **decision** = `(case, cycle, stage, signal)` — its *inputs*, not when it was taken. A scheduler
  polling every minute records one decision per real decision, not one per sweep;
* **policy version** = `(org, code, version)`, so republishing is idempotent and publishing a
  different body under the same version is a constraint violation rather than a silent rewrite of
  the rules a live case is being judged by.

## 5. Lifecycle

Nine case states — `DRAFT · SCHEDULED · ACTION_DUE · IN_PROGRESS · BLOCKED · RECOVERED ·
EXHAUSTED · CANCELLED · ESCALATED` — and every one is asserted **reachable** by walking the machine
from `DRAFT` (`test_every_published_case_state_is_reachable`). That test exists because of M9.16-R1
finding P-5: a published state the engine cannot enter reads to integrators as a supported feature.

What the machine **refuses** is the specification:

* **no edge out of `RECOVERED`** — not for a stale worker, a replayed event, or a policy edit;
* **no edge out of `EXHAUSTED`** — `EXHAUSTED → SCHEDULED` would let a policy change resurrect a
  closed recovery and re-dun a customer who was told the matter was closed. A genuinely new
  commercial decision opens a **new cycle**, which leaves the previous one readable in full;
* **no edge out of `CANCELLED`**;
* **no `BLOCKED → ACTION_DUE`** — a case waiting on an ambiguous payment does not become actionable
  because time passed. It becomes actionable when the ambiguity is *resolved*, which is
  reconciliation's news to deliver, not the clock's;
* **`ESCALATED` is terminal for automation only** — a human may recover or cancel it, the two facts
  a human can actually establish, and nothing else.

Actions run `PLANNED → CLAIMED → EXECUTED | FAILED | OBSOLETE | SKIPPED`, with no edge out of
`EXECUTED` and none back into `CLAIMED` from `OBSOLETE`.

## 6. Policy versioning

`CollectionPolicyRepository` has **no `update` and no `delete`**, and that absence is the
reproducibility guarantee. Changing a strategy publishes version *n+1*; a case bound to *n* keeps
naming *n*, and `_CollectionSupport.bound_policy` reads the version the **case** names rather than
today's. That one method is the whole of policy reproducibility — reading the latest version there
would mean a strategy edited on Tuesday silently re-judges a decision taken on Monday.

Proven by `test_a_decision_replays_identically_after_the_strategy_is_rewritten`: the March decision
recomputes unchanged against the March version while the same case against April's version selects
something else.

## 7. The collection decision engine

Pure and **clock-free**: `decide(case, policy, *, signal, at, failure_class, since)` takes the
instant it reasons about. `test_the_engine_reads_no_clock` scans the class for `time.time`,
`datetime` and `clock.now`.

Branch order is the specification, and the first two are the point:

1. **`SUCCEEDED` → recover.** Nothing else is relevant and no stage can override it;
2. **`AMBIGUOUS` → block**, *before the policy is consulted at all*. The only action reachable is
   `AWAIT_RECONCILIATION`;
3. quiescent case → nothing;
4. off the end of the ladder → exhaust;
5. permanent failure → skip to the terminal stage, **recorded in the decision path**;
6. otherwise the stage's kind, passed through the money-request gate.

## 8. Payment boundary — three gates on the M9.16-R1 invariant

An ambiguous payment must never become a retry. Enforced three times, independently and in
differently-shaped ways, because M9.16-R1's S0 was a single-gate failure:

| Gate | Where | Shape | Catches |
|---|---|---|---|
| 1 | `decide` branch 2 | blacklist, before the policy is read | any stage arrangement |
| 2 | `_gate_money_request` | **whitelist** — a signal added later defaults to refusing | a policy that names `REQUEST_PAYMENT_RETRY` |
| 3 | `CollectionGuardService.guard_money_request` | re-checks the signal **at execution time** | an action planned an hour ago while a reconciliation has since moved the attempt back to ambiguous |

`SignalReader` is the single translation point, and **five** payment attempt states map to
`AMBIGUOUS` — `started`, `pending`, `requires_action`, `authorized`, `ambiguous` — because all five
mean *a movement may be in flight*. An unrecognised state reads as `AMBIGUOUS`, never as `FAILED`:
a state the engine cannot vouch for is one whose safety it cannot vouch for.

A retry is executed by dispatching M9.16's own `StartPaymentAttempt`, injected as a callable. With
no payment engine wired the retry is recorded as `DEFERRED` — honest, rather than a silent no-op.

## 9–11. Invoice, Ledger and communication boundaries

**There is no money in this domain at all.** No amount, no balance, no `outstanding`, no copied
invoice total — not as a column, not as a field, not on the wire.
`test_no_collections_module_holds_an_amount_a_balance_or_a_total` walks the AST of all eight files
and bans the identifiers; `test_no_collections_module_imports_money_at_all` bans even the `Money`
*type*, because a domain that can hold one eventually stores one.

The notifier port has **one provider-neutral verb**. No `send_email`, no template, no recipient, no
vendor string — `NoticeRequest` carries an account, an obligation, a kind and an idempotency key.
Delivery follows the same three-phase discipline M9.16 uses for money: the action is committed as
`claimed`, the notice is sent with no transaction open, the evidence is recorded after. A transport
that raises is `DEFERRED`, never `FAILED` — `NoticeNotSent` is the one assertion a notifier may make
that nothing left, exactly mirroring `ProviderNotSent`.

## 12. Persistence

Six tables via migration **0027**, nothing seeded. A fresh installation collects nothing until an
operator publishes a strategy — the same posture 0025 takes on tax rates and 0026 on payment routes,
and for the same reason: a dunning schedule shipped in a migration would start chasing real
customers on an installation nobody had configured.

Two rows carry the concurrency story: `cp_billing_collection_case` has its **primary key derived
from the obligation** plus a `version` column every transition compare-and-sets against, and
`cp_billing_collection_action` has a derived id plus a conditional `planned → claimed`.

## 13. Concurrency

Proven with **two independent Applications over two independent connections** to one file-backed
database, with explicit interleaving — not threads, which may not actually overlap and produce a
green test that proved nothing. All six mandated scenarios:

1. two workers cannot open two cases for one obligation (derived PK);
2. two workers cannot execute the same action (conditional `planned → claimed`);
3. a stale worker cannot advance a case it read before (`CaseStale`);
4. a payment success racing a retry produces no second payment request;
5. an exhausted case cannot be reopened by a stale worker;
6. a recovered case cannot be moved back by a stale worker.

`CaseStale` is declared in `foundation/errors.py`, **not** persistence — the Application must react
to it and may not import persistence. Same placement and same reason as `RefundSequenceConflict` at
M9.16-R1.

## 14–15. API and Studio

25 paths at **`/recovery`**. The prefix was chosen by inspection: `/collections` is already M9.16's,
where it serves payment *collection*. Controllers decide nothing —
`test_controllers_decide_nothing` bans comparisons against signals and stage arithmetic. `/signal`
takes payment **state strings**, never a verdict: a body carrying `recovered: true` would let anyone
who can reach the endpoint close a recovery.

`recovery.html` is a Consumer (ADR-0021): no storage, no schedule arithmetic, no grace arithmetic,
no money, no vendor, and buttons taken from the server's `allowed_triggers`.

## 16. Testing

97 tests across fifteen sections: domain and identity, the decision engine, absence of a scheduler,
the ambiguity invariant, recovery and cycles, idempotency, the payment boundary, the money
boundaries, communication, operator authority, real-connection concurrency, the adversarial hunt,
HTTP, Studio, and architecture.

## 17. Defects found and fixed

**D-1 — a re-evaluated case raised instead of doing nothing.**
*Old:* a scheduler evaluating a case already `SCHEDULED` fired `SCHEDULE` again and the FSM refused
it, so a scheduler could not safely run twice. *Root cause:* no way to tell "this transition is
illegal" from "this transition has already happened". *Fix:* `TRIGGER_ESTABLISHES` plus
`CaseLifecycleService.establishes`; already-there is a no-op, any other refusal stays loud. **A
self-loop was deliberately not added** — it would publish a scheduler sweep as a business fact.
*Regression:* `test_re_evaluating_an_unchanged_case_plans_no_second_action`.

**D-2 — `ACTION_DUE` and `IN_PROGRESS` were unreachable.**
*Old:* evaluation went straight from `SCHEDULED` back to `SCHEDULED`; two published states could
never be entered — precisely the M9.16-R1 P-5 shape. *Fix:* maturity is now a real step
(`_wait_or_mature`), and `_mature_and_claim` walks `SCHEDULED → ACTION_DUE → IN_PROGRESS` one legal
edge at a time. *Regression:* `test_every_published_case_state_is_reachable`.

**D-3 — an unbounded dunning loop.** *The serious one.*
*Old:* a failed action did not advance the stage, so a `REQUEST_PAYMENT_RETRY` that failed re-planned
the same stage forever. The first end-to-end drain of the engine ran fourteen rounds and never left
stage 1 — in production that is a customer chased without end and a strategy that can never
exhaust. *Root cause:* `_advances_stage` treated *failure* as "stay here", conflating it with
*deferral*. *Fix:* a failed action **climbs the rung** — we tried what the stage asked and it did
not work, which is a completed step; only `DEFERRED`/`NOT_SENT`, where nothing reached anyone, hold
the stage. Bounded a second way: the decision row is the memo, so a repeated evaluation reuses the
action it names rather than planning another, and a stage that was attempted and could not complete
escalates to a human instead of looping. *Regression:*
`test_exhaustion_does_not_mean_the_customer_owes_nothing` drains the whole ladder.

**D-4 — a recovered case raised at a worker that did the right thing.**
*Old:* `execute_action` guarded the case before checking the payment, so a worker holding a retry
when the money landed got "this case is recovered" — indistinguishable, to a scheduler, from a real
fault. *Fix:* overtaken-by-events is checked **first** and is not an error; the action is retired as
obsolete and the fact is emitted, because a chase that did *not* happen is invisible otherwise.
Retiring is idempotent, since two workers reaching it at once is the normal shape of that race.
*Regression:* `test_a_payment_success_racing_a_retry_produces_no_second_payment_request`.

## 18. Documentation

This document, `docs/INDEX.md`, `CHANGELOG.md`, and **DL-0128**. ADR-0023 is **not** amended: no
payment ownership moved, and M9.16-R1's P-4 limitation is unchanged.

## 19. Architecture compliance

Foundation is pure (no SQL, no clock, no transport, no provider, no scheduler) and imports **no
other Billing domain** — payment facts arrive as strings, translated in one place. `PaymentSignal`
is deliberately *not* a copy of `AttemptState`: six members against nine, many-to-one on purpose,
because a duplicate enum would be a second place to fix the M9.16-R1 invariant and only one of the
two would get fixed. Deny-by-default on every new message. Billing remains absent from the runtime
composition root (ADR-0031 CR2).

## 20. Limitations

1. **No scheduler ships.** `/recovery/cases/due` and `/recovery/actions/due` are reads; production
   needs a caller outside this plane. This is deliberate, and it means an unattended deployment
   collects nothing.
2. **No notifier ships.** An unconfigured installation defers every notice. Wiring a real transport
   — and its idempotency-key behaviour — is untested against a real provider.
3. **No production provider webhook exists** (inherited from M9.16-R1 P-4), so payment state reaches
   Collections by polling or by an internal relay, not by callback.
4. A stage that repeatedly **defers** escalates rather than exhausting. That is the safe direction,
   but it means a broken transport eventually produces operator work rather than a clean failure.
5. Grace is modelled as seconds added to a stage wait. Calendar-aware grace (business days,
   holidays) is not supported.
6. `max_cycles` is enforced, but nothing expires a case that sits `ESCALATED` forever.
7. Search is bounded and applies residual predicates in memory over an indexed candidate set; it has
   not been measured against a large tenant.
8. M9.8 attribution remains an unresolved cross-domain dependency and was **not** solved here.

## 21. Final recommendation

M9.17 is complete against its gate. Collections owns only collection policy and collection facts;
Payments remains the sole owner of payment movement; Ledger remains the sole owner of financial
truth; no duplicate amount or balance exists anywhere; ambiguous payments are never automatic retry
candidates; a payment success racing a collection cannot create a duplicate payment action; stale
workers cannot overwrite recovered or exhausted cases; cases, actions and decisions are idempotent;
policy decisions are reproducible; history is append-only where it must be; no scheduler and no
provider exist inside the domain; there is no Runtime dependency; every new message is
deny-by-default; the concurrency tests use real independent connections; the full repository suite
is green; ruff is clean; and no frozen milestone was silently modified.
