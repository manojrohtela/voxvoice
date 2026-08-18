# M9.21 — Financial Investigation & Audit Evidence Engine

**Status:** implemented · **Milestone:** M9.21 · **Decision log:** DL-0132
**Amends:** nothing. ADR-0023 **PY5** is unchanged and now holds at a third altitude.

---

## The question this milestone exists to answer

M9.19 observes that two financial facts disagree. M9.20 proves which of those observations share a
cause. **Neither records what a human found out**, and today that answer lives in application logs —
which rotate, which are sampled, and which nobody can prove were not edited.

> **What exactly happened, what evidence supports each statement, what did the system know at each
> point in time, what actions were taken, and can an auditor reconstruct the investigation without
> relying on application logs?**

Under one constraint that shapes everything else:

> **An investigation records what was observed and concluded. It never becomes the source of
> financial truth.**

The altitudes stay strict. M9.19 asks *do these two facts agree?* M9.20 asks *which of those
disagreements are the same problem?* M9.21 asks *what did we find out about it, and can you check?*
Reconciliation ownership does not move; correlation ownership does not move.

---

## The audit

The pre-implementation sweep across M9.2–M9.20 found **no existing investigation, evidence-capture,
chain-of-custody or audit-package aggregate** — the concept is genuinely new. Six artifacts were
inspected and **none was changed**.

| Existing artifact | Owner | Meaning | M9.21 action | Reason |
|---|---|---|---|---|
| `IntegrityIncident` | M9.20 | one correlated financial integrity incident | **read-only input** | never written; no incident repository is imported |
| `FindingLink` | M9.20 | which findings belong to an incident | **read-only** | supplies the chain the evidence walk follows |
| `IncidentRepair` | M9.20 | what was asked of which owning domain | **read-only reference** | M9.21 executes no repair and cannot request one |
| `IntegrityFinding` | M9.19 | one divergence between two facts | **read-only** | snapshotted as evidence, never mutated |
| `IncidentStatus.INVESTIGATING` | M9.20 | an incident somebody is working | **not reused** | see below |
| `derive_evidence_digest` | M9.19 **and** M9.20 | flat tuple digest | **not extended** | see below |

**Two names were deliberately not reused.**

`IncidentStatus.INVESTIGATING` already means *somebody is working this incident*. M9.21's
`InvestigationStatus.INVESTIGATING` means *this case is in progress*. Same word, two enums, no
shared type — a client cannot pass one where the other is meant, and a test asserts it.

`derive_evidence_digest` exists twice already, in M9.19 and M9.20, both over a flat tuple of
strings. M9.21 needed a **structural** canonicalizer over nested representations, and gave it a
distinct name (`canonical_digest`, `derive_evidence_id`) rather than a third colliding one. That is
M9.20's own D-2 lesson applied *before* it could recur — and it is not the only place this
milestone had to learn it (see D-2 below, where it recurred anyway, in the wire layer).

---

## Ownership

| Question | Owner |
|---|---|
| do these two financial facts agree? | **M9.19** |
| which disagreements are the same problem? what caused it? | **M9.20** |
| what did we find out, on what evidence, and can you check? | **M9.21** |
| what is a balance, a total, a tax, a price, a recognized amount? | the domain that decided it |

M9.21 owns investigation cases, investigation state and timeline, immutable evidence references and
snapshots, evidence provenance and availability, questions, observations, hypotheses, conclusions,
chain-of-custody metadata, audit packages, export manifests and completeness.

It owns **nothing financial**. No balance, no total, no invoice figure, no tax amount, no payment
amount, no recognized amount, no rating, no usage, no pricing, no entitlement, no account hierarchy,
no ledger posting, no repair execution and no incident resolution.

**M9.21 has no outward command at all.** M9.20 at least dispatched M9.19's repair request; M9.21
dispatches nothing. It *references* a repair the owning domain performed and can ask no domain to
perform one. `dispatch` is not even injected into the use cases, and a test asserts it.

---

## Live reference versus evidence snapshot

This is the design's central distinction, and the failure it prevents is specific: an audit package
containing **today's corrected figure while claiming to show what was known before the correction**.

* a `LIVE_REFERENCE` is a *pointer*. It says where the current authoritative object lives and
  asserts nothing about its content, now or ever. It has **no body and no content digest**, and
  `supports_conclusion` is `False` for every one — pointing at an object that may have changed since
  is not evidence of what was true when the investigation ran;
* an `EVIDENCE_SNAPSHOT` is a *claim*. This exact representation existed, and here is the digest
  that proves the copy was not edited afterwards.

The two are kept apart **structurally**, not by convention:

1. `EvidenceGuardService.guard_mode_matches_content` refuses both directions at construction — a
   live reference carrying a body, and a snapshot carrying none;
2. the digest is **recomputed** from the canonical form by the factory and never accepted from the
   caller, so a body and its attestation cannot be written apart;
3. **the evidence store has no update path at all.** `EvidenceRepository` declares no `update`, no
   `replace` and no `delete`, so a capture's mode, body and digest are fixed for the life of the
   row. Without that, every guarantee above would hold only until somebody called a setter.

The caller never supplies a representation either: `CaptureEvidenceBody` has no `body` field.
**Evidence a caller could author is not evidence.**

---

## Evidence identity and canonicalization

An evidence id is **content-addressed**:

```
evd_ = sha256(org_id, domain, source_type, source_id, revision, canonical_digest(body))[:32]
```

Three consequences, each one a mandate requirement stated as an identity:

* the same immutable source captured twice converges on one row — two workers, or one worker
  restarted mid-capture, write once;
* a changed representation computes a **different** id, so the new capture is a *new link in the
  chain* rather than an overwrite of what was true before;
* **two organizations can never collide**, because the org is inside the key rather than only in a
  `WHERE` clause — even on byte-identical content.

There is **exactly one canonicalization algorithm** in this milestone. The snapshot digest, the
observation identity, the conclusion identity and the audit-package digest all reduce to
`canonical_form`. Two canonicalizers is how one piece of evidence comes to have two identities.

It is deterministic by construction:

* mappings emit in **sorted key order**;
* scalars are **type-tagged**, so `{"n": 1}`, `{"n": "1"}` and `{"n": true}` cannot share a digest;
* **absent and null are distinguishable** — *the provider returned no reference* and *we never
  asked* are different facts about an audit, and a canonicalizer that flattened them would let one
  be presented as the other;
* integers emit exactly; strings emit exactly, with no case folding, trimming or locale collation;
* enums emit as their `value`, so renaming a Python member does not silently re-identify historical
  evidence;
* sequences preserve order.

**`float` is refused, not coerced.** A digest that depends on how a float is formatted is stable on
the machine that wrote it and different on the machine that audits it. Timestamps reach a body
through `canonical_instant`, which converts to integer microseconds first — and a test walks an
entire exported manifest at every depth asserting not one float survives.

---

## Chain of custody

Nothing is ever replaced. A source becoming unavailable, redacted or superseded appends a **new
link** naming the one it follows via `previous_evidence_id`, so the trail answers *what did we hold
first, what did we hold later, what changed, and what did not* without depending on a backup.

Chains are keyed on the locator **without** the revision. That is deliberate: a document moving from
revision 1 to revision 2 is exactly the change an auditor wants to see *within* one chain, and
keying on the revision would file each version separately and lose the fact that anything moved.

`EvidenceAvailability` has four members, and the two that do **not** support a conclusion carry the
rule:

| | supports a conclusion | meaning |
|---|---|---|
| `AVAILABLE` | ✓ | retrieved and captured |
| `REDACTED` | ✓ | representation narrowed; the fact remains attested |
| `UNAVAILABLE` | ✗ | **could not be retrieved — and this never rounds to "verified"** |
| `SUPERSEDED` | ✗ | a newer capture of this source is the evidence; cite that one |

A gap names its source, its reason, **when it was last known to be retrievable**, and whether it
blocks a conclusion — the difference between an audit finding and a shrug.

Redaction keeps the original identity, digest, author and timestamp; the replacement is appended.
Note what redaction is *not* for here: because §30 forbids a card number, credential, token or
provider secret from ever entering an evidence body, there is nothing secret to remove after the
fact. It exists for over-broad detail.

---

## Closure is earned, not clicked

`INVESTIGATION_LIFECYCLE` has **no edge from any working state directly to `CLOSED`**:

```
OPEN → TRIAGED → INVESTIGATING ⇄ AWAITING_EVIDENCE
                       ↓
                  CONCLUDED ⇄ (resume)
                       ↓
                    CLOSED          ← no outgoing edge
```

The only route to closure runs through `CONCLUDED`. Paired with `INCONCLUSIVE` always being
available, that makes two mandate rules structural at once:

* an investigation **cannot be quietly shut with nothing on record**;
* **nobody is ever forced to invent a root cause** in order to close a case.

`CLOSED` has no outgoing edge, so a case is never reopened and never re-used as the container for a
later problem — a recurrence gets its own incident from M9.20 and its own investigation from here.

Closure criteria are **computed**, not asserted. `ClosureService.assess` returns the same
`ClosureAssessment` the `CloseInvestigation` command consults, so an operator sees exactly what is
outstanding *before* pressing anything. It blocks on: a required question with no outcome, a
required question **never asked** (otherwise a case could close by never raising the question an
examiner will ask), an unsettled hypothesis, unavailable evidence that blocks a conclusion, and a
missing conclusion.

**Closing the investigation is not resolving the incident.** M9.20 owns that, and a case may
legitimately close `INCONCLUSIVE` while its incident is still open — asserted by a test that reads
the incident's version before and after.

---

## Facts, hypotheses and conclusions

An `OBSERVED_FACT` must cite at least one capture that **actually supports it** — narrower than "was
cited", because a live reference attests to no content and an unavailable capture attests to
nothing. A `HYPOTHESIS` is held to no such bar; that is what makes it a hypothesis.

A hypothesis **cannot be promoted**. The observation identity includes the `kind`, so re-submitting
the same wording as a fact appends a second, separately-identified record and the trail shows both,
in order, with their authors. A hypothesis is settled once, by an appended verdict naming the
evidence — the statement itself is never rewritten, because *what did we believe, and what changed
our mind* is what an examiner asks.

Conclusions are append-only. A revision names what it `supersedes` and both stay readable, so an
auditor can see that the first answer was wrong and when it was corrected.

`CONFIRMED` requires an available snapshot. `INCONCLUSIVE` requires none. That asymmetry is the
whole point: requiring evidence to say *we could not determine this* would push an investigator
toward the one classification that does pass.

### The root-cause boundary

M9.20 owns root-cause classification. When an investigation's evidence contradicts it, M9.21 records
`CONTRADICTS_INCIDENT_CLASSIFICATION` — a **discrepancy addressed to the owning workflow**, carrying
both what M9.20 said at the time and what the evidence indicates. The incident is not touched: a
test reads its `root_cause` and `version` before and after and asserts both unchanged.

A contradiction must name an alternative (one that says only "M9.20 is wrong" is not actionable) and
may not name the cause the incident already carries (that would raise a discrepancy against a
classification that agrees).

---

## The audit package

Immutable and **content-addressed by its canonical manifest**:

* exporting an unchanged investigation twice yields the **same package id** and writes nothing;
* any change yields a different manifest, so a different digest, so a **new package beside the old
  one** — never a rewrite;
* `manifest_digest` is recomputable from `manifest` by anyone holding the response, which is what
  makes the export auditable rather than merely downloadable.

`PackageCompleteness` is decided on **availability, never on optimism**. An unreadable incident, an
unavailable source or an unresolved required question each make it `INCOMPLETE`, and every gap is
named rather than dropped — a package that quietly omitted what it could not fetch would present as
thorough exactly when it is least trustworthy.

The manifest holds **references and digests, never recomputed figures**. Nothing is summed, priced
or compared numerically.

**A package cannot contain the record of its own creation.** `AUDIT_PACKAGE_CREATED` is excluded
from the manifest timeline, because including it made every export change the thing it was
describing — see D-1. Exports remain visible on `/timeline`; what is excluded is only the
self-reference that would make determinism impossible to state.

---

## Sensitive evidence

M9.16's token-reference-only boundary holds through this layer. Evidence bodies are assembled by
`_EvidenceCollector` from the **safe identifiers the owning domain publishes**, never by copying
whatever an object happens to hold. No card number, CVV, bank credential, provider secret, API key
or authentication token can enter a model, a DTO, an event, the Studio or an audit package — a test
scans all five for the forbidden vocabulary with whole-word matching (`span` is not a PAN,
`secretary` is not a secret), and a second test scans the bodies the collector actually produces at
runtime rather than by inspection.

The one figure anywhere in this milestone is inside a captured body: a snapshot of an M9.19 finding
carries the integer minor units the owning domain wrote. That is **evidence of what that domain
said**, carried as a plain integer because reconstructing `Money` here would be M9.21 forming an
opinion about a figure it does not own. `Money` is not imported into the domain or the schemas at
all.

---

## Concurrency and scale

| Race | Arbitrator |
|---|---|
| two investigators open a case on one incident | derived id `(org, incident)` + primary key |
| two workers capture the same source | **content-addressing** — both compute the same id |
| a stale worker writes an investigation | compare-and-set on `version` → `InvestigationStale` |
| two conclusions submitted at once | append-only — **both survive**, ordered |
| a question answered twice | CAS + a refusal; the record shows one outcome, not two |
| two exports of the same state | content-addressed package id |
| a restart mid-flow | every identity is derived; replay lands on the same rows |

Every one is proven with **two Applications over two real database connections**, not a sequential
simulation.

Pagination is a database concern: `page()` issues `COUNT` + `LIMIT/OFFSET` against an index and
`counts()` issues `GROUP BY` aggregates. Nothing is filtered in Python after loading.

---

## Layers

| Layer | Artifact |
|---|---|
| Foundation | `commercial_investigations.py`, `investigation_rules.py`, `investigation_factories.py` |
| Application | `investigation_engine.py` — 16 commands, 14 queries |
| Persistence | `investigation_models.py`, `investigation_repositories.py`, migration **0031** (7 tables) |
| Customer API | `investigation_schemas.py`, `routers/investigation_engine.py` — **29 paths** at `/investigations` |
| Studio | `investigations.html` — 12 screens, self-contained (ADR-0021 Consumer) |

Seven tables: `cp_billing_investigation`, `_event`, `_evidence`, `_question`, `_observation`,
`_conclusion`, and `cp_billing_audit_package`. There is no separate hypothesis table (a hypothesis
is an observation with a kind) and no separate custody table (the chain is the evidence rows linked
by `previous_evidence_id`) — either would be a second copy of a fact these already hold.

No DELETE, PUT or PATCH exists anywhere on the surface. Redaction is a POST that appends.

---

## Limitations

1. **I implemented and self-reviewed this milestone.** Evidence work is a poor candidate for that:
   a package that silently omitted something looks exactly like a complete one.
2. **Required questions are a fixed set of three.** `WHY_DIVERGED`, `WHICH_FACTS_AFFECTED` and
   `REPAIR_EVIDENCE` are hard-coded rather than configurable per organization or per incident kind.
   A regulator with a different checklist cannot express it today.
3. **`_BODY_READERS` covers three source types.** Incidents, reconciliation findings and repair
   references render bodies; a ledger entry, invoice document, tax assessment, payment intent or
   revenue schedule captured directly resolves to `UNAVAILABLE`. That is honest — an unreadable
   source stays visible as one — but it means direct capture of those five is not yet useful, and
   the evidence chain is correspondingly shallower than the mandate's §16 illustration.
4. **Redaction cannot remove a secret that was never allowed in.** By design, but it means the
   feature is narrower than the word suggests: it narrows over-broad detail and does not defend
   against a body that should never have been captured.
5. **The denormalized counts are best-effort.** `_recount` swallows a lost compare-and-set on
   purpose — failing an evidence capture to protect a counter that can be recomputed would lose the
   evidence. Nothing an auditor reads depends on them, but a dashboard can briefly disagree with
   the rows.
6. **No cross-investigation search.** Finding *every case that cited this ledger entry* requires
   walking investigations one at a time.
7. **The manifest is JSON only.** No human-readable rendering exists; §21 defers that deliberately
   to a consumer of the immutable package.

---

## What this does not change

M9.19 remains the reconciliation owner. M9.20 remains the correlation owner and remains
authoritative for whether an incident exists, which findings belong to it, its severity, its
lifecycle and its root cause. ADR-0023 is **not amended** — PY5 is unchanged and now holds at a
third altitude: M9.19 made *a divergence is never auto-resolved* structural for one finding, M9.20
for a group of them, and M9.21 makes the investigation of one non-falsifiable.

Every M9.2–M9.20 module and table is untouched.
