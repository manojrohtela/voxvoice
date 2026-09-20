# ADR-0050 — Production wiring of the Epic 10 durable spine, latency tap and metering seam: discharging DW10 / RL17 / RC16 / RT9 / HD12

| | |
|---|---|
| **Status** | **Accepted 2026-08-24** (ratified at M11.1). Recommended by the Epic 11 **M11.0 Blueprint**; this milestone ratifies it. **This ADR is the architectural authorization for M11.12** and for nothing earlier. |
| **Kind** | Epic 11 architecture ADR. **Amends no Constitution Part A or Part B section.** |
| **Amends** | Nothing. **ADR-0032, ADR-0033, ADR-0034, ADR-0035, ADR-0036, ADR-0037, ADR-0038, ADR-0039, ADR-0040, ADR-0041, ADR-0042 and ADR-0043 all remain immutable and intact.** ED1–ED12, DS1–DS13, DW1–DW11, RL1–RL17, RC1–RC16, MS1–MS10, TP1–TP11, MB1–MB16, SV1–SV12, RT1–RT14 and HD1–HD16 are preserved in full. This ADR **discharges deferrals**; it changes no Epic 10 semantics. |
| **Deciders** | Technical Owner |
| **Related** | Constitution v2 **§A9** (Stage 2 = *"in-process bus + durable outbox"*), **§A5**; ARCHITECTURE_RULES **AR-2**, **AR-30**, **AR-31**, **AR-32**, **AR-42**; [ADR-0035](ADR-0035-durable-write-interposition-on-the-event-bus.md) **DW10**, [ADR-0036](ADR-0036-outbox-retry-ladder-dead-letter-and-quarantine.md) **RL17**, [ADR-0037](ADR-0037-outbox-recovery-driver-and-restart-re-dispatch.md) **RC9/RC16**, [ADR-0041](ADR-0041-retention-guards-drained-by-outbox-and-the-metered-watermark.md) **RT9**, [ADR-0042](ADR-0042-m10.12-hardening.md) **HD12**; [ADR-0038](ADR-0038-runtime-latency-taps-and-the-metric-turn-producer.md) **TP10**; [ADR-0039](ADR-0039-metric-stream-buffer-and-api-billing-edge.md) **MB6**; [ADR-0047](ADR-0047-audio-driven-endpointing-and-metric-turn.md) **ME9**; [DL-0017](../../.voxvoice/DECISION_LOG.md) |

## Status

**Accepted 2026-08-24**, ratified at milestone **M11.1**. **This ADR authorizes no code now.** It
authorizes **M11.12** to perform the wiring described below, and it is deliberately written so
that the wiring can be reviewed against it before a line is changed.

## Context

Epic 10 built a complete durable spine and **left it unwired on purpose**. Every deferral is
named in an Accepted ADR: **DW10**, **RL17**, **RC16**, **RT9** and **HD12** all state that no
production wiring is performed, and **RC9** ships `DeliveryTransport` as a contract with no
implementation.

Verified at `3779e932`:

- `platform/events/bus.py::InProcessEventBus.__init__` already exposes the attachment seam —
  `durable: DurableEventLog | None = None`, `on_durable_error: DurableWriteFailureHook | None =
  None` — and `is_durable` returns `False` by default, documented as *"`False` is the default"*.
- `platform/runtime/orchestrator/orchestrator.py:175` constructs a **bare** `InProcessEventBus()`.
  No composition root builds a spine.
- `RuntimePipeline.__init__` accepts `latency: LatencyObserver | None`, but the orchestrator's
  `RuntimePipeline(...)` construction (`orchestrator.py:380–394`) **passes no `latency=`**.
  `TurnLatencyTap` therefore never runs outside tests.
- `api/metering/metric_buffer.py::MetricTurnBuffer` subscribes to `metric.turn` — into a bus that
  nothing publishes `metric.turn` into.
- `grep -rn "SqlDurableEventSpine" platform/ --include=*.py` outside `events/persistence/` and
  `tests/` returns nothing.

Constitution **§A9** places exactly this configuration at **Stage 2**: *"one host: one Control
Plane process + one Session Worker process; managed relational database; **in-process bus +
durable outbox**."*

## Decision

### SW1 — The deferrals are discharged here, and only here

**DW10, RL17, RC16, RT9 and HD12 deferred production wiring; they did not authorize it.** This
ADR is that authorization, scoped to M11.12. No earlier milestone may wire any part of it.

### SW2 — Wiring happens at the composition root, through the seam Epic 10 already built

The durable spine is attached by **constructor argument**:
`InProcessEventBus(durable=<DurableEventLog>, on_durable_error=<DurableWriteFailureHook>)`.
**No Epic 10 module is modified.** A composition root builds `EventDatabase` +
`SqlDurableEventSpine` and supplies the bus the orchestrator uses, replacing the bare construction
at `orchestrator.py:175`.

### SW3 — Epic 11 duplicates nothing Epic 10 delivered

This is the binding constraint on the whole milestone. Epic 11 **must not** create a second event
store, a second outbox, a second retry ladder, a second dead-letter path, a second retention
mechanism, a second metric producer or a second metering buffer. Where Epic 10 provides the
capability, Epic 11 **wires** it.

### SW4 — Outbox, retry ladder, DLQ and quarantine are wired unchanged

`events/ladder.py`'s `OUTBOX_LIFECYCLE`, the retry semantics, dead-lettering and quarantine are
used exactly as ratified. **RL3/RL4 stay in force** — no redrive out of `DEAD_LETTERED`, no
operator quarantine control. The read-only `count_dead_lettered()` / `count_quarantined()` surfaces
(ADR-0042 HD7–HD10) remain observability, not control.

### SW5 — `DeliveryTransport` receives its first implementation

RC9 shipped the contract without one. M11.12 provides a concrete implementation behind the
unchanged contract. It is the **only** new component this ADR authorizes.

### SW6 — The recovery driver is started; retention is scheduled

`events/recovery.py` performs restart re-dispatch (RC-series semantics unchanged).
`events/retention.py` guards are scheduled, with **ADR-0041's** drained-and-metered watermark
preconditions intact — retention remains the sole sanctioned deletion path with a default-deny
predicate.

### SW7 — `TurnLatencyTap` is injected by the orchestrator

The orchestrator constructs a `TurnLatencyTap` with an **injected monotonic clock** and a
source-bound publish handle, and passes it as `latency=` to `RuntimePipeline`. The pipeline's
tap call sites already exist (ADR-0038) and are **not changed**.

### SW8 — TP10 is absolute and is not relaxed by wiring

`record()` reads a clock and writes a dict. **Nothing is published inside the turn budget.** The
single publish happens in `complete()`, after the turn is terminal. Wiring a durable sink makes
`publish()` more expensive — it may now perform a durable write inline — which makes TP10 *more*
load-bearing, not less. Any wiring that would publish per boundary is prohibited.

### SW9 — `platform/observability/` remains the sole producer of runtime `metric.*`

**AR-2** and ADR-0033 **MS8** are unchanged. Wiring adds no second producer.

### SW10 — `MetricTurnBuffer` subscribes; Billing continues to pull

The buffer subscribes in-process to `metric.turn` (ADR-0039 MB1) and Billing pulls through the
**frozen** `MetricStreamPort`. **Billing never subscribes. `events → billing` remains forbidden.**
The single authorized edge remains `api/metering/** → billing.foundation`.

### SW11 — The buffer is not durable, and wiring does not imply that it is

**MB6** stands: `MetricTurnBuffer` has no persistence, its contents are lost on process exit, and
a closed billing period cannot be reconstructed from it. Attaching a durable spine to the *event
bus* does not make the *metric buffer* durable, and no document produced by M11.12 may suggest
otherwise.

### SW12 — Metering wiring depends on real VAD

Per ADR-0047 **ME9**: `voice_to_voice_ms` requires `speech_end`, which originates only in the
voice-activity tap. **M11.12 therefore hard-depends on M11.7 with VAD on by default** in the
real-provider profile. Wiring metering without it yields `missing_measurement` for every turn —
correct behaviour, but zero signal.

### SW13 — Wiring happens **after** the real path works

M11.12 follows M11.11. Wiring the spine while providers are still being integrated would mean
diagnosing a wiring failure and a provider failure simultaneously.

### SW14 — Stage 2 is the deployment context

Per §A9. The durable outbox is a Stage-2 property, which aligns this milestone with the Stage-2
host that G9 and G11 independently require.

### SW15 — Producers never block on consumers

**AR-31** and ED10 are unchanged. A slow or dead subscriber, a failing durable write, or an
unavailable delivery destination must never stall a conversation. `DurableWriteFailures` (ADR-0035
DW4) remains the bounded accounting for failed durable writes.

## Consequences

- M11.12 delivers: one composition-root change, one `latency=` injection, one subscription, one
  `DeliveryTransport` implementation, one recovery-driver start and one retention schedule.
- `is_durable` becomes `True` in the Stage-2 profile and stays `False` in the hermetic local
  profile, so the 5447 floor is unaffected.
- DW10, RL17, RC16, RT9 and HD12 are discharged with evidence at **G8**.
- Epic 10's modules are **read and used**, not edited.

## Alternatives considered

- **Wire the spine early, alongside provider integration.** Rejected — SW13.
- **Have Billing subscribe directly.** Rejected: ADR-0033 MS3 and the standing `events → billing`
  prohibition.
- **Make `MetricTurnBuffer` durable as part of wiring.** Rejected: it would reopen ADR-0039 MB3/MB6
  and is not required to discharge any deferral. If durable metering is ever needed, it is a new
  decision, not a wiring detail.
- **Publish `metric.turn` per boundary now that the bus is durable.** Rejected outright — SW8.
- **Implement operator redrive/quarantine control while wiring the outbox.** Rejected: RL3/RL4
  remain in force; the deferred operator-control milestone stays unentered.
- **Skip `DeliveryTransport` and deliver in-process only.** Rejected: RC9 deferred the
  implementation, not the requirement, and an outbox with no transport is an outbox that never
  delivers.

## Evidence required later (G8)

1. Proof that `is_durable` is `True` in the Stage-2 profile and `False` in the local profile.
2. A restart test proving durable re-dispatch through the recovery driver.
3. Proof that `TurnLatencyTap` is injected and that `metric.turn` reaches `MetricTurnBuffer`.
4. A TP10 guard proving nothing is published between the first and last tap of a turn.
5. Proof that no Epic 10 module was modified.
6. Proof that `events → billing` remains absent and Billing still pulls through the frozen port.
7. An explicit discharge record for **DW10, RL17, RC16, RT9, HD12** naming this ADR.
