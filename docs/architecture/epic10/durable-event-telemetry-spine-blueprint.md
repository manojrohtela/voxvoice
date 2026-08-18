# VoxVoice — Epic 10 (Durable Event & Telemetry Spine) Blueprint (M10.0)

| | |
|---|---|
| **Milestone** | **M10.0** — Epic 10 blueprint (design only; the first Epic 10 milestone) |
| **Status** | ✅ **Accepted** at M10.0; persisted to the repository at **M10.3**. Design only — no code, no ADR, no schema, no migration was created by this milestone. |
| **Date** | Accepted 2026-08-18 · persisted 2026-08-18 (M10.3) |
| **Target release** | **v5.0.0** |
| **Authoritative inputs** | Constitution v2 (Part A + Part B/P-ADR-0001), ADR-0001…ADR-0023, ADR-0031, **DL-0017**, Epic 5–9 freezes, Epic 9 release baseline `v4.0.0` |
| **Verdict** | **M10.0_BLUEPRINT_READY** — conditional on the M10.0a corrections and open decisions |

> **Persistence note (M10.3).** This blueprint was produced and accepted at M10.0 but deliberately
> **not** written to the repository at the time (M10.0 was authorized as design-only, with no file
> creation). M10.3 persists it unchanged as the historical M10.0 record, because ADR-0032/0033/0034
> each cite it and because M10.4+ scope must be repository-derivable. See the revision note below
> for the one finding later superseded by measurement.

> **Revision note — C-1 superseded at M10.1.** §3 records a discrepancy between a stated baseline of
> 4690 tests and the repository's recorded 4643, and recommends treating 4690 as unsubstantiated.
> **That characterization was withdrawn by the Owner at M10.1 after measurement**: the suite was
> executed in a clean offline environment and returned **4690 collected / 4690 passed / 0 failed /
> 0 skipped**, reproduced across three independent environments and two pytest majors. The
> resolution stands as ruled: **4643 is preserved as the historical Epic 9 record and is not
> edited**; **4690 is the authoritative Epic 10 regression baseline**; the **+47 delta is recorded
> as an Epic 10 observation with no cause assigned**. The body below is left as written at M10.0.

---

## 1. Executive Summary

The proposed Epic 10 scope is substantially supported by the repository. S1 (durable event store) and
S2 (outbox/DLQ) are not merely permissible — they are **pre-authorized** by **DL-0017**, which names
"durable outbox + retry/DLQ + async/non-blocking delivery" as deferred work to be built behind the
unchanged `EventBus` contract. S4's Billing-side seam **already exists in frozen code** as
`MetricStreamPort`, whose docstring explicitly anticipates an "authoritative log" for replay.

The inspection nevertheless surfaced **four corrections** the proposal must absorb before M10.1 and
**three open decisions** requiring Owner resolution. The most consequential: **the `metric.*` event
vocabulary does not exist anywhere in the repository**, despite `AR-42` mandating it and Billing's
frozen documentation describing it as "the authoritative `metric.*` stream". S3 is therefore larger
than "build out `observability/`" — it must first *define* a vocabulary in frozen shared `core`.

## 2. Current Repository State

**[VERIFIED]** `HEAD` = `532b0bd72d55921fa385a3e4162d899c90fbe6ad`; `git status` clean;
`git cat-file -t v4.0.0` = `tag` (annotated); `git rev-list -n1 v4.0.0` matches HEAD; parent
`a376e27` (Epic 8 / v3.0.0). Tags: `foundation-v1`, `v0.1.0-runtime-foundation`,
`epic5-runtime-platform-v1.0`, `v1.0.0`–`v4.0.0`.

`platform/` holds 40+ packages; `docs/` holds `adr/`, `architecture/` (epic5–epic9), `releases/`,
`roadmap/`, `audits/`, `runtime/`, `operations/`; `.voxvoice/` holds 14 governance files.

Governance hierarchy intact: **Constitution → ADRs → `.voxvoice/` governance → plans → code**
(`docs/adr/README.md`: ADRs are "second-highest authority… below the Engineering Constitution,
above governance").

## 3. Epic 9 Boundary Verification

| Boundary item | Result |
|---|---|
| Certification digest `39790b95…d2d8` | **[VERIFIED]** `epic9/certification.md:70`, `releases/epic9-billing-platform-v1.0.md:19` |
| Freeze manifest digest `eed69222…6b65c` | **[VERIFIED]** release doc |
| Migration head `0033` | **[VERIFIED]** `freeze.md:108` (FZ-2, "none after 0033"); last file `v0033_billing_consolidation.py` |
| 27 certification controls | **[VERIFIED]** `certification.md:66` — 24 blocking, 3 advisory |
| Advisories `NS-5`, `ST-3` | **[VERIFIED]** open, named owners |
| Nine limitations + owners | **[VERIFIED]** release doc §11 |
| ADR-0023 / ADR-0031 | **[VERIFIED]** Accepted 2026-08-01 |
| Tag `v4.0.0`, commit `532b0bd` | **[VERIFIED]** |
| `platform/billing/**` | **[VERIFIED]** present (foundation/application/persistence/customer) |
| Frozen Epics 5–9 | **[VERIFIED]** release docs present; Epic 7 (M7.0F) and Epic 8 (M8.20) freezes recorded |

**Discrepancy recorded (not resolved at M10.0):** the brief stated 4690 tests; the repository records
**4643** twice (`freeze.md:244`, release doc:36). All four digests match exactly, so the certification
and freeze attestations are intact. *(Superseded at M10.1 — see the revision note above.)*

## 4. Problem Statement

`InProcessEventBus` (`platform/events/bus.py`) states the gap itself: *"in-process only. No durable
persistence, no distributed broker, no external transports (those are M4.2+). Strict
producer/consumer async decoupling (an outbox) is a durable-delivery concern deferred with them
(DL-0017)."*

**[VERIFIED]** Events live in a `deque` dropped on `shutdown()`; delivery is synchronous
run-to-completion on the producer's thread; a raising subscriber is caught, logged and **discarded**
with no retry, dead-letter or durable trace; on process exit the entire history ceases to exist.

Consequences: **AR-31** unsatisfied for the durable case; **nothing can be re-derived**; **Billing
cannot be metered** — `MetricStreamPort.replay()` documents its input as *"the authoritative log —
the truth being re-read"*, and that log does not exist. **AR-42** is unmet.

## 5. Why Epic 10 Is Needed

**[VERIFIED]** DL-0017 (Accepted 2026-07-17) closes with: *"Durable outbox + retry/DLQ +
async/non-blocking delivery + WS/SSE gateway + distributed broker are M4.2+ (**implement the same
`EventBus` contract — no producer change**)."* This supplies (1) authorization for S1/S2 as ratified
deferred scope, (2) the binding compatibility constraint, and (3) evidence the deferral was
deliberate. Additionally the Epic 9 release record (line 98) pre-sanctions post-`0033` additive
migrations.

## 6. G1–G5 Gap Verification

**G1 — CONFIRMED.** `platform/events/` = `__init__.py`, `bus.py`, `errors.py`, `webhooks.py`. No
persistence, outbox or DLQ.

**G2 — CONFIRMED, with two refinements.** `platform/observability/` contains exactly two files
(`README.md`, `__init__.py`), the latter stating *"Boundary only at M1 — no behaviour."*
**(a)** The `metric.*` vocabulary **does not exist at all** — the full `EventType` enum contains no
`metric.*` member; nearest are `TOOL_METRICS_UPDATED` and `COST_UPDATED = "cost.updated"`.
**(b)** G2 must be scoped to the runtime plane: a separate, fully-built **frozen Epic 8**
Observability capability exists (`api/foundation/observability.py`, `api/application/observability.py`,
`api/customer/routers/observability.py`, `v0012_observability.py`, `cp_alert_rule`).

**G3 — CONFIRMED, but the Billing half already exists.** `MetricStreamPort(ABC)` is declared in
`billing/foundation/metering_factories.py` — *"The **only** inbound path for `metric.*` signals.
Implemented outside this plane."* — with `MetricSignal`, `NullMetricStream`, `RecordingMetricStream`.
It is unwired at both composition roots (`metric_stream=None`).

**G4 — CONFIRMED.** Every adapter under `platform/providers/` is a local stub (`llm/echo.py`,
`stt/local.py`, `tts/local.py`, `transport/local.py`, `voice_activity/local.py`); `analytics/` and
`storage/` hold only `__init__.py`. *(Epic 11.)*

**G5 — PARTIALLY CONFIRMED.** SDK absent (confirmed). But `platform/gateway/` is populated
(11 modules) and `core/session_control.py` exists, so "runtime read/control surfaces absent"
overstates the gap. *(Epic 12.)*

## 7. Epic 10 Goals

**G-1** every event durably persisted, ordered per session, surviving restart, behind the
**unchanged** contract with **no producer change**. **G-2** bounded retry → DLQ → quarantine, so a
dead consumer never stalls a producer (**AR-31**). **G-3** a `metric.*` vocabulary in `core` and
`metric.turn` emitted from real per-turn taps (**AR-42**). **G-4** a `MetricStreamPort`
implementation **outside** `platform/billing/`, honouring ADR-0023 and ADR-0031, with
`platform/billing/**` byte-for-byte unchanged. **G-5** usage for a closed period independently
re-derivable from the log and shown to agree with the metered result. **G-6** certify, freeze and
release as **v5.0.0** under the Epic 9 sequence, preserving all Epic 9 historical state.

## 8. Explicit Non-Goals

Epic 10 does **not**: modify `platform/billing/**`; change Billing architecture or behaviour; alter
migration head `0033` as historical state; recompute or invalidate the Epic 9 digests; re-run or
reinterpret the 27 controls, `NS-5`, `ST-3` or the nine limitations; close any Epic 9 limitation;
supersede ADR-0023 or ADR-0031; wire Billing into the runtime composition root; introduce a
distributed broker, WS/SSE gateway expansion or external transport; integrate any real vendor
(Epic 11); build an SDK, read API or mid-call control (Epic 12); reopen Epics 5–9; or touch Epic 8's
observability capability.

## 9. Architecture Scope

**[VERIFIED]** Constitution §A14 (lines 263–264) already assigns both homes:

```
│   ├── events/          # Event Bus implementations, outbox, webhook dispatcher
│   ├── observability/   # metric taps, tracing, cost meter, log configuration
```

Epic 10 fills two folders A14 defined and reserved. Packages touched: `platform/events/` (additive),
`platform/observability/` (additive), `platform/core/events.py` (**additive `EventType` members
only**), and the composition root implementing `MetricStreamPort`. Untouched: `platform/billing/**`,
`platform/replay/**`, `platform/gateway/**`, `platform/providers/**`, Epic 5–8 runtime packages.

## 10. Event Durability Concept

An append-only durable log stores the **already-stamped** envelope; the bus assigns the authoritative
monotonic `seq` and the store persists it verbatim. The durable implementation sits **behind**
`core.events.EventBus` with no new abstract method and no producer change. Writes are append-only —
the repository already has this discipline in `api/persistence/audit_store.py`. Re-derivation reads
the log and re-materializes envelopes, read-only and side-effect-free. Retention is a policy over a
closed window, expressed as configuration.

**⚠️ Collision with `platform/replay/`.** **[VERIFIED]** `platform/replay/` already exists (M4.3,
ten modules incl. `engine.py`, `recorder.py`, `metrics.py`), is *entirely in-memory*
(`ReplaySession._events: list[Event]`), and is *visualization-only*. Epic 10's "replay-from-store" is
a different concept (durable source, re-derivation and recovery, survives restart, feeds
`MetricStreamPort.replay()`). The noun must be resolved explicitly — **OD-3**.

## 11. Outbox / Retry / DLQ Concept

**Transactional outbox** — the event is written in the same transaction as the state change that
produced it. **[VERIFIED]** precedent: `api/persistence/audit_store.py` — *"an emitted audit row is
part of the same transaction as the business write… the `SqlUnitOfWork` commits both together, or
rolls both back."* **Retry ladder** — bounded attempts, declared backoff, hard cap, every parameter
configuration. **DLQ** — after the cap, with failure history retained. **Quarantine** — a
deterministic failure (poison message) quarantined *without* consuming the ladder. **Semantics:
at-least-once**, which the frozen consumer already assumes: *"a signal returned twice is not an
error, because ingestion is idempotent on `(event_id, meter_code)`."*

**[VERIFIED]** `platform/events/webhooks.py` already exists (the A14 webhook dispatcher); its
relationship to the new outbox must be checked at M10.1 to avoid two competing retry mechanisms.

## 12. Telemetry Producer Concept

`platform/observability/` is built out from skeleton to a real producer within its declared budget
(*"Imports `core` + stdlib only"*). Per-turn latency taps at every conversation-loop stage boundary
(`AR-42`), feeding `metric.turn` through the ordinary `EventPublisher` path. **One producer per
type** (`AR-2`). Taps must be non-blocking and must never affect turn behaviour (invariant I1).
Prerequisite: the `metric.*` members must first exist in `core.events.EventType`.

## 13. Producer→Billing Seam Concept

**[VERIFIED] Frozen and unchangeable:** `MetricStreamPort.drain(org_id, limit)` and
`replay(org_id, period_start, period_end)`; the port is *"declared here rather than an import of any
producer"*; *"The Customer Platform implements this port at the composition root, exactly as it
implements `BillingAuthorizer`"*; *"There is **no emit, publish, or write method**"*; *"An unwired
port means no ingestion, not invented usage."*

**⚠️ Correction to the framing.** The contract is **pull-shaped, not push**: *"Read-only and
pull-shaped by design: `drain` asks the platform's own buffer for what it has already received, so
Billing never subscribes to a runtime bus, never opens a connection, and never holds a runtime
resource."* The real topology is producer → bus → durable log → platform-owned buffer ← **Billing
pulls**. "Producer→Billing" taken literally as a push design would violate the frozen port — **C-2**.

**Confirmatory finding:** `replay()`'s docstring reads *"Re-read a closed window from **the
authoritative log** — the input to a deterministic replay. **The log, not Billing, is the truth being
re-read.**"* Epic 9 already specified the log Epic 10 proposes to build.

**Cross-boundary status:** the implementation lives outside `platform/billing/`, but wires a frozen
Epic 9 port to a new Epic 10 producer at a frozen Epic 8 composition root — **requires an ADR**.

## 14. Dependency Direction

**[VERIFIED]** `AR-10` (`core/` imports nothing above it), `AR-11`, `AR-14`, DL-0017's DAG
`core ← events ← runtime`, `replay/` and `observability/` at `core` + stdlib, ADR-0031's
`core`-only budget for `billing/`.

Target: `core ← events ← runtime`; `core ← observability`; Billing gains **no** new import; nothing
frozen depends on Epic 10; direction one-way and acyclic.

**⚠️ The genuine tension.** `events/` imports `core` + stdlib only, but durable storage needs
persistence (which lives in `api/persistence/`). Importing `api` would invert the DAG. Resolution is
dependency inversion — a storage **port** in `events/`, concrete adapter bound at a composition root
— the pattern ADR-0031 used and that `MetricStreamPort` itself uses.

## 15. Frozen Boundary

**F1** `platform/billing/**` byte-for-byte · **F2** Billing architecture/behaviour · **F3** migration
head `0033` as historical state · **F4/F5** both Epic 9 digests · **F6** 27 controls · **F7** `NS-5`,
`ST-3` · **F8** nine limitations + owners · **F9/F10** ADR-0023, ADR-0031 · **F11** tag `v4.0.0`,
commit `532b0bd` · **F12** released Epic 9 documentation · **F13** frozen Epics 5–9.

**Additionally identified by this inspection:** **F14** `MetricStreamPort`/`MetricSignal`/
`NullMetricStream` signatures · **F15** Epic 8 observability capability (`api/**/observability*`,
`cp_alert_rule`, `v0012`) · **F16** `platform/replay/**` · **F17** existing `EventType` members
(`AR-32` — additive only).

## 16. Proposed Milestone Breakdown

M10.0 Blueprint · **M10.0a Boundary reconciliation (new)** · M10.1 Implementation plan + ADR
ratification · M10.2 Foundation · M10.3 Application · M10.4 Persistence · M10.5 Durable log +
retention · M10.6 Transactional outbox · M10.7 Retry ladder + DLQ + quarantine · **M10.8 renamed per
OD-3** · **M10.8a `core` `metric.*` vocabulary (new, gates M10.9)** · M10.9 observability +
`metric.turn` · **M10.10 renamed "metric-stream seam" per C-2** · M10.11 shadow validation ·
M10.12 Hardening · M10.13 Certification · M10.14 Freeze · M10.15 Repository release v5.0.0.

*Rationale for M10.2–M10.4 preceding the feature milestones: this foundation → application →
persistence layering is the structure `platform/billing/` itself uses, and it certified successfully.*

> **Execution note (M10.3).** The executed sequence diverged: **M10.2 absorbed the persistence layer**
> (schema, migration `0034`, concrete adapter, durable log) alongside the foundation, and **M10.3 was
> re-scoped to planning/governance closure**. The authoritative allocation from here is
> [`docs/roadmap/epic10-implementation-plan.md`](../../roadmap/epic10-implementation-plan.md).

## 17. Proposed ADR Set

**[VERIFIED]** Numbering: ADR-0031 Accepted; ADR-0025–0030 *"Reserved — recommended, not yet
ratified"*; ADR-0024 reserved for Coach; ADR-0011 also reserved. **ADR-0032 is the correct next fresh
number.**

**ADR-0032** event durability and delivery semantics — sound and necessary. **ADR-0033** metric-stream
seam — sound and mandatory (the cross-boundary decision). **ADR-0034** — **⚠️ mis-scoped as titled**:
§A14 already assigns `outbox`→`events/` and `metric taps`→`observability/`, so no new top-level
directory exists and the CODING_STANDARDS §2 trigger does not fire. The real question is *how a
`core`-only package acquires durable persistence without inverting the DAG* — **OD-2**.

## 18. Data / Migration Strategy

Epic 10 migrations begin at **`0034`**, strictly after the frozen head; no `ALTER` on any existing
table; no edit to `0001`–`0033`. Declarative schema only, idempotent create-if-absent (the `v0012`
shape). A distinct prefix, not `cp_billing_*`.

**⚠️ Two structural facts.** **(a)** The chain spans two directories — `api/persistence/migrations`
(`0001`–`0012`) and `billing/persistence/migrations` (`0013`–`0033`); head `0033` is a *logical* head
across both, each with its own ordered registry — **OD-4**. **(b)** The `core.events.EventType`
extension is the sharpest governance question: `AR-32` and extensive Epic 6 precedent permit additive
members, but the file is shared by five frozen epics — **OD-1**.

## 19. Testing & Hermeticity Strategy

Baseline: the full existing suite must pass unchanged. **Zero modifications to
`tests/test_billing_*.py`** — needing to edit one means the boundary was crossed. Hermetic: no
network, no clock dependence, no credentials; seams injected (`billing/foundation/seams.py`,
`InProcessEventBus(clock=…, id_factory=…)`). **Durability tests must prove survival across a
simulated restart**, not across a function call. Reuse `RecordingMetricStream`, whose `drain` is
deliberately non-destructive. Shadow validation compares metered usage against usage re-derived via
`replay(...)`; divergence is an Epic 10 defect, never a reason to touch Billing.

## 20. Certification Strategy

Epic 10 defines its **own** control set with fresh identifiers; it does not renumber, reuse or extend
Epic 9's 27. Mandatory boundary controls: `billing/**` unchanged; migrations `0001`–`0033` unchanged
and Epic 10 `≥ 0034`; Epic 9 digests reproduced verbatim as historical state; no new import into
`billing/`; `NS-5`/`ST-3` still open with original owners; nine limitations still open; existing
`EventType` members unchanged. Model on `tests/certification/runner.py`. Preserve the
digest/verdict separation. LIM-7 (self-administered certification) is inherited honestly.
`RELEASED_WITH_ADVISORIES` is an acceptable outcome.

## 21. Freeze / Release Strategy

Follow Epic 9's freeze → release split exactly. **[VERIFIED]** the release record §12: at M9.25 the
implementation was frozen but *uncommitted* — risk **D-11** — *"the freeze/release split exists
precisely so that condition is discharged rather than repeated."* **Epic 10 must not repeat D-11**:
M10.14 freezes, M10.15 commits and tags. Annotated tag **`v5.0.0`**. **Do not bump
`platform/pyproject.toml`** — *"The tag is the single release identity, and inventing a second one
here would create the competing version source §5 forbids."*

## 22. Risks

**R-1** `core.events.EventType` extension touches code shared by five frozen epics *(High)* ·
**R-2** seam built push-shaped, violating the frozen pull-shaped port *(High)* · **R-3** D-11
recurrence *(High)* · **R-4** durable-store persistence inverts the DAG *(Medium)* · **R-5**
`replay/` naming collision *(Medium)* · **R-6** migration numbering conflict across two registries
*(Medium)* · **R-7** shadow-validation divergence tempts a Billing "fix" *(Medium)* · **R-8**
baseline test-count ambiguity *(Medium)* · **R-9** latency taps violate I1 *(Medium)* · **R-10**
Epic 11/12 scope creep *(Low)* · **R-11** new outbox competes with `events/webhooks.py` *(Low)* ·
**R-12** suite not runnable in the inspection environment *(Low)*.

## 23. Open Decisions

**Corrections C-1…C-6** and **open decisions OD-1…OD-9** are recorded in full, with evidence and
resolutions, in [`m10.0a-decision-gate.md`](m10.0a-decision-gate.md).

## 24. Definition of Done for M10.0

D1 repository state independently verified ✅ · D2 every frozen-boundary item located ✅ · D3 G1–G5
verified against code ✅ · D4 scope tested for conflict ✅ (six corrections) · D5 all 25 sections ✅ ·
D6 verified/proposed/open distinguished ✅ · D7 cross-boundary Billing decisions flagged as requiring
an ADR ✅ · D8 no ADR created; nothing claimed ratified ✅ · D9 no implementation claimed without code
verification ✅ · D10 no repository file created or modified ✅ · **D11 Owner ratification of
C-1…C-6 and OD-1…OD-7 — discharged at M10.0a.**

## 25. Final Recommendation

**Proceed with Epic 10 as scoped, with the six corrections applied.** The scope is the completion of
explicitly deferred, ratified work: DL-0017 pre-authorizes S1/S2 by name and supplies the binding
constraint; Epic 9 already built and froze the consumer half of S4 and already called the durable log
*"the authoritative log… the truth being re-read"*; §A14 already reserves both folders; the additive
migration path is pre-sanctioned; and `AR-31`/`AR-42` are real constitutional debt.

The one correction that materially changes the plan is **C-4**: `metric.*` does not exist, so S3
begins in frozen shared `core`, not in `observability/`.

**Sequencing: Epic 10 before Epic 11 is correct.** Real providers will generate the traffic that makes
durability and metering matter; building the spine first avoids a retrofit. Epic 12's SDK and read API
are natural consumers of the durable log Epic 10 creates.

---

## Verdict

# **M10.0_BLUEPRINT_READY**

Conditional on M10.0a: complete and repository-supported, with six corrections and seven open
decisions requiring Owner resolution before M10.1. **Not blocked** — no repository fact prevents
Epic 10 from proceeding.
