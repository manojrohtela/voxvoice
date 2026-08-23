# Durable Event & Telemetry Spine v1.0 — Epic 10 Repository Release

**Release:** **v5.0.0** · **Milestones:** M10.2–M10.14 (frozen) · released by **M10.15**
**Status:** 🔒 **Epic 10 FROZEN (architecture)** (M10.14) · 📦 **RELEASED** (M10.15) · **Depends on:** Billing
Platform v1.0 (Epic 9, frozen), Customer Platform v1.0 (Epic 8, frozen), Product Runtime v1.0
(Epic 7, frozen), Execution Platform v1.0 (Epic 6, frozen), Runtime Platform v1.0 (Epic 5, frozen).

> This is the repository release of Epic 10. **M10.14** froze the certified architecture and
> performed **no commit and no tag** — its own artifacts were left intentionally uncommitted at the
> freeze boundary (owner ruling **R-2**, **DL-0146**). **M10.15** discharges that condition: the
> frozen tree is committed and tagged **v5.0.0**, which is what makes the freeze durable rather
> than a description of a working directory.
>
> The freeze → release split exists precisely so roadmap risk **D-11** — *"implementation exists
> but is uncommitted at freeze"* — is discharged rather than repeated.

**Freeze manifest digest:** `d3d4858d5f18b7f6d583f4d34caeec52933acc262e36d4f8b343e2e8b6a00d38`
**Certification digest:** `7e57bc14764201b123c49b9321f15d9c41f0ee7015e6879c64c0307e56db3d0d`

---

## 1. Executive Summary

The **Durable Event & Telemetry Spine** (`platform/events/*`, `platform/observability/turn_latency.py`,
`platform/api/metering/*`) is Epic 10's durability plane — an append-only durable event log, a
transactional outbox with a bounded retry ladder, dead-letter and quarantine, a caller-driven
recovery sweep, guarded retention, runtime latency taps producing `metric.turn`, and a
process-owned metric-stream buffer feeding Billing through its frozen pull-shaped port.

Delivered across milestones **M10.2–M10.12**, independently certified in **M10.13** (verdict
**`certified`** — 13 controls, 0 blocking violations, 0 advisories, 0 inconclusive), and frozen in
**M10.14** (verdict **`FROZEN`** — 12 attestations, all satisfied). The architecture is released
here as **Durable Event & Telemetry Spine v1.0** under platform baseline **v5.0.0**.

Evidence at freeze: **5345 repository-wide tests pass, 0 failed, 0 skipped**;
`ruff check . --exclude voicebot` and `ruff check billing/ tests/` clean; migration chain
**0034–0036** valid and additive; migrations `0001`–`0033` unchanged; and frozen Epics 5/6/7/8/9
unchanged apart from the single authorized `MeterSource.OBSERVABILITY` member.

## 2. Frozen Scope

Enumerated by the M10.14 freeze manifest across `v4.0.0 → db8c508`: **44 platform paths** — 18
modules added, 7 modified, 14 test files added, 5 modified, 3 migrations.

**Modules added (18).** `events/ports.py` · `events/ladder.py` · `events/recovery.py` ·
`events/retention.py` · `events/persistence/{__init__,database,durable_log,mapping,models,retention,spine}.py` ·
`events/persistence/migrations/{__init__,v0034_durable_event_log,v0035_durable_outbox,v0036_outbox_retry_ladder}.py` ·
`observability/turn_latency.py` · `api/metering/{__init__,metric_buffer}.py`

**Modules modified (7).** `billing/foundation/catalog.py` *(the single authorized MB14 delta)* ·
`core/events.py` · `events/__init__.py` · `events/bus.py` · `observability/__init__.py` ·
`runtime/contracts.py` · `runtime/pipeline/pipeline.py`

**Schema.** Tables `evt_event`, `evt_outbox`, `evt_outbox_failure`; indexes
`ix_evt_event_seq_id`, `ix_evt_event_session_seq`, `ix_evt_outbox_failure_event_attempt`,
`ix_evt_outbox_state_next_attempt`, `ix_evt_outbox_state_seq`. Every table and index is
`evt_`-prefixed (ADR-0034 DS9). Migration head **`0036`**.

## 3. Frozen Ownership

Epic 10 owns the durable event spine and runtime telemetry, and owns **no revenue fact**. Billing
remains the sole owner of revenue (ADR-0023) and never subscribes to a runtime bus: the seam stays
pull-shaped, with a platform-owned buffer subscribing in-process and Billing pulling through its
frozen `MetricStreamPort`. The `metric.billing.*` namespace remains reserved to Billing and Epic 10
defines none.

## 4. Frozen Dependency Direction

`events → core` only. `events` imports no `api`, `billing`, `product`, `actuator`, `runtime`,
`replay`, `gateway`, `observability` or `providers` package. The one authorized inbound edge into
Billing is `api/metering/** → billing.foundation`. `events → billing` remains forbidden; PL-2 and
IR4 are unwidened. Only `platform/events/persistence/**` may import `sqlmodel`/`sqlalchemy`.

## 5. Frozen Durability Guarantees

Delivery is **at-least-once**; consumers are idempotent on `(event_id, meter_code)`. Identity is
`Event.id`; the bus is the sole sequence authority and the store persists `seq` verbatim. The
durable write is the state change and precedes dispatch. The log is **append-only**: the sole
sanctioned deletion path is retention, whose five-part predicate is **default-deny** — the failure
mode is to retain too much rather than to delete too early. `DELIVERED` alone counts as drained;
`DEAD_LETTERED` and `QUARANTINED` are terminal but never drained.

## 6. Extension Policy

Additive extension is permitted: new event types (AR-32), new migrations `≥ 0037`, new controls in
Epic 10's own namespaces. Forbidden without a new ADR and a new baseline: changing an existing
`EventType` member's name or value, adding an abstract method to `core.events.EventBus`, widening
PL-2/IR4, introducing a second sequence authority or a second recovery mechanism, or any deletion
path outside retention.

## 7. Certification & Freeze Verdicts — evidence only

| | |
|---|---|
| **Certification (M10.13)** | **`certified`** — catalogue `m10.13.1`, digest `7e57bc14764201b1…`, 13 controls `E10-1`…`E10-13`, **0** blocking violations, **0** advisories, **0** inconclusive |
| **Freeze (M10.14)** | **`FROZEN`** — manifest `m10.14.1`, digest `d3d4858d5f18b7f6…`, 12 attestations `EF-1`…`EF-12`, all `satisfied` |
| **Release (M10.15)** | **`RELEASED`** — this commit and tag |

Both are consumed here as **evidence**. **M10.15 did not re-certify M10.13 and did not re-freeze
M10.14**; it recomputed neither digest and altered neither artifact. The two digests remain
distinct surfaces: the **certification** digest is computed over findings alone and never sees a
verdict (ADR-0043 **CD8**), while the **freeze manifest** digest covers the whole manifest,
verdict included.

Epic 9's historical digests — manifest `eed692224f0aeb82…`, certification `39790b95d061a763…`,
both as of `v4.0.0` — are quoted here as **Epic 9 history only** and are neither recomputed nor
replaced. Epic 9's current post-M10.10 values remain a separate, authorized state.

## 8. Advisories

**No advisory was fixed in M10.15.**

Epic 10 declared **no advisories of its own** — its certification reported zero. The two open
advisories in the repository are **Epic 9's**, and they remain open with their original owners:

| Advisory | Owner | Statement | Fixed by M10.15? |
|---|---|---|---|
| **NS-5** | Epic 9 (M9.24) | No OpenAPI schema name is module-qualified by a Pydantic model collision | **No** |
| **ST-3** | Epic 9 (M9.24) | No Studio page rescales a currency by a hardcoded exponent when rendering integer minor units | **No** |

Neither is an Epic 10 advisory, and M10.15 neither closed, reclassified nor re-owned either.

## 9. Declared Limitations

**No limitation was closed in M10.15. All Epic 10 declared limitations remain open.**

### Epic 10's own limitations — four, all open

| # | Owner | Statement |
|---|---|---|
| **E10-LIM-1** | M10.13 | Epic 10's certification is **self-administered**: the same author implemented the architecture and the suite that certifies it. Inherited honestly from Epic 9's `LIM-7`, which keeps its own statement and its own owner. |
| **E10-LIM-2** | Epic 11 | The controls are **structural, not behavioural**. They certify that boundaries, prefixes, vocabularies and artifacts are what they claim to be; they certify nothing about the durable spine's behaviour under load, in production, or against real providers. |
| **E10-LIM-3** | M10.13 | Every "unchanged since" control depends on the `v4.0.0` tag remaining reachable. In a clone without tags or history those controls are `INCONCLUSIVE` — which does not pass, by design. |
| **E10-LIM-4** | separate governance decision | The three structural Epic 9 release-suite assertions recorded in **DL-0139** are not resolved here. Roadmap §M10.13 states that adding an Epic 10 suite does not resolve them. |

### Epic 9's limitations — nine, still Epic 9's

`LIM-1` … `LIM-9` remain declared, open, and owned by the Epic 9 milestones that can close them
(`M9.5`–`M9.24`). **They are not Epic 10 limitations**, were not inherited as Epic 10's, and were
not closed by M10.15. `LIM-7` — Epic 9's self-administered-certification limitation — keeps its own
statement and its owner `M9.24`; Epic 10 states the same condition in its own vocabulary as
`E10-LIM-1` rather than re-owning it.

## 10. Governance Status

| Record | Status |
|---|---|
| **DL-0139** | **Resolved** — discharged at M10.10; *"no longer blocking M10.15 / v5.0.0"*. **Not reopened by M10.15.** |
| **DL-0141** | **Resolved** — discharged at M10.5. **Not reopened by M10.15.** |
| **DL-0146** | **Accepted** — records the M10.14 freeze rulings R-1…R-7, including R-2's deferral of this commit to M10.15 and R-6's assignment of release documentation to M10.15. |
| **ADR-0032 – ADR-0043** | All Accepted and unmodified by M10.14 and M10.15. |
| **ADR-0044** | **Not created.** R-3 ruled M10.14 governance/freeze evidence rather than an architectural change, following the M9.25/DL-0136 precedent. |

## 11. Performance — no claim is made

**Epic 10 makes no latency or performance claim, and M10.15 created no performance baseline.**

Epic 10 deliberately established no latency requirement and measured none. **ADR-0042 HD2** ruled
out any wall-clock benchmark or production p50/p95 at M10.12, and **HD3** records **I1 as a
standing Epic 11 obligation**. `docs/performance/` does not exist and M10.15 did not create it.
PERFORMANCE_POLICY §5.1 gates benchmarking at Stage 2+ against a baseline that has never been
recorded.

Nothing in this release asserts that voice-to-voice latency budgets are met. **I1 remains an
Epic 11 obligation.**

## 12. Repository State — released (M10.15)

**The Epic 10 freeze artifacts are committed and tagged.**

At M10.14 the freeze manifest recorded the honest two-part state: the certified implementation was
**committed** at `db8c508`, while M10.14's own freeze artifacts were **intentionally uncommitted**
at the freeze boundary, to be committed by M10.15 (`commit_state.kind = "freeze_boundary_evidence"`).
That statement is historical evidence of the freeze boundary, not a present-tense claim — it remains
what was true at freeze, and M10.15 discharges it.

* Branch: `feat/m10.2-durable-event-foundation`
* Release commit: the commit **`v5.0.0` points at** — a document cannot contain its own hash, so
  the tag is the identity, exactly as it is for v1.0.0, v2.0.0, v3.0.0 and v4.0.0
* Release tag: **`v5.0.0`**, annotated — the established convention
* Parent: `db8c508 feat(certification): add Epic 10 certification`
* Working tree after release: **clean**
* **Version files: unchanged.** `platform/pyproject.toml` remains **`version = "0.1.0"`**, as it
  has through v1.0.0, v2.0.0, v3.0.0 and v4.0.0. **`v5.0.0` is the single release identity**, and
  inventing a second one here would create a competing version source.
* **Not pushed.** Push is outside M10.15's authorized scope; the commit and tag are local.

## 13. Release Verification

| Check | Result |
|---|---|
| M10.13 certification | `certified`, digest `7e57bc14764201b1…`, consumed unchanged |
| M10.14 freeze | `FROZEN`, digest `d3d4858d5f18b7f6…`, unchanged by the release |
| Frozen boundary | `platform/billing/**` unchanged since `v4.0.0` except the authorized MB14 member; migrations `0001`–`0033` unchanged |
| Migration head | `0036`; no `0037+` exists |
| `platform/replay/**`, `api/**observability*` | unchanged |
| Epic 9 freeze/certification/release artifacts | unchanged |
| `platform/pyproject.toml` | unchanged at `0.1.0` |
| `v4.0.0` | intact at `532b0bd72d55921fa385a3e4162d899c90fbe6ad` |
| Ruff | clean for `ruff check . --exclude voicebot` and `ruff check billing/ tests/` — **no repository-wide claim**; `voicebot/bot.py` carries two pre-existing findings outside Epic 10's scope |

## 14. Release Notes — Epic 10, Durable Event & Telemetry Spine v1.0

**What this release contains.** The durable event and telemetry spine, frozen at M10.14 and
committed here: the append-only durable log, the transactional outbox with its retry ladder,
dead-letter and quarantine, the caller-driven recovery sweep, guarded retention, the runtime
latency taps and `metric.turn` producer, the platform metric-stream buffer and its single
`api → billing` edge, shadow validation against Billing's own metering engine, the M10.13
certification suite and the M10.14 freeze manifest.

**What this release contributes to production behaviour: nothing new.** M10.15 adds repository
release metadata only — no event type, no table, no migration, no route, no port method, no
production module and no version-file change. The durable runtime remains deliberately unwired;
no concrete `DeliveryTransport` ships, and recovery must be invoked by a caller no milestone has
yet wired.

### Known release advisories

**No advisory was fixed.** `NS-5` and `ST-3` remain open Epic 9 advisories with their original
owners. Epic 10 declared no advisories of its own.

### Declared limitations at release

**No limitation was closed.** Epic 10's four declared limitations — `E10-LIM-1`, `E10-LIM-2`,
`E10-LIM-3`, `E10-LIM-4` — all remain open. Epic 9's nine limitations remain open and remain
Epic 9's.

### Not included

No real-provider integration, no vendor SDK, no streaming or semantic VAD, no Conversation Runtime
rework and no production latency validation — all **Epic 11**. No SDK, read API or mid-call control
— **Epic 12**. No distributed broker, external transport or WS/SSE gateway expansion.

### Milestone record

| Milestone | Delivery |
|---|---|
| M10.2 | Durable event foundation |
| M10.5 | Retention guards |
| M10.6 | Transactional outbox / durable write-side integration |
| M10.7 | Retry ladder, dead-letter, quarantine |
| M10.8 / M10.8a | Durable-log re-derivation and recovery · `metric.*` vocabulary |
| M10.9 | Observability build-out and `metric.turn` |
| M10.10 | Metric-stream seam |
| M10.11 | Shadow validation |
| M10.12 | Hardening |
| M10.13 | Certification — `certified` |
| M10.14 | Freeze — `FROZEN` |
| M10.15 | **Repository Release** — this commit and tag |
