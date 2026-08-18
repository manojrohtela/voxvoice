# Invoice Engine (M9.14)

**Status:** 🟢 Implemented · **Milestone:** M9.14 · **Date:** 2026-08-08
**Authority:** Engineering Constitution v2.0 · ADR-0019 · ADR-0021 · ADR-0023 · ADR-0031
**Predecessors:** M9.2–M9.13 (all frozen; none reshaped)

---

## 1. What this milestone is

Eight milestones produced facts. M9.6 said **what is sold**, M9.7 **what it costs**, M9.8 **who we
bill**, M9.9 **what was agreed**, M9.10 **what may be used**, M9.11 **what was consumed**, M9.12
**what should be charged**, M9.13 **what is owed**. None of them produced anything a customer, a
court, or a tax authority could read.

This one does, and that changes the failure mode again. M9.13 was the first domain whose errors
could not be corrected by re-running anything. M9.14 is the first whose output **leaves the
building**. An issued invoice reaches a customer's accounts-payable system, an auditor's sample, and
in several jurisdictions a statutory filing. None of those parties can be asked to accept a
corrected copy of a document they already hold.

So the milestone is organised around one sentence:

> **The Ledger owns financial truth. The Invoice owns presentation of it.**

A document is not an accounting record; it is the *evidence* an accounting record produces. That is
why a line here carries a `journal_entry_id` rather than a recomputed amount, why no service in this
package folds a balance, and why rendering is downstream of a snapshot that was already sealed.

**The Invoice Engine owns** invoices, credit notes, debit notes, their numbering, their immutable
snapshots, their lines, their presentation, their rendering models, their document relationships,
their delivery metadata, their attachment metadata, their lifecycle, their revision history, their
diagnostics, and their search.

**It owns nothing else.** It calculates no price, resolves no pricing, meters no usage, rates no
charge, posts no ledger entry, computes no balance, evaluates no entitlement, authorizes no request,
executes no payment, and determines no tax. Nine AST-driven tests assert each of those separately.

---

## 2. The mandatory audit — what already existed, and what was amended

Before writing code, every previous Epic 9 milestone was audited for pre-existing invoice ownership.
Two genuine overlaps were found. Neither was drifted past; both were resolved by **additive
extension**, exactly as M9.13 resolved the same class of overlap beside M9.2's `ledger.py`.

| Question | Finding | Resolution |
|---|---|---|
| Does invoice ownership already exist? | **Yes, partially.** M9.2's `invoicing.py` declares `Invoice`, `InvoiceState`, `InvoiceTrigger`, `IMMUTABLE_INVOICE_STATES`, `ChargeLine`, `ChargeLineKind`, `InvoiceNumber`, `InvoiceTotals`, `CreditNote`, `BillingRun`. M9.5 exposes four routes at `/invoices`. M9.3/M9.4 carry `DraftInvoice`/`IssueInvoice` and `cp_billing_invoice`. | New modules **beside** `invoicing.py`, never a reshape of it. Every enum and value object above is **reused unchanged**. The M9.2 aggregate, its table, and its four routes are untouched and still work. |
| Does any milestone already generate customer documents? | **No.** No PDF, HTML, template, or document-model code exists anywhere in the repository. | M9.14 introduces the first. |
| Does duplicate numbering logic exist? | **Partially.** M9.2 declares `InvoiceNumberFormatter` (the *format*) and an `InvoiceNumberAllocator` ABC (the *allocation*) — the ABC was never implemented. | `NumberingService` **delegates to** `InvoiceNumberFormatter`; a test asserts `services.py` remains the only place the format is declared. Allocation is implemented behind a new, org-scoped, series-keyed `NumberSeriesAllocator` (see §7). |
| Do duplicate financial calculations exist? | **`InvoiceTotalsService` (M9.2) already declares the order** discount → credit → tax. | `DocumentAssembler` **delegates to it**. A test asserts `services.py` remains the only declaration and that the assembler contains no `+` of its own. |
| Duplicate tax ownership? | No. `tax.py` (M9.2) owns `TaxProfile`, `TaxDetermination`, `TaxComponent`, `TaxTreatment`. | Consumed read-only; `TaxComponent` and `TaxTreatment` are reused, not restated. |
| Duplicate balance logic? | No — M9.13 owns it. | Never read, never computed. Asserted. |
| Duplicate payment logic? | No — `payments.py` (M9.2) owns it. | `MarkInvoicePaid` records a document *status* a payment fact reported; it moves no money. Asserted. |
| Does any previous limitation block Invoice? | **No blocking limitation.** M9.13 §14 records eight; the one that touches M9.14 is the **AR-subledger-vs-GL** question, which affects external accounting integration and not document production. | Restated as inherited, not resolved here. See §14. |
| Are the Ledger outputs Invoice needs stable? | **Yes.** `AccountingPeriod.is_closed`, `JournalEntry.signed_minor`, `EntryReferences`, and `for_period` are all M9.13 surfaces with tests behind them. | Consumed as-is. |

**One amendment was required, and it is to a test rather than to a domain.** M9.3's
`test_the_application_duplicates_no_foundation_rule` banned the substrings `basis_points` and
`.allocate(` anywhere under `billing/application/`. Both are correct bans for what they were written
for — rate arithmetic and `Money.allocate`'s exact splitting — but neither could distinguish
*carrying* a Foundation value from *duplicating* a Foundation rule once a document had to transport
a determined tax rate into a wire DTO and draw an invoice number from a persistence seam.

The assertion was **sharpened, not weakened**, following the precedent M9.7 set in the very next
test in that file ("a command may *carry* a caller's declared rounding policy… but no Application
code path may *apply* one; the distinction is where the name appears"):

* `basis_points` is now banned inside an **arithmetic expression** — checked by walking for
  `BinOp`/`AugAssign`/`UnaryOp` nodes — rather than as a substring;
* `.allocate(` is now banned **by receiver**: only a call on `…numbers` is permitted, so
  `Money.allocate` remains forbidden by name *and* by structure.

Recorded in DL-0124. No domain was changed to satisfy a test.

A second boundary was caught by an existing frozen assertion during the full run and fixed in the
**code**, not the test: `test_persistence_imports_only_its_allowed_dependencies` refuses `hashlib`
inside `billing/persistence/`, and the first draft derived snapshot, revision, and rendering row ids
there. An identity is a domain fact. `derive_snapshot_id`, `derive_revision_id`, and
`derive_rendering_id` now live in `commercial_invoicing.py` and persistence calls them.

---

## 3. Components

| Layer | Module | Lines | Responsibility |
|---|---|---|---|
| Foundation | `commercial_invoicing.py` | 959 | Documents, snapshots, parties, lines, numbering policy, relations, delivery/attachment metadata, rendering models, inspection, search — pure domain |
| Foundation | `invoice_rules.py` | 1 283 | Lifecycle, line building, assembly, snapshot integrity, numbering, corrections, rendering, reconciliation, inspection, validation, search |
| Foundation | `invoice_factories.py` | 579 | `InvoiceFactory` + six repository contracts |
| Application | `invoice_engine.py` | 2 014 | 13 commands, 14 queries, use cases, view DTOs |
| Persistence | `invoice_models.py` | 234 | 7 `cp_billing_*` tables |
| Persistence | `invoice_repositories.py` | 562 | 6 SQL implementations |
| Persistence | `migrations/v0024_billing_invoicing.py` | 46 | Additive migration 0024 |
| Customer API | `invoice_schemas.py` | 784 | Pydantic v2 wire DTOs |
| Customer API | `routers/invoice_engine.py` | 783 | 26 paths at `/documents`, transport only |
| Studio | `studio/invoices.html` | 640 | 11 screens, self-contained |
| Tests | `test_billing_invoice_engine.py` | 2 096 | 156 tests incl. 19 named demonstrations |

**Additive throughout.** M9.2's `invoicing.py` is unreshaped; `cp_billing_invoice`,
`cp_billing_credit_note`, and `cp_billing_run` are untouched; `InvoiceState`, `InvoiceTrigger`,
`IMMUTABLE_INVOICE_STATES`, `ChargeLineKind`, `InvoiceNumber`, and `InvoiceTotals` are **reused
unchanged** — as are `TaxComponent`/`TaxTreatment` from `tax.py` and `BillingAddress` from
`accounts.py`.

---

## 4. The four structural invariants

Every legal guarantee is made a property of a type, a table, or a state machine — never of
discipline.

### 4.1 An issued document's content is addressed by digest, never by mutation

Content lives in an **append-only snapshot store** keyed by `(org, document, revision)`. The
document *header* carries no lines and no totals — asserted by enumerating
`CommercialInvoice.__dataclass_fields__`, exactly as M9.13 asserts no stored aggregate carries a
balance. Issuance pins one revision's digest onto the header, and every read of an issued document
resolves **that digest** through `SqlInvoiceSnapshotRepository.by_digest`.

The consequence is the point: if a later revision somehow existed, it could not become the served
document by being newer, because the lookup is by content address rather than by recency. Selecting
by "latest" would make immutability depend on nobody appending; selecting by digest depends on
nobody at all.

`SnapshotIntegrityService.guard_pinned` closes the loop — a stored revision that no longer hashes to
the pinned digest raises rather than serves, because at that point an issued document has changed
underneath its pin and that is an incident, not a warning.

### 4.2 Corrections are new documents

A credit note and a debit note are themselves numbered, issued, immutable documents carrying a
`DocumentRelation` to what they correct. Three absences make this the only path:

* **no edge back to `draft`** from any state in `IMMUTABLE_INVOICE_STATES`;
* **no `VOID` out of any issued state** — a number has been allocated, and a gapless series has no
  hole to spare. Post-issue cancellation is a full credit note carrying
  `DocumentRelationKind.CANCELS`, which is what jurisdictions that forbid deleting an issued invoice
  actually require;
* **no deletion state anywhere**, asserted by scanning the state vocabulary.

Amounts on a correction are **mirrored, never recomputed**. Asking pricing and rating what the
invoice should have said is precisely the reach-back this milestone forbids — and it would produce a
credit note that disagrees with the invoice it credits the moment a price has changed since.
`CorrectionService`'s code is scanned, docstrings stripped, for any mention of rating or resolution.

The edge is recorded on **both** documents: the correction points at the original, and the original
records a `CORRECTED_BY` back-reference so a reader who starts at the invoice finds the credit note
without having to search for one. Appending a relation to the header is not editing content — the
sealed snapshot is untouched and its digest is unchanged.

### 4.3 Numbering is gapless because allocation is transactional

`cp_billing_invoice_number` is an **allocation ledger** whose primary key is
`(org_id, series_key, sequence)`. A duplicate invoice number is therefore not a finding a validator
might report — it is a row the database refuses.

The sequence is drawn from a durable per-series counter (`cp_billing_invoice_series`) **inside the
issuing transaction**. That is the entire crash-safety mechanism: a worker that dies between
allocating and committing rolls back both, so the number is never consumed. A pre-allocated pool
cannot offer this — a crash between allocation and use leaves a hole, and where a jurisdiction
requires gapless numbering there is no legal way to fill one afterwards. A test simulates exactly
that crash and asserts the series continues 1, 2, 3.

A series is scoped per legal entity, per jurisdiction, per document kind, and per reset bucket,
because "gapless" is a property *of a series* and merging two jurisdictions into one counter makes
each look gapped to the authority reading it. `NumberingReset.ANNUAL` and `MONTHLY` exist because
India's GST rules and several EU VAT regimes require a series that restarts each financial year; a
platform that only ever counted upward could not issue a compliant invoice there.

### 4.4 Rendering computes nothing

`DocumentModelBuilder` prepares a `DocumentModel` whose every leaf is **a string**. `HtmlRenderer`
and `PdfRenderer` receive that model and never see an `InvoiceSnapshot`, a `Money`, or a minor unit
— asserted by scanning each class's docstring-stripped source for `Money(`, `.times(`, `.scaled(`,
`.allocate(`, `sum(`, and `minor`.

Handing a template no numbers is cheaper than trusting it not to add them up. A renderer that could
compute a total is a renderer that can disagree with the ledger, and the customer holds the
renderer's output.

Producing PDF *bytes* needs a rendering library — vendor code that does not belong in a pure domain
(ADR-0031 IR1). What belongs here is everything that decides *what the PDF says*: ordering,
pagination, and the exact lines. `PdfRenderer` emits that model; a byte generator downstream
consumes it and adds nothing.

---

## 5. Lifecycle

One FSM over M9.2's states and triggers, shared by all three document kinds — because a credit note
is not a flag on an invoice, it is a numbered issued document in its own right.

```
draft ──submit_for_approval──▶ pending_approval ──approve──▶ issued
  │                                   │
  ├──issue────────────────────────────┼──────────────────▶ issued
  └──void──▶ voided ◀──void───────────┘

issued ──mark_overdue──▶ overdue ──send_to_collection──▶ in_collection
issued / overdue / in_collection ──mark_paid──▶ paid
issued / overdue / in_collection ──credit────▶ credited
```

`APPROVE` lands on `issued` directly rather than returning to `draft`. In an enterprise approval
flow the approver's decision *is* the act of issuing, and a state meaning "approved but not yet
issued" would be a window in which an approved document could still be edited. Both routes publish
one fact — asserted by `LIFECYCLE_EVENTS[ISSUE] is LIFECYCLE_EVENTS[APPROVE]`.

`paid`, `credited`, and `voided` are terminal. Re-opening one would make "was this ever paid?" a
question with two answers.

`MarkInvoicePaid` records a document status a payment fact reported. It executes nothing.

---

## 6. What the Invoice consumes, and what it refuses

Preparing a document is the only path by which financial facts become customer documents, and it is
guarded on both sides.

**The accounting period must be `closed`.** An open period can still receive a posting; a document
prepared from one would be a legal statement of a figure that has not stopped moving. This is the
boundary the milestone brief states as non-negotiable, enforced at the one place it can be —
`_InvoiceSupport.closed_period`, called by both the command and the query side so a preview and an
issue can never disagree about what is eligible.

**The lines come from M9.13's journal entries, never from M9.12's charges.** The ledger is the
authority on what is owed; rating is the authority on what *should have been* charged. Reading the
latter would let a document disagree with the balance the customer is asked to settle whenever a
reversal or an adjustment sits between them.

**The customer identity is copied at issue, not referenced.** This is the one place in the Billing
plane where copying is correct rather than the drift §A2.5 warns about: a document that re-read the
customer's current name would print a name the customer did not have when they were billed, and a
legal document may not do that.

**The supplier arrives on the request.** Who VoxVoice contracts *as* is published organization
information, which Billing references and never owns (ADR-0023 TX3). A supplier registry inside this
plane would be Billing holding a fact about the organization it bills for. Recorded as limitation 2.

---

## 7. Persistence

Seven `cp_billing_*` tables via migration **0024**. Four are append-only by construction: snapshots,
revisions, allocated numbers, and renderings — no update method and no delete method exists to call.

| Table | Key | Note |
|---|---|---|
| `cp_billing_commercial_invoice` | derived `id` | Header only: state, number, pinned digest. No lines, no totals. |
| `cp_billing_invoice_snapshot` | derived `id`, unique `(org, document, revision)` | **The content.** `digest` indexed — it is the lookup key, not a checksum. |
| `cp_billing_invoice_revision` | derived `id` | Who prepared what, when, why. Survives issuance. |
| `cp_billing_invoice_number` | **`(org_id, series_key, sequence)`** | The allocation ledger. A duplicate number is an impossible row. |
| `cp_billing_invoice_series` | `(org_id, series_key)` | The durable counter. |
| `cp_billing_rendered_document` | derived `id` | Keyed by `(document, digest, format, template version)`. |
| `cp_billing_invoice_activity` | `id` | Append-only history. |

Idempotent `append` is defended the same three ways M9.11–M9.13 defend theirs: a derived primary
key, a read-before-write, and a `SAVEPOINT` around the insert whose `IntegrityError` becomes a read
of the winner — so losing a race does not destroy the caller's transaction along with the failed
insert.

Round-trip fidelity is asserted for `InvoiceSnapshot`, the deepest aggregate in the plane: parties,
addresses, lines, tax components, totals, payment instructions, presentation, relations, and an
`InvoiceNumber`, through the generic M9.4 codec unchanged.

---

## 8. API and Studio

**26 paths at `/documents`** (243 total; no collisions). **No DELETE, no PUT, no PATCH anywhere** —
asserted. No route voids an issued document, no route reopens one, and no controller allocates a
number: the sequence is drawn inside the Application's transaction, and a controller that could
allocate one could leak it on a failure.

Static paths (`/preview`, `/search`, `/activity`, `/number-series/{key}`) are declared **before**
`/{document_id}`, so a preview is a preview and not a document whose id happens to be "preview".

The **Studio** (11 screens: Documents, Prepare, Document, Rendering, Corrections, Relations,
Revisions, Numbering, Reconcile, Delivery, Activity) computes no total, holds no state, stores
nothing, and renders nothing. The markup it displays is the server's artefact handed straight to a
sandboxed iframe, so what a viewer sees is byte-identical to what was issued. Tests scan the
**script body only** — the header comment deliberately names what the Studio does not do, and a scan
that could not tell prose from code would flag the explanation as the violation (the M9.6 lesson,
applied again).

---

## 9. Testing — 156 tests

Organised in 18 sections: identity, lifecycle, line building, totals, digests, immutability,
numbering, corrections, rendering, validation, reconciliation, search, wiring, the 19 named
demonstrations, more wiring, HTTP, Studio, and architecture compliance.

### Defects found and fixed

**1. All three document kinds printed the same number.** Found by the end-to-end smoke, which issued
an invoice, a credit note, and a debit note and got `VOX-KA-000001` back three times. Each kind
counts in its own gapless series — correctly — so all three legitimately reached sequence 1, but the
formatted number carried no kind token. Two documents stating opposite things would have shared one
printed identifier, and `by_number` would have been ambiguous. Fixed by adding a `CN`/`DN` token;
an invoice's token is empty, so `VOX-KA-000001` remains exactly what M9.2's formatter has always
produced.

**2. A revision silently discarded the draft's identity.** Found by the same smoke, at step 6. A
revision restates the whole document, and `_append_revision` read only the command's fields — so a
redraft that changed nothing but a note lost the supplier, the template, and the payment
instructions the draft was prepared with, and would then have been *issued* that way. Fixed by
inheriting everything the command does not override; the lines are the deliberate exception, always
re-read from the ledger so a correction posted since the last draft reaches the document.

**3. `EMPTY_INVOICE` was already declared at M9.2.** Re-declaring the *name* in a `StrEnum` is not an
alias — it is a `TypeError` at import, which is how this was found. The M9.13 lesson generalised: the
code is reused, and the comment now records which earlier codes M9.14 reuses rather than restates.

**4. Row identity was being derived in persistence.** Caught by the frozen
`test_persistence_imports_only_its_allowed_dependencies`, which refuses `hashlib` there. An identity
is a domain fact; a storage layer that minted one would be a second place the answer to "which row
is this?" lives. Moved to `commercial_invoicing.py`.

**5. The series audit read the wrong source.** The first draft inferred allocated sequences from the
numbers printed on documents. A sequence allocated to an issue that then failed is a **real hole**,
and auditing the documents alone would hide precisely the case the audit exists to find. Now reads
the allocation ledger, and `NumberSeriesAllocator.allocated` was added to the contract to make that
possible.

---

## 10. The document demonstrations

Nineteen named tests, one per claimed invariant, because an invariant nobody can point at is one
that decays.

| # | Demonstration |
|---|---|
| 1 | An open accounting period cannot become a document |
| 2 | A closed period produces a document that matches the ledger |
| 3 | Preparing twice produces one document |
| 4 | A preview persists nothing and allocates no number |
| 5 | A revision inherits everything it does not override |
| 6 | Issuing allocates a gapless number and seals a digest |
| 7 | An issued document can never be revised |
| 8 | An issued document can never be voided |
| 9 | The document reads byte-identically every time |
| 10 | The document survives a restart with new seams |
| 11 | Rendering is deterministic and carries the document digest |
| 12 | A credit note leaves the original exactly as it was |
| 13 | A correction is numbered in its own series |
| 14 | A correction is discoverable from the original |
| 15 | The number series is gapless and auditable |
| 16 | The document reconciles against the ledger |
| 17 | Every revision is kept and exactly one is issued |
| 18 | The settled document accepts no further move |
| 19 | The Invoice writes to no other domain |

---

## 11. Compliance

| Check | Result |
|---|---|
| Never recalculates rating / prices / usage | ✅ AST-asserted separately |
| Never recalculates the ledger or computes a balance | ✅ AST-asserted |
| Never determines tax; consumes a published determination | ✅ AST-asserted |
| Never performs a payment operation | ✅ AST-asserted |
| Never determines an entitlement; authorizes nothing | ✅ AST-asserted |
| **Writes to no other domain** | ✅ AST write-target scan + before/after equality across five domains |
| Issued document immutable; no deletion state; no void after issue | ✅ FSM + repository surface |
| Historical documents reproduce byte-identically | ✅ demonstrated across a restart |
| Numbering gapless, deterministic, jurisdiction-aware, collision-safe, crash-safe | ✅ primary key + transactional allocation + simulated crash |
| Rendering never influences totals | ✅ type-level (strings) + AST |
| Foundation pure (no I/O, no clock, no template engine) | ✅ import scan |
| Controllers transport-only; Studio computes nothing | ✅ AST + script-body scan |
| Migration additive; M9.2 tables untouched; bootstrap clean | ✅ |
| Repository-wide suite | ✅ **3 449 / 3 449** |

---

## 12. Surface changes

Additive only. `BillingRepositories.invoice_engine` / `BillingContext.invoice_engine` default to
`None`, so a build without invoicing is exactly what M9.13 assembled.

| Surface | Before | After |
|---|---|---|
| Foundation exports | 642 | **714** |
| Application exports | 492 | **552** |
| `billing.*` events | 168 | **178** (10 new; 6 M9.2 invoice facts reused) |
| Diagnostic codes | 162 | **177** (15 new; `empty_invoice` and 5 others reused) |
| Migration chain | …0023 | **…0024** |
| API paths | 217 | **243** |
| Repository tests | 3 293 | **3 449** |

---

## 13. Limitations

Recorded honestly rather than deferred silently.

1. **The M9.8 attribution ceiling now blocks five milestones.** Usage attribution requires exactly
   one active billing account per organization. Invoice does not worsen it — it documents per
   *account*, named directly by the closed period — but it inherits the ceiling on who can be billed
   at all. **Not fixable inside this boundary.**
2. **There is no supplier registry.** Who VoxVoice contracts as is published organization
   information, so the supplier party arrives on the request and is captured into the snapshot. A
   caller that omits it issues a document with an empty supplier name — refused by
   `validate_issue`, but only at issue time. **Owner: whichever milestone gives Billing a read
   surface onto organization legal entities.**
3. **Tax determination is consumed, never triggered.** M9.14 reads a determination already recorded
   against the document. Nothing here *asks* a tax adapter to determine one, so a document prepared
   before determination presents no tax lines. Correct by ownership (M9.15 owns determination), and
   the ordering dependency is real. **Owner: M9.15.**
4. **PDF bytes are not produced.** `PdfRenderer` emits the paginated document *model* — everything
   that decides what the PDF says. Turning it into bytes needs a vendor library that does not belong
   in a pure domain. **Owner: an adapter at the edge.**
5. **Delivery is recorded, never performed.** There is no mailer, queue, or transport. An agent
   outside this boundary reports what happened. Deliberate, and named so it is not mistaken for a
   working send.
6. **Attachments are metadata only.** No bytes are stored, read, or served. The object store is
   outside this plane.
7. **`related_to` matches on a projected comma-joined id list.** Correct and indexed, but a
   substring match — a document id that were a prefix of another would over-match. Ids are
   fixed-length digests today, so it cannot happen; named because it depends on that.
8. **Search filters in Python over a bounded fetch** (2 000 documents). Consistent with
   M9.7/M9.11–M9.13.
9. **A jurisdiction's numbering policy is passed per issue, not stored.** `NumberingPolicy` is
   assembled from the document plus the issue command. An organization that must guarantee "every
   Karnataka invoice resets annually" has no place to declare that once. **Owner: a billing-entity
   configuration milestone.**
10. **29 pre-existing qualified OpenAPI schema names** inherited from M9.5/M9.7–M9.11. M9.14 adds
    none, and its own surface is asserted clean. **Owner: an API-surface cleanup milestone.**

---

## 14. Recommendation

**Proceed to M9.15 (Tax Determination)**, which is the natural next consumer: it records a
determination against a document, and M9.14 already presents one when it exists.

Three items should be scheduled alongside:

1. **The M9.8 attribution ceiling**, now blocking five milestones. It is a dependency, not a
   limitation.
2. **Ratify the AR-subledger decision into ADR-0023** (inherited from M9.13 §14.2). An invoice now
   depends on the ledger's shape; the decision should be written down before an external accounting
   integration depends on it too.
3. **Billing-entity configuration** — supplier identity and per-jurisdiction numbering policy, the
   two things M9.14 currently takes per request because there is nowhere to declare them once.
