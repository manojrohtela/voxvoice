# ADR-0032 — Event durability and delivery semantics: at-least-once, append-only, behind the unchanged `EventBus` contract

| | |
|---|---|
| **Status** | **Accepted 2026-08-18** (ratified at M10.1). Recommended by the Epic 10 **M10.0 Blueprint** (accepted; not yet persisted to `docs/architecture/epic10/`) and settled at the **M10.0a** owner decision gate; this milestone ratifies it. |
| **Amends** | Introduces the Epic 10 **Durable Event & Telemetry Spine** beneath the already-built runtime. Amends no Constitution Part-A/Part-B section and no frozen Epic; it discharges the durability work **DL-0017 deferred** and records the delivery semantics every Epic 10 implementation milestone must honour. |
| **Deciders** | Technical Owner |
| **Related** | [DL-0017](../../.voxvoice/DECISION_LOG.md) (M4.1 Event Bus; defers outbox/retry/DLQ/async delivery), [ADR-0004](ADR-0004-event-driven-core.md) (event-driven core, one typed envelope), [ADR-0006](ADR-0006-single-system-of-record.md) (single System of Record; brokers only when needed), [ADR-0033](ADR-0033-metric-stream-seam.md) (metric-stream seam), [ADR-0034](ADR-0034-durable-storage-seam-core-only-packages.md) (where durable storage physically lives), [event-platform.md](../runtime/event-platform.md), Constitution §A5 (event envelope) / §A12 (additive extension), ARCHITECTURE_RULES AR-2 (one producer per type) / AR-30 (every runtime fact is an event) / AR-31 (producers never block on consumers) / AR-32 (additive vocabulary) |

## Status

**Accepted 2026-08-18**, ratified at milestone **M10.1**. This ADR authorizes no code. It fixes
the delivery semantics that every Epic 10 implementation milestone (M10.2–M10.11) must honour.
Epics 5–9 remain frozen; `platform/billing/**` is byte-for-byte unchanged; the `v4.0.0` baseline
is not reopened.

It deliberately decides **semantics only**. Where durable storage physically lives, which library
implements it, how tables are named and numbered — all of that is [ADR-0034](ADR-0034-durable-storage-seam-core-only-packages.md),
and this ADR is written so that it can be read without knowing any of it.

## Context

`InProcessEventBus` (`platform/events/bus.py`, M4.1) is the platform's single internal
communication mechanism. Its own module docstring states the gap plainly:

> "Scope (M4.1): in-process only. No durable persistence, no distributed broker, no external
> transports (those are M4.2+). Strict producer/consumer async decoupling (an outbox) is a
> durable-delivery concern deferred with them (DL-0017)."

Verified properties of that implementation today:

* events live in a `deque` and are **discarded on `shutdown()`**;
* delivery is synchronous run-to-completion inside `publish()`, on the producer's own thread;
* a raising subscriber is caught, logged, and **dropped** — there is no retry, no dead-letter
  path, no quarantine, and no durable trace that a delivery ever failed;
* on process exit the entire event history ceases to exist.

Three consequences are architecturally load-bearing:

1. **AR-31 is not satisfied for the durable case.** DL-0017 argued — correctly — that the
   in-process reentrant case is non-blocking, and explicitly deferred the general case.
2. **Nothing can be re-derived.** There is no log to read a closed window back out of.
3. **Billing cannot be metered.** The frozen `MetricStreamPort.replay()` contract documents its
   input as *"the authoritative log — the truth being re-read"*. That log does not exist.

DL-0017 did not leave this open-ended. Its deferred column reads:

> "Durable outbox + retry/DLQ + async/non-blocking delivery + WS/SSE gateway + distributed broker
> are M4.2+ (**implement the same `EventBus` contract — no producer change**)."

That sentence supplies both the authorization and the binding constraint this ADR ratifies.

## Decision

### ED1 — Durability is implemented *behind* the existing contract

The durable event log implements `core.events.EventBus` as it stands. **No abstract method is
added to `core.events.EventBus`, no existing method signature changes, and no producer changes.**
A producer holding an `EventPublisher` cannot tell whether the bus behind it is durable.

This is DL-0017's constraint quoted verbatim, and it is the reason Epic 10 can be additive at all.

### ED2 — Delivery is **at-least-once**

Exactly-once is **not** the default and is not attempted. A consumer may legitimately see the
same event twice; consumers are required to be idempotent.

This is not a concession — it is the semantics the already-frozen consumer was built for. The
`MetricStreamPort.drain` contract states it:

> "a signal returned twice is not an error, because ingestion is idempotent on
> `(event_id, meter_code)`. That contract is what makes the whole pipeline at-least-once-safe
> rather than requiring the stream to be exactly-once."

Choosing exactly-once here would impose a guarantee the consumer never asked for, at a cost the
consumer already paid to avoid.

### ED3 — One event identity, one sequence authority

* **Identity is `Event.id`.** Epic 10 does **not** invent a second event identity, a surrogate
  key exposed as identity, or a content hash used as identity.
* **The bus is the sole sequence authority.** `InProcessEventBus.publish` stamps the authoritative
  monotonic `seq`; the durable store **persists that value verbatim** and never assigns,
  re-assigns, re-number, or derives a competing sequence.
* Storage may hold its own internal row identifier for physical purposes; it is never surfaced as
  the event's identity or ordering key.

A second identity or a second sequence would give one fact two names, and the day they disagree is
the day nothing in the system can be trusted to be the same event.

### ED4 — Ordering is per-session, by bus `seq`

Ordering guarantees are exactly the guarantees the bus already makes: ordered per session by
`seq`, in subscriber-registration order for fan-out. The durable log preserves that order on
read-back. No global cross-session total order is claimed, because none exists today and inventing
one would be a stronger promise than the producers can keep.

### ED5 — The log is append-only

No update path and no delete path exist on the durable event log, with the single exception of
retention (ED9). This matches the discipline already proven twice in the repository —
`SqlAuditStore` (*"appends one immutable row (never updates, never deletes)"*) and
`SqlBillingEventStore` (*"an append point only: there is no update or delete path"*).

### ED6 — The outbox is transactional

An event destined for durable delivery is written **in the same transaction as the state change
that produced it**. Both commit or both roll back. A fact and the event announcing it can never
disagree about whether they happened.

Precedent: the Epic 8 audit store — *"an emitted audit row is part of the same transaction as the
business write that produced it (the `SqlUnitOfWork` commits both together, or rolls both back)."*

### ED7 — Retry is a bounded, explicitly configured ladder

* Retry attempts are **bounded**. Infinite retry is prohibited.
* The backoff schedule and attempt cap are **explicit configuration**. No hidden retry constants,
  no magic numbers buried in a call site.
* Retry timing is **injected**, never read from an ambient clock, so retry behaviour is
  deterministic under test.

### ED8 — Dead-letter and quarantine are distinct, durable states

* **Dead-letter (DLQ):** an entry that exhausted the retry ladder. Durable, with its failure
  history retained.
* **Quarantine:** an entry that failed *deterministically* — a poison message (malformed envelope,
  unknown type) for which retrying is known-futile. Quarantine does **not** consume the retry
  ladder.
* The two are **distinguishable by state**, not by log-message archaeology. "We tried ten times and
  gave up" and "this can never succeed" are different operational facts requiring different
  operator responses.

### ED9 — Retention is explicit, and never destroys unfinished work

* Retention is **explicit configuration**. There is **no implicit deletion default**.
* Retention applies **only as a policy over a closed window**.
* Retention **never deletes an un-drained event** and **never deletes an un-metered event**.

A retention policy that can delete a fact before it has been consumed and metered is a revenue
defect wearing an operations costume.

### ED10 — Producers never block on consumers

Durable delivery satisfies **AR-31** for the general case: a failed, slow, or dead consumer delays
that consumer's delivery and nothing else. It never stalls a producer, a turn, or a conversation.

### ED11 — Restart-safe, recoverable, re-derivable

The log survives process termination. A closed window can be read back and usage re-derived from
it. Per the M10.0a **OD-3** ruling, the architectural noun **"replay" remains reserved to
`platform/replay/`** (the in-memory, visualization-only M4.3 subsystem, which Epic 10 does not
touch). Epic 10 uses **re-derivation** and **recovery**. `platform/replay/**` is part of Epic 10's
frozen boundary.

The one deliberate exception: the frozen `MetricStreamPort.replay()` **method name** is
implemented exactly as specified. It is a method on a frozen contract, not an architectural noun.

### ED12 — Metric identity belongs to the frozen contract

Billing's metering identity is `(event_id, meter_code)`, defined by the frozen Billing plane.
Epic 10 **consumes** that definition and does not redefine, extend, or reinterpret it.

## What this ADR does **not** decide

Deliberately out of scope, and settled by [ADR-0034](ADR-0034-durable-storage-seam-core-only-packages.md):

* which package physically holds durable storage;
* which persistence library is permitted, and where;
* the storage engine, connection ownership, and URL compatibility;
* table naming and migration numbering.

Also out of scope: the metric vocabulary and the Billing-facing seam — see
[ADR-0033](ADR-0033-metric-stream-seam.md). And explicitly deferred, exactly as DL-0017 listed them
alongside the outbox: **a distributed broker, external transports, and WS/SSE gateway expansion**.
Epic 10 takes durability, outbox, retry, DLQ and quarantine; it does not take the broker.

## Dependency Direction

Unchanged and acyclic: `core ← events ← runtime`. The durable implementation lives behind the
`core.events.EventBus` contract, so no consumer of the bus acquires a new dependency, and `core`
continues to import nothing above it (AR-10).

## Consequences

**Gained.** AR-31 satisfied for durable delivery · a restart-safe, ordered, append-only record of
every runtime fact · recovery and re-derivation of closed windows · the authoritative log the
frozen `MetricStreamPort.replay()` already documents as its input · a durable, inspectable failure
path where today failures vanish into a log line.

**Accepted costs.** Consumers must be idempotent (the metering consumer already is) · durable
writes cost latency on the delivery path, which is why ED10 keeps producers off it · an operator
surface now exists that must be watched (DLQ depth, quarantine count) — an operational duty the
platform did not previously carry.

**Explicitly preserved.** `platform/billing/**` byte-for-byte unchanged · migrations `0001`–`0033`
untouched · existing `EventType` members unchanged · ADR-0023 and ADR-0031 intact · Epic 9
certification and freeze digests preserved as historical state · `platform/replay/**` untouched ·
the Epic 8 observability capability untouched · existing webhook delivery semantics unchanged.

## Alternatives Considered

| # | Alternative | Rejected because |
|---|---|---|
| A1 | **Exactly-once delivery** | The frozen consumer documents at-least-once as the contract it was built for and is idempotent on `(event_id, meter_code)`. Exactly-once would add distributed-consensus cost to buy a guarantee nobody asked for — and would still not be exactly-once at the application boundary. |
| A2 | **Add a `publish_durable()` (or similar) method to `core.events.EventBus`** | Violates DL-0017's explicit "implement the same `EventBus` contract — no producer change", and would fork the envelope model ADR-0004 exists to keep singular. Every producer would eventually have to choose, and some would choose wrong. |
| A3 | **Let the durable store assign its own sequence** | Two sequence authorities for one event stream. The store's number would inevitably be used for ordering somewhere, and would disagree with `seq` under redelivery. |
| A4 | **Introduce a distributed broker now** | DL-0017 lists the broker *alongside* the outbox as deferred, not as a prerequisite for it. ADR-0006 says brokers only when needed; a durable local log is what is needed for metering, and the broker can implement the same contract later without a producer change. |
| A5 | **Retry with an unbounded ladder and no quarantine** | An infinite ladder turns one poison message into a permanent hot loop, and without quarantine an operator cannot distinguish "will never work" from "has not worked yet". |
| A6 | **Extend `platform/replay/` with a durable backend** | `platform/replay/` is M4.3, in-memory, visualization-only, and frozen for Epic 10. Overloading it would give one package two lifetimes and two truths. Settled as OD-3. |

## Compliance Verification

Executable assertions, extended per milestone (the Epic 9 IR1–IR9 / boundary-suite pattern):

| # | Assertion |
|---|---|
| **EV1** | `core.events.EventBus` gains no abstract method; existing signatures unchanged. |
| **EV2** | No existing `EventType` member's name or value changed. |
| **EV3** | The durable store never writes a `seq` differing from the bus-stamped value. |
| **EV4** | No update or delete path exists on the durable event log outside the retention policy. |
| **EV5** | Outbox write and originating state change share one transaction (proven by rollback). |
| **EV6** | Retry is bounded; no retry constant is defined outside explicit configuration. |
| **EV7** | DLQ and quarantine are distinct persisted states; failure history is retained. |
| **EV8** | Retention deletes no un-drained and no un-metered event. |
| **EV9** | **Restart durability**: publish → persist → terminate and recreate the runtime → reopen the store → recover → re-derive → verify exact event identity and order. A function call is not a restart; the test uses a file-backed database. |
| **EV10** | No Epic 10 module imports `platform/billing/**`; `platform/billing/**` is byte-for-byte unchanged against `v4.0.0`. |
| **EV11** | `platform/replay/**` unchanged; no Epic 10 package or class takes "replay" as an architectural noun. |

## Documentation

Linked from [`docs/INDEX.md`](../INDEX.md) and registered in [`docs/adr/README.md`](README.md).
Cross-referenced from [`docs/runtime/event-platform.md`](../runtime/event-platform.md), whose
"Extension points for M4.2+" section named this work.
