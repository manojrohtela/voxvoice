# M9.24 — Billing Certification

**Status:** implemented · **Milestone:** M9.24 · **Decision log:** DL-0135
**Amends:** nothing. ADR-0023, ADR-0021 and ADR-0031 are unchanged.
**Verdict:** `CERTIFIED_WITH_ADVISORIES` · digest `39790b95d061a763…`

---

## What this milestone is, and what it is not

The roadmap specifies M9.24 exactly (`docs/roadmap/epic9-implementation-plan.md` §10, phase P6):

> **M9.24 Certification** — independent certification against ADR-0023, the Blueprint, and the
> Provider Strategy. Output: *a certification report with an honest verdict*. Test strategy: *an
> executable certification suite (boundaries, append-only, determinism, reproducibility,
> no-vendor-types, no-synchronous-billing, gate classification completeness)*.
> DoD: **"Certification is reproducible as tests, not a one-off document."**

So M9.24 owns exactly one fact, and it is not a financial one:

> **Does this repository satisfy the architecture it ratified?**

It creates no revenue fact, no money, no account, no period and no document. It reads structure —
module graphs, package namespaces, schema metadata, route tables, FSM definitions, migration
chains — and returns a verdict.

## Where certification lives, and why it is not in `billing/`

**M9.24 adds zero production code.** The catalogue and the runner live in `tests/certification/`,
and that placement was arrived at the hard way.

The first implementation put the runner in `billing/application/`. It failed **nine of the
repository's own architecture tests** at once, because a certification runner must import
`sqlmodel`, `billing.persistence` and `billing.customer` in order to inspect them — and the
Application layer is forbidden all three. The layer rules were right; the placement was wrong.
Certification inspects all four layers, so it belongs to none of them, and the roadmap's DoD says
where it goes: *"reproducible as tests."*

The alternative considered and rejected was a cross-layer exemption for a new `billing/certification/`
package, on the DL-0010 composition-root precedent. It was rejected because inventing an
architectural exemption at the last milestone before a freeze — in the milestone whose job is
catching exactly that — is the wrong trade. A test asserts the files are absent from `billing/`.

## What M9.24 deliberately does not ship

**No table, no migration, no repository, no command bus, no customer API and no Studio page.** Each
absence is a decision, not an omission:

* **No persistence.** The DoD sentence is the whole argument: a stored verdict is a claim about a
  tree that has already moved on. The authority is the suite re-run, so the report is derived and
  content-addressed on the evidence it examined. A row would be a second, staler answer to a
  question the tests already answer definitively. Migration chain therefore ends at **0033**,
  unchanged from M9.23, and a test asserts it.
* **No org-scoped API.** *"Does this repository satisfy its architecture?"* is not an
  organization's question. An org-scoped endpoint serving a repository-wide engineering fact would
  be a lie about ownership — in the milestone whose entire job is catching those.
* **No dispatch.** There is nothing for M9.24 to ask another domain to do.

---

## The verdict

| | |
|---|---|
| **Verdict** | `CERTIFIED_WITH_ADVISORIES` |
| **Controls** | 27 (24 blocking, 3 advisory) |
| **Blocking violations** | **0** |
| **Inconclusive** | **0** |
| **Advisories** | 2 — `NS-5`, `ST-3`, both with named owners |
| **Digest** | `39790b95d061a7631e6d1b61dab14b227f02038873ee87b5c6a34658bb01d2d8` |

`CERTIFIED_WITH_ADVISORIES` is a pass, not a softened failure: the architecture holds and the debt
is named, owned and non-blocking. The verdict was not tuned to reach it — §"The two advisories"
below argues each classification on mechanism, and both findings are recorded as limitations with
owning milestones rather than closed by relabelling.

---

## Why `INCONCLUSIVE` exists

`SATISFIED` and `VIOLATED` are not enough. A control whose evidence could not be gathered is
**`INCONCLUSIVE`**, never `SATISFIED` — and a blocking control that is inconclusive **fails
certification**, because a guarantee nobody could verify is not a guarantee.

An evaluator that raises is caught and converted to `INCONCLUSIVE` with the exception recorded. A
test proves it: monkeypatching an evaluator to raise flips the verdict to `NOT_CERTIFIED`. This is
the same rule M9.21 wrote for evidence, M9.22 for report sources and M9.23 for consolidation
sources, applied one altitude up: *"could not check" must never render as "checked and fine."*

---

## The controls

Declared as **data**, not as a list of test functions, so "what is certified?" is answerable by
reading one table — and so a control cannot be quietly deleted to make a suite green. Two structural
guards enforce that: every declared control must produce a result (`guard_complete`), and every
control must cite a ratified clause (`CertificationControl.__post_init__`).

| Family | Controls | Certifies |
|---|---|---|
| Ownership | `OWN-1..4` | ADR-0023 §6/§7 — Billing writes only `cp_billing_*`; no Forbidden-Write concept; no `metric.*` produced; domain events are `billing.*` |
| Revenue safety | `RS-1`, `RS-5`, `RS-6` | RS1/RS3 append-only; RS5 integer minor units; no FX rate at any altitude |
| Payment | `PY-5`, `PY-6` | no auto-resolution edge; no instrument data anywhere |
| Tax | `TX-1` | no rate table ships with Billing |
| Placement | `PL-1..4` | ADR-0031 IR1/IR3/IR4 import direction; Foundation purity; no vendor SDK outside an adapter |
| Consumer | `ST-1..3` | ADR-0021 — Studio derives no financial value, stores no domain state |
| Namespace | `NS-1..6` | true shadowing, index/table/route/schema uniqueness |
| Determinism | `DT-1..4` | lifecycle reachability, identity sources, one canonicalizer, additive ordered migrations |

---

## `NS-1`, and the defect class this epic kept shipping

M9.20 shipped a duplicate identity helper. M9.21 shipped a wire-level `ReasonBody` collision. M9.22
shipped **seven** symbol collisions. M9.23 shipped an index collision plus four caught pre-landing.
Phase 15 of this milestone required the class to be closed structurally.

**The audit found 10 colliding names across 21 definitions still in the tree**, all latent (every
internal call site uses a module-path import, so nothing computed a wrong value) and all now fixed
by renaming the losing side — the precedent M9.22 and M9.23 already set. Two of the ten were
identity functions, which is the dangerous kind:

| Name | Defined in | Package served | Fix |
|---|---|---|---|
| `derive_evidence_digest` | M9.19 *and* M9.20 | M9.19's | M9.20's → `derive_incident_evidence_digest` |
| `derive_policy_version_id` | M9.17 *and* M9.18 | M9.17's | M9.18's → `derive_recognition_policy_version_id` |
| `explanation_view` | 5 Application engines | tax's | domain-scoped per engine |
| `activity_view`, `attempt_view`, `event_view`, `policy_view`, `position_view` | 2–4 engines each | one arbitrary winner | domain-scoped per engine |
| `entry_view` | M9.12 (via `__init__`) *and* M9.18 (via `__all__`) | ambiguous by import style | M9.18's → `recognition_entry_view` |
| `run_view` | M9.11 (via `__init__`) *and* M9.19 (via `__all__`) | ambiguous by import style | M9.19's → `integrity_run_view` |

**The control itself had to be fixed twice**, and the second fix is the interesting one:

1. The first draft scanned `__all__` only. It reported a reassuring **1,405 symbols inspected** —
   while being blind to **110 of 183 Billing modules**, including `commercial_reporting`, because
   they declare no `__all__` at all. A control that cannot see the code it certifies is worse than
   no control.
2. The second draft scanned every AST-defined name and fired **39 times** on module-local helper
   functions in different modules — which never share a namespace and are not a hazard. A control
   that fires on non-hazards teaches people to ignore it.

The shipped control checks the two routes by which a name actually reaches a package namespace —
what `__init__.py` **imports**, and what a submodule puts in **`__all__`** — with coverage that does
not depend on `__all__` existing. It inspects **2,669 package-namespace names** and found the final
two shadowings (`entry_view`, `run_view`) that the first draft had missed entirely.

---

## The two advisories

### `NS-5` — 29 module-qualified OpenAPI schema names

Twenty-nine Pydantic model names collide across the customer schema modules (`AddressBody`,
`ScopeBody`, `BundleResponse`, `SubscriptionResponse` and others appear in two or more), so FastAPI
emits `billing__customer__account_schemas__AddressBody` rather than `AddressBody`.

**Advisory, and the reason is the mechanism rather than the count.** `NS-1` is blocking because
Python *silently* serves one of two objects under one name. FastAPI does the opposite: it **detects**
the collision and qualifies the schema name, so no client is ever handed an ambiguous contract. The
cost is a generated document that reads badly. No ratified clause requires unqualified schema names.

The classification test applied: *would the same judgment hold at a count of 2?* Yes — the argument
is about disambiguation, not volume. Recorded as **LIM-8**, owner M9.5/M9.6/M9.9/M9.10/M9.11;
closing it is a rename inside the owning schema modules.

### `ST-3` — two Studio pages rescale currency by a hardcoded exponent

`accounts.html` and `pricing.html` render integer minor units as `minor / 100`. Every one of the
eight currencies this platform supports has exponent 2, so the arithmetic is **correct today** and
the finding is latent. It becomes a wrong number on screen the day a 0-exponent (JPY) or 3-exponent
(KWD) currency is added.

`ST-1` — which blocks — was narrowed to the act of **creating** a financial value client-side (a
total, an aggregation, a tax, a percentage); no Studio page does that, and none contains a
`.reduce(`. `ST-3` covers **rendering** a value the server already decided. The split is a real
semantic boundary, not a softened `ST-1`: the newer pages (M9.19–M9.23) render minor units raw and
were written that way deliberately, which is what makes the older pattern visible as debt rather
than as house style. Recorded as **LIM-9**, owner M9.7/M9.8, with the trigger condition stated:
**adding a non-exponent-2 currency must be preceded by a Studio formatting fix.**

---

## An observation that is not a control

Thirty-nine module-local view mappers share generic names across engines (`activity_view`,
`diagnostics_view`, `inspection_view`, `money_view` and others). They occupy no shared namespace and
are not a hazard, so no control fires on them — a control must cite a ratified clause, and no clause
covers naming taste. It is recorded here because a reader of the freeze manifest should know the
pattern exists before adding the fortieth.

---

## Declared limitations at freeze

Nine, each with an owning milestone, asserted non-empty and fully-owned by the suite. An epic
claiming zero limitations at freeze is an epic that stopped looking.

| Id | Raised by | Owner | Limitation |
|---|---|---|---|
| LIM-1 | M9.23 | **M9.15** | Tax publishes no period-scoped assessment read |
| LIM-2 | M9.23 | **M9.16** | Payment publishes no period-scoped intent read |
| LIM-3 | M9.23 | **M9.18** | Revenue recognition publishes no period-scoped read |
| LIM-4 | M9.23 | **M9.8** | No paginated account read; org-wide scope capped at `MAX_SCOPE_MEMBERS` |
| LIM-5 | M9.23 | **M9.8** | No reverse payer index; `PAYER_GROUP` reads the organization |
| LIM-6 | M9.22 | **M9.13/M9.14** | No organization-wide period read |
| LIM-7 | M9.24 | **M9.24** | Certification is self-administered |
| LIM-8 | M9.24 | **M9.5/M9.6/M9.9/M9.10/M9.11** | 29 module-qualified OpenAPI schema names |
| LIM-9 | M9.24 | **M9.7/M9.8** | Two Studio pages rescale by a hardcoded exponent |

**None was closed by stealing ownership.** LIM-1..6 belong to the domains that own the missing
reads; M9.24 certifies that they are *declared and owned*, which is the only thing it may honestly
do about them.

---

## Determinism

```
digest = canonical_digest([{control, outcome, sorted(violations), examined} …])
```

Reusing M9.21's single canonicalizer — `DT-3` asserts no second one exists. The digest excludes the
clock, the host, the process and the run order, so the same tree certifies to the same digest on
any machine, forever. Proven three ways: replay produces an identical digest and verdict; a changed
finding produces a different one; the digest is order-independent.

The digest attests to **what was found**; the verdict **interprets** it. Reclassifying `NS-5` from
blocking to advisory therefore changed the verdict and left the digest untouched — which is correct,
and worth knowing when comparing two runs.

---

## Layers

| Artifact | Contents |
|---|---|
| `tests/certification/catalogue.py` | vocabulary, verdict rule, control catalogue, declared limitations. Pure: no `ast`, no `importlib`, no `Path`, no `sqlmodel` (asserted) |
| `tests/certification/runner.py` | evidence gathering and 27 evaluators |
| `tests/test_billing_certification.py` | 70 tests |
| `billing/**` | **unchanged by this milestone**, apart from the 21 shadowing renames |

---

## ADR compliance

**No ADR is amended.** ADR-0023's 53 ratified clauses, ADR-0031's 53, and ADR-0021's Allowed/
Forbidden Writes are unchanged. Where a control could not be traced to a ratified clause it was not
written — which is why the 39-name observation above is prose and not a control.

ADR-0023 **PY5** now holds at a sixth altitude: M9.19 made *a divergence is never auto-resolved*
structural for one finding, M9.20 for a group, M9.21 for the investigation of one, M9.22 for the
filing, M9.23 for the scope — and M9.24 asserts it across **all 29 declared lifecycles at once**,
by proving no lifecycle anywhere offers an automatic resolution trigger.

---

## Limitations of the certification itself

1. **It is self-administered.** The same author implemented the architecture and the suite that
   certifies it. The roadmap asked for *independent* certification; this is not independent, and no
   amount of test coverage changes that. Recorded as LIM-7.
2. **It certifies structure, not behaviour.** `NS-1` proves no two objects share a name; it cannot
   prove a correct invoice. The behavioural guarantees live in the 4,600 tests the domain milestones
   shipped, and this suite does not re-derive them.
3. **Two controls were wrong before they were right**, and one of them (`NS-1`) was wrong in the
   direction of false confidence. Every control here should be read as *"this is what was checked"*,
   not as *"this is what is true"*.
4. **The roadmap's own control list is partially deferred.** `no-synchronous-billing` and
   `gate classification completeness` are asserted by M9.17's enforcement suite rather than
   re-implemented here; `no-vendor-types` is `PL-4`.
