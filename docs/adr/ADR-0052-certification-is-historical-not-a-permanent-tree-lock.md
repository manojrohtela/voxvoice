# ADR-0052 — A certification is a statement about a tree that *was* certified, not a permanent lock on the repository

| | |
|---|---|
| **Status** | **Accepted 2026-08-26** (ratified at M11.2.1). |
| **Kind** | Governance-mechanism ADR — repairs how certification and freeze are *evaluated*. **Amends no Constitution Part A or Part B section.** |
| **Amends** | Nothing. **No Epic 9 or Epic 10 finding, verdict, digest, control definition, limitation or artifact changes.** ADR-0043 and its **CD1–CD18** remain intact, as do DL-0136 and DL-0146. This ADR changes *what question the evaluators ask*, never *what Epic 9 or Epic 10 concluded*. |
| **Deciders** | Technical Owner |
| **Related** | [ADR-0043](ADR-0043-m10.13-certification.md) (CD2 independence, CD6 two-state digests, CD11, CD16), [ADR-0042](ADR-0042-m10.12-hardening.md), [ADR-0044](ADR-0044-audio-representation-resampling-and-media-buffers.md) (the Epic 11 work that exposed this), [ADR-0051](ADR-0051-epic11-certification-control-set.md) (CE2 — Epic 9/10 suites are never executed as Epic 11 controls), [DL-0139](../../.voxvoice/DECISION_LOG.md), [DL-0146](../../.voxvoice/DECISION_LOG.md), GOVERNANCE §10, TESTING_POLICY §3 |

## Status

**Accepted 2026-08-26**, ratified at milestone **M11.2.1**. It authorizes exactly one thing: the
repair of the evaluation mechanism described below. It authorizes no production change.

## Context

M11.2 implemented [ADR-0044](ADR-0044-audio-representation-resampling-and-media-buffers.md) and
reached **BLOCKED** with eleven failures — every one of them in a freeze, certification or release
suite, and **not one of them behavioural**. All 5466 other tests passed.

The cause was mechanical and shared. Assertions written to ask *"did **this milestone** change
X?"* were expressed as comparisons with an **implicit** far end:

```python
_git("diff", "--name-only", CERTIFIED_COMMIT, "--", plane)   # → compares against the WORKING TREE
_git("diff", "--name-only", "HEAD", "--", path)              # → compares against the WORKING TREE
_git("status", "--short")                                    # → describes the checkout right now
certify().report_digest                                      # → recomputed over the LIVE tree
```

When M10.14 ran, `HEAD` *was* the state being frozen, so "the working tree" and "the frozen state"
were the same thing and the forms above were correct. **They stop being the same thing the moment
anything else happens in the repository.** From then on the same expression silently asks a
different and much larger question: *"has anybody changed this, ever, in any later epic?"*

Three concrete consequences, all verified at M11.2:

1. `tests/freeze/epic10.py` pins `FROZEN_PLANES` including `platform/core` and `platform/runtime`.
   ADR-0044 — Accepted at M11.1 — **requires** modifying both. `EF-1` and `EF-10` therefore
   reported `NOT_FROZEN`, and the range `db8c508 → 3779e93` is in fact **empty**: M10.14 changed
   nothing. The freeze was being failed by work it had no authority over.
2. `test_no_adr_0044_was_created` globbed the live `docs/adr/` and asserted `ADR-0044*` was absent.
   That encoded a second claim M10.14 was never entitled to make — *that ADR numbering would never
   advance past 0043*. `docs/adr/README.md` states numbering is sequential and never reused, so
   0044 was simply the next free number, and Epic 11 consumed 0044–0051 legitimately at M11.1.
3. Epic 9's certification digest is a function of the whole platform: control **PL-2** records how
   many modules it examined. M11.2's two new files under `runtime/pipeline/audio/` moved that count
   from **256 to 258**, moving the report digest from `c044b3f6…` to `206e5c4e…`. Every other one
   of the 27 control results was **byte-identical**. Recomputing the digest over the live tree had
   quietly turned *"is the certified tree unchanged?"* into *"has the repository stopped
   evolving?"*.

The distinction was already understood inside the codebase. `EPIC10_FREEZE_BOUNDARY_PATHS`
carries this note verbatim:

> Asking it of the live working tree instead cannot distinguish *"M10.14 created a release
> document"* from *"M10.15 created the one it was required to create"*, so the freeze would report
> `NOT_FROZEN` the moment the release it exists to authorise happens.

`EF-11` was corrected on exactly that reasoning. `EF-1`, `EF-2`, `EF-10`, `E10-10` and several
tests simply were not. **This ADR finishes applying a distinction the repository already states.**

## Decision

### HC1 — Certification and freeze are **historical** claims

A certification answers: *"was the certified state internally consistent and unchanged **at the
time of certification**?"* It does **not** answer *"has nobody modified a frozen plane during
every later epic?"* Those are different questions and only the first belongs to Epic 9 or Epic 10.

### HC2 — Every historical comparison names **two** pinned endpoints

No historical assertion may leave an endpoint implicit. `git diff A -- path` compares against the
working tree and is therefore prohibited in these suites; `git diff A B -- path` is required.

The pinned ranges are:

| Question | Range |
|---|---|
| What did the **M10.13 certification** change? | `CERTIFICATION_PARENT` (`8ee7d3c1…`) → `CERTIFICATION_COMMIT` (`db8c5082…`) |
| What did the **M10.14 freeze** change? | `CERTIFIED_COMMIT` (`db8c5082…`) → `FREEZE_BOUNDARY_COMMIT` (`3779e932…`) |
| Does the **released tree** still reproduce its recorded digests? | evaluated at `RELEASED_COMMIT` (`3779e932…` = `v5.0.0`) |

### HC3 — Inventory questions read the historical tree, never the filesystem

"Did an artifact exist at commit X?" is answered with `git ls-tree`/`git cat-file` against X, never
by globbing the checkout. This is what makes the ADR-numbering claim durable: the honest fact is
*"no ADR existed at the freeze boundary that did not exist at the certified commit"*, which stays
true forever regardless of which numbers later epics consume.

### HC4 — Digest claims stay **real recomputations**, performed against the historical tree

The two claims that require *executing* certification rather than reading files — *"the certified
tree still produces the certified digest"* and *"the released tree still produces the released
manifest digest"* — are recomputed inside a detached `git worktree` of the historical commit
(`platform/tests/history.py`). The certification runs **in full** and must reproduce the pinned
digest **exactly**.

This is deliberately not a relaxation. Verified at M11.2.1: the `v5.0.0` tree reproduces
`c044b3f6a23066df…` precisely — the pinned value — so the assertion still fails on any real drift
in the certified tree.

### HC5 — No control definition, finding, verdict, digest or limitation changes

Epic 9's `catalogue.py` and `runner.py` are **not edited** (ADR-0043 **CD2** holds). No control is
deleted, no assertion is replaced with a tautology, no frozen plane is excluded from any check, and
no pinned digest is re-baselined. All 27 Epic 9 controls, all 13 Epic 10 controls and all 12 `EF-`
attestations continue to run and continue to reach the same outcomes.

### HC6 — No epic-specific exception exists, and none may be added

There is no `if epic11:` branch, no allow-list naming ADR-0044, and no rule that
`platform/core` "may change after M11". The repair is **general**: it fixes what the question is,
so it holds identically for Epic 12 and every epic after it.

### HC7 — Negative guards are preserved

Each repaired control keeps the synthetic test proving it still *fails* when its condition is
genuinely violated. A guard that has only ever seen a passing repository has not been shown to
guard anything — the principle `test_epic10_freeze.py` already states, and which HC5 would be
hollow without.

### HC8 — A release advances the pins

`RELEASED_TAG`/`RELEASED_COMMIT` name the newest released tree. Advancing them is part of a release
milestone, exactly as the `v4.0.0` pins were treated. This is ordinary release bookkeeping, not
epic-specific machinery.

## Consequences

- Epic 11 — and every later epic — can modify planes Epic 10 froze, under its own accepted ADRs
  and its own certification gates, without falsifying a historical certification.
- Epic 9's and Epic 10's verdicts, digests, limitations and advisories are **unchanged**; `NS-5`,
  `ST-3` and all declared limitations stay open with unchanged owners.
- Freeze and certification suites gain a real dependency on git history. A shallow clone without
  the pinned commits fails **closed** — `HistoricalEvidenceUnavailable`, never a silent pass.
- The digest assertions cost a `git worktree` per process (~12 s once, then cached).

## Alternatives considered

- **Edit Epic 10's freeze tests to skip `platform/core` and `platform/runtime`.** Rejected: it
  removes coverage rather than correcting a question, and it is precisely the "make it pass by
  excluding the planes" move the M11.2.1 charter forbids.
- **Add an Epic 11 exception.** Rejected — HC6. The next epic would need its own, and the mechanism
  would rot into a list of pardons.
- **Renumber Epic 11's ADRs to avoid 0044.** Rejected: `docs/adr/README.md` states numbers are
  sequential and never reused, and it would let a stale assertion dictate governance numbering.
- **Re-baseline the Epic 9 digest to `206e5c4e…`.** Rejected outright: that value is an artefact of
  Epic 11's files existing, would move again on the next module added, and would destroy the
  digest's meaning as evidence about a certified tree.
- **Delete the digest assertions.** Rejected — HC4/HC5; certification by omission.
- **Commit Epic 11's work so the tree is clean.** Rejected: it does not help. `EF-1`/`EF-10` compare
  against a hardcoded commit, so a commit changes nothing, and `PL-2`'s module count is unaffected
  by whether files are committed.

---

## Amendment 1 — historical **counts** are digest inputs too (M11.5.1, 2026-09-07)

**Status:** Accepted 2026-09-07. **Additive.** HC1–HC8 above are unchanged and remain in force;
this amendment adds **HC9** and does not reinterpret a word of the original decision.

### What M11.5 found

M11.5 needed four additive `EventType` members for AR-6 session-reality events. Adding them moved
Epic 10's published certification digest from `7e57bc14764201b1…` to `1b65916c39c21a21…` —
**while every control still reported `satisfied`**. Measured, not inferred.

The cause was a form of this ADR's defect the original repair did not reach. HC2 fixed comparisons
whose *far endpoint* was implicit; HC4 fixed digests *recomputed* over the live tree. Neither
covered a control that simply **counts** the live tree:

```python
# tests/certification/epic10.py, before M11.5.1
return _bad("E10-5", violations, len(current), ...)   # len(current) = live EventType members
```

`examined` is a digest input (`{control, outcome, violations, examined}`). So the digest was
measuring **the repository's growth**, not the certified tree — and any later epic adding a file,
a member, a table or a module could move a frozen historical value without anything being wrong.
Contrast **E10-4**, which was already correct: `len(historical)`.

M11.5 stopped rather than working around it, which is what §21 of its authorization required.

### HC9 — a value that describes the certified state is derived from the **certified tree**

Every digest input that represents *what was certified* is read from `CERTIFICATION_COMMIT`, never
from the checkout. This covers, and is not limited to:

* git diff / git state assertions (HC2);
* digest recomputation (HC4);
* historical range assertions (HC2);
* **filesystem and tree counts** — how many modules, files or declarations existed;
* **source, member and module counts** — how many enum members, tables or indexes existed.

**The rule, stated so it needs no interpretation:** *a certification digest must not change merely
because a later milestone legitimately adds files, members, tables, providers or modules, when the
certification is intended to describe the previously certified tree.*

### HC10 — the live tree keeps its job

HC9 governs **digest inputs only**. A control's **negative invariant** continues to scan the live
tree wherever the rule is an ongoing prohibition rather than a historical observation. The two
responsibilities are separate and both survive:

| Control | Historical (digest input) | Live (ongoing invariant) |
|---|---|---|
| **E10-3** | count of `evt_`-prefixed names at the certified commit | every table/index under `events/` still carries the prefix |
| **E10-5** | count of `EventType` members at the certified commit | no `metric.billing.*` member may ever appear (ADR-0033 MS9) |
| **E10-6** | count of Billing modules at the certified commit | no Billing module subscribes to a bus (`events → billing` forbidden) |

A control that read *only* history would stop guarding; one that read *only* the live tree is the
defect. Both halves are asserted, including negative tests proving each invariant still fires.

### HC11 — nothing is re-baselined, deleted or weakened

No published digest is updated: the repair is correct precisely **because** the digest still reads
`7e57bc14764201b1…`. No control is removed, no assertion becomes a tautology, no verdict changes,
`INCONCLUSIVE` still does not pass, and Epic 9's `catalogue.py`/`runner.py` remain untouched
(ADR-0043 **CD2** holds). Epic 10's implementation, freeze manifest and freeze digest
(`d3d4858d5f18b7f6…`) are not touched.

### HC12 — no epic-specific exception

As with HC6: there is no allow-list for Epic 11, no `if epic11:`, and no rule that `EventType`
"may grow after M11". The repair fixes *what the question is*, so it holds identically for Epic 12
and every epic after it.

## Amendment 2 — HC9 applied to E10-7, which Amendment 1 missed (M12.1.1, 2026-09-21)

**Status:** Accepted 2026-09-21. **No new rule.** HC1–HC12 are unchanged. This amendment records
that **HC9 now also governs E10-7** and states the evidence. It is not an Epic 10 redesign.

**What was wrong.** E10-7 (ADR-0043 **CD7**, *"no pre-existing `platform/api/` file modified;
additions permitted"*) set `examined = len(changes)`, where `changes` is
`git diff --name-status v4.0.0 -- platform/api` against the **working tree**. `examined` is a
digest input. Because CD7 *permits* additions, every legitimate later `api/` file moved Epic 10's
certification digest while E10-7 stayed `satisfied`. This is exactly the defect class HC9 names.
Amendment 1 repaired E10-3, E10-5 and E10-6 but did not reach E10-7.

**Measured.** Recorded as finding F-6 (DL-0237) and re-verified at M12.1.1:

| Tree | live `api/` changes since `v4.0.0` | E10-7 `examined` (before) | certification / freeze digest (before) |
|---|---|---|---|
| certified `db8c508` | 2 | 2 | `7e57bc14…` / `d3d4858d…` |
| `m11-local-baseline` `d5ae2c1` (clean) | 9 | 9 | `0cf851eb…` / `fa69f933…` |
| M12.1 `1a03d90` | 16 | 16 | `ed2a9cf9…` / `2df266c2…` |

Three tests failed on the clean `m11-local-baseline` for this reason alone. M11.16.4 (DL-0226)
appeared to restore `7e57bc14…` only because its new `api/` files were still **untracked**, and
`git diff` does not list untracked paths.

**The repair (HC9 + HC10, same shape as E10-5).**

* **examined** → `_certified_changes_since_baseline("platform/api")`, which is
  `git diff --name-status v4.0.0 <CERTIFICATION_COMMIT>`. Both endpoints are pinned (HC2), and the
  result is the two `api/metering` additions, permanently.
* **violations** → unchanged: still `_changed_since_baseline("platform/api")` over the live tree.
  Any modified or deleted pre-existing `api/` file still fails E10-7.
* Empty historical evidence is `INCONCLUSIVE`, never a pass (ADR-0043 CD4).

The helper reuses the existing `_git`, `_require_baseline` and name-status parsing; no second
framework was introduced.

**Result.** After the repair, the digest is `7e57bc14764201b123c49b9321f15d9c41f0ee7015e6879c64c0307e56db3d0d`
and the freeze is `d3d4858d5f18b7f6d583f4d34caeec52933acc262e36d4f8b343e2e8b6a00d38`. Both equal
the pinned literals exactly, on both `d5ae2c1` and `1a03d90`. **Nothing was re-baselined** (HC11):
the published values are reproduced, not updated. No control, assertion, verdict or limitation
changed. No file under `events/`, `observability/`, `api/metering/` or `billing/` was touched.
Epic 10 runtime semantics are unchanged. No exception names M11, M12 or `console` (HC12).

**Neighbouring controls, surveyed and not changed.** E10-3/5/6 were already historical
(Amendment 1). E10-4 reads `v4.0.0`, and E10-10 to E10-13 count pinned literals. E10-1, E10-8 and
E10-9 count live changes, but for those any change is itself a violation, so the count cannot move
on permitted work. **E10-2** counts the live Epic 10 migration chain: latent, because it moves only
if a later milestone adds an Epic 10 (`events/`) migration. It is recorded, not repaired (DL-0238).

---

## Evidence required later

1. Every repaired assertion keeps its negative/synthetic counterpart green.
2. `git grep` shows no `git diff <ref> --` with an implicit second endpoint in these suites.
3. The digest recomputations still reproduce the pinned values from the historical tree.
4. Full repository suite green, with the Epic 9/Epic 10 verdicts and digests unchanged.
