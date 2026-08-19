# VoxVoice — Epic 10 (Durable Event & Telemetry Spine) Implementation Plan (M10.3)

| | |
|---|---|
| **Milestone** | **M10.3** — Epic 10 planning/governance closure. Produced *after* M10.1/M10.2 rather than before, for the reason recorded in §2. |
| **Status** | 🟡 **Planning.** No implementation, no code, no schema, no migration, no runtime change is authorized by this document. |
| **Date** | 2026-08-18 |
| **Target release** | **v5.0.0** |
| **Authoritative inputs** | Constitution v2 (Part A + Part B/P-ADR-0001), **ADR-0032 / ADR-0033 / ADR-0034** (ratified M10.1), **DL-0017**, **DL-0138**, **DL-0139**, [M10.0 Blueprint](../architecture/epic10/durable-event-telemetry-spine-blueprint.md), [M10.0a Decision Gate](../architecture/epic10/m10.0a-decision-gate.md), Epic 9 release baseline `v4.0.0` |
| **Introduces** | **No new architecture and no new ADR.** This plan *realizes* ADR-0032/0033/0034 + the Blueprint + the M10.0a rulings as an executable roadmap. It mints nothing. |

## 1. Standing constraints (bind every milestone)

1. **Epics 5–9 are frozen.** `platform/billing/**` stays **byte-for-byte unchanged**; migrations
   `0001`–`0033` are untouched; migration head `0033` remains historical state; both Epic 9 digests
   (`39790b95…d2d8`, `eed69222…6b65c`), the 27 controls, advisories `NS-5`/`ST-3` and all nine
   declared limitations are preserved as historical state; `v4.0.0` is never re-tagged.
2. **ADR-0032/0033/0034 are the acceptance criteria of every milestone**, checked per milestone
   rather than once at the end.
3. **Durability sits behind the unchanged contract.** DL-0017: *"implement the same `EventBus`
   contract — **no producer change**."* No abstract method is added to `core.events.EventBus`.
4. **Delivery is at-least-once.** Consumers are idempotent; the frozen `MetricStreamPort` documents
   idempotency on `(event_id, meter_code)`.
5. **One event identity, one sequence authority.** Identity is `Event.id`; the bus assigns `seq` and
   the store persists it verbatim (ADR-0032 ED3).
6. **The storage exception is scoped.** Only `platform/events/persistence/**` may import
   `sqlmodel`/`sqlalchemy` (ADR-0034 DS3/DS4), enforced by executable per-layer assertions.
7. **Billing never subscribes.** The metric-stream seam is pull-shaped; a platform-owned buffer
   subscribes and Billing pulls through the frozen port (ADR-0033 MS2/MS5).
8. **Namespace partition.** `platform/observability/` owns runtime `metric.*`; **`metric.billing.*`
   is reserved to Billing** and Epic 10 defines and emits none (ADR-0033 MS8–MS10).
9. **Reserved nouns.** "replay" belongs to `platform/replay/` and "event store" to Billing; Epic 10
   says **durable log**, **re-derivation**, **recovery** (M10.0a OD-3).
10. **Hermetic tests.** No network, no credentials, no vendor service; clock and ids injected;
    restart durability proven against **file-backed SQLite via `tmp_path`**, never in-memory.
11. **Regression baseline: 4690** (measured at M10.1). No existing test may disappear.
12. **Out of scope for the whole epic:** real providers, vendor SDKs, streaming/semantic VAD,
    Conversation Runtime changes, production latency validation (**Epic 11**); SDK, read API,
    mid-call control (**Epic 12**); distributed broker, external transports, WS/SSE gateway
    expansion (deferred with DL-0017).

## 2. Milestone allocation — recorded honestly

The executed sequence diverged from the M10.0 breakdown, and this plan records the divergence rather
than retro-fitting the history:

* **M10.2 absorbed the approved M10.4 "Persistence" scope** — the `evt_event` schema, migration
  `0034`, and the concrete `SqlDurableEventLog` adapter all landed at M10.2 alongside the foundation,
  together with the durable append-only log itself.
* **M10.3 was re-scoped from "Application" to planning/governance closure.** The Application content
  it originally carried (outbox orchestration, retry, DLQ, quarantine) belongs to M10.6/M10.7 and is
  not brought forward.
* **M10.3 does not implement durable bus integration.** See §M10.3 and §M10.6.
* **M10.5 (Retention) is deferred and re-sequenced** (DL-0141), not cancelled. Its approved
  semantics are unchanged, but its ED9/DS13 guards test **drained** and **metered** state that only
  M10.6 and M10.10 create, so the documented order (M10.5 before M10.6) is **not executable**. Its
  earliest executable position is **after M10.10**, completing before M10.12/M10.13. See §M10.5.

Milestone **numbering and terminology are preserved** (M10.0a, M10.8a included); only the content
allocation and, for M10.5, the **execution position** are corrected here. **No milestone is
renumbered.**

## 3. Milestones

### M10.0 — Blueprint ✅ complete

Design only. Delivered the 25-section [Blueprint](../architecture/epic10/durable-event-telemetry-spine-blueprint.md);
verified the Epic 9 boundary and gaps G1–G5 against code; verdict **M10.0_BLUEPRINT_READY**.
Persisted to the repository at M10.3.

### M10.0a — Boundary reconciliation ✅ complete

Decision gate, no implementation. Ratified corrections **C-1…C-6** and resolved open decisions
**OD-1…OD-9** — including the two the inspection surfaced (**OD-8** seam placement, **OD-9**
`metric.*` namespace partition). Recorded in the
[Decision Gate](../architecture/epic10/m10.0a-decision-gate.md).

### M10.1 — Implementation plan + ADR ratification ✅ complete

**Delivered:** the measured regression baseline (**4690 collected / 4690 passed / 0 failed /
0 skipped**, established as the first action, superseding the C-1 recommendation while preserving
4643 as the historical Epic 9 record; **+47 delta recorded as an Epic 10 observation with no cause
assigned**), and three ratified ADRs:

* **[ADR-0032](../adr/ADR-0032-event-durability-and-delivery-semantics.md)** — event durability and
  delivery semantics: at-least-once, append-only, one event identity and one sequence authority,
  transactional outbox, bounded retry ladder, distinct DLQ and quarantine, explicit retention — all
  behind the unchanged `EventBus` contract.
* **[ADR-0033](../adr/ADR-0033-metric-stream-seam.md)** — metric-stream seam (pull-shaped) and
  `metric.*` namespace ownership, including the AR-32 additive vocabulary extension (OD-1), the
  additive-`api/`-modules ruling (OD-8), and the `metric.billing.*` reservation (OD-9).
* **[ADR-0034](../adr/ADR-0034-durable-storage-seam-core-only-packages.md)** — durable storage seam
  for `core`-only edge packages: scoped `events/persistence/**`, own `Database`, per-layer boundary
  assertions, single global migration chain continuing at `0034`, `evt_*` prefix.

**Ratification prerequisites (all satisfied):** ADRs Accepted **before** implementation
(DOCUMENTATION_RULES §3.7); sequential numbering (0032–0034) with ADR-0011/0024/0025–0030 left
Reserved; registered in `docs/adr/README.md` and `docs/INDEX.md`; cross-referenced from
`docs/runtime/event-platform.md`. **DL-0138** records the trunk/release-branch divergence — Epic 10
branches from `feat/epic8-customer-platform` because `main` predates Epics 4–9; reconciliation is
deferred and no Epic 10 work is authorized to perform it.

### M10.2 — Durable event foundation ✅ complete

**Delivered:**

* **`DurableEventLog` port** (`events/ports.py`, `core` + stdlib) — `append`, `append_many`,
  `read_session`, `read_all`, `count`. **No update and no delete method.**
* **Persistence package** (`events/persistence/**`) — the one sub-package permitted
  `sqlmodel`/`sqlalchemy`, owning its own `EventDatabase` (never `api`'s or `billing`'s).
* **`evt_event` schema** — projection + payload shape; `event_id` PK (identity is `Event.id`);
  `seq` persisted verbatim; separate `ts` (producer) and `recorded_at` (injected clock); `data`/`meta`
  JSON columns. All tables **and index names** `evt_`-prefixed, so certification control **NS-2** —
  which checks Billing index names against every table in the process — cannot collide.
* **Migration `0034`** (`events/persistence/migrations/`) — continues the single global chain
  (`0001`–`0012` api → `0013`–`0033` billing → `0034` events); own ordered `MIGRATIONS` tuple;
  declarative, idempotent create-if-absent; `downgrade` a documented no-op.
* **`SqlDurableEventLog`** — append-only; idempotent on identity (at-least-once makes redelivery
  ordinary traffic); `append_many` is one transaction, all-or-nothing.
* **Boundary enforcement** — **9 static AST test functions/assertions** in
  `tests/test_events_boundary.py`, checking both directions (permitted inside `persistence`,
  forbidden one directory up), plus dependency-direction and `observability`-budget checks. No
  existing global boundary test was weakened. *(Corrected at M10.4: an earlier revision of this
  document said "11". The sub-count was wrong; the implementation was not. The M10.2 total of
  **52** added tests is unchanged and correct — 43 in `test_events_persistence.py` plus these 9.)*
* **52 tests** (4690 → 4742 collected), including restart durability against file-backed SQLite.

**Accepted condition carried forward: [DL-0139](../../.voxvoice/DECISION_LOG.md).** Three
repository-state assertions in the frozen Epic 9 release suite
(`tests/test_billing_release.py`) fail: two are structurally false for every post-`v4.0.0`
development commit and already failed at M10.1; the third is transient. They are an accepted,
documented governance condition — **not implementation failures** — and are not hidden, skipped or
suppressed. **This is a deferral, not a resolution** (see M10.13/M10.15).

### M10.3 — Planning / governance closure ✅ (this document)

**Scope: planning and governance only.** No durable bus integration, no runtime implementation, no
modification to `platform/events/bus.py`, no persistence seam introduced into `InProcessEventBus`,
no M10.6/M10.7 behaviour brought forward.

**Why:** M10.3's original "Application" content belongs to M10.6/M10.7, and the durable
write/dispatch integration cannot be implemented without settling a decision **not covered by
ADR-0032/0033/0034** — `InProcessEventBus.publish()` stamps `seq` and dispatches in one call, so
there is no seam between them. A decorator over `publish()` yields **dispatch-before-persist** (a
consumer can act on an event the log does not yet contain); adding a seam to the bus would be a new
architectural decision. **That decision is deferred to M10.6**, where ADR-0032 **ED6** (the event is
written in the same transaction as the state change that produced it) already governs it.

**Delivers:** the M10.0 Blueprint, the M10.0a Decision Gate, and this plan — making M10.4 onward
repository-derivable.

### M10.4 — **CLOSED_BY_ABSORPTION** (delivered in M10.2)

**Owner ruling (DL-0140): M10.4 is `CLOSED_BY_ABSORPTION`.** Its approved Persistence scope was
**fully delivered in M10.2**, commit `15199e9f176fa68adfe2f77cdaecd900c063b239`. **M10.2 is the
delivery milestone for that scope.**

The approved M10.4 content — from the [M10.0 Blueprint](../architecture/epic10/durable-event-telemetry-spine-blueprint.md)
§16 ("M10.4 Persistence") and the [M10.0a Decision Gate](../architecture/epic10/m10.0a-decision-gate.md)
Part 5 (*"`events/persistence/` with its own `Database` + `migrations/` at `0034`, prefix `evt_*`,
IR1a-style scoped exception with an executable per-layer assertion"*) — was verified item-by-item
against that commit:

| Approved M10.4 item | Delivered in M10.2 |
|---|---|
| `platform/events/persistence/` package | ✅ |
| its own **`EventDatabase`** (never `api`'s or `billing`'s) | ✅ |
| **migration `0034`**, continuing the single global chain | ✅ |
| **`evt_*`** table and index naming | ✅ |
| concrete **`SqlDurableEventLog`** adapter | ✅ |
| **IR1a-style** scoped persistence exception + executable boundary enforcement | ✅ |

**M10.4 has no remaining approved implementation scope, and no replacement scope was invented.**
**M10.5 proceeds directly from the M10.2 foundation.** Milestone **numbering is unchanged** — M10.4
keeps its number and its place in the sequence; only its disposition is recorded.

This is a **governance closure, not a code change**: nothing was implemented, moved, renumbered, or
brought backward from M10.5 or later under this milestone.

> **Note on [ADR-0034](../adr/ADR-0034-durable-storage-seam-core-only-packages.md).** Its §Status
> enumerates the milestones it binds as *"(M10.2, M10.4–M10.7)"*. That was accurate when written at
> M10.1, names which milestones the ADR's rules govern rather than assigning content, and is **not
> corrected** — accepted ADRs are immutable (DOCUMENTATION_RULES §3.4).

### M10.5 — Retention · **DEFERRED / RE-SEQUENCED** (not cancelled)

**Approved semantics — unchanged and not weakened.** Retention over the durable log, per ADR-0032
**ED9** and ADR-0034 **DS12/DS13**: explicit configuration with **no implicit deletion default**;
applied **only as a policy over a closed window**; **never deletes an un-drained event and never
deletes an un-metered event**. Clock injected. This is the **only** exception to the append-only
invariant, and it arrives with the guarantees that make it safe — M10.2 deliberately shipped no
removal path at all.

**Status: deferred by Owner ruling (DL-0141). No retention implementation is authorized at this
point.** The milestone is **not cancelled**, its **number is not changed**, and the approved design
in ADR-0032/ADR-0034 remains **immutable and unaltered**.

**Why.** The M10.5 design gate established that the approved guarantee cannot be implemented
truthfully against the current repository state. Retention is time-based over a closed window, and
`evt_event` carries an indexed `recorded_at` that could define one — but the two **guards** cannot be
evaluated, because the state they test does not exist:

| Guard (ED9 / DS13, verified by EV8 / SV9) | State required | Milestone that creates it |
|---|---|---|
| "never deletes an **un-drained** event" | consumption state of the platform-owned metric buffer, surfaced through `MetricStreamPort.drain()` | **M10.10** |
| "never deletes an **un-metered** event" | Billing's ingestion state, keyed `(event_id, meter_code)` | **M10.10** (Billing side, frozen) |
| delivery/consumption state generally | outbox rows and their delivery state | **M10.6** |

The M10.2 foundation contains **neither delivery/drain state nor metering state** — by design, since
speculative fields were excluded. Deleting solely by `recorded_at` would satisfy DS12 while violating
**DS13, EV8 and SV9** — exactly the *"revenue defect wearing an operations costume"* ED9 names.

**Prerequisite chain and derived position.** M10.5 depends on **M10.6** (delivery/consumption state)
and **M10.10** (drained/metered state). Its **earliest executable position is therefore after
M10.10**, and it must complete **before M10.12/M10.13**, since hardening and certification must cover
it. The originally documented ordering (M10.5 before M10.6) is **not executable**, and this plan
records that dependency rather than pretending otherwise. **Numbering is unchanged** — M10.5 keeps
its number and its place in this document; only its execution position moves.

**Resumption condition.** M10.5 resumes only when its prerequisites exist and the retention gate can
be satisfied **and tested honestly** — that is, when EV8 and SV9 can be written as real assertions
rather than aspirations. Until then no retention code, schema, migration or guard column is
authorized, and no M10.6/M10.10 work is pulled backward to enable it.

### M10.6 — Transactional outbox / durable write-side integration

> **Design gate closed — [ADR-0035](../adr/ADR-0035-durable-write-interposition-on-the-event-bus.md)
> ratified 2026-08-18, before implementation.** The gate found ADR-0032 **ED6** not literally
> executable: the runtime producers it governs hold no transaction (`platform/runtime/**` has no
> `Database`, `Session` or `UnitOfWork`), and `InProcessEventBus.publish()` stamps `seq` and
> dispatches in one method with no seam between them. ADR-0035 **completes ADR-0032 where it is
> silent and supersedes nothing** — ADR-0032 stays immutable, ED1–ED12 preserved, ADR-0033/0034
> unchanged, AR-31/AR-32/DL-0017 intact. It rules: **DW1** the durable append **is** the state change
> where no originating transaction exists, and ED6 binds unchanged where one does — **no database,
> `UnitOfWork` or transaction is introduced into `runtime/`**; **DW2/DW3** an *additive,
> default-disabled* persistence seam inside `InProcessEventBus`, writing **after `seq` stamping and
> before enqueue**, preserving the sole sequence authority, reentrancy, FIFO dispatch and subscriber
> isolation (the subclass fallback is rejected; `bus.py` may import `DurableEventLog` from
> `events/ports.py` but never `sqlmodel`, `sqlalchemy` or `events.persistence`); **DW4** a failed
> durable write never raises into the producer, is never dispatched, never rewinds `seq`, and
> surfaces through an injected typed hook with bounded in-process accounting; **DW5** immutable
> `evt_event` is separated from a mutable **`evt_outbox`** (one row per event, `event_id` reference,
> no second identity or sequence authority, in-process subscribers **not** destinations) supplying
> the `un-drained` predicate ED9 needs; **DW6/DW7** M10.7 owns retry/DLQ/quarantine and M10.8 owns
> recovery — neither may be implemented early; **DW10** M10.6 must **not** modify
> `runtime/orchestrator/orchestrator.py` or wire the durable sink into the runtime.
>
> **Declared M10.6 limitation (ADR-0035 DW4).** A durable-write failure at M10.6 leaves the event
> **neither durably recorded nor delivered**, and the in-process failure record does **not** survive
> process termination. **M10.7** closes this gap through the retry/failure-state mechanism.
>
> **M10.6 provides the durable write-side capability and proves its semantics, but the production
> runtime is not yet wired to the durable sink.**
>
> **EV5 proof (ADR-0035 DV6).** A hermetic **test-only** compound producer in the
> `events/persistence` suite writes the `evt_event` and `evt_outbox` rows through the **same**
> `EventDatabase.session`, forces an exception and proves both roll back. The existing `append_many`
> rollback tests remain valid evidence but are **not sufficient alone** to claim compound
> state+event atomicity. No production runtime transaction abstraction is created.

The transactional outbox (ADR-0032 **ED6**): the event is written in the **same transaction** as the
state change that produced it; both commit or both roll back. Precedent: `api/persistence/audit_store.py`
(*"audit is atomic with the fact it records"*).

**This milestone owns the deferred M10.3 decision.** It must settle, explicitly and with evidence:
the **ordering** of durable write versus dispatch; the **interposition mechanism** between `seq`
stamping and dispatch; the **transaction boundary**; and the **failure semantics** when the durable
write fails (a producer must not be raised into, and durability must not be silently lost).
**If that resolution requires a new architectural decision beyond ADR-0032/0033/0034 — for example a
persistence seam on `InProcessEventBus` — it requires a new ADR, ratified before implementation.**
Also at M10.6: audit `events/webhooks.py` (best-effort, no retry today) so the outbox does not create
a second, competing delivery mechanism; **M10.2 and M10.3 changed nothing there.**

### M10.7 — Retry ladder + DLQ + quarantine

> **Design gate closed — [ADR-0036](../adr/ADR-0036-outbox-retry-ladder-dead-letter-and-quarantine.md)
> ratified 2026-08-19, before implementation.** It **completes ADR-0032 ED7/ED8 and continues
> ADR-0035 DW5/DW6 and supersedes nothing.** Rulings: **RL1** resolves the M10.7/M10.8 boundary in
> favour of ADR-0035 DW7 — **M10.8 owns recovery, restart re-dispatch and the driver**, so M10.7
> contains **no polling, scheduler, worker, recovery loop, `sleep`, timer, thread or automatic
> redelivery** and exposes `due(now)` as a query it never acts on; **RL2** a five-state lifecycle via
> **`core.fsm`** with `DELIVERED`/`DEAD_LETTERED`/`QUARANTINED` terminal and `RETRYING → PENDING`
> refused; **RL3** **no operator redrive**; **RL4** **no operator-driven quarantine**; **RL5**
> `DeliveryOutcome` through an **injected classifier**, no transport taxonomy in `events/`; **RL6**
> **reuse `core.policies.RetryPolicy`** — `max_attempts=5`, `base_delay_s=30.0`, `multiplier=2.0`,
> `max_delay_s=3600.0` (30→60→120→240→480s), no constant inside transition logic; **RL7** **no
> jitter**; **RL8** 1-based attempts incremented *before* the outcome, and a permanent failure
> **does not consume the ladder**; **RL9** a **pure, I/O-free `OutboxLadder`** plus a
> `DeliveryTransport` **contract only** — no concrete transport shipped; **RL10**
> `events/webhooks.py` **byte-for-byte unchanged**; **RL11** migration `0036` (five `evt_outbox`
> columns, `ALTER TABLE` authorized); **RL12** append-only **`evt_outbox_failure`** as authoritative
> evidence, **no `last_error`**; **RL13** transition + history in **one transaction**, no
> `UnitOfWork`; **RL14** injected clock, absolute `next_attempt_at`; **RL16** retention consequences
> recorded, not implemented; **RL17** the production runtime **remains unwired**.

Bounded retry with an explicitly configured backoff schedule and hard attempt cap (**no infinite
retry, no hidden constants**, injected timing); **DLQ** after exhaustion with failure history
retained; **quarantine** for deterministic poison messages, which does **not** consume the ladder.
DLQ and quarantine are **distinguishable persisted states** — "we tried and gave up" and "this can
never succeed" require different operator responses (ADR-0032 ED7/ED8). **AR-31** must hold: a dead
consumer never stalls a producer.

### M10.8 — Durable-log re-derivation & recovery

Read a closed window back out of the durable log and re-derive from it; recovery after restart.
Read-only and side-effect-free — no runtime logic executes. **Naming per OD-3:** "replay" stays
reserved for `platform/replay/` (in-memory, visualization-only, frozen for Epic 10); Epic 10 says
re-derivation and recovery. The one permitted exception is implementing the frozen
`MetricStreamPort.replay()` **method name** exactly as specified.

> **Narrowed at M10.7 by [ADR-0036](../adr/ADR-0036-outbox-retry-ladder-dead-letter-and-quarantine.md)
> RL1 (owner ruling, 2026-08-19).** The M10.7 design gate found that the "read-only and
> side-effect-free" clause above contradicted **[ADR-0035](../adr/ADR-0035-durable-write-interposition-on-the-event-bus.md)
> DW7**, which assigns *"reading `PENDING` rows back and re-dispatching"* to M10.8 — re-dispatch
> being a side effect. **DW7 remains authoritative and ADR-0035 is not modified.** The clause is
> narrowed: it continues to describe **re-derivation**, which is genuinely read-only, and does
> **not** constrain recovery. **M10.8 owns** reading persisted pending/retryable rows after restart,
> deciding which are due for re-delivery, performing restart/recovery re-dispatch, and the recovery
> driver/worker/scheduler that performs it. Nothing else about M10.8 is changed.

### M10.8a — `metric.*` vocabulary

Additive `metric.*` members in `core.events.EventType`, including **`metric.turn`** (`AR-42`).
Permitted by **AR-32** without a dedicated ADR (OD-1), recorded in ADR-0033. **No existing member's
name or value may change**; `COST_UPDATED = "cost.updated"` stays untouched and outside `metric.*`
(OD-6). **Excludes `metric.billing.*`**, which is reserved to Billing (OD-9). Follows the Epic 6
source-comment convention. **Must complete before M10.9** — there is nothing to emit until it does.

### M10.9 — Observability build-out + `metric.turn`

Build `platform/observability/` from skeleton to a real producer within its `core` + stdlib budget:
per-turn latency taps at every conversation-loop stage boundary feeding `metric.turn` (**AR-42**),
emitted through the ordinary `EventPublisher` path. Taps must be cheap, non-blocking, must never
alter conversation behaviour, and must use an injected clock. **Sole producer of runtime `metric.*`**
(`AR-2`). Not a general-purpose logging framework. The **frozen Epic 8** observability capability
(`api/**/observability*`, `cp_alert_rule`, `v0012`) is untouched.

### M10.10 — Metric-stream seam

Implement `MetricStreamPort` **outside `platform/billing/**`**, as **additive new modules under
`platform/api/`** (OD-8, ADR-0031 §8 *"(additive)"*), creating the first `api → billing` edge.
**No pre-existing `api/` file may be modified** — asserted. A platform-owned buffer subscribes to the
bus and decodes `metric.*` into `MetricSignal`; **Billing pulls** via `drain`/`replay` and never
subscribes, never opens a connection, never holds a runtime resource. ADR-0023 and ADR-0031 are
preserved, not amended. An unwired port ingests nothing — no usage is invented.

### M10.11 — Shadow validation

For a closed period: re-derive usage from the durable log via `replay(...)`, obtain the metered
usage, compare, and prove agreement. **Any divergence is an Epic 10 defect and is never resolved by
modifying Billing.**

### M10.12 — Hardening

Performance (latency taps must not violate invariant I1), failure drills, operator surfaces (DLQ
depth, quarantine count), and the boundary/AST suite extended per milestone.

### M10.13 — Certification · **mandatory DL-0139 revisit**

Epic 10 defines its **own** control set with fresh identifiers; it does not renumber, reuse or extend
Epic 9's 27, and does not re-run or reinterpret them. Minimum controls: `billing/**` unchanged;
migrations `0001`–`0033` unchanged and Epic 10 `≥ 0034`; `evt_*` prefix conformance; existing
`EventType` members unchanged; no `metric.billing.*` defined by Epic 10; Billing holds no `EventBus`
subscription; no pre-existing `api/` file modified; `platform/replay/**` unchanged;
`api/**/observability*` unchanged; Epic 9 digests reproduced verbatim as historical state; `NS-5`,
`ST-3` and the nine limitations still open with their owners. Preserve the digest/verdict separation.
LIM-7 (self-administered certification) is inherited honestly.

**Mandatory: DL-0139 must be revisited here.** The three structural release-suite assertions must be
resolved by a **separate Owner-authorized governance decision**. Note that merely adding an Epic 10
release-certification suite carrying the generalized invariant does **not** resolve the existing
Epic 9 assertions, which would continue to fail.

### M10.14 — Freeze

Freeze manifest enumerating every Epic 10 module, table, migration and test, plus explicit
attestation that the frozen boundary is unchanged. Fresh digest; the Epic 9 manifest digest is quoted
as history, never recomputed. **Freeze does not commit** — that is M10.15, and the split exists so
risk **D-11** (*"implementation exists but is uncommitted at freeze"*) is discharged rather than
repeated.

### M10.15 — Repository release · v5.0.0

Commit the frozen tree and create an **annotated** tag `v5.0.0`. **Do not bump
`platform/pyproject.toml`** — every prior release left it at `0.1.0`; the tag is the single release
identity. Release notes state plainly whether any advisory was fixed or limitation closed.

**⚠️ Release gate — binding.** `GOVERNANCE.md` §10 makes **green CI a mandatory release gate**, and
two of the three **DL-0139** assertions cannot pass at any release commit after `v4.0.0`.
**v5.0.0 MUST NOT be released while DL-0139's structural release-condition remains unresolved.**
Resolving it is a prerequisite of this milestone, not a formality.

## 4. Definition of Done (every milestone)

Frozen boundary verified (`git diff v4.0.0 -- platform/billing` empty; migrations `0001`–`0033`
unchanged) · full suite run with the exact result reported, no test lost against the 4690 baseline ·
`ruff check billing/ tests/` and `ruff check . --exclude voicebot` clean · `git diff --check` clean ·
boundary/AST assertions extended, never weakened · new behaviour covered by new tests · documentation
updated in the same change-set (DOCUMENTATION_RULES) · ADR ratified **before** implementation if the
change is architectural · DECISION_LOG entry for notable non-ADR decisions · every reported failure
classified with evidence (regression / pre-existing / environment / needs-ADR) before it is accepted.
