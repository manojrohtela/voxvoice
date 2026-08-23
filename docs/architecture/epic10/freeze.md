# M10.14 — Epic 10 Architecture Freeze

**Status:** frozen · **Milestone:** M10.14 · **Decision log:** DL-0146
**Amends:** nothing. ADR-0023, ADR-0031 and ADR-0032–ADR-0043 are unchanged.
**Freeze manifest digest:** `d3d4858d5f18b7f6…` · **Certification digest:** `7e57bc14764201b1…`
**Verdict:** `FROZEN`

> **This is freeze evidence, not a release document.** It states what was true at the M10.14 freeze
> boundary. It makes **no `v5.0.0` claim**, contains **no release notes**, and does **not** imply
> that M10.15 has occurred — M10.15 owns the commit, the annotated tag and the release
> documentation (owner ruling **R-6**, DL-0146).

---

## Mandate

`docs/roadmap/epic10-implementation-plan.md` §M10.14 specifies this milestone exactly:

> Freeze manifest enumerating every Epic 10 module, table, migration and test, plus explicit
> attestation that the frozen boundary is unchanged. Fresh digest; the Epic 9 manifest digest is
> quoted as history, never recomputed. **Freeze does not commit** — that is M10.15, and the split
> exists so risk **D-11** (*"implementation exists but is uncommitted at freeze"*) is discharged
> rather than repeated.

Owner rulings **R-1 … R-7** resolved its seven ambiguities and are recorded in **DL-0146**. No ADR
was required: M10.14 introduces no production behaviour, API surface, database change, migration,
event type, provider integration, performance requirement, certification control, operational
control or security/privacy change, so DOCUMENTATION_RULES §3.1's architectural trigger does not
fire (**R-3**, following the M9.25 / DL-0136 precedent).

## The freeze principle

> Freeze the certified architecture and implementation state. **Do not improve the architecture
> during the freeze.**

M10.14 introduces **no production module, no table, no migration, no route, no port method and no
runtime behaviour**. It reads the tree M10.13 certified and writes down what is there,
deterministically. It closes **none** of Epic 9's nine limitations and reclassifies none of its
advisories — closing another epic's debt under cover of governance work is exactly the ownership
theft a freeze must not perform.

## The frozen boundary

| | |
|---|---|
| **Baseline** | `v4.0.0` → `532b0bd72d55921fa385a3e4162d899c90fbe6ad` |
| **Certified commit** | `db8c50828c91567aa9a7c518e58f804593a592f9` (M10.13, `feat(certification): add Epic 10 certification`) |
| **Milestone range** | M10.2 … M10.13 |
| **Migration head** | `0036` |

The boundary is **pinned as a literal**, never read from `HEAD`. A boundary derived from the thing
under test cannot detect that thing moving, and would silently absorb M10.15's commit into the
frozen inventory.

## Inventory — every Epic 10 module, table, migration and test

**44 platform paths** differ between `v4.0.0` and the certified commit: 32 added, 12 modified.

### Modules added (18)

`api/metering/__init__.py` · `api/metering/metric_buffer.py` · `events/ladder.py` ·
`events/ports.py` · `events/recovery.py` · `events/retention.py` ·
`events/persistence/__init__.py` · `events/persistence/database.py` ·
`events/persistence/durable_log.py` · `events/persistence/mapping.py` ·
`events/persistence/models.py` · `events/persistence/retention.py` ·
`events/persistence/spine.py` · `events/persistence/migrations/__init__.py` ·
`events/persistence/migrations/v0034_durable_event_log.py` ·
`events/persistence/migrations/v0035_durable_outbox.py` ·
`events/persistence/migrations/v0036_outbox_retry_ladder.py` · `observability/turn_latency.py`

### Modules modified (7)

`billing/foundation/catalog.py` *(the single authorized MB14 delta)* · `core/events.py` ·
`events/__init__.py` · `events/bus.py` · `observability/__init__.py` · `runtime/contracts.py` ·
`runtime/pipeline/pipeline.py`

### Tables (3) and indexes (5)

`evt_event` · `evt_outbox` · `evt_outbox_failure`
`ix_evt_event_seq_id` · `ix_evt_event_session_seq` · `ix_evt_outbox_failure_event_attempt` ·
`ix_evt_outbox_state_next_attempt` · `ix_evt_outbox_state_seq`

Every table and index is `evt_`-prefixed (ADR-0034 DS9).

### Migrations (3)

`v0034_durable_event_log` · `v0035_durable_outbox` · `v0036_outbox_retry_ladder`.
Migrations `0001`–`0033` are unchanged; the chain head is `0036`.

### Tests added (14), modified (5)

Added: `certification/epic10.py` · `test_api_metric_buffer.py` · `test_core_metric_vocabulary.py` ·
`test_epic10_certification.py` · `test_epic10_failure_drills.py` ·
`test_epic10_shadow_validation.py` · `test_events_boundary.py` · `test_events_durable_seam.py` ·
`test_events_outbox.py` · `test_events_persistence.py` · `test_events_recovery.py` ·
`test_events_retention.py` · `test_events_retry_ladder.py` · `test_observability_turn_latency.py`

Modified: `certification/runner.py` · `test_billing_application.py` ·
`test_billing_foundation.py` · `test_billing_freeze.py` · `test_billing_release.py`

**M10.14's own artifacts are deliberately absent from this inventory.** They are uncommitted at the
freeze boundary, and the commit-state block below — not the inventory — is where they are accounted
for.

## Frozen-boundary attestation — `EF-1` … `EF-12`

All twelve **`SATISFIED`**. `INCONCLUSIVE` is not a passing outcome, and cannot produce `FROZEN`.

| # | Statement | Outcome |
|---|---|---|
| **EF-1** | Every frozen production plane is unchanged since the certified commit | `satisfied` |
| **EF-2** | The migration head is `0036` and no M10.14 migration exists | `satisfied` |
| **EF-3** | Migrations `0001`–`0033` remain unchanged since `v4.0.0` | `satisfied` |
| **EF-4** | The billing boundary carries exactly the one authorized MB14 delta | `satisfied` |
| **EF-5** | Epic 9's freeze implementation and artifacts are unchanged | `satisfied` |
| **EF-6** | The M10.13 certification surface is unchanged and consumed only as evidence | `satisfied` |
| **EF-7** | The M10.13 verdict is `certified`, with nothing blocking, advisory or inconclusive | `satisfied` |
| **EF-8** | Epic 9's original digests reproduce from `v4.0.0` and are recorded separately from the current values | `satisfied` |
| **EF-9** | Epic 9's nine limitations and advisories `NS-5`/`ST-3` keep their owners | `satisfied` |
| **EF-10** | The freeze introduces no production module, table, migration or route | `satisfied` |
| **EF-11** | The freeze creates no release document, release notes or `v5.0.0` artifact | `satisfied` |
| **EF-12** | The `v4.0.0` baseline still names its release commit | `satisfied` |

## Certification consumed, not modified

| | |
|---|---|
| Catalogue version | `m10.13.1` |
| Verdict | `certified` |
| Digest | `7e57bc14764201b123c49b9321f15d9c41f0ee7015e6879c64c0307e56db3d0d` |
| Controls | 13 (`E10-1` … `E10-13`), all `satisfied` |
| Blocking violations · advisories · inconclusive | 0 · 0 · 0 |

The certification digest is **copied**, never recomputed. It stays verdict-free per ADR-0043
**CD8**; the freeze manifest digest, by contrast, covers the whole manifest **including** its
verdict, because a freeze whose digest ignored its own verdict could be altered in the one field
that matters without moving.

## Epic 9's two digest states (R-1)

| State | Source | Manifest digest | Certification digest |
|---|---|---|---|
| **Historical** | `v4.0.0:docs/releases/epic9-billing-platform-v1.0.md` | `eed692224f0aeb82…` | `39790b95d061a763…` |
| **Current** | working tree, same file | `cdafb4ab6cba00bc…` | `c044b3f6a23066df…` |

The historical pair is **quoted from `v4.0.0` and never recomputed**. The current pair is the
authorized post-M10.10 re-baseline and is recorded under a **separate key**, from a **separate
source**. Neither is ever presented as the other, and `EF-8` fails if both are read from one source.
No current Epic 9 artifact was reverted to restore the historical values.

## Declared freeze inputs

Facts about *running* and about VCS posture — not derivable by reading the tree, and recorded as
declared rather than pretending otherwise.

| Input | Value |
|---|---|
| Repository-wide tests | **5345** |
| Failed · skipped | **0** · **0** |
| Ruff | **clean**, for exactly `ruff check . --exclude voicebot` and `ruff check billing/ tests/` |

**No repository-wide ruff claim is made.** `ruff check .` is *not* clean: `voicebot/bot.py` carries
two pre-existing findings (I001, F401). That file is outside M10.14's scope and is not modified
(**R-7**).

## Commit state at the freeze boundary (R-2)

| | |
|---|---|
| Certified implementation | **committed**, at `db8c50828c91567aa9a7c518e58f804593a592f9` |
| M10.14 freeze artifacts | **uncommitted**, intentionally |
| Committed by | **M10.15** |

Two facts, not one. Epic 9's single classification could not express this, because Epic 10 committed
each milestone as it landed: risk **D-11** — *"implementation exists but is uncommitted at freeze"* —
is therefore **already discharged** for the implementation. That discharge is not a licence to pull
M10.15's commit into M10.14; the freeze → release split is the roadmap's instruction regardless of
why it exists.

**This is historical evidence, not a present-tense claim.** Once M10.15 commits the freeze
artifacts, the statement above remains what was true at the freeze boundary — exactly as Epic 9's
manifest still declares `commit_state="uncommitted"` today.

## Independence from Epic 9 (R-5)

The Epic 10 freeze lives at `platform/tests/freeze/epic10.py`, **beside** Epic 9's `manifest.py`.
Adjacency is filesystem placement only:

* its own namespace (`EF-`), version (`m10.14.1`), verdict vocabulary, canonicalizer and digest;
* `tests.freeze.manifest` is **never imported**, and none of `build_manifest`, `manifest_digest`,
  `derived_inventory` or `canonical_digest` is called;
* the two `__all__` lists share **no name** — during implementation this guard caught a real
  collision, `CERTIFIED_MIGRATION_HEAD`, which Epic 10 now spells `EPIC10_MIGRATION_HEAD`;
* Epic 9 data enters in one direction only, as **values** read for evidence.

## What this freeze does not do

* It does not commit, tag, or release. **M10.15 owns all three.**
* It does not create release notes or any `v5.0.0` artifact.
* It closes no Epic 9 limitation and reclassifies no Epic 9 advisory.
* It reopens neither **DL-0139** nor **DL-0141**; both remain `Resolved`.
* It modifies no Accepted ADR and creates no ADR-0044.
* It changes no production code, migration, billing file, or M10.13 certification artifact.
* It asserts nothing about production latency, real providers, Epic 11 or Epic 12.

## Known limitations carried into the freeze

Epic 10's own four declared limitations are carried forward unchanged from M10.13:

* **E10-LIM-1** — certification is self-administered, inherited honestly from Epic 9's `LIM-7`,
  which keeps its own statement and its own owner (`M9.24`).
* **E10-LIM-2** — the controls are structural, not behavioural.
* **E10-LIM-3** — every "unchanged since" control depends on the `v4.0.0` tag remaining reachable.
* **E10-LIM-4** — the three structural Epic 9 release-suite assertions recorded in **DL-0139** are
  not resolved here; they require their own Owner-authorized governance decision.

Epic 9's nine limitations (`LIM-1` … `LIM-9`) and its advisories `NS-5` and `ST-3` remain open with
their existing owners. Epic 10 neither closes nor re-owns them.
