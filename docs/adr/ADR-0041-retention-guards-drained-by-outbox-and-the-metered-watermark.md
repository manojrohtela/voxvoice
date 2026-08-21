# ADR-0041 — Retention guards: "drained" is durable outbox state, and "metered" arrives as an injected attestation

| | |
|---|---|
| **Status** | **Accepted 2026-08-20** (ratified at the M10.5 architectural decision gate, **before** implementation — DOCUMENTATION_RULES §3.7). |
| **Completes** | [ADR-0032](ADR-0032-event-durability-and-delivery-semantics.md) **ED9** and [ADR-0034](ADR-0034-durable-storage-seam-core-only-packages.md) **DS12/DS13**, which state the retention *guarantee* but never define, in evaluable terms, what **drained** and **metered** mean or who may ask. **It supersedes nothing.** ADR-0023, ADR-0031, ADR-0032, ADR-0033, ADR-0034, **ADR-0035**, ADR-0036, ADR-0037, ADR-0038, **ADR-0039** and ADR-0040 remain immutable and intact; **ED1–ED12**, **DS1–DS13**, **DW1–DW11**, **RL1–RL17**, **RC1–RC16**, **MS1–MS10**, **TP1–TP11**, **MB1–MB16**, **SV1–SV12**, **AR-2**, **AR-15**, **AR-31** and **AR-32** all continue to bind. |
| **Deciders** | Technical Owner |
| **Milestone** | Ratified at **M10.5**. Authorizes the M10.5 **architecture** and nothing beyond it; implementation requires its own authorization (**RT12**). |
| **Related** | [ADR-0032](ADR-0032-event-durability-and-delivery-semantics.md) (ED4, ED5, ED9, EV4, EV8), [ADR-0034](ADR-0034-durable-storage-seam-core-only-packages.md) (DS9, DS11, DS12, DS13, SV9), [ADR-0035](ADR-0035-durable-write-interposition-on-the-event-bus.md) (DW3, DW5, DW10, DW11), [ADR-0036](ADR-0036-outbox-retry-ladder-dead-letter-and-quarantine.md) (RL2, RL17), [ADR-0037](ADR-0037-outbox-recovery-driver-and-restart-re-dispatch.md) (RC2, RC9, RC16), [ADR-0039](ADR-0039-metric-stream-buffer-and-api-billing-edge.md) (MB1, MB3, MB5, MB6), [ADR-0040](ADR-0040-durable-log-historical-re-derivation-for-shadow-validation.md) (SV5), [ADR-0031](ADR-0031-billing-plane-repository-placement.md) (IR3, IR4), [ADR-0023](ADR-0023-billing-sole-owner-of-revenue.md), [DL-0141](../../.voxvoice/DECISION_LOG.md), [Epic 10 implementation plan](../roadmap/epic10-implementation-plan.md) §M10.5 |

## Status

**Accepted 2026-08-20.** This ADR ratifies the **M10.5 architecture** only. Epics 5–9 remain frozen;
`platform/billing/**` is byte-for-byte unchanged; `platform/api/**`, `platform/runtime/**`,
`platform/observability/**` and `platform/core/**` are unchanged; `MetricTurnBuffer`,
`MetricStreamPort` and the `DurableEventLog` contract are unchanged; migrations `0001`–`0036` are
untouched; certification controls, freeze manifests and release artifacts are untouched; `v4.0.0` is
not reopened. **DL-0139 stays Resolved. DL-0141 stays Open** (**RT13**).

## Context

**ED9** states the retention guarantee in three clauses — explicit configuration, a closed window,
and *"never deletes an un-drained event and never deletes an un-metered event"* — and closes with the
sentence that has governed this milestone ever since: *"A retention policy that can delete a fact
before it has been consumed and metered is a revenue defect wearing an operations costume."*
**DS12/DS13** repeat it for the storage layer, and **EV8/SV9** are the criteria that must become real
assertions before any of it may ship.

**DL-0141** deferred M10.5 at M10.2 because the two guards tested state that did not exist. Its
sequencing was *"earliest executable position after M10.10, completing before M10.12/M10.13"*, and
its resumption condition was precise: *"M10.5 resumes only when its prerequisites exist and EV8/SV9
can be written as real assertions rather than aspirations."*

M10.6 through M10.11 are now complete, and the M10.5 investigation found the prerequisites **half
met**: one guard is answerable, the other is answerable only from a plane that may not ask.

### The conflict over "drained"

Three ratified sources describe the same guard and two of them now disagree:

| Source | Authority | Says |
|---|---|---|
| **ADR-0035 DW5** | **ADR** | the outbox must *"provide the durable predicate **ED9** requires (`never deletes an un-drained event`), which is answerable as **an `evt_outbox` row not in `DELIVERED`**"* |
| `platform/events/ports.py` (`DurableOutbox.read_pending`) | ratified code | *"This is the durable `un-drained` predicate **ED9** requires before retention (M10.5) can run"* |
| **DL-0141** and **plan §M10.5** | decision log (Level 3) / plan | *"consumption state of the platform-owned metric buffer, surfaced through `MetricStreamPort.drain()`"* |

They agreed only while **ADR-0035 DW11** asserted that the metric buffer *"is a pull-shaped,
asynchronous consumer … and is therefore an outbox destination under DW5, not an in-process
subscriber."* **[ADR-0039](ADR-0039-metric-stream-buffer-and-api-billing-edge.md) MB1 ruled the
opposite** — the buffer is an **in-process subscriber** — and DW5 states that in-process synchronous
subscribers are **not** outbox destinations. The buffer's `drain()` therefore never touches
`evt_outbox`, and **MB5/MB6** leave no durable state to consult instead: `drain` is destructive, the
buffer is resident-only, and its contents are lost on process exit.

### The reach problem for "metered"

Billing holds exactly the state DL-0141 named:
`UsageRecordRepository.exists(org_id, event_id, meter_code) -> bool`. It is unreachable from where
retention belongs: **ADR-0031 IR4** forbids `events → billing`, the certification control **PL-2**
permits exactly one inbound Billing edge (`api/metering/** → billing.foundation`, ADR-0039 MB11),
and the two planes use **separate databases** (`EventDatabase`, `BillingDatabase`), so there is no
cross-table query either.

## Decision

### RT1 — Precedence: ADR-0035 DW5 controls the meaning of "drained"

**Owner ruling (R-1).** For **ED9** and **DS13**, *"drained"* means what **ADR-0035 DW5** and
`platform/events/ports.py` say it means: **durable outbox delivery state**.

The repository's authority hierarchy decides this without amending anything. `.voxvoice/README.md`:
*Engineering Constitution → **ADRs** → Governance documents → Technical Design Documents → **Sprint
plans** → Implementation tasks → Source code*, and *"a lower artifact may never contradict a higher
one."* **ADR-0035 is an Accepted ADR. DL-0141 is a Level-3 decision-log entry and §M10.5 is a plan.**
Both sit below ADR level, so the higher artifact stands and the lower ones are corrected — **ADR-0035
is not modified, reinterpreted or weakened by this ADR.**

### RT2 — Why this is also the *correct* meaning, not merely the higher-ranked one

A precedence ruling that happened to be semantically wrong would be a bad ruling. This one is not.

The outbox exists for consumers that can **lag**. Under **DW3** the durable write happens **after
`seq` stamping and before enqueue**, and `InProcessEventBus.publish()` is **synchronous
run-to-completion** — so by the time `publish()` returns, **every in-process subscriber has already
been called**. There is no persistent condition in which an in-process subscriber "has not yet"
consumed a durably-recorded event.

The metric buffer therefore has **nothing to guard** — not because the guarantee was waived, but
because the state the guard tests **cannot arise** for a synchronous consumer. DW5's exclusion of
in-process subscribers from outbox destinations is that same fact, stated structurally. ED9's safety
intent is preserved in full: every consumer that *can* fall behind is exactly the set the outbox
tracks.

### RT3 — `DELIVERED` only; `DEAD_LETTERED` and `QUARANTINED` are **not** drained

**Terminality is not consumption.** `OutboxState` has three terminal states (**ADR-0036 RL2**), and
only one of them means the event was consumed:

| State | Drained? | Why |
|---|---|---|
| `PENDING` | **No** | written, never attempted |
| `RETRYING` | **No** | attempted, not yet succeeded |
| `DELIVERED` | **Yes** | the consumer has it |
| `DEAD_LETTERED` | **No** | *"we tried and gave up"* — it was **never delivered** |
| `QUARANTINED` | **No** | *"this can never succeed"* — it was **never delivered** |

Deleting a dead-lettered or quarantined event would destroy the evidence an operator needs precisely
when delivery failed. A test that admitted terminal states generally would pass while doing the one
thing ED9 forbids.

### RT4 — `MetricTurnBuffer` is not consulted, and ADR-0039 is not worked around

**Owner ruling (R-2).** Metric-buffer consumption is **rejected** as the authoritative guard.
**ADR-0039 MB1, MB3, MB5 and MB6 remain correct, in force and unamended.**

This is not a workaround. **MB5** (destructive `drain`) and **MB6** (resident-only, best-effort
`replay`, *"no historical replay guarantee"*) are the **reason** retention uses durable outbox state:
a bounded, non-durable, destructively-drained buffer is the wrong instrument for a guarantee that
must survive restarts, and ADR-0039 said so deliberately rather than by omission. Retention reaches
for the durable artifact because ADR-0039 was honest about the non-durable one.

Nothing here extends the buffer, gives it persistence, makes `drain` non-destructive, or asks
`replay()` to become historical. **ADR-0040 SV2 continues to hold verbatim.**

### RT5 — DL-0141 and plan §M10.5 are superseded on this point

The *"metric-buffer consumption"* reading of **un-drained** in **DL-0141**'s guard table and in
**plan §M10.5**'s identical table is **superseded by RT1/RT2/RT3**.

Both were written at M10.2, before ADR-0039 existed, and both were reasonable then — they simply
recorded DW11's since-overruled assumption. **They must be corrected as documentation** under a
separate authorization (**RT14**). Neither is edited by this ADR, and **DL-0141 is not closed**
(**RT13**).

### RT6 — The `MeteredWatermark` attestation: declared by `events/`, supplied from outside

**Owner ruling (R-3).** Retention lives in **`platform/events/`** and **never imports Billing**.
The metered guard arrives by **dependency inversion**, the pattern this repository already uses for
its most sensitive seam:

* **`events/` declares the port.** A `MeteredWatermark` contract stating which events are covered by
  a metering attestation. `events/` imports no `billing`, no `api`, no `runtime`.
* **Something outside the `events` plane implements it.** A composition root may legitimately see
  both planes; the `events` plane may not. This mirrors `MetricStreamPort` exactly — Billing declares
  it, the platform implements it, and the arrow never points the forbidden way (ADR-0033 MS4/MS6,
  ADR-0031 IR3/IR4) — and it mirrors `OutboxRecovery`'s **injected classifier** (ADR-0037), which
  kept transport taxonomy out of `events/` the same way.
* **The attestation is an *input*, never a derivation.** `events/` never computes, infers, caches or
  reconstructs metering state. It cannot be wrong about a fact it does not own (**ADR-0023 M1**:
  Billing measures nothing; symmetrically, `events/` meters nothing).
* **Absent, unavailable or failing ⇒ retain.** An unsupplied attestation covers nothing, so nothing
  is deletable (**RT8**).

**No Billing implementation, query, repository call or database access path is invented, designed or
named by this ADR.** How a deployment satisfies the port is a composition concern outside the M10.5
boundary, and inventing one here would decide a Billing-facing question this ADR has no mandate over.

### RT7 — The safe-to-delete predicate, complete

An `evt_event` row may be deleted **if and only if all five hold**:

1. **An explicit retention policy is configured** (**ED9**, **DS12**) — there is no implicit deletion
   default, and *"a deployment that configures nothing deletes nothing."*
2. **`recorded_at` falls strictly inside a closed retention window**, computed from an **injected
   clock** (**DS11**). The window is closed, never "up to now".
3. **The corresponding `evt_outbox` row is `DELIVERED`** (**RT1**, **RT3**) — not `PENDING`, not
   `RETRYING`, and **not** `DEAD_LETTERED` or `QUARANTINED`.
4. **A supplied `MeteredWatermark` attestation covers the event** (**RT6**).
5. **Default-deny** (**RT8**).

### RT8 — Default-deny is the behaviour, not the fallback

If **any** condition in RT7 is absent, ambiguous, unavailable, unsupplied, or fails to evaluate for
any reason, **the event is retained**. Retention never deletes on a maybe.

Stated as behaviour rather than as error handling because it is the property EV8/SV9 actually test:
an unconfigured deployment deletes nothing; a deployment with no attestation deletes nothing; an
event whose outbox row cannot be read is kept. **The failure mode of this component is to retain too
much**, which costs storage, rather than to delete too early, which costs revenue and evidence.

### RT9 — M10.5 is hermetic; no production wiring

**Owner ruling (R-4).** M10.5 delivers the **mechanism and its safety proofs**, hermetically. It
authorizes **no production wiring**: no runtime worker, scheduler, thread, timer or transport change;
no durable sink wired into the production `EventBus`; no `MetricTurnBuffer` wiring; no rebinding of
Billing's `metric_stream` away from `NullMetricStream`; and **no change to `MetricTurnBuffer`,
`MetricStreamPort`, the `DurableEventLog` contract or `platform/billing/**`**.

This is consistent with **DW10 / RL17 / RC16**, which keep the durable runtime deliberately unwired,
and with **ADR-0040 SV5**. Following **ADR-0037 RC2**, any sweep is **caller-driven and
deterministic** — no scheduler, no polling loop.

**This is not the "inert subset" DL-0141 rejected.** That disposition was *"enforcement that deletes
nothing **because it cannot prove the guards**"*. Under RT1 and RT6 both guards are **genuinely
evaluable and hermetically testable**, so **EV8 and SV9 become real assertions** — precisely the
resumption condition DL-0141 set. The mechanism can delete; whether a deployment activates it is
configuration (RT7.1), and whether the runtime is wired at all belongs to a later epic.

### RT10 — No PL-2 / IR4 widening

**No certification-control or boundary-guard change is part of this decision.**
`PERMITTED_BILLING_EDGES` keeps its single entry (`api/metering/** → billing.foundation`), both IR4
test guards keep their current rule, and `events → billing` remains **forbidden**. RT6 exists so that
retention needs no such widening: an inversion costs nothing where a new edge would cost a boundary.

### RT11 — No migration is required to support deletion

Deleting rows needs no schema change, and an explicitly configured policy needs no table. **M10.5
adds no migration merely to delete**, and the chain stays at `0036`. Should implementation
demonstrate that a migration is genuinely required, it is `0037` in the single global chain, carries
the `evt_` prefix (**DS9**, certification **NS-2**), and its migration-list assertion update is
already pre-authorized by **DL-0144** — but it is not assumed here.

**EV4 remains valid.** *"No update or delete path exists on the durable event log **outside the
retention policy**"* — M10.5 is that explicitly sanctioned exception, and the sole one. If an
existing EV4-style guard requires a source or test adjustment to admit it, **implementation STOPS and
reports** rather than changing the guard.

### RT12 — Architecture ratification only

Ratified before implementation per DOCUMENTATION_RULES §3.7. **This ADR does not authorize
implementation.** No `platform/events/**` module, no test, no migration and no wiring is created by
it.

### RT13 — DL-0141 stays Open

**DL-0141 is not closed by this ADR.** Its sequencing (*after M10.10, before M10.12*) is satisfied
and its blocking condition is discharged by RT1/RT6, but the milestone it defers has not been
implemented. **DL-0141 remains `Open` until M10.5 implementation is complete**, at which point its
disposition is a separate governance decision. **DL-0139 remains Resolved and is not reopened.**

### RT14 — The gates that follow

1. **Registration and documentation corrections** — link ADR-0041 from `docs/INDEX.md` and
   `docs/adr/README.md` (DOCUMENTATION_RULES §3.6), note RT1/RT9 in plan §M10.5, and correct the
   superseded *"un-drained"* rows in **DL-0141** and **plan §M10.5** (**RT5**).
2. **M10.5 implementation** — new modules under `platform/events/` and new tests under
   `platform/tests/`, subject to every boundary above.
3. **Commit** — separately authorized.

Each gate is its own authorization. None is granted here.

## Invariants

| # | Invariant |
|---|---|
| **RI1** | **Drained means `evt_outbox.state == DELIVERED`** — nothing else, ever. |
| **RI2** | **`DEAD_LETTERED` and `QUARANTINED` are never drained**, terminality notwithstanding. |
| **RI3** | **`events/` never imports `billing`**, `api`, `runtime` or `observability`. |
| **RI4** | **The metered attestation is an input**, never derived, inferred or cached by `events/`. |
| **RI5** | **Default-deny** — absent, ambiguous or unavailable ⇒ retain. |
| **RI6** | **No implicit deletion default** — an unconfigured deployment deletes nothing. |
| **RI7** | **Closed window only**, from an injected clock. |
| **RI8** | **No production wiring**, worker, scheduler, thread, timer or transport. |
| **RI9** | **`MetricTurnBuffer`, `MetricStreamPort`, `DurableEventLog` and `billing/**` are unchanged.** |
| **RI10** | **No PL-2/IR4 widening**; the single authorized Billing edge is unchanged. |
| **RI11** | **Retention is the sole sanctioned exception to append-only** (**EV4**). |
| **RI12** | **ADR-0032, ADR-0034, ADR-0035 and ADR-0039 are unamended.** |

## Dependency topology

```
platform/events/
    ├── reads   evt_event.recorded_at        (the closed window — RT7.2)
    ├── reads   evt_outbox.state             (the drained guard  — RT7.3)
    ├── declares MeteredWatermark  ◀──────── implemented outside the events plane
    └── performs the sanctioned retention deletion   (EV4's sole exception)

external composition boundary
    └── supplies MeteredWatermark            (may see both planes; events/ may not)

events/ → billing   :  FORBIDDEN  (ADR-0031 IR4, certification PL-2)
billing → events    :  FORBIDDEN  (ADR-0031 IR3)
```

One-way, acyclic (**AR-15**). The four state domains stay distinct and none is conflated with
another: **durable event history** (`evt_event`), **durable outbox delivery state** (`evt_outbox`),
**`MetricTurnBuffer`'s in-process state** (not consulted — RT4), and **Billing's metering state**
(reached only as an attestation, never queried — RT6).

## Relationship to ADR-0039 MB5/MB6 — stated explicitly

MB5 and MB6 are **not obstacles this ADR routes around**. They are correct, in force, unamended, and
they are the **reason** RT1 reaches for durable outbox state: ADR-0039 established plainly that the
metric buffer is bounded, in-memory, destructively drained and lost on process exit, and a retention
guarantee that must survive restarts cannot rest on any of those properties.

ADR-0039 was honest about what the buffer is not. This ADR takes it at its word.

## Rejected interpretations

| # | Interpretation | Rejected because |
|---|---|---|
| **RR1** | **Metric-buffer consumption as the drained guard** | Contradicts ADR-0035 DW5 and `ports.py`, both higher-authority than DL-0141/the plan; and MB5/MB6 leave no durable state to test, so the guard would be unfalsifiable. |
| **RR2** | **Giving the buffer durable consumption state** | Reverses MB3/MB5/MB6 and ADR-0040 SV2, ratified days earlier, to obtain something `evt_outbox` already provides. |
| **RR3** | **Treating all terminal outbox states as drained** | `DEAD_LETTERED`/`QUARANTINED` mean delivery **failed**; deleting them destroys the operator's evidence exactly when it is needed (RT3). |
| **RR4** | **`events/` importing Billing to check metering** | Violates ADR-0031 IR4, requires widening PL-2, crosses a database boundary, and inverts a dependency the architecture deliberately points the other way. |
| **RR5** | **Widening PL-2's allow-list for retention** | Moves the certification and freeze digests to buy what an injected port supplies free (RT10). |
| **RR6** | **`events/` deriving metering state itself** | A second metering authority — the drift ADR-0023 M1 and ADR-0033 MS4 exist to prevent. |
| **RR7** | **Deleting by `recorded_at` alone** | DL-0141's original finding: satisfies DS12 while violating DS13, EV8 and SV9 — *"a revenue defect wearing an operations costume."* |
| **RR8** | **Wiring the runtime so retention becomes "meaningful"** | Contradicts DW10/RL17/RC16 and ADR-0040 SV5; makes a retention milestone the vehicle for an unauthorized operational change. |
| **RR9** | **Fail-open on an unavailable guard** | Inverts RT8. A retention policy that deletes when it cannot check is worse than one that never runs. |

## Verification criteria

* **RV1** — Nothing is deleted when retention is unconfigured (**SV9**).
* **RV2** — Nothing is deleted outside the closed window; the boundary is exact and clock-injected.
* **RV3** — A `PENDING` or `RETRYING` event is never deleted (**EV8**).
* **RV4** — A `DEAD_LETTERED` or `QUARANTINED` event is never deleted, even when old and attested.
* **RV5** — A `DELIVERED` event outside the attestation is never deleted.
* **RV6** — With no attestation supplied, nothing is deleted regardless of age or outbox state.
* **RV7** — An event satisfying all five RT7 conditions **is** deleted — the mechanism is not inert.
* **RV8** — `platform/events/**` imports no `billing`, `api`, `runtime` or `observability` (AST).
* **RV9** — No scheduler, worker, thread, timer or transport is introduced (AST).
* **RV10** — `MetricTurnBuffer`, `MetricStreamPort`, `DurableEventLog` and `billing/**` are unchanged.
* **RV11** — `PERMITTED_BILLING_EDGES` and both IR4 guards are unchanged.
* **RV12** — Deletion removes `evt_event` history only as RT7 permits; no other delete path exists.
* **RV13** — Tests are offline, hermetic and deterministic; restart behaviour uses file-backed SQLite
  via `tmp_path`, never in-memory (**SV10**, plan §1.10).

## Consequences

**Gained.** M10.5 becomes executable for the first time since M10.2, with **EV8/SV9 as real
assertions** rather than aspirations — DL-0141's own resumption test. The append-only log gains its
one sanctioned exception, with the guarantees that make it safe arriving in the same milestone.
`evt_outbox` acquires its second reader after recovery.

**Accepted.** Because no production `DeliveryTransport` exists (**RC9**), no outbox row reaches
`DELIVERED` in production today, so a wired deployment would currently delete nothing. That is
correct behaviour, not a defect: RT7.3 is doing its job. Retention also cannot delete events whose
delivery failed — dead-lettered and quarantined rows accumulate until an operator resolves them,
which is deliberate (RT3). And the metered guard is only as good as the attestation supplied; a
deployment that supplies a wrong watermark can delete too early, which is why RT6 makes the
attestation an explicit, external, auditable input rather than something inferred.

**Unchanged.** Billing, the runtime, the observability producer, the metric buffer, the durable log
contract, the outbox lifecycle, the recovery driver, certification, freeze and the release record.

## Known limitations

1. **No production effect.** M10.5 is hermetic (RT9); production remains unwired by ratified intent.
2. **Metric-path consumption is unguarded — and needs no guard.** In-process subscribers consume
   synchronously at publish (RT2), so no lag state exists. If a future epic makes the metric path
   asynchronous, that epic must revisit RT2 rather than assume it still holds.
3. **Attestation granularity is undecided.** RT6 fixes the *contract shape* — declared by `events/`,
   supplied externally, default-deny — not its resolution or cadence. Implementation must not invent
   a Billing-side producer for it.
4. **Dead-letter accumulation.** Nothing in M10.5 drains the DLQ; redrive remains explicitly out of
   scope (**ADR-0036** non-goals).

## What this ADR does not change

**ADR-0023, ADR-0031, ADR-0032, ADR-0033, ADR-0034, ADR-0035, ADR-0036, ADR-0037, ADR-0038, ADR-0039
and ADR-0040 are unchanged.** No ADR text is edited, deleted, reworded or superseded.

**Supersession: none.** ADR-0041 resolves a conflict between an Accepted ADR and two lower-authority
documents by **precedence**, and the correction lands on the lower-authority documents (**RT5**),
never on ADR-0035.

It creates no schema, no migration and no persistence; changes no conversation behaviour, no
`EventType`, no `EventSource`, no `MeterSource`, no Billing code and no certification control; and it
does **not** authorize implementation (**RT12**). Every constraint in the
[Epic 10 implementation plan](../roadmap/epic10-implementation-plan.md) §1 continues to bind, as
amended by ADR-0038 **TP1** for §1.12 alone.
