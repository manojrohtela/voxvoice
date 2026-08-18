# M9.23 — Billing Period Scope & Consolidation Engine

**Status:** implemented · **Milestone:** M9.23 · **Decision log:** DL-0134
**Amends:** nothing. ADR-0023 **PY5** is unchanged and now holds at a fifth altitude.

---

## The question this milestone exists to answer

M9.22 shipped with a gap it named rather than hid: **every period-scoped read Billing publishes is
keyed by `(org, account, period)`**, because an accounting period is a property of an *account*. A
filing covering a group of accounts therefore had no authoritative period read to consume, and
M9.22 refused to invent one.

This milestone owns that gap:

> **Which accounts belong to a reporting scope, for which period, at which source revisions — and
> what could not be included, and why?**

Under one constraint that shapes everything else:

> **M9.23 owns scope. It does not own a single financial fact.**

A `ConsolidationRun` may aggregate integer minor units that M9.13 already decided. It may never
decide one — and it imports no repository that could.

---

## The audit

No consolidation aggregate existed: `ConsolidationRun`, `ScopeSnapshot`, `ScopeDefinition` and
`ConsolidationException` are all new names in a package with ~2,400 exports. What *did* exist is
the thing this milestone would otherwise have reinvented.

**M9.8 already owns the account hierarchy, completely.** `parent_account_id`, `payer_account_id`,
`AccountHierarchyResolver` (ancestors, descendants, subtree, `find_cycle`, `MAX_HIERARCHY_DEPTH`),
`PayerResolver` (chain, `find_loop`), and an indexed `children_of(org, parent)` read.
`ScopeResolutionService` takes both resolvers as constructor arguments and **calls** them. A test
asserts the class defines none of `ancestors`, `descendants`, `find_cycle`, `find_loop`, `chain`,
`root`, `depth`, `tree` or `expand` — a second graph walk is a second implementation of a bug.

**M9.22 already owns period vocabulary**, and `PeriodBasis` is imported unchanged. A sixth "kind of
period" would be the duplication §2 forbids. **M9.21's canonicalizer** is reused for every digest.
**M9.22's `SourceStatus` and `ReportTransformation`** are reused, with `CONSOLIDATION_AGGREGATIONS`
naming the three-member subset a consolidation may apply.

| Domain | Read through | Period-scoped? |
|---|---|---|
| Ledger (M9.13) | `entries.for_period(org, account, period)` | ✅ |
| Invoice (M9.14) | `documents.for_period(org, account, period)`, issued only | ✅ |
| Tax (M9.15) | — | ❌ reports the gap |
| Payment (M9.16) | — | ❌ reports the gap |
| Revenue (M9.18) | — | ❌ reports the gap |
| Reconciliation (M9.19) | `findings.for_account(org, account)` | ⚠️ account-bounded |
| Integrity (M9.20) | `incidents.live(org)` | operational queue read |

`SOURCE_CAPABILITIES` is that table as code — including the three entries that **cannot** answer,
recorded there rather than discovered at collection time, each naming the milestone that owns the
missing capability.

`period_expected` separates a **gap** from a **category**. The Ledger, the Invoice, Tax, Payment and
Revenue all describe things that happen *in* a period, so a domain that cannot answer for one has a
genuine hole and blocks. Reconciliation and Integrity are operational queue reads — "which
divergences are open right now" has no period and never had one — so their absence of a period read
is recorded on the binding (`period_applied: false`, `SOURCE_PARTIAL`) and raises an **advisory**
`SOURCE_INCOMPLETE` rather than a blocking `PERIOD_NOT_SUPPORTED`. Without that distinction, §16's
integrity boundary would be permanently unusable: a scope that surfaced the integrity record could
never finalize, which is not the same claim as "the record is unsettled".

---

## Ownership

| Question | Owner |
|---|---|
| who is this account, and who is its parent and payer? | **M9.8** |
| what is a balance, an invoice total, a tax, a payment, recognized revenue? | **M9.13–M9.18** |
| do two financial facts agree? | **M9.19** |
| which disagreements are the same problem? | **M9.20** |
| what did a human find out, on what evidence? | **M9.21** |
| what must be reported, for which period, from which sources? | **M9.22** |
| which accounts does a consolidated view cover, and what did it miss? | **M9.23** |

**M9.23 has no outward command at all.** `dispatch` is not injected. A consolidation is a derived
view of facts other domains own — it cannot ask one of them to change so the total comes out
rounder. `ScopeAccountReader` exposes exactly `get`, `children_of` and `all`: no `add`, no
`replace`, no `assign_parent`, so this milestone is *structurally* unable to change the hierarchy
it consumes.

---

## Scope is a snapshot

The central guarantee. Account A belonged to parent P in January and moved to parent Q in March;
January's consolidation must continue to represent January's scope forever.

That is not achieved by a comment. `ScopeSnapshot` has **no update path anywhere** in the
persistence layer, and its identity is a digest over its own membership:

```
snapshot id = f(org, definition, version, membership digest)
```

Two consequences, and they are §4's guarantees stated as an identity:

* resolving an unchanged hierarchy twice produces **one** snapshot — a retry does not mint a second
  view of January;
* an account that moved produces a **different** snapshot beside the old one, never an edit.

Each `ScopeMember` records `account_revision` — the account's `updated_at` plus its parent and payer
edges as they stood. A test moves an account's payer and demonstrates that the new resolution has a
different id while the stored January snapshot re-reads byte-identical.

---

## Five modes, each with proven semantics

`SCOPE_RULES` declares every mode's inclusion rule, exclusion rule, attribution requirement,
hierarchy graph and currency requirement as **data**, so "what does `PAYER_GROUP` require?" is a
lookup an auditor can read rather than conditionals traced through a resolver. The resolver
dispatches with **no default branch**: an unhandled mode raises `UnsupportedConsolidationRule`
rather than quietly resolving as `SINGLE_ACCOUNT`.

`PAYER_GROUP` and `PARENT_GROUP` are deliberately different modes walking different graphs, because
M9.8 keeps the payer graph and the hierarchy graph separate precisely so a subsidiary can pay for a
parent it does not own.

---

## The attribution ceiling, held rather than papered over

A source fact belongs to exactly one member of the scope, or it belongs to none. There is no third
answer and **no tie-break**: a test scans every file for `first_active`, `newest_account`,
`largest_amount`, `or_payer` and `fallback_account` and finds none, because there is no correct way
to pick.

`ScopeAttributionService.classify` separates two causes that look identical in a total: a fact with
no account at all is `UNATTRIBUTABLE_SOURCE` (M9.8's ceiling); a fact whose account is real but
outside the scope is `ACCOUNT_NOT_IN_SCOPE` (a scope question). Neither is consolidated, neither is
silently dropped, and neither is moved.

---

## A gap never becomes a zero

Enforced independently in four places:

1. `ConsolidationFactory.binding` **discards** an amount offered alongside a non-resolved status —
   so no code path anywhere can produce a gap carrying a number;
2. `ConsolidationAggregationService.aggregate` skips bindings with `amount_minor is None`, so there
   is no value that could be read as nought;
3. `ConsolidationExceptionService` raises a **named** exception — a test asserts no
   `ConsolidationExceptionKind` member is called `FAILED`, `ERROR`, `UNKNOWN`, `OTHER` or
   `GENERIC`, and that a kind raised with an empty diagnostic is refused;
4. the wire and the Studio carry `amount_minor: null` and render the status and the reason.

`SourceStatus` keeps `EMPTY` (the domain answered and had nothing — a legitimate zero) distinct from
`UNAVAILABLE` (we could not look — a gap). `ConsolidationCompleteness` has four values, and none of
them is a figure.

---

## Currency is never combined implicitly

`ScopeCurrencyMode` has **no conversion member**. `CURRENCY_SEPARATED` keeps one aggregate per
currency; `SINGLE_DECLARED` excludes an account billing in another currency, with the reason
recorded on the snapshot. There is no FX rate in this milestone — not from a provider, not from a
table, not from a constant.

`_sum_minor` is the only function that adds money, and it **refuses a mixed-currency input rather
than converting it**. An empty aggregate raises rather than returning zero. An AST test asserts no
other function in the module sums a `.minor`, and a scan asserts no `Money.times`, `allocate`,
`percent`, `apply_rate` or `float(` appears anywhere in the milestone.

---

## Finalization cannot skip its checks

```
DRAFT → SCOPING → COLLECTING → VALIDATING ─pass→ READY → FINALIZED
                       ↑              └─fail→ BLOCKED ─┐
                       └──────────── recollect ────────┘
```

**`FINALIZED` is reachable only from `READY`, and `READY` only from a validation that passed.**
`BLOCKED`'s only outgoing edge returns to `COLLECTING` — a blocked run re-collects; it does not talk
its way to green. Tests enumerate every edge into both states to prove it.

**Acknowledging an exception does not unblock a consolidation.**
`ConsolidationExceptionService.blocking` deliberately does not filter acknowledged exceptions out,
and `blocking_count` counts them: somebody having seen a gap is a different fact from the gap being
closed. `is_blocking` stays `true` on the wire after acknowledgement, and a test finalizes against
an acknowledged blocking exception and is refused.

`FINALIZED` has no outgoing edge. A correction opens a **new run beside the original**; the original
keeps every figure, its digest, its result and its finalized state, gaining only a back-reference. A
correction whose scope and source revisions are both unmoved is refused — it would compute the
identity of a consolidation that already exists.

---

## The two identities, and why there are two

`ConsolidationRun.id` derives from the immutable **request**: `(org, scope snapshot, period,
corrects)`. Two workers opening the same consolidation land on one row — a duplicate consolidation
is not merely rejected, it is inexpressible.

`ConsolidationSnapshot.id` derives from the finalized **artifact**: the run plus a digest over every
source revision that was pinned. Source revisions cannot be part of the run's identity because they
are not known until the run has read them — a run must exist in order to collect. So the revisions
bind where they *can* be known, and the rule §10 protects still holds end-to-end: a finalized
consolidation is content-addressed on the exact revisions it consumed, a later consolidation over
moved sources is a **correction with a different run id**, and finalization is refused outright if
the source digest moved between validation and finalize.

The source digest covers `status` as well as `source_revision`, so a source that went `RESOLVED` →
`UNAVAILABLE` changes the digest even though no revision string moved.

---

## Scale

`cp_billing_scope_member` and `cp_billing_consolidation_source` are separate tables, not JSON
columns. §25 requires this to work at 100K accounts and 1M+ facts, and a membership inside one blob
has to be loaded whole to show ten rows.

`member_ids()` is a **keyset** read — `WHERE account_id > cursor ORDER BY account_id LIMIT n` — so
batched collection stays an indexed range scan at any depth. `CollectConsolidationSources` binds one
bounded batch and advances `collection_cursor`, which makes a restart resumable rather than a
re-scan. Two workers racing on one batch write the same derived binding ids, so the race is a no-op
rather than a double count.

The anchored modes walk M9.8's indexed `children_of` per parent, so a hundred-account reseller tree
inside a hundred-thousand-account organization costs a hundred indexed reads rather than a full
scan.

---

## Layers

| Layer | Artifact |
|---|---|
| Foundation | `commercial_consolidation.py`, `consolidation_rules.py`, `consolidation_factories.py` |
| Application | `consolidation_engine.py` — 11 commands, 14 queries |
| Persistence | `consolidation_models.py`, `consolidation_repositories.py`, migration **0033** (10 tables) |
| Customer API | `consolidation_schemas.py`, `routers/consolidation_engine.py` — **22 paths** at `/consolidations` |
| Studio | `consolidations.html` — 13 screens, self-contained (ADR-0021 Consumer) |

No DELETE, PUT or PATCH exists anywhere on the surface. Seven new `BillingPermission` members,
deny-by-default, with `scope.manage` separated from `generate` because deciding *which accounts a
regulator will be told about* is a strictly larger power than running a consolidation over a scope
somebody else defined.

---

## Limitations

1. **I implemented and self-reviewed this milestone.** The defect that mattered most here
   (`period_expected`) was found by re-reading my own capability table after the suite was green,
   not by any test I had written — which is the honest measure of how much the 121 tests prove. A consolidated financial figure that is
   subtly wrong looks exactly like one that is right until somebody reconciles it by hand.
2. **Three of seven source domains cannot answer for a period at all.** M9.15 (tax), M9.16
   (payment) and M9.18 (revenue) publish no period-scoped read, so a scope requiring any of them
   comes back `BLOCKED` with named gaps. That is honest, and it is the single biggest functional
   gap: a ledger-and-invoice consolidation works; one that must include tax or payments does not.
   Closing it requires period-scoped reads from those milestones, which belong to them.
3. **`MAX_SCOPE_MEMBERS` is a ceiling, not a solution.** `ORGANIZATION` mode resolves through
   `accounts.all(org_id)` — one org-scoped indexed read with no Python filter after it, but an
   unbounded result set. Beyond 10,000 accounts the resolution raises `SCOPE_TOO_LARGE`. The
   paginated account read that would remove the ceiling belongs to **M9.8**.
4. **`PAYER_GROUP` resolution reads the organization.** M9.8 publishes an indexed `children_of` for
   the hierarchy graph but no reverse read for the payer graph, so a payer group is resolved by
   walking every account's payer chain. Correct, and bounded by the ceiling above, but not indexed.
5. **The reconciliation and integrity reads are not period-scoped**, so their bindings carry
   `period_applied: false` and `SOURCE_PARTIAL`. The wire says so rather than implying otherwise,
   but a period-scoped integrity read would be more precise.
6. **The run identity does not include source revisions**, for the reason argued above. A reader who
   expects §10 read absolutely literally should read that section rather than assume.
7. **No consolidation scheduler.** Whoever decides that a consolidation is due lives outside this
   plane.

---

## What this does not change

Every earlier milestone keeps its ownership. ADR-0023 is **not amended** — PY5 is unchanged and now
holds at a fifth altitude: M9.19 made *a divergence is never auto-resolved* structural for one
finding, M9.20 for a group, M9.21 for the investigation of one, M9.22 for the filing, and M9.23 for
the **scope** — a consolidation reaches `finalized` only from a validation that passed, and a
missing account is never consolidated as zero.

Every M9.2–M9.22 table is untouched.
