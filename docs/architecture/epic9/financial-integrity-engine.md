# M9.20 — Financial Integrity Correlation & Audit Engine

**Status:** implemented · **Milestone:** M9.20 · **Decision log:** DL-0131
**Amends:** nothing. ADR-0023 **PY5** is unchanged and now holds at a second altitude.

---

## The question this milestone exists to answer

M9.19 shipped with a stated limitation:

> at real volume, one root cause generates findings across several relationships, and nothing
> groups them.

M9.20 closes exactly that gap, and answers a different question from its predecessor:

> **Which reconciliation findings belong to the same financial integrity incident, what is the
> common evidence, what has happened to that incident over time, and can an auditor reconstruct the
> complete chain?**

Under one constraint that shapes everything else:

> **Correlation is an evidence relationship. It never manufactures a root cause.**

The altitudes are strict. M9.19 asks *do these two facts agree?* M9.20 asks *which of those
disagreements are the same problem?* Reconciliation ownership does not move.

---

## The audit

The pre-implementation sweep across M9.2–M9.19 found **no existing incident or correlation
aggregate** — the concept is genuinely new. Six artifacts were inspected and **none was changed**.

| Existing artifact | Owner | Meaning | M9.20 action | Reason |
|---|---|---|---|---|
| `IntegrityFinding` | M9.19 | one divergence between two facts | **read-only input** | never written; a test asserts no M9.19 repository is imported |
| `IntegrityRun` | M9.19 | one evaluation, keyed by evidence digest | **read-only** | supplies the scope the document trace starts from |
| `IntegrityResolution` | M9.19 | evidence one *finding* was addressed | **read-only** | incident resolution is a separate, stronger claim |
| `IntegrityActivity` | M9.19 | audit trail of one run | untouched | M9.20 keeps its own trail at incident altitude |
| `BillingEvent.correlation_id` | M9.2 | **transport trace id** | **not reused** | see below |
| `derive_correction_id` | M9.11 | a *usage* correction id | **not reused** | see D-2 |

**`correlation_id` already means "which HTTP request produced this".** Reusing it for "which
financial cause produced this" would give one field two meanings, and the next reader would join on
it. M9.20 declares `CorrelationKey`, a hash over immutable financial identifiers with nothing to do
with a request.

---

## Correlation identity: extracted, never inferred

Two findings correlate when their **highest-precedence resolvable anchor is equal**. Each resolver
reads identifiers the *owning* domain already made immutable and returns nothing when they are
absent — it never falls back within a level and never guesses.

| # | Anchor | Composed from | Why it is the strongest available |
|---|---|---|---|
| 1 | `LEDGER_SOURCE` | the journal entry's `(source, source_id)` | the cause **the Ledger itself recorded**; two findings naming it *are* about one fact |
| 2 | `DOCUMENT` | document id + **issued snapshot digest** | traced through M9.14/M9.16/M9.18's own facts; the revision matters, because a revised document is a different artifact |
| 3 | `FINANCIAL_FACT` | the source fact's `(kind, identity)` | a payment attempt, a recognition entry, a tax assessment |
| 4 | `NONE` | — | a **singleton** incident keyed on the finding's own id, cause `UNKNOWN` |

The correlation key is `hash(org, anchor kind, anchor value)` — **org-first**, so two organizations
sharing an invoice number, a payment operation id or a ledger source id can never land on the same
key. Nothing volatile enters: no timestamp, no status, no balance, no `latest()`, no provider text.

### The document trace

Two anchors are *traced* rather than read directly, and both traces are reads of the owning domain's
own immutable facts:

* a recognition finding names a schedule; **M9.18's schedule names its document** and carries the
  issued snapshot digest;
* a payment finding names an attempt; **M9.16's intent names the obligation it collects against**.

When a trace fails the field stays empty and the anchor falls to the next level. A document id
nobody can prove is exactly the invented fact this milestone forbids.

### Why precedence is conservative

Findings resolving at *different* levels do **not** correlate. That under-correlates rather than
over-correlates, and the asymmetry is the right one: an incident that should have been one and is
two is a reporting inconvenience, while an incident that should have been two and is one has merged
two unrelated financial problems and will close both when somebody fixes one.

### What correlates nothing

Amount. Timestamp. Account. Invoice number. Description. Customer. Payment method. State. Period.
**Alone or together** — a test constructs two findings sharing all six and asserts they do not
correlate. None of them appears in a key, because none of them identifies a financial fact.

---

## The evidence graph

Modelled as a graph rather than a flat list. A flat list says *these five things are wrong*; a graph
says *because they all name this one ledger entry* — the difference between a queue and an
explanation.

Edges: `SAME_SOURCE`, `SAME_DOCUMENT`, `SAME_TRANSACTION`, `SAME_FINANCIAL_CAUSE`, `SUPERSEDES`,
`RESOLVES`. **There is no `RELATED` edge.** A link created "for UI grouping" is a claim about the
books nobody can check, and on screen it would be indistinguishable from one that was proved. Every
edge carries the anchor that justified it; `EDGE_BY_ANCHOR` is the only source of edges.

---

## Root cause and impact: classified, never calculated

`RootCause` has ten members and **no infrastructure causes**. There is no `DATABASE_FAILURE`, no
`NETWORK_FAILURE`, no `PROVIDER_FAILURE`, because Billing has no evidence for any of them — a
missing ledger entry looks identical whether the database rejected it, the process died, or nobody
ever tried. Naming a cause we cannot prove is worse than `UNKNOWN`, because somebody downstream will
believe it and stop investigating.

Classification is by table (`ROOT_CAUSE_BY_DIVERGENCE`), and **conflicting divergences classify as
`UNKNOWN`** rather than as the first, the commonest or the worst. An incident whose findings point
at two different causes has not been explained, and saying so is the honest output.

Impact is likewise a table (`IMPACT_BY_ROOT_CAUSE`) over six classifications. **There is no
subtraction anywhere on this path** — an AST scan asserts `RootCauseService` contains no `BinOp` at
all. `expected − actual` is arithmetic the owning domain owns.

---

## Severity is monotonic

Incident severity is separate from finding severity because they answer different questions: a
finding's severity is how bad *that disagreement* is; an incident's is how bad the *situation* is.

`SeverityService.aggregate` returns `max(current, worst floor of any attached finding)`. Two rules,
both load-bearing:

* **resolved findings still count** — a critical divergence that has been repaired still means a
  critical thing happened;
* **the result never decreases** — an incident cannot slip off a dashboard because one of its three
  findings was fixed.

The mandate's rule — *an incident must not become less severe merely because one finding is resolved
while another remains unresolved* — is made impossible rather than remembered. No lifecycle
transition touches the field, and a test asserts `apply` never writes `severity`.

---

## Lifecycle: repair is never resolution

```
   OPEN ──acknowledge──▶ ACKNOWLEDGED ──investigate──▶ INVESTIGATING
                                                        │        │
                                          request_repair │        │ verify
                                                        ▼        ▼
                                            REPAIR_IN_PROGRESS  VERIFYING ──resolve──▶ RESOLVED
                                                        │        │  ▲                  (terminal)
                                                 verify └────────┘  │
                                                                    └── investigate (verification failed)

   every live state ──close──▶ CLOSED (terminal)   ·   every live state ──merge──▶ MERGED (terminal)
```

Read it by what is missing:

* **no `REPAIR_IN_PROGRESS → RESOLVED`.** Requesting a repair is not evidence the repair happened.
  The only path to `RESOLVED` runs through `VERIFYING`, which re-reads the books. **Repair never
  equals resolution**, and here that is an absent edge rather than a comment;
* **no edge out of `RESOLVED`, `CLOSED` or `MERGED`.** A stale worker cannot reopen a settled
  incident, because the machine has nowhere to put it — the same structural device M9.19 used;
* **`VERIFYING → INVESTIGATING` exists**, because verification can legitimately fail and the
  incident must go back rather than sit in a state that implies progress.

`RESOLVED` and `CLOSED` are deliberately separate: resolved means the books agree; closed means
somebody decided to live with it.

### Resolution requires fresh evidence

`ResolutionGuardService.guard_verified` refuses three ways, each a way "it's fine now" is usually
wrong:

1. **nothing was checked** — an incident with no attached findings has been skipped, not verified;
2. **something is still divergent** — the obvious one;
3. **something was unreadable** — the subtle one. A finding that could not be re-read is not
   evidence of agreement, and treating "I could not check" as "it is fine" is how an unrepaired
   incident gets closed. **Partial resolution never masquerades as complete.**

---

## History is never rewritten

Every mutation of *meaning* is an append:

* **merge** — the merged incident moves to `MERGED` and gains a `canonical_id`. Its severity, its
  findings and its whole history stay exactly as they were and remain readable forever. A
  destructive merge would destroy the answer to *what did you believe in March?*;
* **correlation correction** — the old link is **detached, never deleted**, and a
  `CorrelationCorrection` row records the previous key, the corrected key, the reason and the actor.
  A correction that would change nothing is **refused** rather than silently detaching the finding
  from everything (see D-1);
* **audit trail** — `cp_billing_integrity_event` is append-only with a unique
  `(org, incident, sequence)`, and `next_sequence` reads a stored count rather than a derived
  maximum.

Current status is a **projection over the event history**, not the authority.

---

## Persistence

Migration **0030**, six additive `cp_billing_*` tables. Nothing in M9.2–M9.19 is altered.

| Table | Purpose | Write surface |
|---|---|---|
| `cp_billing_integrity_incident` | the aggregate; PK **derived from the correlation key** | insert + **conditional lifecycle UPDATE on `version`** |
| `cp_billing_integrity_finding_link` | which findings belong, **and the anchor that proved it** | insert + detach (never delete) |
| `cp_billing_integrity_event` | the audit trail, totally ordered per incident | **append-only** |
| `cp_billing_integrity_merge` | merge decisions | **append-only** |
| `cp_billing_integrity_correction` | correlation corrections | **append-only** |
| `cp_billing_integrity_repair` | what was asked of which owning domain | insert + observation flag |

**No balance, no total, no amount, no currency** — not one column, not one payload field. A test
iterates every column of every table to assert it. Every figure lives on M9.19's finding, reachable
by `finding_id`, where the domain that decided it put it.

There is deliberately **no separate correlation table**: the key lives on the incident, the anchor
on the link, and a third copy is how the three eventually disagree. The evidence *graph* is derived
on read, for the same reason M9.13 derives balances and M9.18 derives deferred revenue.

Indexes: `(org, status, severity)` for the queue, `(org, first_detected_at, id)` for pagination,
`(org, account, status)`, and `(org, finding, attached_at)` for "which incidents has this finding
ever belonged to". `page()` issues a `COUNT` and a `LIMIT/OFFSET` against an index — at this
milestone's assumed scale an in-memory filter is not a performance detail but an outage.

---

## Concurrency

`SqlIncidentRepository.advance` is `UPDATE ... WHERE version = ?`. Worker A reads version 3, worker
B acknowledges to version 4, worker A resumes: zero rows match, `IncidentStale` is raised rather
than a conclusion silently overwritten. Proven with two Applications over one file-backed database,
each with its own connection — two *connections*, not two threads, because a thread schedule that
does not interleave produces a green test that proved nothing.

Duplicate incidents and duplicate attachments are structurally impossible: both ids are derived, and
both tables carry a matching unique constraint.

---

## Surface

**25 paths at `/integrity/incidents`**, sharing M9.19's prefix. That is deliberate rather than
convenient: correlation and reconciliation are one bounded context — a finding and the incident that
groups it are the same subject at two altitudes — and splitting them would make the client join two
trees for one screen.

The safety requirement is **path disjointness, not prefix disjointness**, and a test asserts the two
routers share no path (25 each, intersection empty). `posture`, `live` and `search` are declared
before `{incident_id}` so a literal segment is never swallowed.

**No DELETE, PUT or PATCH anywhere.** Historical financial evidence is not mutated.

`GET /integrity/incidents/live` is the read an external scheduler polls. **Billing runs no
scheduler.**

Studio (`/studio/incidents.html`) is a Consumer under ADR-0021: it correlates nothing, computes no
severity or root cause, sums nothing, and resolves nothing locally. Buttons come from
`allowed_triggers`. Detached links are shown rather than hidden.

---

## Defects found and fixed

**D-1 — a no-op correction silently orphaned the finding.**
`CorrectCorrelation` detached the link, then re-correlated; when the evidence still pointed at the
same incident, `attach` found the now-detached link by its derived id and returned it unchanged —
leaving the finding attached to nothing. Fixed two ways: the correction **re-derives the key before
detaching** and refuses when nothing would change, and `attach` re-attaches a detached link rather
than returning it (a finding legitimately detached from A can correlate back to A once evidence
moves). The detach is not erased; it is already an immutable line in the trail.

**D-2 — `derive_correction_id` shadowed M9.11's usage-correction identity.**
M9.11 has owned that name since metering shipped, for a *usage* correction. Two functions with one
name in the package namespace is precisely the duplicate-vocabulary failure the mandate forbids, and
the second import silently won. Renamed to `derive_correlation_correction_id`; M9.11's is untouched
and both are now exported distinctly. Caught by ruff's `F811`, which is why the lint gate is part of
the definition of done rather than a formatting preference.

---

## Limitations

1. **Correlation is conservative by construction.** Findings anchored at different precedence levels
   do not correlate even when a human would group them. Merge is the declared remedy.
2. **No automatic merge suggestion.** Discovering that two incidents share a cause is an operator
   judgement; nothing proposes candidates.
3. **The document trace depends on wired domains.** With M9.16 or M9.18 unwired the anchor falls to
   `FINANCIAL_FACT`, which correlates less. Honest, but it means correlation quality varies with
   deployment shape.
4. **`search` filters a page in Python** after a database-paginated fetch; status/severity/account
   are pushed down but root cause, impact and owner are not. Correct, but the total reflects the
   pushed-down filters only.
5. **No cross-org aggregate view** for a platform operator — by design, and it means there is no
   fleet-wide integrity dashboard.
6. **`RootCause.UNKNOWN` is common** in mixed incidents. That is honest, not a gap, but it limits
   automated triage.
7. **No incident-level SLA or ageing policy.** Nothing escalates an incident because it is old.

---

## Architecture compliance

* §A12 additive extension — `IntegrityIncident` lands beside `IntegrityFinding`.
* §A11.2 org-scoped by construction — the correlation key is org-first, before any `WHERE` clause.
* §A2.6 fail fast — structural validation raises `BillingValidationError` at the factory.
* §A5 durable outbox — `billing.incident.*` facts commit with the incident that produced them.
* ADR-0021 — Studio is a Consumer.
* ADR-0023 **PY5** — unchanged, and now structural at incident altitude: no edge out of `RESOLVED`,
  no edge from repair to resolution, and resolution refuses without fresh evidence.
* ADR-0023 **RS2/RS6** — untouched. Correlation holds no bookkeeping of its own.
