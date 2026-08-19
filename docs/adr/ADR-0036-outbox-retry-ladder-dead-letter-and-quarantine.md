# ADR-0036 — Outbox retry ladder, dead-letter and quarantine: a deterministic, I/O-free decision-and-record layer

| | |
|---|---|
| **Status** | **Accepted 2026-08-19** (ratified at the M10.7 owner decision gate, **before** implementation — DOCUMENTATION_RULES §3.7). |
| **Completes** | [ADR-0032](ADR-0032-event-durability-and-delivery-semantics.md) **ED7** and **ED8**, and continues [ADR-0035](ADR-0035-durable-write-interposition-on-the-event-bus.md) **DW5/DW6**. **It supersedes nothing.** ADR-0032 and ADR-0035 remain immutable and intact; ADR-0033 and ADR-0034 are unchanged; **ED1–ED12**, **DW1–DW11**, **AR-31**, **AR-32** and **DL-0017** all continue to bind. |
| **Deciders** | Technical Owner |
| **Milestone** | Ratified at **M10.7**. Authorizes the M10.7 implementation and nothing beyond it. |
| **Related** | [ADR-0032](ADR-0032-event-durability-and-delivery-semantics.md) (ED7 bounded ladder, ED8 DLQ vs quarantine), [ADR-0035](ADR-0035-durable-write-interposition-on-the-event-bus.md) (DW5 outbox, DW6 M10.6/M10.7 split, DW7 recovery), [ADR-0034](ADR-0034-durable-storage-seam-core-only-packages.md) (DS3/DS4 storage scope, DS8/DS9 migrations, DS11 injected clock), [DL-0017](../../.voxvoice/DECISION_LOG.md), [DL-0141](../../.voxvoice/DECISION_LOG.md), [DL-0142](../../.voxvoice/DECISION_LOG.md), [Epic 10 implementation plan](../roadmap/epic10-implementation-plan.md), Constitution §A5/§A14, **AR-31**, **AR-32** |

## Status

**Accepted 2026-08-19.** This ADR authorizes **M10.7** only. Epics 5–9 remain frozen;
`platform/billing/**` is byte-for-byte unchanged; migrations `0001`–`0035` are untouched;
ADR-0023 and ADR-0031 are intact; the `v4.0.0` baseline is not reopened.

## Context

ADR-0032 **ED7** requires a bounded, explicitly configured retry ladder with injected timing, and
**ED8** requires dead-letter and quarantine to be *distinct durable states*. ADR-0035 **DW6**
assigned both to **M10.7** and left M10.6 owning only `PENDING` and `DELIVERED`.

The M10.7 design gate established four things from the repository rather than from convention:

* the mechanism ED7 describes **already exists** in `core/` and is reusable;
* the vocabulary ED8 needs **does not** exist anywhere reachable from `events/`;
* the delivery function M10.7 would have to drive **discards its outcome**; and
* two ratified documents **disagreed** about who owns restart re-delivery.

This ADR settles all four.

### Repository evidence

| # | Fact | Evidence |
|---|---|---|
| **R-1** | `core.fsm` provides `define()`, `StateMachineDefinition.terminal_states()`, and a `StateMachine` whose `fire()` raises `TerminalStateError` on a terminal state and `InvalidTransition` on an undefined edge. `is_terminal` is derived from *"no outgoing edge"*, so a self-loop keeps a state live. Importable by `events/` (`core` budget). | `core/fsm/machine.py:100-230` |
| **R-2** | `core.policies.RetryPolicy` exists with `max_attempts`, `base_delay_s`, `multiplier`, `max_delay_s` and `delay_for(attempt)` — **1-based**, deterministic exponential, capped, **explicitly no jitter**, validated in `__post_init__`. | `core/policies.py:60-77` |
| **R-3** | The repository already runs a **persisted** retry ladder: `cp_billing_dunning` carries `state` and **`next_attempt_at: float \| None`**. The column name is not invented here. | `billing/persistence/models.py:365-375` |
| **R-4** | The persisted-FSM precedent states its own design rule — *"What it **refuses** is the specification"* — and refuses every edge out of `RECOVERED`, `EXHAUSTED` and `CANCELLED`, so *"a policy edit [cannot] resurrect a closed recovery."* | `billing/foundation/commercial_collections.py:203-280` |
| **R-5** | The idempotent-scheduler convention is established: re-establishing the state you are already in is *"nothing to do"*, and any other refusal *"stays loud"* — because *"a scheduler that cannot safely run twice is a scheduler nobody can operate."* | `commercial_collections.py:283-300` |
| **R-6** | The append-only evidence-table precedent: `BillingReconciliationRow` — *"Append-only reconciliation evidence; the finding carries no domain id, so the row uses a surrogate key"* — surrogate `id`, indexed domain reference, **no FK**, `observed_at: float`. | `billing/persistence/models.py:382-393` |
| **R-7** | `reasoning.RetryController` is the in-repo model for an I/O-free ladder: *"it computes the backoff delay and records it; the caller decides whether to honour it"*, *"never performs networking and never sleeps"*, replay-safe, 1-based attempts. | `reasoning/retry.py:1-100` |
| **R-8** | The retryability *concept* appears three times (`RetrySpec.retryable_only`, `RetryController(retryable=…)`, `_RECOVERABLE`) and is **always a caller-supplied predicate**, never a shared enum. | `api/adapters/resilience.py`; `reasoning/retry.py`; `tool/observability/failure.py` |
| **R-9** | The only failure taxonomy (`FailureCategory`) lives in `platform/tool/**` — a plane `events/` may not import. `core.errors` has **no** transient/permanent distinction. | `tool/observability/failure.py:13-32`; `core/errors.py` |
| **R-10** | `events/webhooks.py::emit_event` is `async`, returns `None` on success **and** failure, and swallows `httpx.HTTPError`. **It yields no outcome a ladder could branch on.** | `events/webhooks.py:33-58` |
| **R-11** | Blueprint risk **R-11** is live: *"new outbox competes with `events/webhooks.py`"*, and §11 requires the relationship be checked *"to avoid two competing retry mechanisms."* | blueprint:187-188, 323 |
| **R-12** | Committed `evt_outbox` is exactly `{event_id, seq, state, created_at, delivered_at}`; `state` is VARCHAR precisely so M10.7 may extend it additively (AR-32). | `events/persistence/models.py` |
| **R-13** | **ADR-0035 DW7** says re-dispatch is M10.8; the Epic 10 plan called M10.8 *"read-only and side-effect-free."* Re-dispatch is a side effect — the two disagreed. | ADR-0035 DW7; plan §M10.8 |

## Decision

### RL1 — The M10.7/M10.8 boundary (resolves R-13)

**ADR-0035 DW7 remains authoritative.** **M10.8** owns reading persisted pending/retryable rows
after restart, deciding which are due for re-delivery, performing restart/recovery re-dispatch, and
the recovery driver/worker/scheduler that does it. The Epic 10 plan's *"read-only and
side-effect-free"* description of M10.8 is **narrowed** by this ruling; the plan is amended to say
so and nothing else about M10.8 is rewritten. **ADR-0035 is not modified.**

**M10.7 contains no polling, no scheduler, no worker, no recovery loop, no restart re-dispatch, no
`sleep`, no timer, no thread and no automatic redelivery.** It may expose deterministic queries such
as `due(now)`, but it must never execute the recovery action.

This is what makes M10.7 a *decision-and-record* layer: it decides **what should happen** to an
entry and records it; **when** anything happens is not its concern.

### RL2 — Lifecycle: five states, seven transitions, three terminal

Declared with **`core.fsm.define`** (R-1). No hand-rolled FSM.

| From | Trigger | To |
|---|---|---|
| `PENDING` | `deliver` | `DELIVERED` |
| `PENDING` | `fail_transient` | `RETRYING` |
| `PENDING` | `fail_permanent` | `QUARANTINED` |
| `RETRYING` | `deliver` | `DELIVERED` |
| `RETRYING` | `fail_transient` | `RETRYING` (self-loop) |
| `RETRYING` | `exhaust` | `DEAD_LETTERED` |
| `RETRYING` | `fail_permanent` | `QUARANTINED` |

**Terminal: `DELIVERED`, `DEAD_LETTERED`, `QUARANTINED`** — each has no outgoing edge, so
`core.fsm` derives terminality rather than being told it.

**Refused, and the refusal is the specification (R-4):**

* **`RETRYING → PENDING`** — `PENDING` means *never attempted*. Returning would erase attempt
  history and let a bounded ladder restart forever, defeating **ED7**.
* **`DELIVERED → anything`** — a delivered entry cannot be reopened by a stale caller or a replayed
  event.
* **`DEAD_LETTERED → anything`** and **`QUARANTINED → anything`** — terminal for automation.
* **No implicit transitions.** Every illegal transition stays loud through `core.fsm`'s existing
  `InvalidTransition` / `TerminalStateError`. Where a caller may legitimately re-establish the state
  it is already in, the repository's idempotent-scheduler convention applies (R-5): already
  established means *nothing to do*; anything else stays loud.

`PENDING → QUARANTINED` exists so a deterministically-doomed entry reaches quarantine **without
entering the ladder**, which is ED8's *"quarantine does not consume the retry ladder"* expressed
structurally.

### RL3 — No redrive (ED8 boundary, explicit)

**`DEAD_LETTERED → RETRYING` is not implemented.** There is no redrive API. `DEAD_LETTERED` is
terminal for M10.7 automation. A future operator-control milestone may address redrive **only under
separate authorization**; M10.7 records this as an explicit non-goal rather than leaving it
ambiguous. The operational consequence — a dead-lettered entry stays dead-lettered — is accepted
knowingly, and follows R-4's rule that a closed case is reopened by a *new* decision, never by
rewriting the old one.

### RL4 — No operator-driven quarantine

**`DEAD_LETTERED → QUARANTINED` is not implemented**, and there is no operator-triggered quarantine
API. Quarantine arises **only** automatically, from a permanent (deterministic) failure:
`PENDING → QUARANTINED` and `RETRYING → QUARANTINED`. `QUARANTINED` is terminal and has no outgoing
transition.

### RL5 — Delivery outcome vocabulary

M10.7 introduces exactly:

```
DeliveryOutcome : SUCCESS | TRANSIENT_FAILURE | PERMANENT_FAILURE
```

Three members, because the ladder has exactly three answers. The outcome reaches the ladder through
an **injected classifier/predicate**, which is the repository's established shape for retryability
(R-8) and the only shape available given R-9 (the existing taxonomy is in a plane `events/` may not
import).

* No transport-specific exception hierarchy is introduced.
* `platform/tool`'s failure taxonomy is **not** imported into `events/`.
* HTTP/webhook exception classification is **never** hardcoded into the ladder.
* Ambiguous categories — authentication/authorization and configuration failures — get **no
  built-in rule**. The caller's classifier owns them. Tests supply explicit classifiers mapping such
  failures either way, and the ladder stays unaware the categories exist.

### RL6 — Retry policy: reuse `core.policies.RetryPolicy`

**No second retry-policy type is created.** M10.7 reuses R-2 with delivery-specific configuration,
explicitly constructed and injected:

```
max_attempts = 5      base_delay_s = 30.0
multiplier   = 2.0    max_delay_s  = 3600.0
```

Existing `RetryPolicy` semantics remain authoritative: **1-based** attempts, deterministic
exponential backoff, capped by `max_delay_s`. The resulting ladder is
**30s → 60s → 120s → 240s → 480s**, then exhaustion.

**No retry number appears inside transition logic** — the policy is a parameter, satisfying ED7's
*"no hidden retry constants, no magic numbers buried in a call site."*

*Recorded honestly:* at these values the `max_delay_s` cap is never reached (attempt 5 yields 480s),
so the cap is configured but not exercised by the default ladder. It is nonetheless real and is
tested with a policy that does reach it.

### RL7 — No jitter

Backoff is **deterministic and replay-safe**. No randomness is introduced, preserving the property
`core.policies` and `reasoning.RetryController` already state (*"no jitter here"*, *"replay-safe"*).

### RL8 — Attempt semantics

* Attempts are **1-based**; the initial delivery is **attempt 1**.
* `attempt_count` increments **when an attempt begins, before its outcome is known** — so a crash
  mid-attempt cannot under-count and grant a free retry.
* Initial `attempt_count = 0`; first attempt begins ⇒ `1`.
* First transient failure ⇒ `RETRYING`, `attempt_count = 1`,
  `next_attempt_at = clock() + policy.delay_for(1)`.
* When `attempt_count` reaches `max_attempts` and the outcome is transient ⇒ `DEAD_LETTERED`,
  `attempt_count = max_attempts`, `dead_lettered_at = clock()`.
* Success increments nothing further.
* **A permanent failure does not consume the ladder** (ED8): `attempt_count` is left unchanged.

### RL9 — The ladder is a pure decision function

M10.7 ships:

1. a pure, **I/O-free `OutboxLadder`**;
2. `DeliveryOutcome`;
3. a framework-neutral **`DeliveryTransport`** contract.

The ladder receives *(current state, attempt count, `DeliveryOutcome`, `RetryPolicy`, current clock
value)* and returns a deterministic transition decision:

* transient, attempts remain ⇒ `RETRYING`, `next_attempt_at = now + policy.delay_for(attempt_count)`
* transient, attempts exhausted ⇒ `DEAD_LETTERED`
* permanent ⇒ `QUARANTINED`
* success ⇒ `DELIVERED`

It performs **no network I/O, no database I/O**, and never sleeps, loops, polls, schedules, starts
threads, recovers or dispatches. This is R-7's split applied one layer down, and it is the
structural reason M10.7 cannot become the recovery worker.

`DeliveryTransport` is a **contract only**. **No concrete webhook transport is shipped at M10.7.**

### RL10 — `events/webhooks.py` stays byte-for-byte unchanged

M10.6 left it untouched; M10.7 does too. It is best-effort by design, it is `async` against a sync
persistence layer, and it returns no outcome (R-10) — so it cannot drive a ladder without a
signature change that is not in scope. **No second retry mechanism is created there** (R-11).

The pre-existing behaviour that `json.dumps(...)` sits outside its `try` (so a serialization failure
propagates from a "best-effort" function) is **recorded here as a deferred issue and deliberately
not fixed at M10.7**.

### RL11 — Schema and migration 0036

`evt_outbox` receives exactly five new columns, and nothing else:

| Column | Type |
|---|---|
| `attempt_count` | integer, default `0` |
| `next_attempt_at` | `float \| None` (indexed) |
| `dead_lettered_at` | `float \| None` |
| `quarantined_at` | `float \| None` |
| `quarantine_reason` | string |

Two explicit terminal timestamps, **not** a generic `terminal_at`: *"we tried and gave up"* and
*"this can never succeed"* are different operational facts (ED8) and a shared column would make an
operator re-derive which one happened. **`last_error` is deliberately absent** — failure history is
the authoritative error evidence (RL12).

**Forbidden:** claim/lease fields, worker fields, retry-history JSON blob, retention fields, metric
fields, and any change to `evt_event`.

**Migration `0036`** follows `0035`, leaves `0034` and `0035` unchanged, extends `evt_outbox`,
creates `evt_outbox_failure`, uses `evt_`-prefixed indexes (certification control **NS-2** checks
Billing index names against every table in the process), is idempotent, has a documented no-op
`downgrade`, and is appended to the ordered registry. **SQLite `ALTER TABLE ADD COLUMN` is
authorized** for the new `evt_outbox` columns — verified to preserve existing rows and to backfill
`attempt_count = 0`. **No general schema-migration framework is introduced.**

### RL12 — Failure history: `evt_outbox_failure`, append-only

A **separate append-only evidence table**, on R-6's shape:

| Column | Notes |
|---|---|
| `id` | surrogate integer primary key — a failure carries no domain identity |
| `event_id` | indexed; **no database FK**, per repository convention |
| `attempt` | integer |
| `outcome` | string |
| `occurred_at` | float, from the **injected clock** |
| `error` | text representation preserving the failure evidence |

Indexed/ordered to support `(event_id, attempt)`. It supports **N failure records per event**, which
is exactly what ED8's *"with its failure history retained"* requires and what a single `last_error`
column could never provide. **No failure-history column is added to `evt_event`** (ED5), and **no
update or delete API is provided** for history records.

### RL13 — Atomicity

Every state transition and its failure-history write use the **existing `EventDatabase.session`
transaction** — one commit, all-or-nothing:

| Transition | One transaction contains |
|---|---|
| transient failure | `evt_outbox` update **+** `evt_outbox_failure` insert |
| exhaustion | `evt_outbox` → `DEAD_LETTERED` **+** final failure insert |
| permanent failure | `evt_outbox` → `QUARANTINED` **+** failure insert |
| success | `evt_outbox` → `DELIVERED` **+** `delivered_at` |

A commit failure rolls back the complete transition. A `RETRYING` row whose failure row is missing
would be the fact/event disagreement ED6 forbids, one layer up.

**No `UnitOfWork` is introduced. No second transaction abstraction. No second database or session.**

### RL14 — Clock

The existing injected `Callable[[], float]` idiom (ADR-0034 DS11). **No `time.time()` on the M10.7
transition path.** `next_attempt_at` is persisted as an **absolute timestamp**, never a delay
recomputed after restart — matching `cp_billing_dunning` (R-3) and ensuring a restart cannot
silently reset the ladder. Tests use a frozen clock, assert `next_attempt_at` by value, and contain
no sleeps and no wall-clock assertions.

### RL15 — Duplicates and concurrency, unchanged

At-least-once stands (**ED2**). A crash after delivery but before the `DELIVERED` commit results in
redelivery and a duplicate at the consumer; consumers are idempotent on `Event.id`, and Billing's
frozen contract is idempotent on `(event_id, meter_code)` (**ED12**). M10.7 attempts **no**
exactly-once, two-phase commit, dedup table or confirmation handshake.

**M10.7 requires no concurrency machinery** — no worker exclusivity, claim/lease, row locking,
`SELECT … FOR UPDATE`, advisory locks, process ownership or scheduler coordination — because under
RL9 it owns no loop and no worker, so there is nothing to contend. It makes no thread-safety claim
(DW8) and introduces no distributed semantics (DL-0017 defers the broker).

### RL16 — Retention interaction (recorded, not implemented)

M10.7 implements no retention and changes nothing in M10.5, which remains sequenced after M10.10
(DL-0141). It records the consequence for that milestone: `PENDING` and `RETRYING` are **un-drained**;
`DELIVERED` is drained; **`DEAD_LETTERED` and `QUARANTINED` never arrived**, so under ED9 they are
un-drained too and are the very evidence ED8 requires to be retained. Whether terminal-failed entries
ever become retention-eligible is an **M10.5 decision this ADR creates and does not make**.

### RL17 — Runtime remains unwired

M10.7 wires nothing into the production runtime. `platform/runtime/**` is unchanged and
`ConversationOrchestrator` still constructs a plain `InProcessEventBus`. ADR-0035 **DW10** stands:

> **M10.6/M10.7 provide the durable write-side capability and prove its semantics, but the
> production runtime is not yet wired to the durable sink.**

## Verification criteria

| | Criterion |
|---|---|
| **RV1** | Every legal transition fires; **every** forbidden transition raises through `core.fsm` (`InvalidTransition` / `TerminalStateError`). |
| **RV2** | `terminal_states() == {DELIVERED, DEAD_LETTERED, QUARANTINED}`; every state is provably reachable. |
| **RV3** | `RETRYING → PENDING` is refused; no terminal state transitions. |
| **RV4** | Initial delivery is attempt 1; `attempt_count` increments before the outcome is known. |
| **RV5** | Backoff is exponential and asserted **by value** against a frozen clock; the `max_delay_s` cap is proven with a policy that reaches it. |
| **RV6** | Exhaustion occurs at exactly `max_attempts` ⇒ `DEAD_LETTERED`; the ladder is bounded and never infinite. |
| **RV7** | Permanent failure ⇒ `QUARANTINED` directly, and **does not** change `attempt_count`. |
| **RV8** | `quarantine_reason`, `dead_lettered_at` and `quarantined_at` are persisted. |
| **RV9** | Failure history has **one row per failed attempt**, is append-only (no update/delete API), and survives into `DEAD_LETTERED`. |
| **RV10** | A successful transition creates **no** failure-history row. |
| **RV11** | Transition + failure-history insert are atomic; a forced commit failure rolls **both** back. |
| **RV12** | Restart (file-backed SQLite, `tmp_path`) preserves `attempt_count`, `next_attempt_at` and terminal states. |
| **RV13** | `due(now)` is deterministic. |
| **RV14** | **No worker, scheduler, poll, `sleep`, timer, thread, claim/lease or `FOR UPDATE` exists in M10.7** — asserted statically. |
| **RV15** | `events/webhooks.py` is byte-for-byte unchanged and contains no retry mechanism. |
| **RV16** | `platform/runtime/**` unchanged; no runtime wiring; no retention and no `metric.*` implementation. |
| **RV17** | Migration registry is `("0034","0035","0036")`, contiguous; `0034` and `0035` still own only their own tables; `0036` is idempotent with a no-op `downgrade`; every new index is `evt_`-prefixed. |
| **RV18** | All M10.6 tests and all existing event-bus tests pass **unmodified**, except the pre-authorized migration-chain assertions. |

## Non-goals

Recovery, restart re-dispatch and the recovery driver (**M10.8**, RL1) · operator redrive (RL3) ·
operator-driven quarantine (RL4) · a concrete webhook transport (RL9) · any change to
`events/webhooks.py`, including its known `json.dumps` behaviour (RL10) · retention (**M10.5**) ·
metric vocabulary and the Billing seam (**M10.10**/ADR-0033) · runtime wiring (RL17) · exactly-once
delivery, dedup tables, two-phase commit · claim/lease, row locking, advisory locks, multi-process or
distributed semantics · thread-safety claims · a distributed broker or external transports (DL-0017)
· any new dependency · any change to `evt_event`, migrations `0001`–`0035`, `platform/core/**` (beyond
*consuming* `core.fsm` and `core.policies`), `platform/runtime/**`, `platform/providers/**`,
`platform/replay/**`, `platform/billing/**`, `platform/observability/**`, ADR-0032/0033/0034/0035,
DL-0139/0140/0141/0142, or `v4.0.0`.

## Dependency Direction

Unchanged and acyclic: `core ← events ← runtime`. M10.7 **consumes** `core.fsm` and `core.policies`
and modifies neither. Storage stays confined to `events/persistence/**` (ADR-0034 DS3/DS4); the
ladder, the outcome vocabulary and the transport contract are `core` + stdlib only.

## Consequences

**Gained.** ED7's bounded ladder and ED8's distinct durable states, implemented with the
repository's own FSM and policy types rather than new ones · a durable, per-attempt failure record
where M10.6 had only a non-durable in-process counter · the `next_attempt_at` a future recovery
driver needs · an M10.7/M10.8 boundary that is now written down instead of contradictory.

**Accepted costs.** A third `evt_` table and a fourth migration · the first `ALTER TABLE` in this
chain · `DEAD_LETTERED` is permanently terminal until a redrive milestone is authorized (RL3) ·
M10.7 ships a `DeliveryTransport` contract with no implementation, so nothing delivers anything yet —
deliberate, and the price of not becoming the recovery worker · the M10.6 durable-write-failure
limitation (ADR-0035 DW4) is **not** closed by this milestone, because it concerns writes to the log
itself rather than deliveries out of the outbox.

**Explicitly preserved.** ADR-0032 and ADR-0035 immutable · ADR-0033/0034 unchanged · ED1–ED12 and
DW1–DW11 intact · AR-31, AR-32, DL-0017 preserved · `core.events.EventBus` untouched · no producer
changed · `evt_event` untouched · `platform/billing/**` byte-for-byte unchanged · migrations
`0001`–`0035` untouched · `v4.0.0` not re-tagged.

## Alternatives Considered

**Retry logic inside `events/webhooks.py`.** Rejected — it creates exactly the *"two competing retry
mechanisms"* blueprint R-11 warns against, and webhooks are not the outbox's only future
destination (DW5).

**Retry logic inside `events/persistence/**`.** Rejected — persistence would perform I/O and own
policy, dissolving the ADR-0034 separation the storage seam exists to create.

**A worker/scheduler that polls `next_attempt_at` and delivers.** Rejected at M10.7 — that *is* the
recovery driver, which RL1 assigns to M10.8. Building it here would have both milestones own it.

**Failure history as columns on `evt_outbox`.** Rejected — one row per event cannot hold N attempts,
and a `last_error` column keeps only the newest, destroying precisely what ED8 requires DLQ to
retain.

**Failure history on `evt_event`.** Rejected — ED5 forbids an update path on the append-only log.

**A second retry-policy type in `events/`.** Rejected — `core.policies.RetryPolicy` already provides
the exact mechanism ED7 describes; duplicating it would create a second source of truth for backoff.

**A built-in transport exception taxonomy.** Rejected — the only existing taxonomy is in a plane
`events/` may not import (R-9), and every in-repo precedent takes retryability as a caller-supplied
predicate (R-8). Hardcoding HTTP semantics into a transport-agnostic package would be the wrong
coupling.

**Jitter.** Rejected — it would break the deterministic, replay-safe property both existing retry
implementations state, and would require injected randomness M10.7 has no mandate to add.

**Operator redrive and operator-driven quarantine.** Rejected for M10.7 — ED8 describes neither, and
R-4's precedent reopens a closed case with a *new* decision rather than an edge back into the FSM.

**A generic `terminal_at` column.** Rejected — it would collapse the two operational facts ED8
insists must be *"distinguishable by state, not by log-message archaeology."*

## What this ADR does not change

It creates no code, no schema and no migration. It authorizes the M10.7 implementation and nothing
beyond M10.7. Every constraint in the
[Epic 10 implementation plan](../roadmap/epic10-implementation-plan.md) §1 continues to bind.
