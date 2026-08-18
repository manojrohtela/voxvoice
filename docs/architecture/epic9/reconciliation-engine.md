# M9.19 — Billing Reconciliation & Financial Integrity Engine

**Status:** implemented · **Milestone:** M9.19 · **Decision log:** DL-0130
**Amends:** nothing. ADR-0023 **PY5** is unchanged and now has a durable home.

---

## The question this milestone exists to answer

Every financial domain in Epic 9 already answers its own question. M9.13 says what was recorded,
M9.14 what was presented, M9.15 what was determined, M9.16 what moved, M9.17 what was asked for,
M9.18 what was earned. Each is internally consistent by construction.

M9.19 asks the one question none of them can ask about itself:

> **Do the financial facts produced by the Billing domains agree with one another, and if they do
> not, exactly where and why do they diverge?**

And it answers it under one constraint that shapes everything else:

> **A reconciliation result is an observation. It is not permission to mutate another domain.**

---

## The audit that shaped the vocabulary

Reconciliation was not new here, and most of it was correct. The pre-implementation audit found six
existing artifacts and **changed none of them**.

| Existing artifact | Owner | Current meaning | M9.19 action | Reason |
|---|---|---|---|---|
| `ReconciliationVerdict` | M9.2 | four-member payment/ledger verdict | **reused**, projected onto | correct and already consumed by M9.16 |
| `ReconciliationFinding` | M9.2 | one payment↔provider observation | **untouched** | hence `IntegrityFinding` here — shadowing would give one name two meanings |
| `PaymentReconciliation`, `ReconciliationOutcome`, `ReconciliationStatus` | M9.16 | Billing ↔ **provider** disagreement, persisted | **untouched** | M9.19 never reconciles a provider; payment↔provider is not in the matrix |
| `LedgerReconciliation` | M9.13 | ledger ↔ rated charges, an ephemeral read | **untouched** | M9.13's own read; M9.19 adds no second opinion |
| `InvoiceReconciliation` | M9.14 | document ↔ ledger, an ephemeral read | **delegated to** | M9.14 owns the comparison; M9.19 adds durability |
| `TaxReconciliation` | M9.15 | assessment ↔ its subject, an ephemeral read | **delegated to** | same |

Two conclusions came out of it, and both are load-bearing.

**The existing reads were ephemeral; nothing was durable.** They can tell you the books disagree
*today*. They cannot tell you the books disagreed in March, who was told, what was done about it, or
whether it ever recurred. A divergence that leaves no trace is a divergence nobody is accountable
for. M9.19 adds the durable triad — **run, finding, resolution** — and reuses those comparison
services rather than writing a second opinion about the same pair.

**The names had to change, and that is a feature.** `IntegrityFinding` is never confused with M9.2's
`ReconciliationFinding` because they are spelled differently, and
`DIVERGENCE_VERDICT_PROJECTION` states the relationship between them as a table rather than leaving
it to be inferred.

### Why `DivergenceKind` is not `ReconciliationOutcome`

M9.16's enum is provider vocabulary: `MISSING_IN_PROVIDER`, `UNKNOWN_REFERENCE`, `STALE_OPERATION`,
`DUPLICATE_EVENT`. Those words are precise about an external integration and meaningless about
whether an invoice agrees with a recognition schedule. Reusing `MISSING_IN_PROVIDER` to mean "the
ledger has no entry" would be a lie told by a type name, and the next reader would believe it.

`DivergenceKind` is therefore declared in *relationship-neutral* language — **source and
counterpart, never provider and Billing** — and every member projects onto M9.2's four-member
verdict, so nothing downstream needs a second alphabet.

---

## The reconciliation matrix

Six relationships, and the list is closed. **Payment ↔ Provider is deliberately absent**: M9.16 owns
it, and reconciling a provider requires provider evidence M9.19 has no business holding. Two owners
for one pair is how a divergence gets resolved twice and reported once.

The matrix is a **table in code** (`RECONCILIATION_MATRIX`), not six hand-written comparison
functions. Six functions drift in six different ways and no test can iterate them; one engine
parameterised by a spec is checkable by iteration, which is what the completeness, single-owner and
no-matching-on-amount tests do.

| # | Source | Counterpart | Authoritative | Matching identity | Compares | Mode | Owner |
|---|---|---|---|---|---|---|---|
| 1 | payment attempt | journal entry | source | attempt id, carried as ledger `source_id` | amount, currency, account, source reference | pairwise | payment |
| 2 | journal entry | invoice line | source | journal entry id, carried on the line | amount, currency | pairwise | invoice |
| 3 | tax assessment | invoice tax line | source | assessment id + component | amount, currency | pairwise | invoice |
| 4 | invoice snapshot | revenue schedule | source | issued snapshot digest | amount, currency | **aggregate** | recognition |
| 5 | payment intent | commercial invoice | **counterpart** | document id on the intent obligation | currency, account | pairwise | payment |
| 6 | recognition entry | journal entry | source | entry id, carried as ledger `source_id` | amount, currency, account, period | pairwise | recognition |

Two flags carry domain judgements that would otherwise hide inside comparison code:

* **`allows_partial`** is true only for #4. A twelve-month invoice that has recognized three months
  is *correct* — the other nine are deferred revenue, which is the whole point of M9.18. Requiring
  equality would report every service-period invoice as broken for eleven months of its life. Only
  recognizing **more** than was billed diverges (`OVER_ACCOUNTED`);
* **`flags_orphans`** is false only for #5. An invoice with no payment is an *unpaid invoice* — an
  entirely normal state M9.17 already has opinions about. Reporting it here as an integrity error
  would give one situation two owners and two queues.

---

## Severity is a table, not decoration

`SEVERITY_BY_KIND` is the whole definition, and a test iterates it to prove no divergence class is
missing. `INFO` is what a *match* scores; **no divergence is ever informational.**

| Severity | Divergence classes | Why |
|---|---|---|
| **CRITICAL** | `MISSING_COUNTERPART`, `MISSING_SOURCE`, `AMOUNT_MISMATCH`, `CURRENCY_MISMATCH`, `DUPLICATE_SOURCE`, `DUPLICATE_COUNTERPART`, `OVER_ACCOUNTED` | the mandate's floor: a fabricated financial fact, a duplicate financial posting and an amount mismatch are never informational |
| **ERROR** | `ACCOUNT_MISMATCH`, `SOURCE_REFERENCE_MISMATCH`, `UNEXPECTED_COUNTERPART`, `EVIDENCE_MALFORMED` | wrong, but not a figure that reaches a statement |
| **WARNING** | `PERIOD_MISMATCH`, `AMBIGUOUS`, `STALE_EVIDENCE` | see below |

The three warnings each earn it:

* **`PERIOD_MISMATCH`** — M9.13's `LateEntryDisposition` makes it legitimate: a fact recorded while
  its period was closed lands in the next open one *by design*. Flagging correct accounting as an
  error teaches operators to ignore the report;
* **`AMBIGUOUS`** — M9.16's lesson carried forward. An honest unknown is not a known wrong, and
  escalating unknowns to critical trains people to ignore critical;
* **`STALE_EVIDENCE`** — the fix is to re-run, not to repair anything.

Severity is written **once at detection** and no code path changes it. A resolution records that a
critical divergence was fixed; it does not make it retroactively a warning. `FindingLifecycleService.apply`
never touches the field, and a test asserts the absence.

---

## Identity: a run is what it looked at

A run's id is derived from `(org, relationship, scope, evidence digest)` — the M9.18 basis-digest
lesson applied to comparison. The digest covers exactly the facts that were read and **nothing
volatile**: no observation time, no run id, no actor. Three consequences fall out of that one
choice:

* two workers reconciling the same facts compute the same id and one loses the insert;
* **re-running over unchanged evidence is idempotent** — same run, same findings, nothing written;
* re-running *after a repair* has different evidence, so it is genuinely a new run. That is what
  makes "reconciliation observes the result later" a real second observation rather than a rewrite
  of the first.

A finding's id is derived from `(run, relationship, subject, divergence)`, so one run records one
divergence once no matter how many workers detect it — and a recurrence in a later run is a
different id, which is precisely why a resolved finding never needs to be reopened.

**Matching never uses an amount, a timestamp, an account, a document number or a description
alone.** `ReconciliationValidator.validate_fact` refuses an identity that is empty or a bare number
at construction, rather than letting it produce a confidently wrong pairing later — the ₹500 refund
paired with an unrelated ₹500 capture.

---

## Lifecycle: PY5 as an absent edge

```
                ┌──────────────┐
                │     OPEN     │
                └──────┬───────┘
        acknowledge ───┼─── request_repair ─── resolve ─── close
                       ▼
              ┌────────────────┐   request_repair   ┌───────────────────┐
              │  ACKNOWLEDGED  │ ─────────────────▶ │ REPAIR_REQUESTED  │
              └───────┬────────┘                    └─────────┬─────────┘
                      │                                       │
              resolve │ close                         resolve │ close
                      ▼                                       ▼
              ┌───────────────┐                     ┌──────────────┐
              │   RESOLVED    │                     │    CLOSED    │
              └───────────────┘                     └──────────────┘
                  (terminal)                           (terminal)
```

Read it by what is missing:

* **no edge out of `RESOLVED` and none out of `CLOSED`.** ADR-0023 PY5 says a divergence is never
  auto-resolved; the corollary nobody writes down is that a *resolved* one is never auto-un-resolved
  either. A stale worker holding an `OPEN` read cannot reopen a finding another worker resolved,
  because the machine has nowhere to put it — a structural guarantee rather than an
  `if state == RESOLVED: raise` somebody can delete;
* **no `MATCHED` state**, because a match is not a finding. It is a counter on the run. Making
  agreement a row would fill the table with non-events and make "a finding represents one
  divergence" false;
* **no self-loop on `REPAIR_REQUESTED`.** Asking twice is one request — the derived repair-request
  id collapses it — not a second transition that would read as two separate incidents;
* **`OPEN → RESOLVE` exists directly**, because a divergence can be resolved by evidence that
  arrives without anyone asking for a repair. An in-flight posting that lands a second later is
  exactly that, and forcing it through `REPAIR_REQUESTED` would fabricate a repair nobody performed.

`RESOLVED` and `CLOSED` are deliberately separate: **resolved means the books agree; closed means
somebody decided to live with it.** Collapsing them would let "we gave up" be reported as "we fixed
it".

---

## The repair boundary

`REPAIR_COMMANDS` is the whole repair surface — **two entries**, both the owning domain's own
declared repair command, both dispatched through that domain's Application boundary onto the shared
command bus:

| Relationship + divergence | Owner | Dispatched |
|---|---|---|
| recognition↔ledger, `MISSING_COUNTERPART` | recognition | M9.18 `PostRecognitionToLedger` |
| payment↔ledger, `MISSING_COUNTERPART` / `AMOUNT_MISMATCH` / `STALE_EVIDENCE` | payment | M9.16 `ReconcilePaymentAttempt` |

**`PostJournalEntry` is absent from the table and from this milestone's imports, and its absence is
the point**: a reconciliation engine that could post a journal entry would create the financial fact
it claims to have observed, and every `MISSING_COUNTERPART` finding would resolve itself into
existence. A test asserts the absence rather than trusting the comment.

Everything not in the table names an owner and stops. `RepairOwner.OPERATOR` is not a gap — it is
the honest answer for divergences no automatic action can fix, and orphans and malformed evidence
route to it by rule: an orphan is not the source domain's problem, because it has no record of it to
fix.

A `BillingError` from the owning domain is a **considered refusal** — the repair was attempted and
declined — and is recorded as such. Anything else propagates, because a bug is not a refusal
(the M9.16-R1 P-6 correction).

### Resolution is evidence-based

`ResolveFinding` does not close a finding because a caller said so. It **re-reads both sides from
the owning domains** and `guard_evidence_establishes_agreement` refuses unless the new facts
actually agree — both present, same currency, same amount. Without that guard the command is
`auto_fix()` under another name: somebody calls it, the row says resolved, and the books still
disagree.

There is no `force` flag on the wire and never will be.

---

## Persistence

Migration **0029**, five additive `cp_billing_*` tables. Nothing in M9.2–M9.18 is altered.

| Table | Purpose | Write surface |
|---|---|---|
| `cp_billing_integrity_run` | one evaluation; PK **is** the evidence digest | insert + one completion update |
| `cp_billing_integrity_finding` | one divergence; unique on `(org, run, subject, divergence)` | insert + **conditional lifecycle UPDATE on `version`** |
| `cp_billing_integrity_repair_request` | what was asked of whom | **append-only** |
| `cp_billing_integrity_resolution` | evidence a divergence was addressed | **append-only** |
| `cp_billing_integrity_activity` | audit trail | **append-only** |

**No balance, no total, no monetary sum** — not as a column and not inside a payload blob.
`compared` / `matched` / `diverged` are counts; `severity` is a label; the two `*_amount_minor`
columns are copies of the two figures that disagreed, kept so the divergence stays legible after the
domains that produced it have moved on. Nothing sums them. A "total divergent value" column would be
the moment reconciliation became a second set of books.

Indexes: `(org, state, severity)` for the operator queue, `(org, owner, state)` for a domain's
repair queue, `(org, account, state)`, `(org, relationship, started_at)` and
`(org, subject, started_at)` for run history. Every query is org-scoped by construction and bounded.

---

## Concurrency

`SqlIntegrityFindingRepository.advance` is `UPDATE ... WHERE id = ? AND version = ?` — the
stale-worker defence, and the most important method in the milestone. Worker A reads `open`, worker
B resolves and commits, worker A comes back to write: zero rows match, and it raises `FindingStale`
rather than silently replacing a resolution with a stale row.

The domain already makes the *transition* impossible (no edge out of `resolved`). This makes losing
the *race* impossible too. Both are needed: the FSM protects against a worker that re-reads, the
compare-and-set against one that does not.

Proven with two Applications over one file-backed database, each with its own connection — two
*connections*, not two threads, because a thread schedule that does not actually interleave produces
a green test that proved nothing.

---

## Surface

**25 paths at `/integrity`**, a prefix chosen by inspecting every existing one. `/collections` was
the tempting name and is wrong twice over: it already belongs to M9.16, and this domain reconciles
rather than collects. Two routers under one prefix shadow each other by registration order, and the
one that loses does so silently.

**There is no DELETE anywhere on the surface.** Historical divergence is evidence, and evidence is
not deleted.

`GET /integrity/findings` is the read an external scheduler polls. **Billing runs no scheduler** —
whoever decides when to look lives outside this plane, exactly as for `/recovery/cases/due` and
`/revenue/entries/due`.

Studio (`/studio/integrity.html`) is a Consumer under ADR-0021: it compares nothing, computes no
severity, sums nothing, and resolves nothing locally. Buttons come from the server's
`allowed_triggers`, so a resolved finding shows no reopen button for a transition the domain would
refuse. The relationship table is fetched from `/relationships` rather than hard-coded.

---

## Defects found and fixed

**D-1 — a tax-free invoice was reported as a critical integrity failure.**
Demanding a determination for *every* document flagged a zero-rated invoice, an exempt customer and
an installation not using tax determination at all as `MISSING_SOURCE` / `CRITICAL`. A critical that
fires on every correct document is one operators learn to dismiss. Fixed: a determination is
expected only when the document **presents tax**; a document presenting tax with no determination
behind it stays critical, which is the case actually worth alarming on.

**D-2 — the engine was blind to its own headline divergence.**
The recognition↔ledger evidence reader filtered sources to `("recognized", "posted")`, dropping
`UNPOSTED` as "not recognized yet". `UNPOSTED` is the opposite: the Ledger *refused* after the
decision was committed — revenue earned, no financial fact — which is precisely what this
relationship exists to catch. Fixed by reusing M9.18's own `RECOGNIZED_ENTRY_STATES`, which is
deliberately gross of corrections, rather than restating the list.

**D-3 — a `MISSING_COUNTERPART` finding could never be resolved.**
The resolution path re-read `finding.counterpart`, which is `None` for exactly that divergence, so
the guard refused forever and the one case with a declared repair path could never be closed. The
engine could detect and repair but never observe the result. Fixed: the resolution re-runs the
relationship's evidence read against the original run's scope and looks the subject up on both
sides.

---

## Limitations

1. **No production scheduler ships.** `/integrity/findings` and `/integrity/runs` are reads; nothing
   in Billing decides when to reconcile.
2. **Payment ↔ provider is not reconciled here** — deliberately. M9.16 owns it, and the M9.16-R1
   P-4 limitation stands: no production provider webhook endpoint exists yet.
3. **Invoice ↔ tax findings have no automatic repair.** They name `invoice` or `operator`; M9.15
   determinations supersede rather than edit, and choosing which supersession is correct is a
   decision no rule can make.
4. **The invoice↔tax resolution path has no pairwise re-read**, because M9.15 owns that comparison.
   Such a finding is resolved by an operator recording that the determination was corrected.
5. **Aggregate relationships compare one currency only.** Mixed-currency evidence is refused rather
   than converted — converting would be a foreign-exchange opinion Billing does not hold (RS5).
6. **`open_findings` sorts worst-first in Python over an already-bounded page**, not in SQL. Correct
   and dialect-independent, but the ordering is per-page rather than global.
7. **No cross-relationship correlation.** A single root cause producing findings in three
   relationships appears as three findings; nothing groups them.

---

## Architecture compliance

* §A12 additive extension — `IntegrityFinding` lands beside `ReconciliationFinding`, as
  `JournalEntry` did beside `LedgerEntry` and `PaymentIntent` beside `Payment`.
* §A11.2 org-scoped by construction — every identity is org-first and every query carries an org.
* §A2.6 fail fast — structural validation raises `BillingValidationError` at the factory.
* §A5 durable outbox — `billing.integrity.*` facts commit with the run or finding that produced
  them.
* ADR-0021 — Studio is a Consumer.
* ADR-0023 **PY5** — unchanged, and now structural: no edge out of `RESOLVED`, and `resolve`
  refuses without evidence.
* ADR-0023 **RS2/RS6** — untouched. Reconciliation holds no bookkeeping of its own.
