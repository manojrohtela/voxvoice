# ADR-0037 — Outbox recovery and restart re-dispatch: a caller-driven, deterministic sweep

| | |
|---|---|
| **Status** | **Accepted 2026-08-19** (ratified at the M10.8 owner decision gate, **before** implementation — DOCUMENTATION_RULES §3.7). |
| **Completes** | [ADR-0035](ADR-0035-durable-write-interposition-on-the-event-bus.md) **DW7** and [ADR-0036](ADR-0036-outbox-retry-ladder-dead-letter-and-quarantine.md) **RL1**, which assign recovery to M10.8 but decide none of its questions. **It supersedes nothing.** ADR-0032, ADR-0035 and ADR-0036 remain immutable and intact; ADR-0033 and ADR-0034 are unchanged; **ED1–ED12**, **DW1–DW11**, **RL1–RL17**, **AR-31**, **AR-32** and **DL-0017** all continue to bind. |
| **Deciders** | Technical Owner |
| **Milestone** | Ratified at **M10.8**. Authorizes the M10.8 implementation and nothing beyond it. |
| **Related** | [ADR-0032](ADR-0032-event-durability-and-delivery-semantics.md) (ED2 at-least-once, ED3 sequence authority, ED11 recovery, ED12 metric identity), [ADR-0035](ADR-0035-durable-write-interposition-on-the-event-bus.md) (DW7 recovery ownership, DW8 claim nothing, DW10 runtime unwired), [ADR-0036](ADR-0036-outbox-retry-ladder-dead-letter-and-quarantine.md) (RL1 boundary, RL2 lifecycle, RL8 attempt protocol, RL13 atomicity, RL15 concurrency), [ADR-0034](ADR-0034-durable-storage-seam-core-only-packages.md) (DS3/DS4 storage scope, DS11 injected clock), [ADR-0033](ADR-0033-metric-stream-seam.md) (MS2/MS5 pull-shaped metric seam), [DL-0017](../../.voxvoice/DECISION_LOG.md), [DL-0141](../../.voxvoice/DECISION_LOG.md), [DL-0143](../../.voxvoice/DECISION_LOG.md), [Epic 10 implementation plan](../roadmap/epic10-implementation-plan.md), Constitution §A5/§A14, **AR-31**, **AR-32** |

## Status

**Accepted 2026-08-19.** This ADR authorizes **M10.8** only. Epics 5–9 remain frozen;
`platform/billing/**` is byte-for-byte unchanged; migrations `0001`–`0036` are untouched;
ADR-0023 and ADR-0031 are intact; the `v4.0.0` baseline is not reopened.

## Context

M10.6 made events durable and M10.7 made failed deliveries decidable, but **nothing in the
repository delivers an outbox entry**. `SqlDurableEventSpine` records transitions the caller has
already decided; `OutboxLadder` decides transitions nobody applies; `DeliveryTransport` ships
without an implementation. Every verb exists and no sentence calls them.

That gap is deliberate. ADR-0035 **DW7** states it plainly — *"Reading `PENDING` rows back and
re-dispatching is **M10.8**"* — and ADR-0036 **RL1** reaffirmed it after the M10.7 design gate found
the Epic 10 plan contradicting DW7. M10.7 was therefore built as a *decision-and-record* layer with
no loop, and the absence of that loop is what kept the boundary honest.

M10.8 supplies the loop. The question this ADR answers is **what shape it takes**, and the
answer is drawn from the repository rather than from general outbox-and-DLQ architecture, because
the repository has already solved this exact problem twice.

### What the repository already decided

**A periodic sweep over persisted state is caller-driven here, not scheduled.** Two independent
planes ship the same shape, with the same words:

> `# ── maintenance: TTL expiry sweep (deterministic; caller-driven) ─────────`
> `async def expire(self, *, now: float | None = None) -> int:`
> *"Expire TTL-elapsed delivered packages. Deterministic — no background timer; the caller (or a
> scheduler) invokes it. Returns the count expired."*
> — `context/runtime.py`, and verbatim in `memory/runtime.py`

A method taking `now`, defaulting to the injected clock; iterating candidates; advancing each
through an FSM; returning a count; **no background timer**; and the scheduler placed explicitly
*outside* the module, in the caller. RL1's phrase *"the recovery driver/worker/scheduler that does
it"* is a statement of **ownership**, not a requirement to build three things. This ADR builds the
driver and leaves the scheduler where the repository has twice put it.

**Recovery gets its own module.** `workflow/execution/recovery.py` is *"a thin, named surface"* over
engine logic that lives elsewhere, *"kept as its own module so recovery has an explicit,
independently-testable entry point"*. M10.8 follows it exactly.

**Envelope reconstruction already exists.** `events/persistence/mapping.py` provides
`to_event(row) -> Event` under a stated round-trip contract, so recovery invents no mapping.

## Decision

### RC1 — Scope: the loop, and only the loop

M10.8 owns **reading persisted non-terminal outbox rows, deciding which are eligible, attempting
delivery, and recording the outcome through M10.7's existing verbs.** It also retains the
**read-only durable-log re-derivation capability** that RL1 left intact.

It owns nothing else. Every one of `begin_attempt`, `mark_delivered`, `record_failure`,
`read_pending`, `due` and `to_event` already exists and is unchanged. M10.8 contributes the
sentence that calls them — approximately one module — and its tests.

### RC2 — Caller-driven recovery (owner ruling F-3)

```
OutboxRecovery(outbox, log, transport, classifier, *, clock)

    def recover(self, *, now: float | None = None, limit: int | None = None) -> int
```

Deterministic and caller-invoked, on the `context`/`memory` `expire()` precedent. Returns **the
number of delivery attempts made**.

**Forbidden inside M10.8, and asserted statically:** scheduler, cron, background worker, thread,
timer, `sleep`, polling loop, `while True`, asyncio task creation, and any runtime-lifecycle
integration. *When* recovery runs is the caller's concern, exactly as TTL expiry is today.

### RC3 — Candidate selection (owner ruling F-4)

**One sweep, both sources**, composed from the two queries M10.7 already exposes:

* `read_pending()` — `PENDING` rows, **immediately eligible**; they have never been attempted and
  no backoff has been earned, so due-time does not apply to them;
* `due(now)` — `RETRYING` rows with `next_attempt_at <= now`, **eligible only when due**.

`RETRYING` rows with `next_attempt_at > now` are **untouched** — not attempted, not counted, not
mutated. Terminal rows are **never selected**: `due()` filters `state == RETRYING`, `read_pending()`
filters `state == PENDING`, and `_guard_not_terminal` would refuse them even if a caller tried.

**No separate public recovery entry point** is created. One `recover()` is the surface.

### RC4 — Deterministic ordering

Candidates are ordered by **`(seq, event_id)`** — the ordering both underlying queries already
guarantee, applied again across the merged set so the union is deterministic and not merely each
half. `seq` is the bus-assigned value (**ED3**); recovery assigns no ordering of its own and
introduces no second sequence authority.

Determinism here is not cosmetic: it is what makes `limit` meaningful, what makes a restart
reproduce the same sweep, and what makes the tests assertable without a wall clock.

### RC5 — Attempt protocol

For each selected candidate, in order:

1. reconstruct the envelope from `evt_event` via `to_event` — recovery **must** read the log,
   because the outbox stores no payload;
2. `begin_attempt()` — **before** delivery, before the outcome is known (**RL8**);
3. `transport.deliver(event)`;
4. success ⇒ `mark_delivered()`;
5. exception ⇒ `classifier(exc)` ⇒ `record_failure(outcome, error=…)`, which applies the M10.7
   ladder.

Counting the attempt *before* delivering is the crash-safety property, not an ordering accident: a
crash mid-attempt then costs an attempt rather than granting a free one, and **a bound a crash can
widen is not a bound**.

### RC6 — No new FSM transitions

**M10.8 introduces zero transitions.** The seven ratified edges (RL2) are exactly sufficient:
`PENDING → DELIVERED | RETRYING | QUARANTINED`, `RETRYING → DELIVERED | RETRYING | DEAD_LETTERED |
QUARANTINED`. No `DEAD_LETTERED → RETRYING`, no `DEAD_LETTERED → QUARANTINED`, no
`QUARANTINED → RETRYING`, **no terminal resurrection**.

### RC7 — Terminal-state protection

`DELIVERED`, `DEAD_LETTERED` and `QUARANTINED` are terminal and recovery never re-enters them.
Four independent mechanisms enforce it, none of which relies on this module behaving:

1. `OUTBOX_LIFECYCLE` has **no outgoing edge** from any terminal state — `core.fsm.fire()` raises;
2. `TERMINAL_OUTBOX_STATES` is **derived** from the machine, so it cannot drift from it;
3. `_guard_not_terminal` refuses `begin_attempt`, `mark_delivered` and `record_failure` before any
   write;
4. neither selection query can return a terminal row.

Recovery over a table of terminal entries performs **zero writes and raises nothing** — it simply
selects nothing.

### RC8 — No redrive (RL3/RL4 preserved)

M10.8 opens no path out of `DEAD_LETTERED` or `QUARANTINED`, and adds no operator-driven quarantine.
Reopening a closed entry is a **new decision** requiring a separately authorized milestone, not a
rewrite of the old one.

### RC9 — Transport stays a port (owner ruling F-2)

`DeliveryTransport` remains a **pure port**. M10.8 ships **no concrete production transport**.
`events/recovery.py` invokes `deliver()` and must not know whether delivery is HTTP, webhook,
broker or anything else — no HTTP client, no `httpx`, no webhook adapter, no provider integration
may appear there. The production transport is a **composition-root concern for a later milestone**.

Tests use an injected test-only transport. This is what keeps M10.8 provable without creating the
second delivery mechanism blueprint risk **R-11** exists to prevent.

**`events/webhooks.py` remains byte-for-byte unchanged** (RL10). It is not a `DeliveryTransport`,
is not wrapped, and is not called: it is `async` against a synchronous persistence layer, returns
`None` on success *and* failure, and is best-effort by design.

### RC10 — Atomicity unchanged (RL13 preserved exactly)

Each attempt's state transition and its failure-history write remain atomic through the **existing
single `EventDatabase.session`**, inside the M10.7 verbs that already own them.

**No `UnitOfWork`. No second database session. No second transaction abstraction.** Persistence
responsibility does **not** move into `recovery.py`, which stays orchestration over existing verbs
and holds no session, no engine and no storage import.

Partial progress across a *sweep* is expected and correct: each entry is independent, and a sweep
interrupted after three of ten entries has durably completed three.

### RC11 — Concurrency: an accepted non-claim (owner ruling F-5)

The ratified at-least-once posture is **accepted as-is**. M10.8 introduces **no** claim/lease, row
locking, `SELECT … FOR UPDATE`, advisory lock, worker ownership, distributed lock, thread or process
coordination, deduplication, or exactly-once semantics.

**Two concurrent `recover()` invocations may select and deliver the same event.** This is
*tolerated* under **ED2**/**RL15**, not prevented — consumers are idempotent on `Event.id`, and
Billing's frozen contract is idempotent on `(event_id, meter_code)` (**ED12**).

This is recorded as a **non-claim, not a defect to be fixed**. DW8 already says the platform claims
nothing it does not have; inventing exclusivity here would be a distributed-systems claim Epic 10
has no mandate to make and DL-0017 explicitly defers. A static test asserts no exclusivity mechanism
was introduced, so the non-claim stays true rather than merely stated.

### RC12 — Batch limit (owner ruling F-6)

`limit: int | None = None`. `None` processes **all** eligible candidates; a supplied value processes
**at most** that many, in `(seq, event_id)` order. Validation follows repository convention —
a non-positive limit is refused loudly rather than silently coerced.

**No hidden default.** The bound is the caller's, explicit at the call site, in the same spirit as
ED7's prohibition on retry constants buried in call sites.

### RC13 — Crash and duplicate semantics

| Crash point | Persisted state | Re-dispatch? |
|---|---|---|
| after delivery, before `DELIVERED` commit | `PENDING`/`RETRYING`, attempt already counted | **yes** — duplicate delivery, **correct** under ED2/DW7 |
| after `begin_attempt`, before outcome commit | unchanged state, attempt counted, **no** failure row (RL13) | **yes** — the attempt is consumed; the ladder is not widened |
| after retry transition commit | `RETRYING` + absolute `next_attempt_at` | **yes, when due** |
| after permanent-failure commit | `QUARANTINED` | **no** — terminal |
| after exhaustion commit | `DEAD_LETTERED`, history retained | **no** — terminal |

`next_attempt_at` is an **absolute instant** (RL14), never a delay recomputed after restart, so a
restart cannot silently reset the ladder.

### RC14 — Re-derivation, and the M10.8/M10.10 boundary (owner ruling F-1)

**M10.8 owns the read-only durable-log re-derivation capability** required by restart and recovery:
reading a closed window back out of `evt_event` through the existing ordered reads.

**M10.8 does NOT implement `MetricStreamPort.replay()`.** That method — and the whole
`MetricStreamPort` implementation — remains owned by **M10.10**, and M10.8 must not implement,
import or wire it. The **frozen `MetricStreamPort` ABC is not altered.**

This resolves a real contradiction the M10.8 design gate surfaced between two sections of the Epic
10 plan: §M10.8 permitted implementing `MetricStreamPort.replay()` while §M10.10 assigned the entire
port, `replay` included, to M10.10 in `platform/api/`. The two could not both be executed, and the
frozen signature settles it independently — `replay(org_id, period_start, period_end)` returns
`MetricSignal`, a type produced by a decoding buffer that does not exist until M10.10 and a
`metric.*` vocabulary that does not exist until M10.8a. **The plan is amended minimally to state the
boundary; the ABC and ADR-0033 are untouched.**

### RC15 — No migration, no schema change

**M10.8 requires no migration.** Every column it reads was created by `0035` and `0036`. Migration
`0037` **is not created**; migrations `0001`–`0036` are unmodified; `evt_event`, `evt_outbox` and
`evt_outbox_failure` keep their exact M10.7 shapes.

### RC16 — Retention, metrics and runtime remain untouched

No retention, no purge, no expiry (**M10.5**, still sequenced after M10.10 per DL-0141). No metrics,
no `metric.*` emission, no `MetricStreamPort` (**M10.8a/M10.9/M10.10**). **No runtime wiring** —
`platform/runtime/**` is unchanged and `ConversationOrchestrator` still constructs a plain
`InProcessEventBus`. ADR-0035 **DW10** and ADR-0036 **RL17** stand:

> **M10.6/M10.7/M10.8 provide the durable write side, the retry ladder and the recovery driver, and
> prove their semantics — but the production runtime is still not wired to the durable sink.**

No reader may interpret M10.8 as making the live runtime durable or self-recovering.

## Verification criteria

| | Criterion |
|---|---|
| **RV1** | `PENDING` rows are recovered and reach `DELIVERED` on success; the delivered envelope equals the one in `evt_event`. |
| **RV2** | Due `RETRYING` rows are recovered; `next_attempt_at` in the past **and exactly equal to `now`** are both eligible. |
| **RV3** | `RETRYING` rows with `next_attempt_at > now` are untouched — state, `attempt_count` and `next_attempt_at` all unchanged. |
| **RV4** | `DELIVERED`, `DEAD_LETTERED` and `QUARANTINED` are never selected; a table of only terminal rows yields `recover() == 0` and **zero writes**. |
| **RV5** | Candidates are processed in `(seq, event_id)` order, deterministically, across calls and restarts. |
| **RV6** | `limit=None` processes every eligible candidate; a finite `limit` processes exactly that many; a non-positive limit is refused loudly. |
| **RV7** | A `PENDING` row's first attempt is attempt **1**; `attempt_count` increments **before** the outcome is known. |
| **RV8** | Transient failure follows the M10.7 ladder by value against a frozen clock; exhaustion reaches `DEAD_LETTERED` at exactly `max_attempts`. |
| **RV9** | Permanent failure reaches `QUARANTINED` **without** consuming the ladder. |
| **RV10** | Failure history is preserved and appended, never rewritten by recovery. |
| **RV11** | Restart (file-backed SQLite via `tmp_path`) preserves `attempt_count`, `next_attempt_at` and terminal states. |
| **RV12** | Crash-window duplicate delivery is permitted and asserted **correct**, not an error. |
| **RV13** | **No claim, lease, lock, `FOR UPDATE`, advisory lock or exclusivity mechanism exists** — asserted statically. |
| **RV14** | **No `threading`, thread, asyncio task, `Timer`, `sleep`, `while True` or polling loop exists** in M10.8 — asserted statically on executable source. |
| **RV15** | `events/recovery.py` imports no `sqlmodel`, no `sqlalchemy` and no `events.persistence`; it holds no session and constructs no concrete transport. |
| **RV16** | No `DeliveryTransport` implementation ships in production code; the test transport is test-only. |
| **RV17** | `events/webhooks.py` byte-for-byte unchanged (58 lines); `platform/runtime/**` unchanged; orchestrator still constructs a plain bus. |
| **RV18** | `evt_event` unchanged; migrations `0001`–`0036` unchanged; **no migration `0037`**; the migration registry is still `("0034","0035","0036")`. |
| **RV19** | No `MetricStreamPort` implementation, no `replay()` implementation, no `metric.*`, no retention and no purge enters M10.8. |
| **RV20** | `recover()` terminates on every input and returns the attempted count. |
| **RV21** | All M10.6 and M10.7 tests pass **unmodified**. |

## Non-goals

A production `DeliveryTransport` (RC9, later milestone) · a scheduler, worker, thread or timer
(RC2) · operator redrive and operator-driven quarantine (RC8, RL3/RL4) · any change to
`events/webhooks.py` (RL10) · `MetricStreamPort`, its implementation and `replay()` (**M10.10**,
RC14) · retention (**M10.5**) · metric vocabulary and emission (**M10.8a/M10.9/M10.10**) · runtime
wiring (RC16, DW10/RL17) · exactly-once delivery, dedup tables, two-phase commit, confirmation
handshakes · claim/lease, row locking, advisory locks, multi-process or distributed semantics ·
thread-safety claims · a distributed broker or external transports (DL-0017) · any new dependency ·
any migration · any change to `evt_event`, `evt_outbox`, `evt_outbox_failure`, migrations
`0001`–`0036`, `platform/core/**`, `platform/runtime/**`, `platform/providers/**`,
`platform/replay/**`, `platform/billing/**`, `platform/observability/**`, `platform/events/bus.py`,
ADR-0032/0033/0034/0035/0036, the frozen `MetricStreamPort` ABC, DL-0139/0140/0141/0142/0143/0144,
or `v4.0.0`.

## Dependency Direction

Unchanged and acyclic: `core ← events ← runtime`. `events/recovery.py` imports `core` + stdlib +
`events.ports` and `events.persistence.mapping` only — it is a driver over ports, not a storage
module, so **AR-10** and **AR-15** hold and storage stays confined to `events/persistence/**`
(ADR-0034 DS3/DS4). Nothing imports upward; the runtime acquires nothing.

## Consequences

**Gained.** The first component in the repository that actually delivers a durable event · restart
recovery for both never-attempted and retry-scheduled entries · an explicit, independently testable
recovery entry point on the repository's own caller-driven precedent · a written, asserted
concurrency non-claim where previously there was silence · the M10.7 ladder finally exercised
end-to-end rather than only unit-decided.

**Accepted costs.** Nothing delivers in production yet, because no concrete transport ships (RC9) —
deliberate, and the price of not becoming a webhook client · recovery must be invoked by someone,
and until a later milestone wires it, that someone is a test · two concurrent sweeps may duplicate
(RC11), accepted rather than fixed · `DEAD_LETTERED` and `QUARANTINED` accumulate without bound
until M10.5 decides their retention eligibility (RL16).

**Explicitly preserved.** ADR-0032, ADR-0035 and ADR-0036 immutable · ED1–ED12, DW1–DW11 and
RL1–RL17 intact · AR-31, AR-32 and DL-0017 preserved · `core.events.EventBus` untouched · no
producer changed · no FSM transition added · `evt_event` untouched · `platform/billing/**`
byte-for-byte unchanged · migrations `0001`–`0036` untouched · the frozen `MetricStreamPort` ABC
untouched · `v4.0.0` not re-tagged.

## Alternatives Considered

**A background worker thread or asyncio task polling `due(now)`.** Rejected — it would make Epic 10
a thread-safety claimant, which **DW8** explicitly refuses, and no repository precedent schedules
its own maintenance. The two existing sweeps state the opposite convention in their own docstrings.

**A scheduler inside `events/`.** Rejected — *when* to recover is a deployment concern, and both
`context` and `memory` place it in the caller with the same parenthetical, *"(or a scheduler)"*.

**Claim/lease columns so concurrent sweeps cannot overlap.** Rejected — RL11 forbids the columns,
DW8 forbids the claim, and the duplicate it would prevent is already **correct** under ED2. Adding
exclusivity would trade a harmless duplicate for a distributed-systems obligation Epic 10 cannot
honour.

**Making `recover()` idempotent by skipping entries attempted "recently".** Rejected — that is a
lease under another name, requires a timestamp M10.7 deliberately did not add, and would make the
attempt count depend on wall-clock proximity.

**Wrapping `events/webhooks.py` as the transport.** Rejected — RL10 requires it unchanged; it is
`async` against sync persistence and returns no outcome to classify, so wrapping it would need a
signature change and would create the second retry mechanism R-11 warns against.

**Shipping a concrete HTTP transport so M10.8 delivers something real.** Rejected by owner ruling
F-2 — it would put a network client inside `events/` and pre-empt a composition-root decision that
belongs to a later milestone.

**Separate `recover_pending()` and `recover_due()` entry points.** Rejected by owner ruling F-4 —
two surfaces would let a caller recover half the eligible work and believe it had recovered all of
it, and the ordering guarantee would apply within each half rather than across the sweep.

**Implementing `MetricStreamPort.replay()` at M10.8, as the plan's §M10.8 permitted.** Rejected by
owner ruling F-1 — the port belongs to M10.10, and the frozen signature returns a `MetricSignal`
type whose producer does not exist until then.

**A `recover_all()` with no limit.** Rejected — an unbounded sweep over an outbox of unknown size is
exactly the operational hazard `drain(limit=500)` already guards against on the Billing side.

## What this ADR does not change

It creates no schema, no migration and no runtime behaviour. It authorizes the M10.8 implementation
and nothing beyond M10.8. Every constraint in the
[Epic 10 implementation plan](../roadmap/epic10-implementation-plan.md) §1 continues to bind.
