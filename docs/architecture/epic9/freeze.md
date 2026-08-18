# M9.25 — Epic 9 Architecture Freeze

**Status:** frozen · **Milestone:** M9.25 · **Decision log:** DL-0136
**Amends:** nothing. ADR-0023, ADR-0021 and ADR-0031 are unchanged.
**Manifest digest:** `eed692224f0aeb82…` · **Certification digest:** `39790b95d061a763…`
**Verdict:** `FREEZE_WITH_ADVISORIES`

---

## Mandate

`docs/roadmap/epic9-implementation-plan.md` §10 (P6) specifies M9.25 exactly:

> **M9.25 Architecture Freeze** — Billing Platform v1.0 Freeze Manifest: frozen interfaces,
> ownership, dependency direction, extension policy. Output: *a freeze manifest*. Complexity **S**.
> DoD: *architecture frozen; extension policy stated; **repository/commit state recorded honestly***.

And §16 places `docs/releases/epic9-billing-platform-v1.0.md` at M9.25.

**M9.26 remains the Repository Release milestone** — *"Commit, tag, baseline v4.0.0 recommendation,
release notes."* The two are deliberately separate; collapsing them would repeat the M8.19
governance condition the split exists to avoid (roadmap risk **D-11**).

## The freeze principle

> Freeze the certified architecture and implementation state. **Do not improve the architecture
> during the freeze.**

M9.25 introduces **no financial fact, no table, no migration, no route, no Studio page, and no
production module**. It reads the tree M9.24 certified and writes down what is there, deterministically.

---

## What M9.25 changed

| Category | Files |
|---|---|
| A · Freeze manifest | `tests/freeze/manifest.py`, `tests/freeze/__init__.py` |
| C · Freeze tests | `tests/test_billing_freeze.py` (29 tests) |
| B · Freeze documentation | this file, `docs/releases/epic9-billing-platform-v1.0.md`, `INDEX.md`, `CHANGELOG.md`, `DECISION_LOG.md` |
| Defect fix (owned by M9.25) | `tests/certification/runner.py` `_ns_2`, and its hunt test — see **Defects** |

## What M9.25 deliberately did not change

Every `cp_billing_*` table (145), every migration (0013–0033), every router, every Studio page,
every schema, every domain module, every ADR, the certification **catalogue** (27 controls, their
severities and their clauses), and all nine declared limitations with their owners.

---

## Freeze inventory

Derived from the repository, not asserted from prose. Every number below is produced by
`tests/freeze/manifest.py::derived_inventory` and asserted by the freeze suite.

| Area | Value |
|---|---|
| **Milestones** | M9.2 – M9.24, certified state `certified_with_advisories` |
| **Foundation modules** | 78 |
| **Application modules** | 30 (15 engines) |
| **Persistence modules** | 45 |
| **Customer modules** | 28 |
| **Studio pages** | 18 |
| **Migrations** | 21, `0013`–`0033`, ordered ✓ contiguous ✓ additive ✓ |
| **Tables** | 145, all `cp_billing_*`-scoped |
| **Indexes** | 1193, names unique ✓ |
| **API routes** | 521 across 30 prefixes, 0 duplicates |
| **HTTP methods** | DELETE, GET, PATCH, POST, PUT |
| **Billing domain events** | 252, all `billing.*`-namespaced ✓ |
| **Application events** | 29 |
| **Lifecycles** | 29, all with terminal states ✓ |
| **Foundation exports** | 1381 |
| **Application exports** | 1028 |
| **OpenAPI schemas** | 646 |

The HTTP surface includes `DELETE`, `PATCH` and `PUT` — from the M9.5–M9.11 customer surfaces,
which predate the append-only engines. It is recorded as it is, not as later milestones would have
written it.

---

## The freeze manifest

Content-addressed, deterministic, and split into two halves because only one of them can be derived.

**Derived** — module counts, schema counts, route counts, event and lifecycle vocabularies,
namespace sizes, and the full certification result. Recomputed on every run from the tree.

**Declared release inputs** — the repository-wide regression result and the ruff result. These are
facts about *running* the suite, not about reading the tree. A manifest that claimed to derive them
would be overstating what it can prove.

Both halves enter the digest, which reuses **M9.21's canonicalizer** — the single one in this
repository, which certification control `DT-3` asserts is unique. The manifest declares no second
canonicalizer and imports no `hashlib`.

Nothing volatile enters: no timestamp, no UUID, no process id, no absolute path, no host, no
environment value. Proven by generating twice from unchanged state and asserting byte-identical
output and identical digest, and by asserting a changed input produces a different digest.

---

## Freeze invariants

| Id | Invariant | Result | Evidence |
|---|---|---|---|
| FZ-1 | No new financial fact | ✅ | 145 tables, all `cp_billing_*`; no freeze module in `billing/` |
| FZ-2 | Migration head remains 0033 | ✅ | 21 migrations, none after 0033, on disk and in the chain |
| FZ-3 | No schema mutation required | ✅ | chain ordered, contiguous; 1193 unique index names |
| FZ-4 | No API surface introduced | ✅ | 521 routes, 30 prefixes, 0 duplicates |
| FZ-5 | No Studio surface introduced | ✅ | 18 pages |
| FZ-6 | No Runtime / cross-plane dependency | ✅ | `PL-1`, `PL-2` satisfied |
| FZ-7 | No cross-domain ownership change | ✅ | `OWN-1..4`, `RS-1` satisfied; no limitation owned by M9.25 |
| FZ-8 | Certification valid against the frozen tree | ✅ | verdict, digest, catalogue and all 24 blocking controls unchanged |
| FZ-9 | Nine limitations remain owned | ✅ | all nine have owner, milestone and statement |
| FZ-10 | NS-5 and ST-3 remain advisories | ✅ | both still `VIOLATED`, both still advisory |
| FZ-11 | Freeze evidence is deterministic | ✅ | byte-identical across runs; nothing volatile |
| FZ-12 | Reconstructable from manifest + declared inputs | ⚠️ **not yet** | see below |

### FZ-12 is not satisfied today, and that is recorded rather than claimed

Epic 9 is **entirely uncommitted**. `HEAD` is `a376e27 release(epic8): Customer Platform v3.0.0`,
and the whole of `platform/billing/`, its tests, its ADRs and its architecture documents are
untracked working-tree files.

There is therefore **no commit from which this tree can be reconstructed**. The manifest records
`commit_state: uncommitted` rather than implying a reproducibility the repository cannot provide.

This is roadmap risk **D-11** — *"implementation exists but is uncommitted at freeze"* — anticipated
when freeze and release were split, with the mitigation stated as *"commit state is a stated DoD
line, not a footnote."* Discharging it is **M9.26's** work: commit, tag, baseline v4.0.0. M9.25 does
not commit and does not tag, because doing so would collapse the two milestones.

---

## Certification preservation

| | M9.24 closed with | M9.25 freezes |
|---|---|---|
| Verdict | `CERTIFIED_WITH_ADVISORIES` | unchanged |
| Catalogue version | `m9.24.1` | unchanged |
| Controls | 27 (24 blocking, 3 advisory) | unchanged |
| Blocking violations | 0 | 0 |
| Inconclusive | 0 | 0 |
| Advisories | NS-5, ST-3 | unchanged, both still `VIOLATED` |
| Certification digest | `39790b95d061a763…` | **identical** |
| Limitations | 9, all owned | unchanged |

The freeze suite pins the literal certification digest, so a drifting tree fails the freeze rather
than quietly re-certifying to a different value.

---

## Defects found and fixed

Two, both discovered *by* the freeze, both about determinism of evidence, both owned by M9.25
because deterministic freeze evidence is M9.25's mandate. **Neither is a financial-correctness
defect, and neither changed the certification catalogue, a control's severity, or a clause.**

### M9.25 D-1 — the certification digest was context-dependent

**Symptom.** Pinning the certification digest as freeze evidence failed in a full test session:
`39790b95…` in isolation, `e476298d…` once the suite ran.

**Root cause.** `NS-2` counted **every** index in the process. `api.models` registers five
non-Billing tables, so `examined` was 1193 in isolation and 1199 in a full session — and `examined`
enters the digest. M9.24 never caught it because no M9.24 test pinned a literal digest across
execution contexts; its own replay test compares two runs *within* one session, where both see the
same context.

**Fix.** `NS-2` now counts **Billing's** indexes, while still comparing their names against every
table in the process. The control's strength is unchanged; only what it reports having examined is
now deterministic.

**A first attempt at this fix silently weakened the control** — using `setdefault` let a Billing
table register itself as its own index's owner, which stopped cross-plane detection entirely while
still looking green. Caught by running the collision cases explicitly rather than trusting the fix.
The shipped version tracks the full set of claimants per index name.

**Proof.** Digest is now identical in isolation and after importing another plane's models — and it
is **the same value M9.24 recorded**, so no historical report is invalidated. The hunt test was
**strengthened** to cover both a Billing-internal and a cross-plane collision, and to assert the
deterministic `examined` count.

### M9.25 D-2 — the freeze manifest repeated the same defect class

**Symptom.** The manifest reported 145 tables / 1193 indexes in isolation and 150 / 1199 in a full
session.

**Root cause.** `derived_inventory` read the process-wide `SQLModel.metadata` — exactly the defect
M9.24 had already fixed for its ownership controls, reintroduced in new code.

**Fix.** The manifest resolves Billing's tables by which module declared the row class, reusing
`CertificationEvidence._billing_tables()` rather than writing a third implementation.

---

## Extension policy

Epic 9 is frozen as an **additive-only** surface, the posture that held for 17 Epic 8 milestones and
23 Epic 9 milestones:

* new commercial capability lands **beside** frozen aggregates, never inside them (the §A12
  precedent M9.19–M9.23 followed four times);
* new tables carry the `cp_billing_*` prefix and arrive by a migration **after 0033**, never by an
  `ALTER` to a frozen table;
* new API surface takes its own prefix, proven collision-free against all 521 existing routes;
* new Foundation modules land beside existing ones and extend `__all__` additively;
* **the nine declared limitations are closed by their owning milestones**, not by whoever notices
  them next.

Anything that cannot be expressed additively requires an ADR, not a local exception.

---

## Limitations carried into the freeze

All nine, unchanged and still owned. **M9.25 closed none of them** — closing another milestone's
limitation under cover of governance work would be exactly the ownership theft the freeze exists to
prevent.

| Id | Raised by | Owner | Limitation |
|---|---|---|---|
| LIM-1 | M9.23 | M9.15 | No period-scoped tax assessment read |
| LIM-2 | M9.23 | M9.16 | No period-scoped payment intent read |
| LIM-3 | M9.23 | M9.18 | No period-scoped recognition read |
| LIM-4 | M9.23 | M9.8 | No paginated account read |
| LIM-5 | M9.23 | M9.8 | No reverse payer index |
| LIM-6 | M9.22 | M9.13/M9.14 | No organization-wide period read |
| LIM-7 | M9.24 | M9.24 | Certification is self-administered |
| LIM-8 | M9.24 | M9.5/M9.6/M9.9/M9.10/M9.11 | 29 module-qualified OpenAPI schema names |
| LIM-9 | M9.24 | M9.7/M9.8 | Two Studio pages rescale by a hardcoded exponent |

Plus the freeze's own open item: **FZ-12 is discharged by M9.26**, not by M9.25.

---

## Handover to M9.26

M9.26 — Repository Release — receives:

1. a tree whose certification digest is `39790b95d061a763…` and whose freeze manifest digest is
   `eed692224f0aeb82…`;
2. a declared release input set: 4643 repository-wide tests, 0 failed, 0 skipped, ruff clean;
3. **the uncommitted-state condition to discharge** — commit, tag, baseline **v4.0.0**, release
   notes;
4. nine owned limitations and two advisories, none of which M9.26 is expected to close.

M9.25 created no tag and modified no release version.
