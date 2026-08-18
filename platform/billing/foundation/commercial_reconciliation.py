"""Billing Reconciliation & Financial Integrity — the **observation** domain (M9.19).

Every financial domain in Epic 9 already answers its own question. M9.13 says what was recorded,
M9.14 says what was presented, M9.15 says what was determined, M9.16 says what moved, M9.17 says
what was asked for, M9.18 says what was earned. Each is internally consistent by construction.

**M9.19 asks the one question none of them can ask about itself**: do they agree with each other?

> A reconciliation result is an **observation**. It is not permission to mutate another domain.

That sentence is the whole design, and it is enforced structurally rather than by discipline: this
package imports no repository, dispatches no financial command, and owns no aggregate that any other
domain reads. The only thing it can produce is a *finding* — a durable, explainable record that two
immutable facts disagreed — and the only thing a finding can do is name the domain that must fix it.

---

## The audit that shaped this vocabulary

Reconciliation is not new here, and most of it was correct. The pre-implementation audit found six
existing artifacts and **changed none of them**:

| Artifact | Owner | Current meaning | M9.19 action |
|---|---|---|---|
| `ReconciliationVerdict` | M9.2 | 4-member payment/ledger verdict | **reused**, projected onto |
| `ReconciliationFinding` | M9.2 | one payment↔provider record | **untouched** — see below |
| `PaymentReconciliation` | M9.16 | Billing ↔ **provider** disagreement | **untouched** |
| `LedgerReconciliation` | M9.13 | ledger ↔ rated charges, ephemeral | **untouched** |
| `InvoiceReconciliation` | M9.14 | document ↔ ledger, ephemeral | **delegated to**, not rewritten |
| `TaxReconciliation` | M9.15 | assessment ↔ subject, ephemeral | **delegated to**, not rewritten |

Two conclusions came out of it and both are load-bearing.

**The existing reads are ephemeral; nothing was durable.** `LedgerReconciliation`,
`InvoiceReconciliation` and `TaxReconciliation` are computed on request and thrown away. They can
tell you the books disagree *today*; they cannot tell you the books disagreed in March, who was
told, what was done about it, or whether it ever recurred. A divergence that leaves no trace is a
divergence nobody is accountable for. M9.19 adds the durable triad — **run, finding, resolution** —
and reuses those comparison services rather than writing a second opinion about the same pair.

**The names had to change, and that is a feature.** M9.2's `ReconciliationFinding` and M9.16's
`ReconciliationOutcome` are already taken and already correct for what they describe. Shadowing them
would have produced two types with one name meaning different things, which is exactly the drift the
mandate forbids. So this domain is named for what it actually is — **financial integrity** — and
lands *beside* the payment vocabulary, the same way every aggregate since M9.13 has landed beside
its predecessor (§A12). `IntegrityFinding` is never confused with `ReconciliationFinding` because
they are spelled differently, and `DIVERGENCE_VERDICT_PROJECTION` states the relationship between
them as a table rather than leaving it to be inferred.

## Why `DivergenceKind` is not `ReconciliationOutcome`

M9.16's enum is provider vocabulary: `MISSING_IN_PROVIDER`, `UNKNOWN_REFERENCE`, `STALE_OPERATION`,
`DUPLICATE_EVENT`. Those words are precise about an external integration and meaningless about
whether an invoice agrees with a recognition schedule. Reusing `MISSING_IN_PROVIDER` to mean "the
ledger has no entry" would be a lie told by a type name, and the next reader would believe it.

`DivergenceKind` is therefore declared here, in *relationship-neutral* language — source and
counterpart, never provider and Billing — and every member projects onto M9.2's four-member verdict
through `DIVERGENCE_VERDICT_PROJECTION`, so nothing downstream needs to learn a second alphabet.

## What a finding may never become

The lifecycle has **no edge out of `RESOLVED` and no edge out of `CLOSED`**. That is not an
oversight to be filled in later; it is ADR-0023 **PY5** expressed as an absent edge:

> Divergence between provider records and the ledger raises an incident; it is never auto-resolved.

A resolved finding cannot be reopened, downgraded, or rewritten by a stale worker, because the state
machine has nowhere for it to go. If the same underlying divergence happens again, the *next run*
observes it and creates a *new* finding — a second row, with its own evidence and its own timestamp.
The history of a recurring problem is a list of findings, not a single row that keeps changing its
mind.

## Identity is what was looked at

A run's id is derived from `(org, relationship, scope, evidence digest)` — the M9.18 basis-digest
lesson applied to comparison. Three consequences fall out of that one choice:

* two workers reconciling the same facts compute the same run id and one loses the insert;
* **re-running over unchanged evidence is idempotent** — same run, same findings, nothing written;
* re-running *after a repair* has different evidence, so it is genuinely a new run, which is what
  makes "reconciliation observes the result later" a real observation rather than a rewrite.

A finding's id is derived from `(run, relationship, subject key, divergence kind)`, so one run
records one divergence once no matter how many workers detect it — and a recurrence in a later run
is a different id, which is precisely why a resolved finding never needs to be reopened.

## What is deliberately absent

No balance. No total. No amount this module computed rather than copied. No price, rate, tax,
usage or recognized figure. No provider, no HTTP, no SDK. No scheduler, no clock, no ambient
anything. And no write path into any other domain: the only arithmetic here is comparing two
integers that arrived already decided.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum

from core.fsm import StateMachineDefinition, define

from .money import Currency, Money
from .payments import ReconciliationVerdict

# ── identity ────────────────────────────────────────────────────────────────────


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def derive_evidence_digest(refs: tuple[str, ...]) -> str:
    """A digest over **exactly what was compared**, order-independent.

    Every fact reference contributing to a run is reduced to a stable string by
    `FactRef.evidence_key()` and the set is sorted before hashing, so two workers that read the same
    rows in different orders compute the same digest. Nothing volatile enters: no observation time,
    no run id, no actor. A timestamp in here would make every re-run a different run and destroy the
    idempotency the digest exists to provide.

    **This digest is evidence that two facts were compared. It is never a financial source of
    truth**, and nothing downstream reads an amount out of it."""
    return _digest(*sorted(refs))


def derive_run_id(org_id: str, relationship: str, scope_key: str, evidence_digest: str) -> str:
    """A run **is** what it looked at. Same org, relationship, scope and evidence — same run."""
    return "irn_" + _digest(org_id, relationship, scope_key, evidence_digest)[:32]


def derive_finding_id(run_id: str, relationship: str, subject_key: str, divergence: str) -> str:
    """One divergence, once per run.

    Keyed on the *run* rather than on the subject alone, deliberately: a divergence that recurs
    after an earlier one was resolved must produce a **new** finding, not a second write to a closed
    row. The run id already carries the evidence digest, so "same problem, same facts" collapses and
    "same problem, new facts" does not."""
    return "ifd_" + _digest(run_id, relationship, subject_key, divergence)[:32]


def derive_resolution_id(finding_id: str, request_key: str) -> str:
    """Two concurrent "this is fixed" submissions under one key are one resolution."""
    return "irs_" + _digest(finding_id, request_key)[:32]


def derive_repair_request_id(finding_id: str, owner: str, action: str, request_key: str) -> str:
    """Two workers asking the same owner for the same repair ask once."""
    return "irq_" + _digest(finding_id, owner, action, request_key)[:32]


# ── the relationships ───────────────────────────────────────────────────────────


class RelationshipKind(StrEnum):
    """The financial pairs M9.19 is allowed to compare.

    Six, and the list is closed. **Payment ↔ Provider is deliberately absent**: M9.16 owns it, has
    owned it since it shipped, and reconciling a provider requires provider evidence M9.19 has no
    business holding. Two owners for one pair is how a divergence gets resolved twice and reported
    once."""

    PAYMENT_LEDGER = "payment_ledger"
    INVOICE_LEDGER = "invoice_ledger"
    INVOICE_TAX = "invoice_tax"
    INVOICE_RECOGNITION = "invoice_recognition"
    PAYMENT_INVOICE = "payment_invoice"
    RECOGNITION_LEDGER = "recognition_ledger"


class MatchMode(StrEnum):
    """How a relationship's two sides are compared.

    `PAIRWISE` matches individual facts by a deterministic identity. `AGGREGATE` compares totals,
    which is the only honest way to ask "is this invoice fully accounted for by its recognition
    schedule?" — an invoice has one amount and a schedule has twelve, so there is no pair to
    make."""

    PAIRWISE = "pairwise"
    AGGREGATE = "aggregate"


class RepairOwner(StrEnum):
    """Who must fix a divergence. **Never M9.19.**

    `OPERATOR` is not a cop-out: some divergences — an orphan ledger entry nobody can explain, a
    malformed snapshot — have no automatic repair and pretending otherwise would be worse than
    naming a human."""

    LEDGER = "ledger"
    INVOICE = "invoice"
    TAX = "tax"
    PAYMENT = "payment"
    RECOGNITION = "recognition"
    OPERATOR = "operator"


class DivergenceKind(StrEnum):
    """What kind of disagreement was observed.

    Deliberately **relationship-neutral**: `source` and `counterpart`, never `provider` and
    `billing`. The same twelve members describe an invoice disagreeing with a ledger and a
    recognition disagreeing with a posting, which is what lets one comparison engine drive all six
    relationships instead of six hand-written comparisons drifting apart.

    `AMBIGUOUS` is the M9.16 lesson carried forward: an honest unknown is not a divergence to
    repair.
    It is a divergence to *resolve*, and only new evidence resolves it."""

    MISSING_COUNTERPART = "missing_counterpart"  # the source happened; nothing records it
    MISSING_SOURCE = "missing_source"  # the counterpart exists; nothing caused it
    AMOUNT_MISMATCH = "amount_mismatch"
    CURRENCY_MISMATCH = "currency_mismatch"
    ACCOUNT_MISMATCH = "account_mismatch"
    PERIOD_MISMATCH = "period_mismatch"
    DUPLICATE_SOURCE = "duplicate_source"
    DUPLICATE_COUNTERPART = "duplicate_counterpart"  # a duplicate financial posting
    SOURCE_REFERENCE_MISMATCH = "source_reference_mismatch"
    UNEXPECTED_COUNTERPART = "unexpected_counterpart"  # an orphan
    OVER_ACCOUNTED = "over_accounted"  # the counterpart total exceeds what the source states
    AMBIGUOUS = "ambiguous"  # the evidence does not establish agreement or disagreement
    EVIDENCE_MALFORMED = "evidence_malformed"
    STALE_EVIDENCE = "stale_evidence"


class FindingSeverity(StrEnum):
    """How bad it is. Named `FindingSeverity` because `Severity` is M9.2's diagnostic scale.

    Deterministic, table-driven, and **not UI decoration**: `SEVERITY_BY_KIND` is the whole
    definition and a test iterates it to prove no divergence class is missing."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


#: Severity per divergence class. **The mandate's floor is enforced here**: a fabricated financial
#: fact, a duplicate financial posting, and an amount mismatch are `CRITICAL` — never informational.
#:
#: The three that are not critical earn it:
#:
#: * `PERIOD_MISMATCH` is a **warning** because M9.13's `LateEntryDisposition` makes it legitimate:
#:   a fact recorded against a closed period lands in the next open one *by design*, and calling
#:   that an error would flag correct accounting as broken;
#: * `AMBIGUOUS` is a **warning** because it is an honest unknown, not a known wrong. Escalating an
#:   unknown to critical trains operators to ignore critical;
#: * `STALE_EVIDENCE` is a **warning** because the fix is to re-run, not to repair anything.
SEVERITY_BY_KIND: dict[DivergenceKind, FindingSeverity] = {
    DivergenceKind.MISSING_COUNTERPART: FindingSeverity.CRITICAL,
    DivergenceKind.MISSING_SOURCE: FindingSeverity.CRITICAL,
    DivergenceKind.AMOUNT_MISMATCH: FindingSeverity.CRITICAL,
    DivergenceKind.CURRENCY_MISMATCH: FindingSeverity.CRITICAL,
    DivergenceKind.DUPLICATE_SOURCE: FindingSeverity.CRITICAL,
    DivergenceKind.DUPLICATE_COUNTERPART: FindingSeverity.CRITICAL,
    DivergenceKind.OVER_ACCOUNTED: FindingSeverity.CRITICAL,
    DivergenceKind.ACCOUNT_MISMATCH: FindingSeverity.ERROR,
    DivergenceKind.SOURCE_REFERENCE_MISMATCH: FindingSeverity.ERROR,
    DivergenceKind.UNEXPECTED_COUNTERPART: FindingSeverity.ERROR,
    DivergenceKind.EVIDENCE_MALFORMED: FindingSeverity.ERROR,
    DivergenceKind.PERIOD_MISMATCH: FindingSeverity.WARNING,
    DivergenceKind.AMBIGUOUS: FindingSeverity.WARNING,
    DivergenceKind.STALE_EVIDENCE: FindingSeverity.WARNING,
}

#: Severities that must not be ignored — what `open_findings` and the Studio badge count.
ACTIONABLE_SEVERITIES: frozenset[FindingSeverity] = frozenset(
    {FindingSeverity.ERROR, FindingSeverity.CRITICAL}
)

#: How a divergence projects onto M9.2's four-member `ReconciliationVerdict`. A table rather than a
#: chain of `if`s, so the projection is checkable by iteration and cannot quietly lose a member —
#: the same device M9.16 used for `VERDICT_PROJECTION`, and for the same reason.
DIVERGENCE_VERDICT_PROJECTION: dict[DivergenceKind, ReconciliationVerdict] = {
    DivergenceKind.MISSING_COUNTERPART: ReconciliationVerdict.MISSING_IN_LEDGER,
    DivergenceKind.MISSING_SOURCE: ReconciliationVerdict.MISSING_IN_PROVIDER,
    DivergenceKind.UNEXPECTED_COUNTERPART: ReconciliationVerdict.MISSING_IN_PROVIDER,
    DivergenceKind.DUPLICATE_COUNTERPART: ReconciliationVerdict.AMOUNT_DIVERGED,
    DivergenceKind.DUPLICATE_SOURCE: ReconciliationVerdict.AMOUNT_DIVERGED,
    DivergenceKind.AMOUNT_MISMATCH: ReconciliationVerdict.AMOUNT_DIVERGED,
    DivergenceKind.CURRENCY_MISMATCH: ReconciliationVerdict.AMOUNT_DIVERGED,
    DivergenceKind.OVER_ACCOUNTED: ReconciliationVerdict.AMOUNT_DIVERGED,
    DivergenceKind.ACCOUNT_MISMATCH: ReconciliationVerdict.AMOUNT_DIVERGED,
    DivergenceKind.PERIOD_MISMATCH: ReconciliationVerdict.AMOUNT_DIVERGED,
    DivergenceKind.SOURCE_REFERENCE_MISMATCH: ReconciliationVerdict.AMOUNT_DIVERGED,
    DivergenceKind.AMBIGUOUS: ReconciliationVerdict.AMOUNT_DIVERGED,
    DivergenceKind.EVIDENCE_MALFORMED: ReconciliationVerdict.AMOUNT_DIVERGED,
    DivergenceKind.STALE_EVIDENCE: ReconciliationVerdict.AMOUNT_DIVERGED,
}


@dataclass(frozen=True, slots=True)
class RelationshipSpec:
    """**The reconciliation matrix, as data.**

    One row per supported pair, declaring exactly what the mandate requires a relationship to
    document: source, counterpart, which side is authoritative, the matching identity, the fields
    compared, the differences that are acceptable, and who owns the correction.

    Written as a table rather than as six hand-written comparison functions for one reason: six
    functions drift, and a table can be iterated by a test. `RECONCILIATION_MATRIX` is checked for
    completeness, for a single owner per pair, and for a matching identity that is never an amount
    or a timestamp."""

    relationship: RelationshipKind
    source_kind: str
    counterpart_kind: str
    authoritative: str  # "source" | "counterpart" — which side wins an argument about the truth
    matching_identity: str  # the deterministic business identity both sides carry
    compares: tuple[str, ...]
    mode: MatchMode
    owner: RepairOwner
    #: Whether the counterpart may legitimately account for *less* than the source. True only for
    #: `INVOICE_RECOGNITION`, where an unrecognized remainder is deferred revenue rather than a gap.
    allows_partial: bool = False
    #: Whether an unmatched counterpart is an orphan (an error) or simply out of scope (ignored).
    flags_orphans: bool = True
    summary: str = ""


#: The six supported relationships. **No pair has two owners**, and a test proves it by grouping.
#:
#: The two `allows_partial` / `flags_orphans` flags carry the domain judgements that would otherwise
#: hide inside comparison code:
#:
#: * `INVOICE_RECOGNITION` allows partial accounting, because a twelve-month invoice that has
#:   recognized three months is *correct*, not divergent. Only recognizing **more** than was billed
#:   is a divergence, which is why `OVER_ACCOUNTED` exists;
#: * `PAYMENT_INVOICE` does not flag orphans, because an invoice with no payment is an unpaid
#:   invoice — an entirely normal state that M9.17 already has opinions about and M9.19 must not
#:   duplicate as an integrity error.
RECONCILIATION_MATRIX: dict[RelationshipKind, RelationshipSpec] = {
    RelationshipKind.PAYMENT_LEDGER: RelationshipSpec(
        relationship=RelationshipKind.PAYMENT_LEDGER,
        source_kind="payment_intent",
        counterpart_kind="journal_entry",
        authoritative="source",
        matching_identity=(
            "payment attempt id, carried as the ledger entry's source_id"
        ),
        compares=("amount", "currency", "account", "source_reference"),
        mode=MatchMode.PAIRWISE,
        owner=RepairOwner.PAYMENT,
        summary="money moved; a financial fact must record that it did",
    ),
    RelationshipKind.INVOICE_LEDGER: RelationshipSpec(
        relationship=RelationshipKind.INVOICE_LEDGER,
        source_kind="journal_entry",
        counterpart_kind="invoice_line",
        authoritative="source",
        matching_identity="journal entry id, carried on the document line",
        compares=("amount", "currency"),
        mode=MatchMode.PAIRWISE,
        owner=RepairOwner.INVOICE,
        summary="the document must present what the ledger recorded",
    ),
    RelationshipKind.INVOICE_TAX: RelationshipSpec(
        relationship=RelationshipKind.INVOICE_TAX,
        source_kind="tax_assessment",
        counterpart_kind="invoice_tax_line",
        authoritative="source",
        matching_identity="tax assessment id + component code, carried on the document",
        compares=("amount", "currency"),
        mode=MatchMode.PAIRWISE,
        owner=RepairOwner.INVOICE,
        summary="the document must present the determination it references",
    ),
    RelationshipKind.INVOICE_RECOGNITION: RelationshipSpec(
        relationship=RelationshipKind.INVOICE_RECOGNITION,
        source_kind="invoice_snapshot",
        counterpart_kind="revenue_schedule",
        authoritative="source",
        matching_identity="issued snapshot digest, carried in the recognition basis",
        compares=("amount", "currency"),
        mode=MatchMode.AGGREGATE,
        owner=RepairOwner.RECOGNITION,
        allows_partial=True,  # an unrecognized remainder is deferred revenue, not a gap
        summary="billed = recognized + deferred; never more than billed",
    ),
    RelationshipKind.PAYMENT_INVOICE: RelationshipSpec(
        relationship=RelationshipKind.PAYMENT_INVOICE,
        source_kind="payment_intent",
        counterpart_kind="commercial_invoice",
        authoritative="counterpart",  # the document states the obligation; payment answers it
        matching_identity="document id, carried on the payment intent obligation",
        compares=("currency", "account"),
        mode=MatchMode.PAIRWISE,
        owner=RepairOwner.PAYMENT,
        flags_orphans=False,  # an unpaid invoice is not an integrity defect
        summary="a payment must answer an obligation that exists",
    ),
    RelationshipKind.RECOGNITION_LEDGER: RelationshipSpec(
        relationship=RelationshipKind.RECOGNITION_LEDGER,
        source_kind="recognition_entry",
        counterpart_kind="journal_entry",
        authoritative="source",
        matching_identity="recognition entry id, carried as ledger source_id",
        compares=("amount", "currency", "account", "period"),
        mode=MatchMode.PAIRWISE,
        owner=RepairOwner.RECOGNITION,
        summary="revenue was earned; a financial fact must record that it was",
    ),
}


# ── the evidence ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FactRef:
    """One side of a comparison, reduced to what can be compared.

    **A copy of somebody else's decision, never a decision of its own.** Every field arrives already
    determined by the domain that owns it; nothing here is computed, and the amount is carried as
    `Money` — integer minor units — because there is no arithmetic in this domain that could round.

    `identity` is the deterministic business identity from `RelationshipSpec.matching_identity`. It
    is never an amount, never a timestamp, never a description, and a validator asserts it."""

    identity: str
    kind: str
    amount: Money | None = None
    account_id: str = ""
    period_key: str = ""
    source: str = ""  # the ledger `PostingSource`, where one applies
    source_id: str = ""  # what the counterpart says caused it
    state: str = ""
    digest: str = ""  # a content digest, where the owning domain publishes one
    at: float = 0.0
    detail: str = ""

    def evidence_key(self) -> str:
        """The stable string this fact contributes to a run's evidence digest.

        Includes everything a comparison can turn on and nothing that cannot: no observation time,
        no actor, no run. `at` is excluded deliberately — a fact re-read a second later is the same
        fact, and including it would make every run unique and every re-run a new row."""
        money = f"{self.amount.minor}:{self.amount.currency.value}" if self.amount else "-"
        return "\x1e".join(
            (
                self.kind,
                self.identity,
                money,
                self.account_id,
                self.period_key,
                self.source,
                self.source_id,
                self.state,
                self.digest,
            )
        )


@dataclass(frozen=True, slots=True)
class ReconciliationScope:
    """What one run covers. Org-scoped by construction — there is no constructor without it."""

    org_id: str
    relationship: RelationshipKind
    subject_id: str = ""  # the account, document, schedule or intent the run is about
    period_key: str = ""

    @property
    def key(self) -> str:
        """The scope as one string. **Org-first**, so a scope key from org A can never equal one
        from org B even when the subject id is identical — which is the cross-org isolation
        guarantee expressed at the identity layer rather than left to a WHERE clause."""
        return "\x1f".join((self.org_id, self.relationship.value, self.subject_id, self.period_key))


# ── run / finding / resolution ──────────────────────────────────────────────────


class RunState(StrEnum):
    """A run is one evaluation. It is complete the moment it has compared what it read."""

    EVALUATING = "evaluating"
    COMPLETED = "completed"
    FAILED = "failed"  # the evidence could not be read; no conclusion was reached


class FindingState(StrEnum):
    """The life of one divergence.

    `RESOLVED` and `CLOSED` are **terminal by construction** — see `FINDING_LIFECYCLE`. There is no
    `MATCHED` member because a match is not a finding: it is a counter on the run. Making agreement
    a row would fill the table with non-events and make "a finding represents one divergence"
    false."""

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    REPAIR_REQUESTED = "repair_requested"
    RESOLVED = "resolved"
    CLOSED = "closed"  # accepted as permanent; a decision, not a repair


class FindingTrigger(StrEnum):
    ACKNOWLEDGE = "acknowledge"
    REQUEST_REPAIR = "request_repair"
    RESOLVE = "resolve"
    CLOSE = "close"


#: The finding lifecycle. **Read this by what is missing.**
#:
#:   * there is **no edge out of `RESOLVED`** and **no edge out of `CLOSED`**. ADR-0023 PY5 says a
#:     divergence is never auto-resolved; the corollary nobody writes down is that a *resolved* one
#:     is never auto-un-resolved either. A stale worker holding an `OPEN` read cannot reopen a
#:     finding another worker resolved, because the machine has nowhere to put it — and that is a
#:     structural guarantee rather than a `if state == RESOLVED: raise` somebody can delete;
#:   * there is **no `OPEN → MATCHED`**, because `MATCHED` is not a state a finding has. A
#:     divergence that stops being true is resolved *by evidence*, recorded as a resolution, and
#:     the next run simply produces no finding at all;
#:   * there is **no self-loop on `REPAIR_REQUESTED`**. Asking twice is one request — the derived
#:     repair-request id collapses it — not a second transition that would make the audit trail
#:     read as two separate incidents;
#:   * `OPEN → RESOLVE` exists directly, because a divergence can be resolved by evidence that
#:     arrives without anyone having asked for a repair: an in-flight posting that lands a second
#:     later is exactly that case, and forcing it through `REPAIR_REQUESTED` would fabricate a
#:     repair nobody performed.
FINDING_LIFECYCLE: StateMachineDefinition = define(
    [
        (FindingState.OPEN, FindingTrigger.ACKNOWLEDGE, FindingState.ACKNOWLEDGED),
        (FindingState.OPEN, FindingTrigger.REQUEST_REPAIR, FindingState.REPAIR_REQUESTED),
        (FindingState.OPEN, FindingTrigger.RESOLVE, FindingState.RESOLVED),
        (FindingState.OPEN, FindingTrigger.CLOSE, FindingState.CLOSED),
        (
            FindingState.ACKNOWLEDGED,
            FindingTrigger.REQUEST_REPAIR,
            FindingState.REPAIR_REQUESTED,
        ),
        (FindingState.ACKNOWLEDGED, FindingTrigger.RESOLVE, FindingState.RESOLVED),
        (FindingState.ACKNOWLEDGED, FindingTrigger.CLOSE, FindingState.CLOSED),
        (FindingState.REPAIR_REQUESTED, FindingTrigger.RESOLVE, FindingState.RESOLVED),
        (FindingState.REPAIR_REQUESTED, FindingTrigger.CLOSE, FindingState.CLOSED),
    ]
)

#: Findings no worker should touch again. Both are terminal in `FINDING_LIFECYCLE`; this names them
#: so a query can filter on the concept rather than re-deriving it.
TERMINAL_FINDING_STATES: frozenset[FindingState] = frozenset(
    {FindingState.RESOLVED, FindingState.CLOSED}
)

#: Findings still awaiting somebody. What `open_findings` returns.
UNRESOLVED_FINDING_STATES: frozenset[FindingState] = frozenset(
    {FindingState.OPEN, FindingState.ACKNOWLEDGED, FindingState.REPAIR_REQUESTED}
)


@dataclass(frozen=True, slots=True)
class Observation:
    """One comparison outcome, before it becomes durable.

    The probes' output type. A `MATCHED` observation carries `divergence=None` and never becomes a
    finding — it increments a counter on the run."""

    subject_key: str
    divergence: DivergenceKind | None
    source: FactRef | None = None
    counterpart: FactRef | None = None
    expected: str = ""
    observed: str = ""
    detail: str = ""

    @property
    def is_divergent(self) -> bool:
        return self.divergence is not None

    @property
    def severity(self) -> FindingSeverity:
        if self.divergence is None:
            return FindingSeverity.INFO
        return SEVERITY_BY_KIND[self.divergence]


@dataclass(frozen=True, slots=True)
class IntegrityRun:
    """**One evaluation.** Identified by what it looked at, so re-running unchanged evidence is a
    no-op and re-running after a repair is genuinely a new observation.

    `compared` / `matched` / `diverged` are counts of this run's own work. They are not a balance,
    not a running total, and not derived from anything outside the run."""

    id: str
    org_id: str
    scope: ReconciliationScope
    evidence_digest: str
    state: RunState = RunState.EVALUATING
    compared: int = 0
    matched: int = 0
    diverged: int = 0
    max_severity: FindingSeverity = FindingSeverity.INFO
    started_at: float = 0.0
    completed_at: float | None = None
    triggered_by: str = ""
    note: str = ""

    @property
    def relationship(self) -> RelationshipKind:
        return self.scope.relationship

    @property
    def is_clean(self) -> bool:
        return self.state is RunState.COMPLETED and self.diverged == 0


@dataclass(frozen=True, slots=True)
class IntegrityFinding:
    """**One divergence**, recorded immutably except for its lifecycle columns.

    Named `IntegrityFinding` rather than `ReconciliationFinding` because M9.2 already owns that name
    for a payment↔provider observation and shadowing it would produce two types with one name.

    Carries both sides' facts verbatim so the disagreement is legible without re-reading the domains
    that produced it — which matters because by the time somebody looks, one of them will have moved
    on. `severity` is written once at detection and **never changes**: a resolution records that a
    critical divergence was fixed, it does not retroactively make it a warning."""

    id: str
    org_id: str
    run_id: str
    relationship: RelationshipKind
    subject_key: str
    divergence: DivergenceKind
    severity: FindingSeverity
    owner: RepairOwner
    state: FindingState = FindingState.OPEN
    source: FactRef | None = None
    counterpart: FactRef | None = None
    expected: str = ""
    observed: str = ""
    detail: str = ""
    account_id: str = ""
    detected_at: float = 0.0
    acknowledged_at: float | None = None
    acknowledged_by: str = ""
    repair_request_id: str = ""
    resolution_id: str = ""
    resolved_at: float | None = None
    resolved_by: str = ""
    version: int = 0
    verdict: ReconciliationVerdict = ReconciliationVerdict.MATCHED

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_FINDING_STATES

    @property
    def needs_attention(self) -> bool:
        return not self.is_terminal and self.severity in ACTIONABLE_SEVERITIES


@dataclass(frozen=True, slots=True)
class RepairRequest:
    """**A request, not a repair.**

    M9.19 records that a domain was asked to fix something and, for the two relationships where the
    owning domain publishes its own repair command, dispatches it through that domain's Application
    boundary. It never performs the repair, and it never touches the owning domain's storage.

    `dispatched_command` names what was asked for; `accepted` records whether the owner took it.
    Neither is evidence that the divergence is gone — only a later run is."""

    id: str
    org_id: str
    finding_id: str
    owner: RepairOwner
    action: str
    request_key: str
    dispatched_command: str = ""
    accepted: bool = False
    detail: str = ""
    requested_at: float = 0.0
    requested_by: str = ""


@dataclass(frozen=True, slots=True)
class IntegrityResolution:
    """**Evidence that a divergence was addressed.** Append-only; there is no update path.

    Every field the mandate requires a resolution to identify is here, and `evidence_digest` is a
    digest over the *new* facts — so "this was fixed" is a claim backed by what the books say now,
    not by an assertion somebody typed."""

    id: str
    org_id: str
    finding_id: str
    run_id: str
    owner: RepairOwner
    action: str
    request_key: str = ""
    repair_request_id: str = ""
    source_command_id: str = ""
    resulting_source: FactRef | None = None
    resulting_counterpart: FactRef | None = None
    evidence_digest: str = ""
    resolved_at: float = 0.0
    resolved_by: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class IntegrityActivity:
    """The audit trail. Append-only."""

    id: str
    org_id: str
    run_id: str
    at: float = 0.0
    kind: str = ""
    summary: str = ""
    actor_id: str = ""
    finding_id: str = ""
    detail: dict[str, str] = field(default_factory=dict)


# ── explanation / inspection / search ───────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FindingExplanation:
    """*Why does this finding exist?* answered from **stored evidence**, without re-reading a
    single domain.

    The mandate's worked example is this type's acceptance test: payment → SUCCEEDED → expected
    ledger posting → no matching ledger fact → `MISSING_COUNTERPART` → severity `CRITICAL` → owner
    `PAYMENT` → repair requested → rerun → matched. Every one of those steps is a field here, so a
    log line is never the source of truth."""

    finding_id: str
    relationship: RelationshipKind
    divergence: DivergenceKind
    severity: FindingSeverity
    owner: RepairOwner
    state: FindingState
    narrative: tuple[str, ...] = ()
    source_fact: str = ""
    counterpart_fact: str = ""
    expected: str = ""
    observed: str = ""
    matching_identity: str = ""
    authoritative_side: str = ""
    evidence_digest: str = ""
    detected_at: float = 0.0
    resolution: str = ""


@dataclass(frozen=True, slots=True)
class RunInspection:
    """A structural read of one run: scoped, complete, explainable, and internally consistent."""

    run_id: str
    relationship: RelationshipKind
    state: RunState
    compared: int = 0
    matched: int = 0
    diverged: int = 0
    findings_recorded: int = 0
    is_counted_correctly: bool = True
    is_scoped: bool = True
    is_complete: bool = True
    unresolved: int = 0
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class IntegritySearchQuery:
    """A scoped, bounded search. `org_id` is required; there is no unscoped read."""

    org_id: str
    relationships: tuple[RelationshipKind, ...] = ()
    states: tuple[FindingState, ...] = ()
    severities: tuple[FindingSeverity, ...] = ()
    owners: tuple[RepairOwner, ...] = ()
    account_id: str = ""
    subject: str = ""
    limit: int = 50


@dataclass(frozen=True, slots=True)
class IntegritySearchResult:
    findings: tuple[IntegrityFinding, ...] = ()
    total: int = 0
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class IntegrityPosture:
    """The org's integrity at a glance — **counts, never money**.

    There is no amount on this type and there never will be. A "total divergent value" would be a
    figure M9.19 computed, and the moment such a number exists somebody reports it, at which point
    reconciliation has quietly become a second set of books."""

    org_id: str
    runs: int = 0
    findings_open: int = 0
    findings_acknowledged: int = 0
    findings_repair_requested: int = 0
    findings_resolved: int = 0
    findings_closed: int = 0
    critical: int = 0
    error: int = 0
    warning: int = 0
    by_relationship: dict[str, int] = field(default_factory=dict)
    by_owner: dict[str, int] = field(default_factory=dict)

    @property
    def is_clean(self) -> bool:
        return self.critical == 0 and self.error == 0


def fact_summary(ref: FactRef | None) -> str:
    """One fact as one human-readable line. Used by the explainer and by nothing that decides."""
    if ref is None:
        return "(absent)"
    money = f"{ref.amount.minor} {ref.amount.currency.value}" if ref.amount else "no amount"
    bits = [f"{ref.kind} {ref.identity}", money]
    if ref.state:
        bits.append(f"state={ref.state}")
    if ref.account_id:
        bits.append(f"account={ref.account_id}")
    if ref.period_key:
        bits.append(f"period={ref.period_key}")
    return " · ".join(bits)


def currencies_of(refs: tuple[FactRef, ...]) -> frozenset[Currency]:
    return frozenset(r.amount.currency for r in refs if r.amount is not None)


def total_minor(refs: tuple[FactRef, ...]) -> int:
    """Sum of minor units. **Integer only**, and only ever over facts other domains decided.

    This is the sole summation in the package and it exists for `MatchMode.AGGREGATE`. It does not
    round, does not convert, and does not touch a currency — the caller has already established that
    the set is single-currency, because summing across currencies would be a foreign-exchange
    opinion Billing does not hold (ADR-0023 RS5)."""
    return sum(r.amount.minor for r in refs if r.amount is not None)


__all__ = [
    "ACTIONABLE_SEVERITIES",
    "DIVERGENCE_VERDICT_PROJECTION",
    "FINDING_LIFECYCLE",
    "RECONCILIATION_MATRIX",
    "SEVERITY_BY_KIND",
    "TERMINAL_FINDING_STATES",
    "UNRESOLVED_FINDING_STATES",
    "DivergenceKind",
    "FactRef",
    "FindingExplanation",
    "FindingSeverity",
    "FindingState",
    "FindingTrigger",
    "IntegrityActivity",
    "IntegrityFinding",
    "IntegrityPosture",
    "IntegrityResolution",
    "IntegrityRun",
    "IntegritySearchQuery",
    "IntegritySearchResult",
    "MatchMode",
    "Observation",
    "ReconciliationScope",
    "RelationshipKind",
    "RelationshipSpec",
    "RepairOwner",
    "RepairRequest",
    "RunInspection",
    "RunState",
    "currencies_of",
    "derive_evidence_digest",
    "derive_finding_id",
    "derive_repair_request_id",
    "derive_resolution_id",
    "derive_run_id",
    "fact_summary",
    "total_minor",
]
