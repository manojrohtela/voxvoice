# ADR-0035 — Durable write interposition on the in-process Event Bus: transaction model, ordering, and durable-write failure semantics

| | |
|---|---|
| **Status** | **Accepted 2026-08-18** (ratified at the M10.6 owner decision gate, **before** any M10.6 implementation — DOCUMENTATION_RULES §3.7). |
| **Completes** | [ADR-0032](ADR-0032-event-durability-and-delivery-semantics.md), **where ADR-0032 is silent**. It supersedes nothing, amends nothing, and weakens no invariant. **ADR-0032 remains immutable and intact**; [ADR-0033](ADR-0033-metric-stream-seam.md) and [ADR-0034](ADR-0034-durable-storage-seam-core-only-packages.md) remain unchanged. **ED1–ED12 are preserved in full**, as are **AR-31**, **AR-32** and **[DL-0017](../../.voxvoice/DECISION_LOG.md)**. |
| **Deciders** | Technical Owner |
| **Milestone** | Ratified at **M10.6** (decision gate). Authorizes the M10.6 implementation that follows it; authorizes nothing beyond M10.6. |
| **Related** | [ADR-0032](ADR-0032-event-durability-and-delivery-semantics.md) (ED1–ED12), [ADR-0033](ADR-0033-metric-stream-seam.md), [ADR-0034](ADR-0034-durable-storage-seam-core-only-packages.md), [ADR-0004](ADR-0004-event-driven-core.md) (event-driven core), [ADR-0006](ADR-0006-single-system-of-record.md), [DL-0017](../../.voxvoice/DECISION_LOG.md) (M4.1 Event Bus; defers outbox/retry/DLQ), [DL-0141](../../.voxvoice/DECISION_LOG.md) (M10.5 deferral), [Epic 10 implementation plan](../roadmap/epic10-implementation-plan.md), [event-platform.md](../runtime/event-platform.md), Constitution §A5 / §A14, ARCHITECTURE_RULES **AR-2** / **AR-10** / **AR-15** / **AR-31** / **AR-32** |

## Status

**Accepted 2026-08-18.** This ADR authorizes the **M10.6** implementation and nothing further. It is
a *completion* record, not a course correction: ADR-0032 left three questions unanswered, this ADR
answers them, and every ADR-0032 decision continues to bind exactly as written.

Epics 5–9 remain frozen. `platform/billing/**` is byte-for-byte unchanged. Migrations `0001`–`0034`
are untouched. `ADR-0023` and `ADR-0031` are intact. The `v4.0.0` baseline is not reopened.

## Context — why ADR-0032 could not be implemented as written

ADR-0032 **ED6** requires that *"an event destined for durable delivery is written in the same
transaction as the state change that produced it."* The M10.6 design gate established, from the
repository rather than from convention, that this cannot be executed as stated, and that two further
questions are entangled with it. All three are settled below.

### Repository evidence

Verified by direct read at commit `5b9154a`.

| # | Fact | Evidence |
|---|---|---|
| **F-1** | `publish()` stamps `seq`, appends to the FIFO deque, and — when not already dispatching — drains to completion, **all in one method**. There is no hook, callback or override point between stamping and dispatch. | `platform/events/bus.py:145-164` |
| **F-2** | The bus is the **sole** sequence authority: `self._seq += 1; stamped = replace(event, seq=self._seq)`. | `platform/events/bus.py:151-152` |
| **F-3** | A reentrant publish returns the stamped event immediately and lets the running drain pick it up. | `platform/events/bus.py:154-155` |
| **F-4** | `shutdown()` **discards** the queue: events accepted but not yet dispatched are lost by design. | `platform/events/bus.py:176-180` |
| **F-5** | The `core.events.EventBus.publish` docstring is contractual and explicit — *"**Must not raise into the producer**"*. This is a `core/` contract, independent of and stronger than ED10. | `platform/core/events.py:264-268` |
| **F-6** | `publish()` does raise `InvalidEvent` for a non-`EventType`, before any state change — a pre-existing precondition check, not a delivery failure. Out of scope here. | `platform/events/bus.py:146-147` |
| **F-7** | The runtime's bus is **constructed inline, not injected**: `self._event_bus = InProcessEventBus()` inside `ConversationOrchestrator.begin()`; `__init__` takes only `context` and `on_transition`. | `platform/runtime/orchestrator/orchestrator.py:66-71, 81, 175` |
| **F-8** | The only runtime producers are `RuntimeEventBridge` (via `EventPublisher`) and `core.module.consumer`. Neither touches a database. | `platform/runtime/event_bridge.py:53`; `platform/core/module/consumer.py:105` |
| **F-9** | `platform/runtime/**` contains **no** `Database`, `Session`, `UnitOfWork` or transaction of any kind. The engines are in-memory. | grep across `platform/runtime` |
| **F-10** | **The repository already runs a transactional outbox, and it commits durably first.** `SqlUnitOfWork.commit()`: *"Commit the DB transaction first; publish events only after durable success."* `rollback()`: *"Undo the repository writes and discard the staged events together."* | `platform/api/persistence/unit_of_work.py:44-60` |
| **F-11** | That outbox stages `ApplicationEvent` through `ApplicationEventEmitter` — a **different envelope on a different seam**. It never reaches `InProcessEventBus` or `core.events.Event`. | `platform/api/persistence/unit_of_work.py:21-27` |
| **F-12** | `SqlAuditStore.emit()` writes through the **shared** `Database.session`, so the audit row and the business write commit or roll back together — ED6's cited precedent, and it works **because the producer already had a transaction**. | `platform/api/persistence/audit_store.py:1-17` |
| **F-13** | The M10.2 port deliberately carries no delivery state: *"the outbox is M10.6, the retry ladder and DLQ are M10.7."* | `platform/events/ports.py` |
| **F-14** | `DurableEventLogError` already states the constraint — *"Never raised into a producer (AR-31)"* — without saying what happens instead. | `platform/events/ports.py` |
| **F-15** | `append_many` is contractually one transaction, all-or-nothing — the only transactional guarantee that exists in `events/` today. | `platform/events/ports.py`, `durable_log.py` |
| **F-16** | `EventDatabase` owns one `Session` and disposes its engine on `close()`. No thread-safety claim is made anywhere. | `platform/events/persistence/database.py` |
| **F-17** | `events/webhooks.py` remains 58 lines, best-effort, no retry. Any outbox must not become a second competing delivery mechanism. | `platform/events/webhooks.py` |
| **F-18** | The Epic 10 plan anticipated this ADR by name: *"If that resolution requires a new architectural decision beyond ADR-0032/0033/0034 — for example a persistence seam on `InProcessEventBus` — it requires a new ADR, ratified before implementation."* | [epic10-implementation-plan.md](../roadmap/epic10-implementation-plan.md) §M10.6 |

**F-10 is load-bearing.** The ordering question is not decided here by appeal to a general outbox
pattern. It is decided by this repository's own production code, which already resolved it: durable
first, deliver second, and a rollback discards the staged events.

## Decision

### DW1 — Transaction model: the durable append *is* the state change (D-1)

For a runtime-originated event, **no originating durable state transaction exists** (F-8, F-9). The
event is therefore not an announcement *about* a durable fact — it **is** the durable fact. The
transaction is the single-statement transaction on `evt_event` that `SqlDurableEventLog.append`
already performs.

ED6's guarantee — *"a fact and the event announcing it can never disagree about whether they
happened"* — is satisfied **maximally**, not by exemption: there is exactly one persisted artefact,
so disagreement is not representable.

**ED6 is fully binding and is NOT amended, weakened, exempted or superseded.** Where a producer
*does* hold an originating transaction, the event and that state change **must still share the same
transaction** — as `SqlUnitOfWork` + `SqlAuditStore` already do (F-10, F-12) on a seam Epic 10 does
not touch, and as any future durable producer must. `append_many` remains the existing all-or-nothing
transactional primitive for the multi-event case (F-15).

**No database, `UnitOfWork`, transaction or persistent state is introduced into `platform/runtime/`
or the voice engines.** A transactional producer is not invented.

### DW2 — Interposition: an additive, default-disabled persistence seam inside `InProcessEventBus` (D-2)

The durable write is placed **inside** `InProcessEventBus.publish()`, because that is the only
position from which it can follow the sequence authority and precede every subscriber (F-1, F-2).

The seam **must**:

* occur **after** the bus assigns `Event.seq`;
* occur **before** the event is enqueued;
* occur **before any subscriber can observe the event**;
* preserve the bus as the **sole** sequence authority (**ED3**);
* preserve the existing `EventBus` contract;
* preserve reentrant-publish behaviour (F-3);
* preserve FIFO dispatch (**ED4**);
* preserve subscriber isolation;
* be **optional and default-disabled**;
* require **no change to `core.events.EventBus`** (**ED1**);
* require **no producer changes** (**ED1**, **DL-0017**).

`platform/events/bus.py` may be modified **only additively** for this seam. It may import
`DurableEventLog` from `platform/events/ports.py`, and **must not** import `sqlmodel`, `sqlalchemy`
or `events.persistence` (**ADR-0034 DS3/DS4**).

The subclass alternative — a `DurableEventBus(InProcessEventBus)` reimplementing `publish()` over the
parent's private state — is **rejected**. It would duplicate the platform's most invariant-dense
method with nothing to detect divergence.

**ED1 is preserved.** The seam is on an *implementation*, never on the `core.events.EventBus` ABC.
No abstract method is added, no signature changes, and a producer holding an `EventPublisher` still
cannot tell whether the bus behind it is durable.

### DW3 — Ordering model

`publish()` executes in this order. Steps marked **[unchanged]** are the current implementation
byte-for-byte.

| Step | Action | |
|---|---|---|
| 1 | Validate `event.type` is an `EventType`; raise `InvalidEvent` otherwise | **[unchanged]** (F-6) |
| 2 | If shut down: log and return the event undispatched | **[unchanged]** (F-4) |
| 3 | `self._seq += 1`; `stamped = replace(event, seq=self._seq)` | **[unchanged]** — sole sequence authority (**ED3**) |
| 4 | **Durable append of `stamped`** through the injected port | **NEW** — after stamping so `seq` persists verbatim (**ED3**); before enqueue so no subscriber can precede durability (**ED6**); idempotent on `Event.id` so redelivery is ordinary traffic (**ED2**) |
| 4a | On failure: do **not** enqueue; invoke the failure hook; account the failure; return `stamped` | **NEW** — see **DW4** |
| 5 | `self._queue.append(stamped)` | **[unchanged]** |
| 6 | `if self._dispatching: return stamped` | **[unchanged]** — the reentrant event was already persisted at **its own** step 4, so **ED6** holds for reentrant publishes with no special case |
| 7 | Set `_dispatching`; drain FIFO through the dispatcher | **[unchanged]** — ordering (**ED4**) and subscriber isolation untouched |
| 8 | `finally`: clear `_dispatching`; clear the queue if shut down | **[unchanged]** |

**Established invariant: every event a subscriber observes is already durable.** It holds for the
top-level case (steps 4 → 7) and for the reentrant case (step 4 of the inner call precedes step 7 of
the outer drain), with no branch distinguishing them.

Durable-write order equals publish-call order equals `seq` order. In the reentrant case persistence
order and dispatch order may differ; **ED4** constrains *read-back* order, which is `seq`-ordered by
construction (M10.2).

### DW4 — Durable-write failure semantics (D-3)

When the append at step 4 fails:

1. **Never raise into the producer.** The exception is caught inside `publish()` — satisfying the
   `core.events.EventBus.publish` contract (F-5), **ED10** and **AR-31**.
2. **Do not dispatch the failed event.** No subscriber may observe an event that is not durable;
   this is what keeps **ED6** exceptionless.
3. **Do not rewind or reuse its `seq`.** Rewinding would let a later event reuse a number an earlier
   event already carried — two events with one identity in the ordering dimension, a direct **ED3**
   violation. A gap in the durable log is honest evidence that a write failed.
4. **Return the stamped envelope** according to the existing `publish` contract.
5. **Surface the failure through an injected, typed operational failure hook** — shaped like the
   `on_error` callback the bus already accepts for subscriber failures. Not merely logged.
6. **Maintain bounded in-process failure accounting.**
7. **Implement no retry, no backoff, no DLQ, no quarantine and no durable failure-history storage
   at M10.6.** Those are **M10.7** (**ED7**, **ED8**).

#### Declared limitation (M10.6)

> **A durable-write failure at M10.6 leaves the event neither durably recorded nor delivered, and
> the in-process failure record does not survive process termination. M10.7 is responsible for
> closing this gap through the retry/failure-state mechanism.**

This limitation is stated here, in the M10.6 milestone record and in the Epic 10 plan. It is not
hidden, softened, or discovered later.

### DW5 — `evt_event` stays immutable; a separate `evt_outbox` carries delivery state (D-4)

**ED5** forbids any update or delete path on the durable event log. An outbox is by definition
*mutable* delivery state. Therefore the two cannot be one table.

* **`evt_event`** — immutable, append-only durable event history. Unchanged by this ADR.
* **`evt_outbox`** — a separate, mutable structure carrying delivery state.

`evt_outbox` **must**:

* reference `Event.id` as `event_id`;
* **never** introduce a second event identity (**ED3**);
* **never** introduce a competing sequence authority (**ED3**);
* maintain delivery state **separately** from `evt_event` (**ED5**);
* use **one row per event**;
* treat **in-process synchronous subscribers as NOT outbox destinations** — they remain M4.1's
  synchronous, isolated fan-out, unchanged. The outbox exists for durable/asynchronous consumers,
  which also keeps it from becoming a second delivery mechanism competing with `events/webhooks.py`
  (F-17);
* provide the durable predicate **ED9** requires (*"never deletes an un-drained event"*), which is
  answerable as *"an `evt_outbox` row not in `DELIVERED`"*;
* leave additive state expansion available for M10.7 (**AR-32** — vocabulary evolves additively,
  never by silent mutation).

Per **ADR-0034 DS9**, the table **and every index** carry the `evt_` prefix, because certification
control **NS-2** checks Billing index names against every table in the process. Per **DS8**, the
structure arrives as the next migration in the single global chain (`0001`–`0012` api →
`0013`–`0033` billing → `0034`– events). Per **DS11**, timestamps use the injected clock.

### DW6 — M10.6 / M10.7 ownership boundary

| Owner | Concern |
|---|---|
| **M10.6** | `evt_outbox` row creation · state `PENDING` · state `DELIVERED` · the `event_id` reference · required timestamps from the injected clock · durable write integration |
| **M10.7** | `RETRYING` · `DEAD_LETTERED` · `QUARANTINED` · attempt counters · backoff · `next_attempt_at` · failure history · the retry ladder |

**M10.7 functionality must not be implemented early.** The M10.6 state vocabulary must admit M10.7's
members additively, without rewriting or migrating M10.6's rows.

### DW7 — M10.6 / M10.8 ownership boundary; crash and recovery

* **Persisted, not yet dispatched** (crash between steps 4 and 7): the event survives in
  `evt_event`; its `evt_outbox` row is `PENDING`. Recoverable.
* **Persisted and dispatched, crash before `DELIVERED` is recorded**: the event is redelivered on
  recovery. Duplicate delivery is **correct** under **ED2** and safe because consumers are idempotent
  on `(event_id, meter_code)` (**ED12**).
* **Not persisted** (crash before step 4, or step 4 failed): the event never existed durably and is
  not recoverable — honest and correct, since a non-durable event is not a durable fact.
* **`shutdown()`** still clears the in-memory deque (F-4), but events already persisted are **not**
  lost by it: they remain in `evt_event` with `PENDING` outbox rows. This is a real improvement over
  M4.1 and is recorded as such.

**M10.6 implements no recovery.** Reading `PENDING` rows back and re-dispatching is **M10.8**
(re-derivation and recovery — **ED11**, and the noun *"replay"* stays reserved to `platform/replay/`).
M10.6's obligation is to persist enough that M10.8 can, and to prove by test that the state survives
a real restart.

### DW8 — Concurrency: claim nothing that does not exist

`InProcessEventBus` is single-threaded and run-to-completion; `_seq` is incremented without a lock
(F-2). `EventDatabase` sets `check_same_thread: False` for SQLite (F-16) — a connection-pooling
accommodation, **not** a thread-safety guarantee. **Epic 10 claims no thread-safety, and M10.6 adds
no lock.** Duplicate delivery arises from recovery redelivery, not from concurrency, and is safe
because `append` is already idempotent on `Event.id` (M10.2). Multi-process claim/lease semantics are
a non-goal (**DW11**).

### DW9 — Boundary and import rules

| Concern | Rule |
|---|---|
| `bus.py` imports | `from .ports import DurableEventLog` only. **No** `sqlmodel`, **no** `sqlalchemy`, **no** `events.persistence`. |
| `events/__init__.py` | Continues to export the **port** and not `events.persistence`; re-exporting would drag a storage library into every importer and dissolve the ADR-0034 scoping. |
| Dependency direction | `core ← events ← runtime` unchanged and acyclic (**AR-10**, **AR-15**). Nothing imports upward. |
| Wiring | The durable sink is bound at a **composition root**, never constructed inside `events/`. |
| Boundary tests | The existing static AST boundary assertions are **extended** — never relaxed — to assert that `bus.py` imports no storage library and does not import `events.persistence`. |

### DW10 — Runtime wiring is explicitly out of M10.6 scope (D-6)

**M10.6 delivers:** the durable persistence seam · the `evt_outbox` structure · the persistence
implementation · the required migration · the tests · this ADR · boundary documentation and
validation.

**M10.6 must not** modify `platform/runtime/orchestrator/orchestrator.py`, or otherwise wire the
durable sink into the runtime producer path. Runtime wiring remains outside M10.6 and stays governed
by the Epic 10 / Epic 11 boundary already recorded in the implementation plan (§1.12 — Conversation
Runtime changes are Epic 11). The orchestrator constructs its bus inline today (F-7); changing that
is a runtime change, not an events change.

> **M10.6 provides the durable write-side capability and proves its semantics, but the production
> runtime is not yet wired to the durable sink.**

No reader may interpret M10.6 as making the live runtime durable.

### DW11 — Interaction with M10.5 and M10.10

* **M10.5 (retention)** — **ED9**'s *"never deletes an un-drained event"* becomes answerable exactly
  when `evt_outbox` exists, i.e. at M10.6; *"never deletes an un-metered event"* becomes answerable
  at M10.10. **[DL-0141](../../.voxvoice/DECISION_LOG.md)**'s sequencing is unchanged and is now
  independently corroborated by this design: M10.5's earliest executable position remains **after
  M10.10**, completing before M10.12/M10.13.
* **M10.10 (metric-stream seam)** — the platform-owned metric buffer is a pull-shaped, asynchronous
  consumer (**ADR-0033 MS2/MS5**) and is therefore an outbox destination under **DW5**, not an
  in-process subscriber. This ADR decides no MS-level question and forecloses none.

## Verification criteria

| | Criterion |
|---|---|
| **DV1** | With the seam disabled (the default), **every pre-existing bus test passes unmodified.** No existing test is weakened, skipped, xfailed, marked or removed. |
| **DV2** | Asserted **from inside a subscriber**: every event a subscriber observes is already present in `evt_event`. (This is the only place the ordering claim is falsifiable.) |
| **DV3** | Reentrant publish: the inner event is durable **before** the outer drain dispatches it. |
| **DV4** | Durable-write failure: the producer receives no exception; the event is **not** dispatched; the failure hook fires **exactly once**; `seq` is **not** rewound and is not reused by the next publish. |
| **DV5** | **ED10 measured, not asserted** — publish latency stays bounded when the sink is slow, and no producer stalls. Proven with an injected fake sink; no dependence on real I/O timing. |
| **DV6** | **EV5 compound proof (D-5).** A hermetic **test-only** compound producer inside the `events/persistence` test suite writes the `evt_event` row **and** the `evt_outbox` row through the **same** `EventDatabase.session`, forces an exception, and proves **both** roll back. Deterministic test data. **No production runtime transaction abstraction is created.** The existing `append_many` rollback tests remain valid evidence but are **not sufficient by themselves** to claim compound state+event atomicity. |
| **DV7** | Restart durability: persisted events and outbox rows survive `close()` and reopening the same **file-backed** SQLite path in `tmp_path` (**ADR-0034 DS10**). In-memory would pass while proving nothing. |
| **DV8** | Boundary: static assertions prove `bus.py` imports no `sqlmodel`, no `sqlalchemy` and no `events.persistence`; `core ← events ← runtime` stays acyclic. |
| **DV9** | `platform/runtime/**` is unchanged; no database, session, `UnitOfWork` or transaction appears anywhere under it. |
| **DV10** | The `evt_outbox` table and **every** index it defines carry the `evt_` prefix (NS-2 safety); migrations `0001`–`0034` are unchanged. |
| **DV11** | Tests are hermetic — offline, deterministic, no credentials, no vendor service; clock and ids injected. The **4690** regression baseline is maintained and no existing test disappears. |
| **DV12** | The **DW4 declared limitation** appears verbatim in the M10.6 milestone record, not only here. |

## Non-goals

Explicitly **not** decided or implemented by this ADR or by M10.6:

* the retry ladder, backoff and attempt caps (**M10.7**, ED7);
* DLQ and quarantine states, attempt counters, failure history (**M10.7**, ED8);
* recovery and re-derivation (**M10.8**, ED11);
* retention (**M10.5**, ED9 — deferred by DL-0141);
* the metric vocabulary and Billing-facing seam (**M10.10**, ADR-0033);
* rework of `events/webhooks.py` — audited at M10.6, not rewritten; no second competing delivery
  mechanism is created (F-17);
* a distributed broker, external transports, WS/SSE gateway expansion (deferred with **DL-0017**);
* thread-safety, multi-process claim/lease or `SELECT … FOR UPDATE` semantics (**DW8**);
* exactly-once delivery (**ED2** is at-least-once by decision);
* cross-session global ordering (**ED4** claims none);
* any change to `core.events.EventBus` (**ED1**);
* any producer change (**ED1**, **DL-0017**);
* runtime/orchestrator wiring (**DW10**);
* any change to `platform/billing/**`, migrations `0001`–`0033`, ADR-0023, ADR-0031, the Epic 9
  certification or freeze digests, or the `v4.0.0` tag.

## Dependency Direction

Unchanged and acyclic: `core ← events ← runtime`. The durable seam is typed by `events/ports.py`, so
`events/bus.py` keeps its `core` + stdlib budget, `core` continues to import nothing above it
(**AR-10**), and no consumer of the bus acquires a new dependency.

## Consequences

**Gained.** A durable write positioned where it can actually hold **ED6** — after the sequence
authority, before any subscriber · **ED6** satisfied without inventing a producer transaction and
without weakening the invariant · a producer path that still never blocks and never receives an
exception (**ED10**, **AR-31**, and the `core` contract F-5) · the `un-drained` predicate **ED9**
needs, unblocking M10.5 after M10.10 · a durable structure M10.7 and M10.8 can attach to additively ·
events that survive `shutdown()` for the first time.

**Accepted costs.** `events/bus.py` — an M4.1 file — is modified, additively and by ratified
decision · one synchronous durable write now sits on the publish path, making **ED10** an obligation
to measure (**DV5**) rather than assert · **the DW4 limitation**: at M10.6 a durable-write failure
leaves the event neither recorded nor delivered, and the in-process failure record does not survive
process termination, until M10.7 closes it · a second table (`evt_outbox`) and a second migration in
the chain.

**Explicitly preserved.** **ADR-0032 immutable**, ED1–ED12 intact · ADR-0033 and ADR-0034 unchanged ·
**AR-31**, **AR-32** and **DL-0017** preserved · `core.events.EventBus` untouched · no producer
changed · `platform/runtime/**` untouched · `platform/billing/**` byte-for-byte unchanged ·
migrations `0001`–`0034` untouched · `platform/replay/**` untouched · reserved nouns respected
(**ED11**) · Epic 9 certification and freeze digests preserved as historical state · `v4.0.0` not
re-tagged.

## Alternatives Considered

**Introduce a transaction into the runtime so ED6 applies literally.** Rejected: it would put a
database inside in-memory real-time voice engines, change producers (violating **ED1** and
**DL-0017**), and cross the Epic 11 boundary the plan draws at §1.12. It also fabricates the very
thing the design gate was told not to invent.

**Declare ED6 inapplicable to runtime events.** Rejected: an exemption from an Accepted invariant,
leaving M10.6 with no atomicity story and no basis for EV5. **DW1** scopes ED6 by producer shape
instead — which preserves it rather than carving it out.

**Buffer events and persist them in batches under `append_many`.** Rejected as the default: a buffer
between publish and durability is exactly the loss window ED6 exists to prevent. Viable later as a
throughput optimization *behind* DW1, never in place of it.

**A decorator implementing `EventBus` that persists, then delegates to `InProcessEventBus.publish`.**
Rejected — architecturally impossible here. `seq` is assigned *inside* the delegate (F-2), so the
decorator can only persist an unstamped event (violating **ED3**) or persist after `publish()`
returns, by which time the drain has already run to completion (F-1) and every subscriber has seen
it. **Dispatch-before-persist.**

**A `DurableEventBus(InProcessEventBus)` subclass overriding `publish()`.** Rejected by owner ruling
D-2. It avoids touching `bus.py` but must reimplement the reentrancy, ordering, isolation and
shutdown logic against the parent's private state, with nothing to detect divergence when the parent
changes — two implementations of one drain loop, which is the outcome DL-0017 wanted avoided.

**A second, from-scratch `EventBus` implementation.** Rejected: strictly worse than the subclass —
it re-derives every M4.1 invariant with no inheritance and no enforcement that the two buses stay
behaviourally identical.

**Swallow durable-write failures and log them.** Rejected: verbatim the defect ADR-0032's Context
indicts — *"failures vanish into a log line."* Breaks **ED2**/**ED6**.

**Raise durable-write failures into the producer.** Rejected: breaks the `core.events.EventBus`
contract (F-5), **ED10** and **AR-31**. A failing disk would stall a live conversation.

**Dispatch anyway and record the failure durably.** Rejected: records a durable-store failure in the
durable store that just failed — circular — and a dispatched-but-unpersisted event is precisely the
fact/event disagreement **ED6** forbids.

**Degrade to in-process delivery on durable-write failure** (dispatch anyway, flag the envelope
non-durable). Rejected by owner ruling D-3: it silently produces two classes of event with different
guarantees, indistinguishable to a consumer that does not check the flag, and delivers a fact the log
will never contain.

**Put mutable delivery state on `evt_event` (single-table outbox).** Rejected: gives the append-only
log an update path, contradicting **ED5**, and leaves **ED8**'s *"distinguishable by state"*
requirement unimplementable.

**Per-consumer outbox rows.** Rejected at M10.6: no durable consumer identity exists —
`Subscription.id` is a per-process integer, meaningless across a restart — so per-consumer rows would
require inventing one. **DW5** takes one row per event and rules in-process subscribers out of the
outbox instead, which needs no new identity and keeps the outbox from competing with the synchronous
dispatcher.

## What this ADR does not change

It creates no code, no schema, no table and no migration. It authorizes the M10.6 implementation
that follows it and nothing beyond M10.6. Every constraint in the
[Epic 10 implementation plan](../roadmap/epic10-implementation-plan.md) §1 continues to bind.
