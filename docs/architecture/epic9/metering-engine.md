# Metering Engine (Epic 9 · M9.11)

**Status:** 🟢 Implemented · **Date:** 2026-08-06 · **Constitution:** v2 (§A2.4, §A2.5, §A2.6, §A5, §A11.2, §A12)
**ADRs:** ADR-0023 (M1–M8, RS1, RS2, RS4, §6), ADR-0031 (D3, IR1, IR3, CR2), ADR-0021
**Depends on:** M9.2 Foundation · M9.3 Application · M9.4 Persistence · M9.5 API · M9.6–M9.10

---

## 1. What this component is

The Metering Engine owns **commercial usage records** and nothing else. It is the highest-risk
correctness milestone of Epic 9, because it is the only domain whose errors are *silent*: a lost
event under-bills and nobody complains; a duplicated event over-bills and the customer leaves.

Everything below is shaped so that **the revenue ledger is reproducible from the event log**
(ADR-0023 RS2) — the only property that makes an accounting error findable at all.

### The load-bearing invariant

```
Billing never measures.
Billing consumes authoritative metric.* events.
Exactly one authoritative usage record exists for every source event.
Usage records are append-only.
Billing never modifies operational metrics.
Billing never blocks Runtime.
```

| Line | How it is enforced |
|---|---|
| Never measures | No clock read, socket, sampler, or timer anywhere in the domain (AST-asserted, 8 modules) |
| Consumes `metric.*` | `MetricStreamPort` — an ABC declared *inside* this plane, implemented outside it |
| Exactly one record per event | Derived primary key + read-before-write + DB unique constraint + `IntegrityError` re-read inside a `SAVEPOINT` |
| Append-only | No update and no delete method exists on the usage or activity contracts |
| Never a metric producer | The port has **no** emit/publish/write method — there is nothing to call |
| Never blocks Runtime | No outbound call of any kind (AST-asserted); an unwired port ingests nothing |

---

## 2. Components implemented

| Layer | Module | Contents |
|---|---|---|
| Foundation | `commercial_metering.py` | Meter/period/quarantine FSMs, `SourceEvent`, `CommercialUsage`, provenance, windows, aggregates, snapshots, corrections, reconciliation, replay, search, export |
| Foundation | `metering_rules.py` | `UsageNormalizer`, `LateArrivalPolicy`, `AttributionService`, `UsageIngestionService`, `MeteringValidator`, three lifecycle services, `UsageAggregator`, `UsageSealingService`, `UsageReplayService`, `UsageReconciliationService`, `UsageInspector`, `UsageSearchService`, `UsageExportService`, `UsageCorrectionService` |
| Foundation | `metering_factories.py` | `MeteringFactory`, **`MetricStreamPort`** + `NullMetricStream`/`RecordingMetricStream`, 7 repository ABCs |
| Application | `metering_engine.py` | 17 commands / 12 queries, use cases, view DTOs |
| Persistence | `metering_models.py`, `metering_repositories.py`, `migrations/v0021_billing_metering.py` | 7 tables, 7 SQL repositories, migration **0021** |
| API | `metering_schemas.py`, `routers/metering_engine.py` | 26 paths at `/usage` |
| Studio | `studio/metering.html` | 11 screens |
| Tests | `tests/test_billing_metering_engine.py` | 184 tests |

**Additive throughout.** M9.2's `metering.py` is unreshaped; `cp_billing_usage_record`,
`cp_billing_usage_aggregate`, and `cp_billing_usage_quarantine` are untouched; M9.5's `/metering`
routes still answer. Foundation exports 429 → 510; application 320 → 380; 18 new events and 20 new
diagnostic codes.

---

## 3. Architecture

### 3.1 The metric-stream seam

`MetricStreamPort` is the **only** inbound path for a `metric.*` signal, and it is an ABC declared
in the Billing Foundation rather than an import of any producer. Three consequences are structural
rather than conventional:

* **Billing never polls Runtime and never reaches into runtime state.** `drain` reads the
  *platform's own decoded buffer*; nothing in this plane opens a connection or holds a runtime
  resource.
* **Billing never becomes a metric producer.** The port has no `emit`, `publish`, or `write`
  method. A test asserts its public surface is exactly `{drain, replay}`.
* **An unwired port ingests nothing.** `NullMetricStream` is the default, so a build that forgets
  to wire the platform's implementation produces no usage rather than inventing it.

`drain` is contractually **safe to call repeatedly and from several instances** — redelivery is not
an error, because ingestion is idempotent. That contract is what makes the pipeline
at-least-once-safe instead of requiring an exactly-once stream that does not exist.

### 3.2 Two unusual, deliberate decisions

**A usage record's id is *derived*, not minted.** `derive_usage_id` hashes
`(org_id, event_id, meter_code)`. Every other Billing aggregate takes its id from the `IdGenerator`
seam; usage does not, because replay must produce **byte-identical** usage — ids included — or
"reprocessing a period yields identical usage records" is a claim nobody can check. It also means
two processes racing on one event compute the *same* primary key, so the database arbitrates.

**A sealed period carries a digest of what it sealed.** `seal_digest` is a deterministic hash over
the period's billable content, so a replay months later can **prove** it reproduced the same
commercial usage. Getting the digest's boundary right is the whole point: it covers ids,
quantities, periods, and statuses, and deliberately excludes ingestion timestamps and run ids,
which legitimately differ between an original ingestion and a replay of it.

### 3.3 M9.2's vocabulary is reused

`UsageStatus`, `MeterAggregation`, `MeterSource`, `Rounding`, `Quantity`, `Scope`, `BillingPeriod`,
and `fold` are imported, not re-declared. Two answers to "how does this aggregation reduce?" is
exactly the drift §A2.5 exists to prevent.

---

## 4. Domain model

**Meters.** A `CommercialMeter` is versioned and has **exactly one authoritative source**. If two
planes emit an overlapping signal, one is designated and the other is quarantined as
`NON_AUTHORITATIVE_SOURCE` rather than silently double-counted — that single field prevents the
highest-severity metering failure there is. `pin` (`code@version`) is what every usage record's
provenance points at, which is what keeps an old invoice explainable after the meter moves on.

**Normalization is exact integer arithmetic.** `raw × numerator / denominator` with a declared
rounding applied to an exact remainder. No float appears in the domain. `HALF_EVEN` is available
and matters at volume: rounding halves consistently upward biases every meter in VoxVoice's
favour.

**Ingestion is a pure function.** `UsageIngestionService.decide(event, meter, period,
already_recorded, …)` performs no I/O, so the same inputs always yield the same decision. Its order
is deliberate — malformed → unknown meter → non-authoritative → retired → unit → **attribution** →
**exactly-once gate** → landing. The gate sits *after* attribution so a duplicate of an
unattributable event is reported as unattributable rather than swallowed as a duplicate.

**Attribution is total.** Every event resolves to exactly one billing account or is quarantined
(M6). Resolution is narrowest-first — agent → project → workspace → organization — because a
customer who attached billing at the project level meant it.

**A duplicate is a success.** It commits, emits `billing.usage.duplicate_ignored`, and appears in
the timeline. At-least-once delivery makes replays normal; treating one as an error would make
every retry look like an incident, and the recorded fact is the *evidence* idempotency worked.

**Corrections never edit.** A correction produces a **new** record with `ADJUSTMENT` status
pointing at the original, so the original figure, the change, and the reason stay separately
readable — what an auditor asks for and what one overwritten number can never provide.

---

## 5. Lifecycles

**Meter** `draft → active → deprecated ⇄ active → retired`. No deletion state (a meter names what
an invoice billed). No `retired → active` (the code may have been reused; resurrecting it makes
`code@version` ambiguous). **Deprecated still counts** — a deprecated meter stops being *offered*,
not being *sent*; only retired refuses, and its events are quarantined rather than dropped.

**Usage period** `open → closing → sealed`, plus a direct `open → seal` for an empty period.
**Sealing is a one-way door**: there is no edge out of `sealed`, to anything, and a test enumerates
the whole machine. A period cannot be sealed without a digest.

**Quarantine** `pending → resolved | discarded`. Both exits terminal, both recorded, neither a
deletion — "what happened to that event?" always has an answer.

---

## 6. Persistence

Seven additive tables via **migration 0021**; three append-only. The load-bearing object is
`uq_cp_billing_commercial_usage_source` on `(org_id, source_event_id, meter_code,
corrects_usage_id)`.

`SqlCommercialUsageRepository.append` defends exactly-once three deep: a derived primary key, a
read-before-write for the common redelivery, and — for the genuine race — an insert inside a
**`SAVEPOINT`** whose `IntegrityError` is caught and turned into a read of the winner. The
savepoint is the part usually missing: without it a caught integrity error poisons the session and
the surrounding transaction dies with it, taking the activity entry and staged event too.

---

## 7. API

**26 paths at `/usage`**, not under M9.5's `/metering` — the same wildcard-shadowing correctness
argument M9.8/M9.9/M9.10 made. Ingestion returns **201** for a new record, **200** for a replay,
**202** for a quarantine; all three are successes, and the caller can tell them apart.

**There is no DELETE anywhere in the surface**, and no PUT/PATCH on a usage record. Asserted.

---

## 8. Studio

Eleven screens in one self-contained file: Meters, Ingest, Usage Records, Snapshot, Periods,
Quarantine, Replay, Reconciliation, Diagnostics, Export, Timeline. It normalizes nothing,
aggregates nothing, and computes no digest; Replay displays the server's verdict rather than
reaching one. No `localStorage`, no money formatter.

---

## 9. Testing — 184 tests

Normalization · identity · the ingestion decision · late arrival · lifecycles · validation ·
aggregation · sealing and digests · replay · reconciliation · corrections, inspection, search,
export · factory discipline and the stream seam · **the correctness demonstrations (§10)** · the
Application over a real database · persistence · HTTP · Studio · architecture compliance.

### 9.1 Defects found and fixed

1. **Usage could land in a sealed period.** The Application looked the period up by the event's
   *scope* id rather than the resolved billing account, so no period row was found and
   `period_state` silently defaulted to `OPEN` — exactly the retroactive write ADR-0023 M7 exists
   to prevent. Fixed by resolving the account first, with the ingestion service's own
   `AttributionService` so the two answers cannot disagree.
2. **An adjustment landed back in the period it was adjusting**, breaking that period's seal
   digest. Fixed in the Foundation: `landing_period` never returns the original for an adjusting
   disposition, and `requires_open_period` declares the rule. With no open period to receive it,
   the Application quarantines — visible and re-ingestible, never written into a frozen period and
   never dropped.
3. **Two overlapping meters could both go live.** The cross-meter conflict check compared only
   *ingestible* meters, so a second meter claiming a taken signal was accepted while a draft and
   nothing re-checked at activation. Fixed: any non-retired meter is a claimant, and activation
   re-runs the check.
4. **A correction was silently swallowed by the exactly-once constraint.** An append-only
   correction derives from the same source event as its original and so collided with it. Fixed by
   making `corrects_usage_id` part of the unique key — the constraint now states the invariant
   precisely: at most one *original* per source event, plus at most one correction per target.
5. **`PeriodBody` collided with M9.5's** identically-named schema; renamed `UsagePeriodBody`.
   Caught by a frozen test, which is what frozen tests are for.

### 9.2 Assertions sharpened (none weakened)

* **Controller transport-only detection** now expands a route body with the bodies of the private
  helpers it calls and requires the dispatch *somewhere in that expansion*. Stricter than exempting
  helpers by name, and it does not force duplicated serialization into every route.
* **`test_ir1_billing_imports_core_and_stdlib_only`** admits `hashlib` for the Foundation, with the
  reason recorded inline: derived ids and seal digests are the mechanism that makes replay
  verifiable, and both must be computed in the domain layer.
* **The entitlement migration-chain test** moved from a tail assertion to position+ordering, so a
  later migration cannot break it for no reason.
* **`test_repositories_decide_nothing`** forbids the decision *call* (`.disposition(`,
  `.normalize(`, `.seal_digest(`) rather than a column that happens to share a name.

---

## 10. The correctness demonstrations

Each is an explicit test, not an implication.

| Guarantee | Demonstration |
|---|---|
| One authoritative record per source event | One signal → `record_count == 1` |
| Duplicates never create duplicate usage | Five drains of one signal → 1 record, 4 duplicates |
| Idempotency under concurrent ingestion | Two Applications over one database both drain; `recorded == 1`, `duplicates == 1` |
| A racing append returns the stored record | `append` twice → same id, one row, no exception |
| Deterministic replay produces identical usage | Seal, then replay → 0 produced, `matches_seal is True` |
| Replay after restart | Second Application, **different id generator and clock** → same seal digest, still matches |
| Late events remain deterministic | Repeated ingestion of one late event → same id, same landing period |
| Usage sealing rules | Post-seal event → quarantined; digest and state unchanged |
| A sealed period is never reopened | `begin_close` after seal → `InvalidBillingTransition` |
| Late event → adjustment in an open period | With a later period open → `adjusted == 1`, landing in that period |
| Append-only commercial history | No update/delete on either contract; timeline records the write |
| Duplicates are recorded as facts | `metering.usage_duplicate_ignored` appears in the timeline |
| Multi-instance correctness | Aggregate rebuilt on instance B equals instance A's, digest included |
| No dependency on Runtime availability | No stream wired → reads answer, zero usage invented |

---

## 11. Documentation updated

`docs/INDEX.md` · `CHANGELOG.md` · `.voxvoice/DECISION_LOG.md` (DL-0121) · this spec.

---

## 12. Architecture compliance

| Check | Result |
|---|---|
| Billing never measures | ✅ AST-asserted |
| Billing never becomes a metric producer | ✅ port surface asserted |
| Billing never polls Runtime / reaches runtime state | ✅ no outbound call, AST-asserted |
| Billing never blocks Runtime or Deployment | ✅ absent from `bootstrap.py`; unwired port is inert |
| No import of any frozen Epic | ✅ import-graph assertion |
| Exactly one usage record per source event | ✅ 4 defences + 4 demonstrations |
| Usage append-only | ✅ no update/delete method exists |
| Sealing one-way | ✅ FSM enumerated |
| Deterministic replay | ✅ digest comparison, incl. across restart |
| No money, no ledger, no invoice, no entitlement | ✅ AST-asserted |
| Foundation pure and clock-free | ✅ asserted |
| One event per fact; none for a rejected command | ✅ both directions |
| Deny-by-default across all 29 messages | ✅ |
| Migration chain extended, alters nothing | ✅ M9.2 usage tables untouched |
| M9.6–M9.10 boundaries still hold | ✅ re-asserted |
| Repository-wide suite green | ✅ 3011/3011 |

---

## 13. Limitations (recorded, not hidden)

1. **Attribution needs exactly one active account per organization.** M9.8 models no
   scope→account attachment, so an organization with two active accounts resolves to *nothing* and
   its events quarantine as `UNATTRIBUTABLE`. That is the correct behaviour under M6 — never
   silently billed, never silently dropped — but it means multi-account organizations cannot be
   metered until an explicit scope→account binding exists. **This is the highest-value follow-up
   in the epic** and needs a milestone with a mandate to touch M9.8.
2. **M9.5's `RecordUsage` write surface still coexists** with the engine and writes M9.2's
   minimal records. Same shape as the M9.10 entitlement finding; re-pointing it changes a
   published surface.
3. **No scheduler drains the stream.** `DrainMetricStream` is a command a caller dispatches. The
   surface that would run it continuously is not owned by any milestone yet.
4. **`RecordingMetricStream` is the only shipped port implementation.** The Customer Platform's
   real implementation is its own work; nothing here can be pointed at a live bus today.
5. **Aggregates are rebuilt on demand, not maintained incrementally.** `UsageAggregator` is
   incremental-*safe* (order-independent, digest-stable), but the Application recomputes a period
   on `RebuildUsageAggregates`. At the volumes the blueprint anticipates this becomes a
   performance concern and wants an incremental path — the design admits one without a reshape.
6. **`payload_digest` is carried but never verified.** Provenance records it so a future
   reconciliation can prove the event a record derived from is the event the log still holds;
   nothing checks it yet.

---

## 14. Recommendation

Proceed to **M9.12 (Rating Engine)** — usage × price book × subscription — which is the first
milestone that can turn what this one records into money.

Before or alongside it, resolve limitation 1: attribution that cannot address a multi-account
organization is a real ceiling on who can be billed, and it is the only limitation here that
blocks revenue rather than deferring polish.
