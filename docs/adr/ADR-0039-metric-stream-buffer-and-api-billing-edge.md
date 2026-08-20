# ADR-0039 — The platform metric-stream buffer and the first `api → billing` edge: resolving the MS3/DW11 precedence conflict

| | |
|---|---|
| **Status** | **Accepted 2026-08-19** (ratified at the M10.10 owner decision gate, **before** implementation — DOCUMENTATION_RULES §3.7). |
| **Completes** | [ADR-0033](ADR-0033-metric-stream-seam.md) **MS3–MS7**, which declare the pull-shaped seam and the platform-owned buffer but decide neither the buffer's boundedness, its drain/replay semantics, its hot-path budget, its ordering claims, nor its failure behaviour. **It supersedes nothing.** ADR-0023, ADR-0031, ADR-0032, ADR-0033, ADR-0034, ADR-0035, ADR-0036, ADR-0037 and ADR-0038 remain immutable and intact; **ED1–ED12**, **DW1–DW11**, **RL1–RL17**, **RC1–RC16**, **MS1–MS10**, **TP1–TP11**, **AR-2**, **AR-15**, **AR-31**, **AR-32** and **AR-42** all continue to bind. |
| **Amendments** | **Amendment 1 — 2026-08-19** (owner ruling B-1/B-2/B-3): adds **MB14–MB16**, which fix the decoder's field mapping and record that implementation is **gated on a separate additive `MeterSource` vocabulary authorization**. Nothing in MB1–MB13 is edited, reworded or withdrawn; the amendment is **additive only**. |
| **Deciders** | Technical Owner |
| **Milestone** | Ratified at **M10.10**. Authorizes the M10.10 **architecture** and nothing beyond it. **It does not authorize implementation** — see **MB13**. |
| **Related** | [ADR-0033](ADR-0033-metric-stream-seam.md) (MS2–MS10), [ADR-0035](ADR-0035-durable-write-interposition-on-the-event-bus.md) (DW5, DW10, DW11), [ADR-0037](ADR-0037-outbox-recovery-driver-and-restart-re-dispatch.md) (RC9, RC16), [ADR-0038](ADR-0038-runtime-latency-taps-and-the-metric-turn-producer.md) (TP5, TP6, TP10 — the producer this buffer consumes), [ADR-0032](ADR-0032-event-durability-and-delivery-semantics.md) (ED3 sequence authority, ED12 idempotency), [ADR-0031](ADR-0031-billing-plane-repository-placement.md) §8, [ADR-0023](ADR-0023-billing-sole-owner-of-revenue.md), [DL-0013](../../.voxvoice/DECISION_LOG.md) (auxiliary observation degrades gracefully), [Epic 10 implementation plan](../roadmap/epic10-implementation-plan.md) §M10.10, Constitution v2 I1, **AR-15**, **AR-42** |

## Status

**Accepted 2026-08-19.** This ADR ratifies the **M10.10 architecture** only. Epics 5–9 remain frozen;
`platform/billing/**` is byte-for-byte unchanged; every pre-existing file under `platform/api/**` is
byte-for-byte unchanged; migrations `0001`–`0036` are untouched; `EventType` and `EventSource` are
unchanged; the frozen Epic 8 observability capability (`api/**/observability*`, `cp_alert_rule`,
`v0012`) is untouched; `v4.0.0` is not reopened.

## Context

M10.9 shipped the producer. `TurnLatencyTap` publishes exactly one `metric.turn` per terminal turn
through the ordinary `EventPublisher` path — and **nothing in the repository consumes it**. The
M10.10 read-only investigation confirmed that `metric.turn` currently has zero subscribers, that the
Billing side of the seam is complete and frozen (`MetricStreamPort`, `NullMetricStream`,
`RecordingMetricStream`, `IngestMetricSignal`, `UsageIngestionService`, a customer drain router), and
that `platform/api/` does not import `platform/billing/` anywhere today. M10.10 exists to close that
gap from the platform side without touching Billing.

The design gate found one **blocking conflict between ratified documents** and six architectural
properties that no ratified document specifies at all.

### The conflict

**[ADR-0033](ADR-0033-metric-stream-seam.md) MS3:**

> The platform-owned buffer **subscribes to the `EventBus`**, decodes `metric.*` envelopes into
> `MetricSignal` values, and holds them for collection … it is the **only** thing on this path that
> touches the bus.

**[Epic 10 implementation plan](../roadmap/epic10-implementation-plan.md) §M10.10:**

> A platform-owned buffer **subscribes to the bus**.

**[ADR-0035](ADR-0035-durable-write-interposition-on-the-event-bus.md) DW11:**

> the platform-owned metric buffer is a pull-shaped, asynchronous consumer (ADR-0033 MS2/MS5) and is
> therefore **an outbox destination under DW5, not an in-process subscriber**.

**[ADR-0035](ADR-0035-durable-write-interposition-on-the-event-bus.md) DW5:**

> treat **in-process synchronous subscribers as NOT outbox destinations** — they remain M4.1's
> synchronous, isolated fan-out.

These specify mutually exclusive delivery mechanisms. Either the buffer subscribes — in which case
DW5 places it outside the outbox destinations — or it is delivered through the outbox, in which case
MS3's *"subscribes to the `EventBus`"* is false. The M10.10 gate stopped and escalated rather than
reconciling them silently, exactly as the M10.7 and M10.8 gates did with DW7 and RC14.

### What no document specifies

Boundedness, capacity, overflow, backpressure, flush and shutdown behaviour appear **nowhere** in
connection with the metric buffer. Neither do ordering guarantees, the meaning of
`MetricSignal.sequence`, decode-failure behaviour, what `raw_units` carries when the primary scalar is
absent, or what happens to a signal that cannot be attributed to an organization. Two facts make
these load-bearing rather than academic:

* `MetricSignal.raw_units` is a **mandatory `int`**, while ADR-0038 TP5's `voice_to_voice_ms` is
  `int | None` and is absent whenever `speech_end` or `first_audio_out` was not reached.
* `Scope.__post_init__` **raises `BillingValidationError` on an empty `org_id`**, and `metric.turn`
  envelopes carry `org_id = ""` unless a composition root supplied one.

## Decision

### MB1 — Precedence: M10.10 uses in-process `EventBus` subscription

**Owner ruling (D-1).** The M10.10 platform metric buffer **subscribes to the `EventBus`**:

```
bus.subscribe(..., types=EventType.METRIC_TURN)
```

It is **not an outbox destination**. **ADR-0033 MS3 and plan §M10.10 control this narrow M10.10
delivery-mechanism question.**

**ADR-0035 is not amended, not superseded, and not reinterpreted generally.** DW11 remains in force
for every other purpose, and no character of ADR-0035 changes. The precedence ruling is scoped
strictly to *how the metric buffer receives `metric.*` events at M10.10* and settles nothing else.

The ruling rests on the documents themselves, not on convenience:

* **ADR-0033 owns this seam.** It is the ADR whose subject *is* the metric stream; MS3 is a decision
  within its own scope. DW11 is a remark about the metric buffer inside an ADR whose subject is
  durable write interposition.
* **DW11 disclaims its own authority here.** ADR-0035 states that *"This ADR decides no MS-level
  question and forecloses none"* — and the buffer's delivery mechanism is precisely an MS-level
  question. The owner therefore resolves the precedence in favour of **ADR-0033 MS3** and the
  **M10.10 plan** for this milestone's narrow delivery mechanism.
* **The plan agrees with MS3**, in the very section that defines M10.10.

Because the buffer is an in-process synchronous subscriber, **DW5 applies to it correctly and
consistently**: DW5 excludes such subscribers from outbox destinations, and this buffer is exactly
such a subscriber. The chosen design does not contradict DW5; it falls squarely inside the category
DW5 already carves out.

### MB2 — Why the outbox option is not executable within M10.10

Recorded so a later reader does not re-open the question expecting a cheap alternative:

* **[ADR-0037](ADR-0037-outbox-recovery-driver-and-restart-re-dispatch.md) RC9** deliberately defers a
  **concrete `DeliveryTransport`** — the port has *no* production implementation, by ratified
  decision, and delivery to a real destination therefore has no mechanism today.
* **DW10 / RL17 / RC16** each keep the **durable runtime wiring explicitly unwired**: the production
  runtime performs no durable write and drives no recovery sweep.

Choosing the outbox path would therefore pull **transport implementation and runtime wiring** —
work that three separate ratified ADRs deliberately deferred — **into M10.10**. Option (a) requires
neither. The in-process subscriber is not merely the better reading of the documents; it is the only
one executable inside M10.10's boundary without re-opening decisions made elsewhere.

### MB3 — The buffer is bounded, drops the oldest, and counts what it drops

**Owner ruling (D-2).**

* **One process-wide, in-memory buffer.**
* **Fixed capacity: 10 000 signals**, declared as **one named module-level constant**.
* **M10.10 reads capacity from no configuration, no environment variable and no settings object.**
  The constant is the whole configuration surface, and there is no other.
* **Overflow policy: drop-oldest.** When the buffer is at capacity, the oldest resident signal is
  discarded to make room for the newest.
* **Every drop increments a monotonically increasing `dropped` counter**, readable for tests and
  operators.
* **Overflow logging is `WARNING`**, and must be **rate-limited or first-occurrence-only** — a log
  line per drop is itself an unbounded cost on the hot path, which would make the overflow guard the
  thing that hurts the conversation.
* **No persistence. No durable storage. No migration. No flush. No shutdown hook.**
* **Buffer contents are lost on process exit.** This is stated plainly and must not be softened:
  M10.10 provides **no durability whatsoever**, and nothing in this design may be read as implying it.

**Why bounded.** MS5 makes a Billing outage an **expected, tolerated condition** — *"a total Billing
outage delays billing and nothing else."* An un-drained buffer is therefore a normal steady state,
not an exceptional one, which means an **unbounded** in-memory buffer on a long-lived voice process
has a guaranteed path to memory exhaustion during a condition the architecture explicitly promises to
survive. Drop-oldest with a counter degrades observability — recoverable, and visible — rather than
the conversation runtime, which is neither.

### MB4 — The hot-path budget (enforceable)

**Owner ruling (D-3).** On the subscriber path the buffer performs **bounded O(1) in-memory work
only**:

* read the envelope,
* decode it,
* append to the bounded ring,
* increment counters.

It **may not**:

* perform I/O of any kind,
* block,
* sleep,
* call a provider,
* call `EventPublisher.publish()`, directly or transitively,
* spawn a thread, task or timer,
* acquire a lock that another hot-path caller can hold.

**Structure.** The buffer is built on a structure equivalent to `collections.deque(maxlen=...)`,
whose append and pop are individually atomic, so that a **bus-side append** and an **API-side drain**
require **no mutual exclusion on the hot path**. If implementation later determines that a lock is
unavoidable, **that requires a new owner ruling** — it is not an implementation choice.

**Why.** `InProcessEventBus.publish()` is **synchronous run-to-completion**: it stamps `seq`, may
perform a durable write inline, and then drains its queue calling **every subscriber on the caller's
stack**. `metric.turn` is published from `TurnLatencyTap.complete()`, which runs **inside the
conversation coroutine**. Every millisecond a subscriber spends is therefore charged to a turn under
invariant **I1** (voice-to-voice ≤ 1.0 s) — Blueprint risk **R-9** exactly, and the same reasoning
that produced ADR-0038 TP10.

`EventDispatcher`'s per-subscriber exception isolation is a **safety net, not permission**: it
protects correctness when the buffer fails, and it protects nothing at all against latency. The
buffer is expected to be **total** — to handle every input without raising — and the isolation exists
for the case where that expectation is wrong.

### MB5 — `drain` is destructive, org-keyed and insertion-ordered

**Owner ruling (D-4).** One process-wide buffer whose entries are **keyed by `org_id`**.

`drain(org_id, limit)`:

* is **destructive** — returned signals are removed from the buffer,
* returns **up to `limit`** signals for that organization,
* **preserves insertion order** within the organization,
* **leaves other organizations' entries untouched**,
* **may scan the bounded buffer**, whose cost is bounded by the MB3 capacity and which runs **off the
  hot path** (an API request, never the bus path).

**No cursor model is introduced.** There is no read offset, no acknowledgement protocol and no second
notion of consumption state. Removal on drain *is* the consumption state.

**Why destructive.** A non-destructive drain combined with a bound is the worst of both: the oldest
signals are dropped by overflow **before anyone reads them**, so the buffer silently loses precisely
the data it exists to hold. Destructive drain plus the at-least-once tolerance the architecture
already assumes downstream (**ED2** delivery, **ED12** idempotency on `(event_id, meter_code)`) is the
combination the rest of the system is built for.

`RecordingMetricStream`'s non-destructive `drain` is a **test-double property, not a platform
specification**: it is deliberately built so that a redelivery bug is visible to Billing's idempotency
tests — its own docstring says *"a real platform buffer may redeliver, and a test stream that could
not would hide exactly the bug idempotency exists to prevent."* **Billing remains unchanged**, and
`RecordingMetricStream` is not modified, extended or reinterpreted by this ADR.

### MB6 — `replay` is best-effort over resident signals and guarantees nothing historical

**Owner ruling (D-4).** `replay(org_id, period_start, period_end)`:

* is **non-destructive**,
* filters **currently resident** signals by `observed_at`,
* is **best-effort only**,
* provides **no historical replay guarantee**,
* **cannot** recover signals already dropped through bounded overflow (MB3) or lost on process exit.

This is stated bluntly because the honest limitation is the useful part. An in-memory bounded buffer
**cannot** reconstruct a past billing period, and any consumer that needs that guarantee needs a
different mechanism, separately ratified. M10.10 must not be cited later as having provided one.

Note the naming discipline (plan §1 OD-3): "replay" here is the **frozen Billing-owned
`MetricStreamPort.replay()` method name**, which M10.10 implements as ratified at the
[ADR-0037](ADR-0037-outbox-recovery-driver-and-restart-re-dispatch.md) RC14 / plan owner ruling F-1
boundary. It is neither `platform/replay/**` nor Epic 10's durable-log **re-derivation**, and the
frozen `MetricStreamPort` ABC is unchanged.

### MB7 — Ordering is per-org insertion order; `sequence` is provenance only

**Owner ruling (D-5).**

* Buffer ordering is **insertion order per organization**.
* `MetricSignal.sequence` receives `Event.seq` as **provenance only**.
* `Event.seq` remains authoritative **only within one `EventBus` instance** (**ED3**).
* The repository's architecture uses **one `EventBus` per session**.
* **Therefore `Event.seq` carries no global and no cross-session ordering guarantee**, and concurrent
  sessions produce independently numbered streams that a shared buffer interleaves.
* **M10.10 introduces no second sequence authority** (**ED3** is preserved exactly).
* **No consumer may be told that `sequence` is globally ordered** — not in a docstring, not in a
  comment, not in a test name.

Recording the number is useful provenance and costs nothing. Claiming that it orders concurrent
sessions would be false and would be relied upon.

### MB8 — Malformed events are dropped, logged and counted

**Owner ruling (D-6).** A malformed or undecodable `metric.turn` is:

* **dropped**,
* **explicitly logged**,
* **counted** in a **dedicated malformed/decode counter**,
* **never raised into the conversation path**,
* **never** routed into a second quarantine mechanism.

**Decode identity strictly, payload shape leniently.** A signal is constructed only from an envelope
whose identity fields are usable; a payload missing **optional** keys is normal operation, not
malformation. **A missing `voice_to_voice_ms` is *not* a decode failure** — it is governed by **MB9**
and counted separately.

This is [DL-0013](../../.voxvoice/DECISION_LOG.md)'s ratified posture: auxiliary observation degrades
**gracefully and loudly**, never silently, while the state machine stays strict. Billing already owns
its own quarantine path; a second one at this seam would create two places a signal can disappear and
two places an operator must look.

### MB9 — A missing `voice_to_voice_ms` produces no `MetricSignal`

**Owner ruling (D-7).** When `voice_to_voice_ms` is missing or `None`:

* **do not construct a `MetricSignal`**,
* increment a **dedicated skip counter**, distinct from the MB8 malformed counter,
* log at **`DEBUG`**,
* **do not** treat it as malformed — an interrupted turn with no complete voice-to-voice measurement
  is normal operation, not a fault.

`raw_units` is populated **only** from `voice_to_voice_ms`. `raw_unit` is **fixed to milliseconds**
and documented as such.

**Do not emit `0`. Do not substitute another scalar.**

**Why.** `raw_units` is a mandatory `int` on a structure that feeds a metering engine. A `0` would
place a **fabricated usage quantity** on a billing path and would be indistinguishable from a real
zero-millisecond measurement — the same objection ADR-0038 TP5 raised against zero-filling unreached
boundaries. Substituting a different scalar would silently change what the meter means. **CV10**
forbids inventing usage: an unmeasurable turn contributes nothing rather than something wrong.

### MB10 — An unattributable signal produces no `MetricSignal`

**Owner ruling (D-8).** When the envelope's `org_id` is empty, or cannot otherwise produce a valid
`Scope`:

* **do not construct a `MetricSignal`**,
* increment a **dedicated attribution-skip counter**, distinct from MB8 and MB9,
* log at **`DEBUG`**.

The buffer **validates before constructing `Scope`**, and **never** allows `BillingValidationError`
to propagate onto the bus path — that is the MB4 totality requirement applied to the one construction
that is known to raise.

**Rejected:** `scope=None` — an unmeterable signal that occupies bounded capacity and defers the
failure into Billing. **Rejected:** synthesized attribution — inventing an organization that did not
incur the usage.

**Why.** `Scope.__post_init__` rejects an empty `org_id`, `metric.turn` envelopes may legitimately
carry `org_id = ""` when no composition root supplied one, and an unattributable signal **cannot be
metered by anyone**. **CV10** governs: ingest nothing rather than invent usage.

### MB11 — The first `api → billing` edge

**M10.10 creates the first `api → billing` architectural edge in the repository.** It does not exist
today: no file under `platform/api/` imports `platform/billing/`.

**Direction:** `platform/api → platform/billing`, one way.

* **Acyclic under AR-15.** The edge points from the platform's API plane *to* Billing. Billing's own
  ratified direction (ADR-0023, ADR-0031) is `Billing → Platform` for consumption of the metric
  stream via a port **it declares and the platform implements** — a dependency **inversion**, not a
  runtime call. Billing imports nothing from `api/`, so no cycle is created.
* **Billing does not subscribe to the `EventBus`** (MS2, MS5). The buffer is *"the only thing on this
  path that touches the bus"* (MS3).
* **Billing holds no runtime resource**, opens **no runtime connection**, and starts nothing.
* **`MetricStreamPort` remains the single inbound path** (MS4). No second door is opened, and the
  frozen ABC is unchanged.
* The **platform buffer implements the Billing-owned `MetricStreamPort`**; **Billing pulls** through
  `drain`/`replay` at times of its own choosing.
* **`NullMetricStream` remains the default** in `billing/application/application.py`, so **an unwired
  port ingests nothing** (MS5, **CV10**) rather than inventing usage.

**Billing is not modified.** `platform/billing/**` remains byte-for-byte unchanged, and nothing in
this ADR should be read as implying otherwise.

### MB12 — Placement

The implementation is **additive new module(s) under `platform/api/`** (plan §M10.10, OD-8,
[ADR-0031](ADR-0031-billing-plane-repository-placement.md) §8 *"(additive)"*), **outside
`platform/billing/**`**. **No pre-existing file under `platform/api/` may be modified** — an assertion
the M10.10 test suite must carry (**CV5**).

The buffer does **not** live under `platform/observability/` (whose `core` + stdlib budget MS8 fixes,
and whose scope guard bans `MetricStream`/`MetricSignal`) and **not** under `platform/events/` (whose
M10.8 scope guard bans the same names in `events/recovery.py`). Neither existing guard is invalidated
by this placement.

### MB13 — This ADR ratifies architecture, not implementation

**This ADR authorizes the architecture but does *not* authorize implementation.** No M10.10 production
implementation is part of the step that ratified it. **Implementation requires a separate, explicit
authorization issued after this ADR is Accepted.**

When that authorization is given, it will permit only:

* new module(s) under `platform/api/`,
* the `MetricStreamPort` implementation,
* the `Event` → `MetricSignal` decoder,
* the bounded buffer,
* new tests,

with:

* **zero modification** to pre-existing `platform/api/**`,
* **zero modification** to `platform/billing/**`,
* **no migration**,
* **no dependency change**,
* **no existing test edits**.

**If an existing test is genuinely invalidated, STOP and request a separate owner ruling** rather than
modifying it.

> **Superseded in part by Amendment 1 (MB16).** Implementation is now gated on **two** authorizations,
> not one: the additive `MeterSource` vocabulary change must be authorized and completed **first**.
> MB13's list of what an implementation authorization may permit is otherwise unchanged.

---

# Amendment 1 — the decoder field mapping and the `MeterSource` gate

**Owner ruling B-1/B-2/B-3, 2026-08-19.** Recorded as an **additive amendment**: MB1–MB13 are
unchanged, and **ADR-0039 remains Accepted and is not superseded**. ADR-0023, ADR-0031, ADR-0033,
ADR-0035, ADR-0036, ADR-0037 and ADR-0038 remain untouched.

The M10.10 implementation gate found three decoder fields that no ratified document decided. One of
them turned out to be a **frozen-boundary blocker**, which is why this amendment exists before any
line of the decoder was written.

### MB14 — `source` requires an additive `MeterSource.OBSERVABILITY`, which is **not** authorized here

**The gap.** `MetricSignal.source` is a **required** field typed as the Billing-owned enum
`MeterSource` (`billing/foundation/metering_factories.py`), whose members are exhaustively
`runtime.session`, `provider.stt`, `provider.tts`, `provider.llm`, `knowledge`, `tool`, `workflow`,
`deployment`, `control_plane.api` (`billing/foundation/catalog.py`). **There is no `observability`
member**, while **`EventSource.OBSERVABILITY` has existed since M10.9** (ADR-0038 **TP4**) and is the
authoritative producer of `metric.turn` (ADR-0033 **MS8**, **AR-2**).

**The ruling.** The decoder must eventually populate:

```
source = MeterSource.OBSERVABILITY      # enum value: "observability"
```

**Rejected — `MeterSource.RUNTIME_SESSION`.** `CommercialMeter.accepts()` matches on **two** parts:

```python
if source is not self.source:
    return False
return not self.accepted_event_types or event_type in self.accepted_event_types
```

`CommercialMeter.source` **defaults** to `RUNTIME_SESSION`, and `accepted_event_types = ()` means
*"any type from the authoritative source"*. Stamping `RUNTIME_SESSION` would therefore let
`metric.turn` be **silently folded into an existing, unrelated commercial meter**, metering turn
latency in milliseconds as session usage on a revenue-bearing path. That is precisely the failure the
two-part key exists to prevent — `accepts()`'s own docstring: *"the **event type** allow-list prevents
one plane's unrelated event being folded into a meter that happens to share its source."*

**Rejected — `MeterSource.CONTROL_PLANE_API`.** The buffer lives under `platform/api/`, but the
**producer** is `platform/observability/`. This misattributes the authoritative source (**ADR-0023 M2**:
*"the **one** authoritative producer of a meter's signal"*) and carries the identical collision hazard
against any control-plane meter.

**Rejected — an empty or default value.** `MeterSource` has no neutral member, and any default is one
of the two collision hazards above.

**Rejected — skipping every `metric.turn` signal.** It satisfies CV10 trivially and nullifies M10.10.

**The gate.** Adding `MeterSource.OBSERVABILITY` is an **additive vocabulary change to frozen
Billing** (`billing/foundation/catalog.py`). It is permitted by **AR-32** in principle, but:

* **the M10.10 implementation authorization explicitly forbids modifying `billing/**`**;
* CV1–CV3 and the Epic 9 freeze require `billing/**` byte-for-byte unchanged;
* **this amendment does not authorize it, and no `MeterSource` change is made by this step.**

It is therefore a **separate authorization boundary**, and the decoder **cannot be implemented until
it is crossed**. See **MB16**.

### MB15 — the remaining decoder field mapping

**B-2 — `producer`.** The decoder must populate:

```
producer = "observability.turn_latency"
```

**Provenance only.** It does **not** participate in meter matching (`accepts()` reads `source` and
`event_type`, never `producer`). The module-qualified form is required — not `"observability"`, not
`""`, and not a class name — so the provenance names the exact producer, not merely its plane.

**B-3 — `payload_digest`.** The decoder must populate:

```
payload_digest = SHA-256( canonical_json( Event.data ) )
```

Canonicalization, exactly:

1. serialize `Event.data` as JSON,
2. **sort object keys**,
3. use **compact separators**,
4. encode the canonical JSON as **UTF-8**,
5. hash with **SHA-256**,
6. hash **`Event.data` only**.

**Excluded from the digest:** `Event.seq`, `Event.ts`, `event_id`, envelope metadata, producer
metadata. The digest covers the **payload**, so it stays stable under anything the envelope stamps —
which is what lets a reconciliation prove *"the event this usage was derived from is the same event
the log still holds"* without Billing keeping a second copy.

**Billing's private `_digest_id` is not reused.** It is a module-private **id** digest with different
inputs, a different purpose and a truncated output; borrowing it would couple this seam to a private
Billing implementation detail whose semantics do not match.

**The complete mapping**, for the implementation that follows:

| `MetricSignal` field | Value |
|---|---|
| `event_id` | `event.id` |
| `event_type` | `event.type` (`"metric.turn"`) |
| `source` | `MeterSource.OBSERVABILITY` — **gated, see MB14/MB16** |
| `observed_at` | `event.ts` (wall clock — `EventPublisher` defaults to `time.time`, which is what Billing's lateness arithmetic and `replay` windowing require) |
| `raw_units` | `voice_to_voice_ms` — and nothing else (**MB9**) |
| `raw_unit` | `"ms"` |
| `scope` | `Scope(event.org_id)` — constructed only after validation (**MB10**) |
| `received_at` | left to Billing's `MeteringFactory.source_event`, which defaults it through its own injected clock |
| `producer` | `"observability.turn_latency"` |
| `sequence` | `event.seq` — **provenance only** (**MB7**) |
| `payload_digest` | `SHA-256(canonical_json(event.data))` |

**The ratified skip rules are unchanged.** **MB8** (malformed → drop, dedicated counter, explicit log,
never raises, missing *optional* payload keys are not malformed), **MB9** (`voice_to_voice_ms` missing
or `None` → no signal, dedicated skip counter, `DEBUG`, never `raw_units = 0`, never a substitute
scalar, never classified as malformed) and **MB10** (empty or invalid `org_id` → no signal, dedicated
attribution counter, `DEBUG`, validate **before** constructing `Scope` so `BillingValidationError` can
never escape onto the bus path, never `scope=None`, never a synthesized `org_id`) all continue to bind
exactly as written.

### MB16 — implementation is gated on two authorizations, in order

**M10.10 decoder implementation is BLOCKED by MB14.** The sequence is:

1. **A separate explicit authorization** for the **additive `MeterSource.OBSERVABILITY` vocabulary
   change** to frozen Billing. **Not granted by this amendment.**
2. That vocabulary change, completed under its own authorization.
3. **Then** a further implementation authorization for M10.10 itself, subject to MB13's unchanged
   limits.

**No `platform/api/` implementation is authorized by this amendment**, and no test may be written that
depends on the unapproved enum member. **No workaround may be invented** — in particular, the decoder
may not substitute a different `MeterSource`, stamp a raw string, or defer the field, since each is one
of the alternatives MB14 rejected.

### Amendment 1 verification criteria

* **MV20** — `source is MeterSource.OBSERVABILITY` on every decoded signal; no other `MeterSource`
  member ever appears on this path.
* **MV21** — `producer == "observability.turn_latency"` exactly.
* **MV22** — `payload_digest` equals SHA-256 over sorted-key, compact-separator, UTF-8 JSON of
  `Event.data`, and is **unchanged** when `seq`, `ts`, `id` or envelope metadata change.
* **MV23** — Billing's `_digest_id` is not imported or reproduced.

---

## Verification criteria

The M10.10 implementation, when separately authorized, must make each of these executable:

* **MV1** — The buffer subscribes to the bus for `EventType.METRIC_TURN` and appears nowhere in any
  outbox path; no `DeliveryTransport` implementation is introduced.
* **MV2** — Capacity is a single named module-level constant equal to `10 000`; no configuration,
  environment variable or settings lookup reaches it.
* **MV3** — At capacity, the oldest signal is discarded, the newest is retained, and the `dropped`
  counter increases by exactly one per drop.
* **MV4** — Overflow logging is rate-limited or first-occurrence-only; a sustained overflow does not
  emit one log record per event.
* **MV5** — The subscriber path performs no I/O, no sleep, no publish, no thread/task/timer creation
  and acquires no hot-path lock (asserted structurally, over the module source).
* **MV6** — The subscriber never raises: every malformed, unmeasurable and unattributable input
  returns normally.
* **MV7** — `drain(org_id, limit)` removes what it returns, returns at most `limit`, preserves
  insertion order within the organization, and leaves other organizations' entries present.
* **MV8** — `replay(...)` removes nothing and returns only resident signals within the window.
* **MV9** — `MetricSignal.sequence` carries the originating `Event.seq`; no test or docstring claims
  cross-session ordering.
* **MV10** — A malformed envelope increments the malformed counter and constructs no signal.
* **MV11** — A turn with `voice_to_voice_ms` absent or `None` constructs no signal and increments the
  measurement-skip counter, **not** the malformed counter.
* **MV12** — An envelope with `org_id == ""` constructs no signal, increments the attribution-skip
  counter, and raises no `BillingValidationError`.
* **MV13** — `raw_units` equals `voice_to_voice_ms` and `raw_unit` is the fixed millisecond unit.
* **MV14** — `platform/billing/**` is byte-for-byte unchanged.
* **MV15** — No pre-existing file under `platform/api/` is modified (**CV5**).
* **MV16** — Billing holds no `EventBus` subscription; `NullMetricStream` remains the default.
* **MV17** — `EventType` and `EventSource` are unchanged; no `metric.billing.*` member exists
  (**CV4**).
* **MV18** — No migration is added; migrations `0001`–`0036` are unchanged.
* **MV19** — Tests remain offline, hermetic and deterministic, with clock and ids injected.

## Non-goals

M10.10 implements none of the following, and this ADR authorizes none of them:

tracing · cost metering · a logging framework · `metric.billing.*` · aggregation · a scheduler · a
thread · a worker · persistence · durable storage · a migration · a flush · a shutdown hook · a
transport · runtime wiring · provider integration · Epic 11 work · M10.11 shadow validation · latency
revalidation · a second sequence authority · a second quarantine mechanism · a configuration surface
for buffer capacity.

## Dependency Direction

```
runtime ──(notify-only, structural)──▶ observability ──publish──▶ EventBus
                                                                    │
                                                            subscribe (MB1)
                                                                    ▼
                                                         api/  (metric buffer)
                                                                    │
                                                   implements MetricStreamPort
                                                                    ▼
                                                              billing  (pulls)
```

`runtime` imports no `observability`; `observability` imports no `events`; `billing` imports no `api`
and no `events`. Every arrow is one-way and the graph is acyclic (**AR-15**).

## Consequences

**Accepted.** Signals are lost on overflow and on process exit, and this design provides no way to
recover them. A billing period cannot be reconstructed from this buffer. These are the honest costs of
an in-memory, bounded, non-durable seam, and they are recorded rather than mitigated because
mitigating them means durability, which M10.10 does not have and must not pretend to have.

**Accepted.** Turns without a complete voice-to-voice measurement, and events without a usable
organization, produce **no billable signal at all**. Under-reporting is the deliberate direction of the
error: **CV10** prefers ingesting nothing to inventing usage.

**Gained.** `metric.turn` acquires its first consumer, the seam ADR-0033 described becomes real, and
M10.11 gains something to compare against. The `api → billing` edge exists for the first time, in the
direction and shape ADR-0031 §8 anticipated.

**Unchanged.** Billing, the runtime, the observability producer, the durable log, the outbox, the
retry ladder and the recovery driver are all untouched by M10.10.

## Alternatives Considered

**Outbox delivery (DW11's reading).** Rejected — see **MB2**. It requires a concrete
`DeliveryTransport` that RC9 deferred and durable runtime wiring that DW10/RL17/RC16 keep unwired,
pulling two deferred bodies of work into M10.10 to obtain a delivery path the bus already provides.

**A hybrid (subscribe now, outbox later).** Rejected — it ratifies two mechanisms for one seam,
contradicting MS4's *"single inbound path"*, and defers the very decision this ADR exists to make.

**Amending or superseding ADR-0035.** Rejected — DW11's substantive subject (durable write
interposition) is correct and in force; only its incidental MS-level remark conflicts, and ADR-0035
already disclaims MS-level authority. Amending an Accepted ADR to fix a remark would violate ADR
immutability (DOCUMENTATION_RULES §3.4) for no benefit a precedence ruling does not already give.

**An unbounded buffer.** Rejected — MS5 makes a Billing outage an expected condition, so an unbounded
buffer has a guaranteed path to memory exhaustion during a scenario the architecture promises to
survive.

**Drop-newest on overflow.** Rejected — it discards the freshest evidence and produces a buffer whose
contents grow steadily staler during exactly the outage an operator is trying to diagnose.

**Persisting the buffer.** Rejected — durability requires a schema and a migration, and M10.10 is
assigned neither; the durable log and outbox already exist for events that need durability.

**Non-destructive drain with a cursor.** Rejected — a second consumption-state model at a seam whose
downstream is already idempotent under ED12, and one that loses the oldest entries to overflow before
any reader reaches them.

**Emitting `raw_units = 0` for unmeasured turns.** Rejected — a fabricated usage quantity on a billing
path, indistinguishable from a real zero.

**`scope=None` for unattributable signals.** Rejected — occupies bounded capacity with something no
meter can consume and moves the failure into Billing, which is frozen.

**Placing the buffer in `observability/` or `events/`.** Rejected — MS8 fixes `observability/` to a
`core` + stdlib budget, the plan assigns M10.10 to `platform/api/`, and both planes carry scope guards
banning these names.

## What this ADR does not change

**ADR-0023 unchanged. ADR-0031 unchanged. ADR-0033 unchanged. ADR-0035 unchanged. ADR-0036 unchanged.
ADR-0037 unchanged. ADR-0038 unchanged.** No ADR text is edited, deleted, reworded or superseded.

**Supersession: none.** ADR-0039 records a **precedence ruling scoped strictly to the metric buffer's
delivery mechanism at M10.10**. **ADR-0035 remains in force for every other purpose**, DW11 included.

It creates no schema, no migration and no persistence; it changes no conversation behaviour, no
`EventType`, no `EventSource` and no Billing code. It authorizes the **M10.10 architecture** and
nothing beyond it, and — per **MB13** — it does **not** authorize implementation. Every constraint in
the [Epic 10 implementation plan](../roadmap/epic10-implementation-plan.md) §1 continues to bind, as
amended by ADR-0038 **TP1** for §1.12 alone.
