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

Milestone **numbering and terminology are preserved** (M10.0a, M10.8a included); only the content
allocation is corrected here.

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
* **Boundary enforcement** — 11 static AST assertions in both directions (permitted inside
  `persistence`, forbidden one directory up), plus dependency-direction and `observability`-budget
  checks. No existing global boundary test was weakened.
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

### M10.4 — ⚠️ approved scope already delivered; **allocation open**

The approved M10.0/M10.0a content for M10.4 was **"Persistence: schema, migration `0034`+, concrete
durable storage adapter"** — and all three landed at **M10.2** (§2). The source material defines no
further M10.4 content.

**This plan does not invent replacement scope.** M10.4 therefore has **no remaining approved
content**, and closing it as *absorbed by M10.2* or re-scoping it requires an **Owner decision**.
Until that decision, M10.4 is not a work milestone and nothing depends on it: M10.5 takes its inputs
from M10.2 directly.

### M10.5 — Retention

Retention over the durable log, per ADR-0032 **ED9** and ADR-0034 **DS12/DS13**: explicit
configuration with **no implicit deletion default**; applied **only as a policy over a closed
window**; **never deletes an un-drained event and never deletes an un-metered event**. Clock
injected. This is the **only** exception to the append-only invariant, and it arrives with the
guarantees that make it safe — M10.2 deliberately shipped no removal path at all.

### M10.6 — Transactional outbox / durable write-side integration

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
