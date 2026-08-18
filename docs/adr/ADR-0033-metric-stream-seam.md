# ADR-0033 — The metric-stream seam is pull-shaped; `metric.*` runtime ownership belongs to `observability/` and `metric.billing.*` stays reserved to Billing

| | |
|---|---|
| **Status** | **Accepted 2026-08-18** (ratified at M10.1). Recommended by the Epic 10 **M10.0 Blueprint** (accepted; not yet persisted to `docs/architecture/epic10/`) and settled at the **M10.0a** owner decision gate (corrections C-2/C-4, decisions OD-1/OD-8/OD-9); this milestone ratifies it. |
| **Amends** | Introduces the Epic 10 producer side of the metering path and the `metric.*` namespace partition. Amends no Constitution Part-A/Part-B section and **no frozen Epic**. It **implements against** ADR-0023 and ADR-0031 without modifying either, and modifies no file under `platform/billing/**`. |
| **Deciders** | Technical Owner |
| **Related** | [ADR-0023](ADR-0023-billing-sole-owner-of-revenue.md) (Billing is sole owner of revenue; consumes `metric.*` read-only), [ADR-0031](ADR-0031-billing-plane-repository-placement.md) (Billing plane placement; DD3/DD4/DD5/DD6, IR9), [ADR-0032](ADR-0032-event-durability-and-delivery-semantics.md) (durability + at-least-once), [ADR-0034](ADR-0034-durable-storage-seam-core-only-packages.md) (storage seam), [ADR-0004](ADR-0004-event-driven-core.md) (one typed envelope), Constitution §A5 / §A15 (observability) / §A12 (additive extension), ARCHITECTURE_RULES AR-2 (one producer per type) / AR-31 / AR-32 / AR-42 (every stage boundary carries a latency tap feeding `metric.turn`) |

## Status

**Accepted 2026-08-18**, ratified at milestone **M10.1**. Authorizes no code. It fixes the shape of
the seam and the ownership of the `metric.*` namespace for every Epic 10 implementation milestone
(M10.8a, M10.9, M10.10, M10.11). `platform/billing/**` is byte-for-byte unchanged; ADR-0023 and
ADR-0031 are preserved, not superseded.

## Context

Three verified facts set this decision, and the first two are easy to get wrong.

**1. The `metric.*` vocabulary does not exist.** `core.events.EventType` contains **no `metric.*`
member**. The nearest are `TOOL_METRICS_UPDATED = "tool.metrics.updated"` (a tool-runtime event)
and `COST_UPDATED = "cost.updated"`. Meanwhile `AR-42` mandates *"every stage boundary in the
conversation loop carries a latency tap feeding `metric.turn`"*, and the frozen Billing plane
describes its input as *"the authoritative `metric.*` stream"*. **Billing was built against a
contract whose producer side was never created.** This is not an Epic 9 defect — ADR-0031
deliberately left Billing unwired — but it means the producer side must be built from the
vocabulary up.

**2. The seam is pull-shaped, not push.** It is natural to describe this work as
"producer → Billing", and that phrasing is wrong in a way that would produce the wrong
architecture. The frozen `MetricStreamPort` states:

> "Read-only and **pull-shaped by design**: `drain` asks the platform's own buffer for what it has
> already received, so Billing never subscribes to a runtime bus, never opens a connection, and
> never holds a runtime resource. There is **no emit, publish, or write method** — Billing is
> structurally incapable of producing an operational metric (ADR-0023 §6)."

ADR-0031 **DD5** reinforces it: *"`billing` imports no runtime, execution, provider, deployment,
gateway, replay, or observability package. Facts from those planes arrive by injection."*

**3. The `api → billing` edge does not exist yet.** `platform/api/` imports `billing` **nowhere**;
`BillingAuthorizer` has no `api`-side implementation. The `MetricStreamPort` docstring's *"the
Customer Platform implements this port at the composition root, exactly as it implements
`BillingAuthorizer`"* describes an **intended pattern, not existing wiring**. Epic 10 creates the
first such edge. ADR-0031 §8 planned exactly this, marking every Epic 8 surface *"(additive)"*.

## Decision

### MS1 — `metric.*` is added to `core.events.EventType` additively, under AR-32

Epic 10 extends `core.events.EventType` with runtime `metric.*` members (including
**`metric.turn`**, which `AR-42` requires). This is an **additive vocabulary extension permitted by
AR-32** — *"schemas … evolve additively or by version bump, never by silent mutation"* — and needs
no dedicated ADR of its own (**OD-1**). It is recorded here, and cross-referenced from ADR-0032.

Binding constraints:

* **No existing member's name or value may change.** Additions only.
* The source follows the **existing Epic 6 comment convention** — the annotation used for the
  `TOOL_*`, `SKILL_*`, `HOOK_*`, `WORKFLOW_*` and `AGENT_*` blocks: *"additive vocabulary extension
  (no existing member changed; AR-32)"*.
* **`COST_UPDATED = "cost.updated"` is untouched** and stays **outside** `metric.*` — not renamed,
  not replaced, not merged, not reinterpreted (**OD-6**). Merging it would be precisely the silent
  mutation AR-32 forbids.
* This lands at **M10.8a**, which **must complete before M10.9**. There is nothing to emit until it
  does.

### MS2 — The seam is pull-shaped from Billing's perspective

Billing **pulls**. Nothing pushes into Billing. The port's two methods —
`drain(org_id, limit)` and `replay(org_id, period_start, period_end)` — are implemented exactly as
frozen, including the `replay` **method name** (a frozen API name, not an architectural noun; see
ADR-0032 ED11).

### MS3 — The platform-owned buffer subscribes to the `EventBus`

The buffer is the component that subscribes, decodes `metric.*` envelopes into `MetricSignal`
values, and holds them for collection. It is owned by the platform, lives outside
`platform/billing/**`, and is the **only** thing on this path that touches the bus.

### MS4 — Billing consumes exclusively through `MetricStreamPort`

`MetricStreamPort` is the single inbound path for `metric.*` into Billing. No second door is
created — no direct repository call, no shared table read, no back-channel.

### MS5 — Billing never subscribes to the `EventBus`

Billing holds no subscription, opens no connection, and holds no runtime resource. A total Billing
outage delays billing and nothing else (ADR-0031 D3). Billing remains **outside the runtime
composition root**.

Corollary preserved from the frozen contract: **an unwired port ingests nothing**, which is the
safe failure. No usage is ever invented.

### MS6 — Additive new modules under `platform/api/` are permitted for the seam

The seam's implementation may be introduced as **new modules under `platform/api/`**, creating the
first `api → billing` dependency. This follows ADR-0031 §8's explicit *"(additive)"* planning
language and **DD3** (*"`api → billing`, never the reverse"*).

**DD4** (*"No frozen Epic (5/6/7/8) may import `billing`"*) is honoured by the additive reading
ADR-0031 §8 itself established: pre-existing Epic 8 files import nothing new; only new modules
carry the edge.

### MS7 — No pre-existing `api/` file may be modified

Not one line of any file that exists under `platform/api/` at `v4.0.0`. The seam is new files only.
This is asserted, not merely intended (see CV5).

Billing never depends back on `api` — `billing → api` remains prohibited by ADR-0031 IR3/DD1.

### MS8 — `platform/observability/` owns runtime `metric.*`

`platform/observability/` is the **sole producer of runtime `metric.*` types**, including
`metric.turn` and the stage-boundary latency taps `AR-42` requires. It keeps its declared import
budget (`core` + stdlib). Taps must be cheap, non-blocking, must never alter conversation
behaviour, and must use an **injected clock** so they are deterministic under test.

`platform/observability/` is **not** to become a general-purpose logging framework.

This is distinct from the **frozen Epic 8 Customer-Platform observability capability**
(`api/**/observability*`, `cp_alert_rule`, migration `0012`), which Epic 10 does not touch.

### MS9 — `metric.billing.*` remains reserved to Billing

ADR-0031 **IR9** states: *"Billing emits no `metric.*` (it produces `billing.*` and
**`metric.billing.*` only**)"*, and ADR-0023 I5 lists Billing as an event producer on
`billing.*` + `metric.billing.*`. That namespace is **reserved to Billing** and is currently
unemitted.

### MS10 — Epic 10 defines no `metric.billing.*` member

Epic 10 **defines none, emits none, and takes no ownership of** the `metric.billing.*` namespace.

**AR-2 remains satisfied** — *"each event type is emitted by exactly one component"* — because
ownership is partitioned by **disjoint event types**, not by a shared prefix with two owners:

| Namespace | Owner | Epic 10 action |
|---|---|---|
| runtime `metric.*` (incl. `metric.turn`) | `platform/observability/` | defines + emits |
| `metric.billing.*` | Billing (ADR-0023 / ADR-0031 IR9) | **none** |
| `cost.updated` | existing `Metering` producer | **none** (OD-6) |

## Topology

```
platform/observability/          ← sole producer of runtime metric.*
        │ publishes metric.* through the ordinary EventPublisher path
        ▼
   core.events.EventBus          ← contract unchanged (ADR-0032 ED1)
        ▼
   durable event log             ← ADR-0032 / ADR-0034
        ▼
 platform-owned metric buffer    ← subscribes (MS3), decodes to MetricSignal
        ▲
        │  drain() / replay()    ← Billing PULLS (MS2)
 MetricStreamPort impl           ← additive new modules under platform/api/ (MS6)
        ▲
        │
     Billing                     ← never subscribes (MS5); billing/** unchanged
```

`Billing → Platform` stays one-way and read-only. No frozen Epic depends on Epic 10.

## Consequences

**Gained.** The producer side the frozen Billing contract has always expected · `AR-42` satisfiable
· a metering path that cannot invert, because the frozen port has no write method to invert
through · shadow validation becomes possible: usage re-derived from the durable log via
`replay(...)` can be compared against metered usage (M10.11).

**Accepted costs.** A first `api → billing` edge now exists and must be kept one-way · the metric
buffer is a new operational component · `metric.*` payload schemas become a compatibility surface
governed by AR-32 from here on.

**Explicitly preserved.** ADR-0023 and ADR-0031 intact and unamended · `platform/billing/**`
byte-for-byte unchanged, including `MetricStreamPort`, `MetricSignal`, `SourceEvent`,
`NullMetricStream`, `RecordingMetricStream`, and Billing's composition/application/persistence ·
IR9 unbroken · no pre-existing `api/` file modified · frozen Epic 8 observability capability
untouched · `COST_UPDATED` untouched.

**Standing rule for M10.11.** Any divergence between re-derived and metered usage is **an Epic 10
defect**. It is never to be resolved by modifying Billing.

## Alternatives Considered

| # | Alternative | Rejected because |
|---|---|---|
| B1 | **Push `MetricSignal`s into Billing** | Contradicts the frozen pull-shaped port, which has no inbound write method. Building it would require changing `platform/billing/**` — the one thing Epic 10 must not do. |
| B2 | **Let Billing subscribe to the `EventBus` directly** | Violates ADR-0031 DD5 and ADR-0023's "Billing never measures / never holds a runtime resource". It would also put Billing on a runtime code path, breaking ADR-0031 D3. |
| B3 | **Place the seam outside `api/` to leave Epic 8's tree wholly untouched** (M10.0a option (b)) | ADR-0031 §8 explicitly planned these surfaces as *"(additive)"* under `api/`. A separate composition root would depart from a ratified topology to avoid an additive file, and would leave two composition roots competing. Rejected at OD-8 in favour of additive `api/` modules plus the CV5 zero-modification assertion. |
| B4 | **Reuse `cost.updated` as the metering signal** | It is existing frozen vocabulary with its own producer. Repurposing it is the silent mutation AR-32 forbids, and would give one type two meanings. Settled as OD-6. |
| B5 | **Give `observability/` the whole `metric.*` prefix** | Directly contradicts ADR-0031 IR9 and ADR-0023 I5, which reserve `metric.billing.*` to Billing. Partitioning by disjoint type satisfies AR-2 without breaking either ADR. |
| B6 | **Define `metric.*` in `observability/` rather than `core.events`** | Forks the vocabulary ADR-0004 keeps singular, and no consumer could filter on it through the `EventBus` type filter. AR-32 permits the additive `core` extension, so the fork buys nothing. |

## Compliance Verification

| # | Assertion |
|---|---|
| **CV1** | `platform/billing/**` is byte-for-byte identical to `v4.0.0`. |
| **CV2** | No Epic 10 module is imported by `platform/billing/**`; `billing` imports no `api`, `events`, `observability`, or `runtime` package. |
| **CV3** | Billing holds **no** `core.events.EventBus` subscription — no `subscribe` call reachable from `platform/billing/**`. |
| **CV4** | Epic 10 defines **no** `metric.billing.*` `EventType` member and emits none (AST scan, mirroring the existing IR9 check). |
| **CV5** | **Zero modification to every pre-existing `api/` file** — every path present under `platform/api/` at `v4.0.0` is byte-for-byte unchanged; the seam adds new files only. |
| **CV6** | No existing `EventType` member's name or value changed; `COST_UPDATED == "cost.updated"` still holds. |
| **CV7** | `platform/observability/` imports `core` + stdlib only, and is the sole emitter of runtime `metric.*`. |
| **CV8** | Latency taps use an injected clock; no ambient `time.time()` on a tap path. |
| **CV9** | Frozen Epic 8 observability capability (`api/**/observability*`, `cp_alert_rule`, migration `0012`) unchanged. |
| **CV10** | With no port wired, ingestion is zero — no usage is invented. |

## Documentation

Linked from [`docs/INDEX.md`](../INDEX.md) and registered in [`docs/adr/README.md`](README.md).
ADR-0023 and ADR-0031 are **referenced, not amended** — neither file is modified by this ADR.
