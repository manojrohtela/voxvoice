# ADR-0040 — The durable event log is the historical source for M10.11 shadow validation

| | |
|---|---|
| **Status** | **Accepted 2026-08-20** (ratified at the M10.11 architectural ruling gate, **before** implementation — DOCUMENTATION_RULES §3.7). |
| **Completes** | [ADR-0033](ADR-0033-metric-stream-seam.md)'s *"Standing rule for M10.11"* and the [Epic 10 plan](../roadmap/epic10-implementation-plan.md) §M10.11, neither of which says **what** a closed period is re-derived *from*. It is the *"different mechanism, separately ratified"* that [ADR-0039](ADR-0039-metric-stream-buffer-and-api-billing-edge.md) **MB6** required any consumer needing historical coverage to obtain. **It supersedes nothing.** ADR-0023, ADR-0031, ADR-0032, ADR-0033, ADR-0034, ADR-0035, ADR-0036, ADR-0037, ADR-0038 and **ADR-0039** remain immutable and intact; **MS1–MS10**, **ED1–ED12**, **DW1–DW11**, **RL1–RL17**, **RC1–RC16**, **TP1–TP11**, **MB1–MB16**, **AR-2**, **AR-15**, **AR-31**, **AR-32** and **AR-42** all continue to bind. |
| **Deciders** | Technical Owner |
| **Milestone** | Ratified at **M10.11**. Authorizes the M10.11 **architecture** and nothing beyond it; implementation requires its own authorization (**SV12**). |
| **Related** | [ADR-0039](ADR-0039-metric-stream-buffer-and-api-billing-edge.md) (MB3, MB5, MB6, MB11, MB12), [ADR-0033](ADR-0033-metric-stream-seam.md) (MS2–MS5, the M10.11 standing rule), [ADR-0032](ADR-0032-event-durability-and-delivery-semantics.md) (ED2, ED3, ED5, ED12), [ADR-0035](ADR-0035-durable-write-interposition-on-the-event-bus.md) (DW3, DW10), [ADR-0036](ADR-0036-outbox-retry-ladder-dead-letter-and-quarantine.md) (RL17), [ADR-0037](ADR-0037-outbox-recovery-driver-and-restart-re-dispatch.md) (RC16), [ADR-0038](ADR-0038-runtime-latency-taps-and-the-metric-turn-producer.md) (TP5, TP11), [ADR-0023](ADR-0023-billing-sole-owner-of-revenue.md), [ADR-0031](ADR-0031-billing-plane-repository-placement.md) |

## Status

**Accepted 2026-08-20.** This ADR ratifies the **M10.11 architecture** only. Epics 5–9 remain frozen;
`platform/billing/**` is byte-for-byte unchanged; every pre-existing file under `platform/api/**` is
unchanged; the M10.10 implementation and its dependency guards are unchanged; migrations `0001`–`0036`
are untouched; `EventType`, `EventSource` and `MeterSource` are unchanged; certification controls,
freeze manifests and release digests are untouched; `v4.0.0` is not reopened.

## Context

The Epic 10 plan §M10.11 states the milestone in four lines:

> For a closed period: re-derive usage from the durable log via `replay(...)`, obtain the metered
> usage, compare, and prove agreement. **Any divergence is an Epic 10 defect and is never resolved by
> modifying Billing.**

ADR-0033 repeats the last sentence as a standing rule under its own heading. Between them, that is the
entire ratified corpus for M10.11 — and the M10.11 design gate found that it is **not executable** as
written against the repository as it stands. Four facts, all established by inspection:

**1. The comparison engine already exists, and it is Billing's.** `billing/application/metering_engine.py`
ships `replay_period(ReplayUsagePeriod)` — *"Re-derive a period from the authoritative log and compare
against its seal… uses the **same pure ingestion decision** the live path uses — not a second
implementation that could drift from it"* — and `_reconcile_report` / `ReconcileUsagePeriod`, which
resolves each signal's meter, normalizes, and reconciles `expected` against `recorded`. Epic 10 builds
no comparator. Its only job is to supply the right historical signals.

**2. The bounded buffer cannot supply them.** Both Billing use cases call
`MetricStreamPort.replay(org_id, period_start, period_end)`, whose frozen docstring says *"Re-read a
closed window from the **authoritative log** … The log, not Billing, is the truth being re-read."* But
**ADR-0039 MB5** made `drain` **destructive** and **MB6** made `replay` **resident-only and
best-effort**, with *"no historical replay guarantee"*. The two interact fatally: **the signals Billing
meters are exactly the signals `drain` removed.** Replaying that same closed period returns `()`,
`expected` is empty against a non-empty `recorded`, and the standing rule would report a **guaranteed
total divergence on every period** — a result the mechanism can never clear.

MB6 anticipated this precisely and refused to paper over it: *"An in-memory bounded buffer **cannot**
reconstruct a past billing period, and any consumer that needs that guarantee needs a **different
mechanism, separately ratified**. M10.10 must not be cited later as having provided one."*

**This ADR is that separate ratification.** It satisfies MB6's warning by **introducing a distinct
historical mechanism**, not by extending the buffer's guarantees.

**3. Nothing in the chain is wired**, by ratified intent. `TurnLatencyTap` is instantiated nowhere in
production; `runtime/orchestrator/orchestrator.py` constructs a bare `InProcessEventBus()` with no
durable sink (**DW10 / RL17 / RC16** keep it unwired); `MetricTurnBuffer` is instantiated nowhere; and
Billing's `metric_stream` defaults to `NullMetricStream` (**CV10**). So there is no metered usage to
obtain and no durable content to re-derive from in a running system.

**4. The durable log has no windowed read.** `DurableEventLog` exposes `append`, `append_many`,
`read_all()` and `count()` — the `read_all()` limitation accepted at M10.8.

## Decision

### SV1 — The durable event log is the historical source

**Owner ruling (N-1).** M10.11 closed-period re-derivation **reads from the durable event log**
(`platform/events/persistence/**`, `evt_event`), never from `MetricTurnBuffer`.

The durable log is the only component in Epic 10 with the properties a closed period needs: it is
**append-only** (ED5), **restart-safe**, **ordered by the bus-assigned `seq`** (ED3), **idempotent on
`Event.id`** (ED2), and it **preserves the envelope verbatim** — *"a round-tripped event equals the
event appended."* Those are exactly the guarantees `MetricStreamBuffer` does not have and, per SV2,
must never acquire.

### SV2 — `MetricTurnBuffer` gains nothing

**MB3, MB5 and MB6 stand unchanged and unqualified.** The buffer remains:

* **bounded** — one process-wide `deque`, fixed capacity `10_000`, drop-oldest with a counter;
* **in-memory** — no persistence, no durable storage, no migration, no flush, no shutdown hook;
  contents are lost on process exit;
* **destructive on `drain`** — removal *is* the consumption state, no cursor model;
* **resident-only and best-effort on `replay`** — no historical guarantee, and it can never recover
  what overflow, process exit or a prior destructive drain removed.

**`MetricTurnBuffer.replay()` MUST NOT become historical storage.** M10.11 does not read it, does not
extend it, and does not reinterpret it. This ADR adds a mechanism *beside* the buffer; it changes
nothing *about* the buffer.

### SV3 — The frozen `MetricStreamPort` contract is untouched

No modification to `MetricStreamPort`, `MetricSignal`, `Scope`, `MeterSource`, `NullMetricStream`,
`RecordingMetricStream` or any other Billing type is authorized by this ADR. The ABC keeps its two
abstract methods and its exact signatures (MS4 — *"the single inbound path"*).

The historical re-derivation path is an **Epic 10 validation mechanism**. It is **not** a change to
Billing's live metering path and must never be described as one: the live path remains
producer → bus → buffer → `drain` → Billing, exactly as ADR-0033 and ADR-0039 ratified it.

### SV4 — Billing is frozen, including when it disagrees

**`platform/billing/**` is byte-for-byte unchanged.** ADR-0033's standing rule is restated here as
binding on M10.11: **any divergence between re-derived and metered usage is an Epic 10 defect**, and it
is *never* resolved by modifying Billing — not by adjusting a meter, a normalization, a tolerance, a
comparison, or the reconciliation service. A divergence is a finding to be reported, not a number to be
reconciled away.

### SV5 — M10.11 is a hermetic proof, not production wiring

**Owner ruling (N-2).** M10.11 is a **hermetic shadow-validation scenario**. It authorizes **no
production wiring**. Specifically, it does **not** instantiate or permanently wire:

* `TurnLatencyTap` into the production composition root,
* a durable event sink into the production `EventBus`,
* `MetricTurnBuffer` into the production runtime,
* Billing's `metric_stream` away from `NullMetricStream`,
* any production worker, thread, task or timer,
* any production transport or outbox path.

The scenario **constructs** the components it needs, inside the validation itself. This is consistent
rather than convenient: DW10/RL17/RC16 keep the durable runtime deliberately unwired, and CV10 makes
`NullMetricStream` the default precisely so an unwired build ingests nothing. Wiring the chain to
validate it would violate three ratified rules to test a fourth.

### SV6 — The scenario proves a complete closed-period flow

```
Event  →  durable event log  →  historical re-derivation  →  metered usage  →  comparison  →  agreement
```

Each arrow must be exercised. A proof that skipped a stage would validate the stages it kept and assert
the one it dropped.

### SV7 — Real contracts and real implementations, not a second system

The proof exercises the **actual ratified contracts and production implementations** wherever possible.
Test doubles are permitted only where the repository already uses them for a seam (an injected clock, an
id factory, a `tmp_path`-backed SQLite database — never in-memory, per plan §1.10).

**No second metering algorithm may be written.** Meter resolution, normalization, idempotency and
comparison belong to Billing's existing engine and are reused as the **comparison authority** without
modifying `billing/**`. Epic 10's contribution is the *derivation of historical signals from durable
envelopes* — the same decoding contract M10.10 ratified (`source = MeterSource.OBSERVABILITY`,
`producer = "observability.turn_latency"`, `raw_unit = "ms"`, `raw_units` ← `voice_to_voice_ms`,
`sequence` ← `Event.seq` as provenance, `observed_at` ← `Event.ts`, `payload_digest` ← SHA-256 over
canonical `Event.data`), together with MB8/MB9/MB10's skip rules. A parallel implementation of any of
these would be the drift `replay_period`'s own docstring warns against.

### SV8 — Both directions must be proven

* **A — Agreement.** A valid closed period re-derived from the durable log **agrees** with the metered
  usage.
* **B — Divergence detection.** An intentionally altered, missing or extra historical record **causes
  the comparison to report divergence**, surfaced as an Epic 10 defect.

**B is not optional.** A validation that can only report success is indistinguishable from one that
does not look, and it is the failure mode a shadow-validation milestone exists to prevent. The injected
discrepancy is never removed by changing Billing (**SV4**).

### SV9 — Location and dependency boundary

**Owner ruling (N-3).** The historical re-derivation **does not live under `api/metering/`**. M10.10's
boundary is unchanged and unweakened:

* `api/metering/** → billing.foundation` remains the **only** permitted Billing edge
  (`PERMITTED_BILLING_EDGES`, PL-2, and both IR4 test guards);
* `api/metering/` remains **forbidden from importing `events`**, as its own M10.10 guards assert.

M10.11 code takes the **smallest location that satisfies the existing architecture and dependency
guards**, and **tests are preferred when no production abstraction is required**. A test-side
composition is the intended shape: the scenario is a composition of existing production components,
which is what a composition root does and what a test may legitimately do.

**If a reusable production-side helper turns out to be genuinely required, implementation STOPS and
reports the exact file and dependency edge before creating it.** No new production layer may be
invented merely to avoid composing in a test.

### SV10 — The durable log's existing API is sufficient

M10.11 uses `DurableEventLog` as it stands — `append`, `append_many`, `read_all`, `count`. **No new
method and no contract change is authorized** unless implementation *proves* the ratified proof is
impossible without one, in which case it stops and reports.

**Filtering `read_all()` in memory for the closed period is acceptable for M10.11 validation.** It is
also explicitly **not** a production historical-query feature: a windowed durable read is a real
capability with real cost, and if one is ever wanted it must be ratified on its own terms rather than
arriving as a side effect of a test helper.

### SV11 — Certification and freeze are untouched

No certification control, freeze manifest, certification digest, release digest or Epic 9 artifact is
modified to execute this decision. The M10.10 values stand: certification `c044b3f6…`, freeze manifest
`cdafb4ab…`. **If implementation later requires a certification change, it STOPS at that boundary and
requests a separate authorization.** **DL-0139 is resolved and is not reopened**; nothing in M10.11
bears on it.

### SV12 — This ADR ratifies architecture, not implementation

Ratified before implementation per DOCUMENTATION_RULES §3.7. **It does not authorize implementation.**
M10.11 implementation requires its own explicit authorization, subject to every boundary above.

## Invariants

| # | Invariant |
|---|---|
| **SI1** | **No production wiring** — no tap, durable sink, buffer, `metric_stream` rebinding, worker, thread, task, timer, transport or outbox path enters production. |
| **SI2** | **No Billing modification** — `platform/billing/**` byte-for-byte unchanged, divergence included. |
| **SI3** | **No ADR-0039 amendment** — MB1–MB16 unchanged; ADR-0039 stays Accepted and immutable. |
| **SI4** | **No second metering algorithm** — meter resolution, normalization, idempotency and comparison are Billing's, reused as-is. |
| **SI5** | **Divergence is an Epic 10 defect** — reported, never reconciled away. |
| **SI6** | **Agreement must be demonstrated** for a valid closed period. |
| **SI7** | **Divergence detection must be demonstrated** by an injected historical discrepancy. |
| **SI8** | **The buffer gains no historical guarantee** — MB3/MB5/MB6 hold exactly. |
| **SI9** | **`api/metering/`'s boundary is unweakened** — one Billing edge, no `events` import. |
| **SI10** | **Certification, freeze and release artifacts are untouched.** |
| **SI11** | **Tests remain offline, hermetic and deterministic** — no network, no credentials, no vendor service; clock and ids injected; durability proven against file-backed SQLite via `tmp_path`, never in-memory. |

## Dependency & topology rules

```
   Event ──publish──▶ EventBus ──DW3 durable write──▶ durable log (evt_event)   [append-only, ED5]
                          │                                    │
                    subscribe (MB1)                   read_all() + in-memory window   ← M10.11
                          ▼                                    │
              api/metering (MetricTurnBuffer)         historical re-derivation
                          │                                    │
                 drain ── │ ─── MetricStreamPort ──▶ Billing ◀─┘  (comparison authority,
                          │        (live path)                      via its own frozen engine)
```

* The live path (**left**) is untouched: producer → bus → buffer → destructive `drain` → Billing.
* The validation path (**right**) is **new, separate and read-only** over the durable log.
* Both terminate at Billing's *existing* engine; neither introduces a second comparator.
* `billing` imports nothing from `api`, `events` or `runtime`. The graph stays acyclic (**AR-15**).
* `api/metering/` participates in the live path only, and its import budget is unchanged (**SI9**).

## Relationship to ADR-0039 MB5/MB6 — stated explicitly

**MB5** (destructive `drain`) and **MB6** (resident-only, best-effort `replay`) are **correct, in force,
and unmodified**. They are not obstacles this ADR works around; they are the reason it exists.

MB6 closed with a specific instruction: a consumer needing historical coverage *"needs a different
mechanism, separately ratified,"* and *"M10.10 must not be cited later as having provided one."* This
ADR obeys both halves. It **does not cite** M10.10 as providing historical coverage — it states plainly
that M10.10 does not. And it **provides the different mechanism**: the durable event log, which already
had the append-only, restart-safe, verbatim-preserving properties, reached by a separate read-only path.

The buffer keeps its honest limitation. The validation gets an honest source. Nothing is reinterpreted.

## Rejected interpretations

| # | Interpretation | Rejected because |
|---|---|---|
| **R1** | **`MetricTurnBuffer` as historical storage** | Contradicts MB3 (no persistence), MB6 (resident-only) and MB6's explicit instruction that historical coverage needs a separately ratified mechanism. It would also make an unbounded-by-time store out of a component whose entire justification is a fixed bound. |
| **R2** | **Making `drain` non-destructive** | Contradicts MB5, and reintroduces the exact defect MB5 rejected: with a bound and no removal, overflow discards the oldest signals *before anyone reads them*. It would also need a cursor model, which MB5 forbids. |
| **R3** | **Persisting historical data inside the buffer** | R1 and R2 combined, plus a schema and migration M10.11 is not assigned and does not need — the durable log already exists and already has the guarantees. |
| **R4** | **Modifying Billing to hide divergence** | Violates SV4, ADR-0033's standing rule, ADR-0023, ADR-0031 and the Epic 9 freeze. A divergence that can be edited away is not a validation. |
| **R5** | **Production runtime wiring as a prerequisite** | Contradicts DW10/RL17/RC16 (durable runtime deliberately unwired) and CV10 (`NullMetricStream` default). It would also make a validation milestone the vehicle for an operational change nobody authorized. |
| **R6** | **A second, Epic-10-owned metering/comparison implementation** | The precise drift `replay_period`'s docstring warns about: two implementations of one rule, agreeing until the day they do not. Reuse is not a convenience here, it is the point. |
| **R7** | **Adding a windowed read to `DurableEventLog` for convenience** | SV10 — a production historical-query capability must be ratified on its own terms, not arrive as a by-product of a test. In-memory filtering of `read_all()` is sufficient for the ratified proof. |
| **R8** | **Placing the re-derivation under `api/metering/`** | It would require `api/metering → events`, which M10.10's own guards forbid, and risks widening the single authorized Billing edge (SI9). |

## M10.11 implementation boundary

When separately authorized (**SV12**), implementation may create:

* **new test file(s) under `platform/tests/`** — the preferred and expected shape;
* **new production module(s) only if SV9's stop-and-report condition is triggered and answered.**

It may **not** modify: `platform/billing/**` · any pre-existing `platform/api/**` file · `platform/api/metering/**`
· `platform/runtime/**` · `platform/observability/**` · `platform/events/**` · `platform/core/**` ·
migrations · dependencies · configuration · lockfiles · certification controls · freeze manifests ·
release artifacts · any existing test · any ADR.

## Consequences

**Gained.** M10.11 becomes executable for the first time. The durable log acquires its first consumer —
until now it was written (when wired) and read only by recovery. Billing's `replay_period` and
`ReconcileUsagePeriod`, built at M9.11 and never exercised end to end, get their first real input.
Divergence detection becomes a demonstrated property rather than a claimed one.

**Accepted.** The proof is **hermetic**, so it validates the *derivation and comparison logic*, not a
running production system — because there is no wired production system to validate, by ratified
intent. Reading a closed period means `read_all()` plus an in-memory filter, which is O(all events);
acceptable for validation, explicitly not a production query path (SV10). And the live path still
cannot serve a closed period through `MetricStreamPort.replay()` — M10.11 validates *against* the
durable log rather than fixing that, which remains a genuine and now clearly-documented limitation.

**Unchanged.** Billing, the runtime, the observability producer, the metric buffer, the outbox, the
retry ladder, the recovery driver, certification, freeze and the release record.

## Known limitations

1. **Hermetic scope.** Agreement is proven for a constructed closed period, not for production traffic.
   Production validation needs the chain wired, which no milestone has authorized and Epic 11 owns the
   runtime side of.
2. **No closed-period `replay()` on the live path.** MB6's limitation is real and stays real: Billing
   calling `MetricStreamPort.replay()` for a drained period still gets `()`. M10.11 does not change
   that; it validates by another route and records the gap.
3. **`read_all()` cost.** Unbounded by design; the M10.8-accepted limitation, unchanged.
4. **One producer, one metric.** `metric.turn` is the only ratified `metric.*` member, so the proof
   covers one signal type. Additional types are additive under AR-32 and out of scope.

## What this ADR does not change

**ADR-0023, ADR-0031, ADR-0032, ADR-0033, ADR-0034, ADR-0035, ADR-0036, ADR-0037, ADR-0038 and
ADR-0039 are unchanged.** No ADR text is edited, deleted, reworded or superseded.

**Supersession: none.** ADR-0040 records the mechanism ADR-0039 MB6 required to be *separately
ratified*, and leaves MB5/MB6 in force exactly as written.

It creates no schema, no migration and no persistence; changes no conversation behaviour, no
`EventType`, no `EventSource`, no `MeterSource` and no Billing code; and it does **not** authorize
implementation. Every constraint in the [Epic 10 implementation plan](../roadmap/epic10-implementation-plan.md)
§1 continues to bind, as amended by ADR-0038 **TP1** for §1.12 alone.
