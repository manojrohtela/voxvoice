"""Reconciliation & Financial Integrity application layer (M9.19) — commands, queries, use cases.

Additive to M9.3/M9.6–M9.18: the buses, Unit of Work, authorization orchestrator, application-event
vocabulary and dispatch discipline are reused exactly as they are. This module adds 10 commands,
15 queries, their use cases, and the view DTOs — and nothing else.

## What crosses this layer's boundaries, and in which direction

    Ledger ──┐
    Invoice ─┤
    Tax ─────┼── read (repositories, through the context) ──▶ Reconciliation
    Payment ─┤                                                     │
    Revenue ─┘                                                     │
                                                                   ├─▶ emits billing.integrity.*
                                                                   │
                          repair ◀── command (two, whitelisted) ────┘

**Five read arrows and two write arrows, and the write arrows do not write anything financial.**
Every read is of an immutable fact another domain already decided; nothing here recomputes a price,
a rate, a tax, an invoice total, a recognized amount or a balance. The two commands in
`REPAIR_COMMANDS` are the owning domains' *own* declared repairs, dispatched through their own
Application boundaries:

* `PostRecognitionToLedger` — M9.18's declared repair for a recognition whose posting never landed;
* `ReconcilePaymentAttempt` — M9.16's declared way to find out what actually happened to an attempt.

`PostJournalEntry` is **deliberately absent from that table and from this module's imports**. If
reconciliation could post a journal entry it would be creating the financial fact it claims to have
observed, and every "missing ledger posting" finding would resolve itself into existence. A test
asserts the absence rather than trusting the comment.

## Reconciliation is not auto-resolution

`ResolveFinding` does not close a finding because a caller said so. It reads both sides *again*, and
`guard_evidence_establishes_agreement` refuses the resolution unless the new facts actually agree —
same amount, same currency, both present. Without that guard the command is `auto_fix()` under
another name: somebody calls it, the row says resolved, and the books still disagree.

That is ADR-0023 **PY5** enforced rather than quoted, and it is the reason the resolution path reads
the domains a second time instead of trusting the repair receipt.

## Where the evidence comes from

`_EvidenceReader` holds one method per relationship. Each reads the *owning* domain's repositories
through the context, reduces what it finds to `FactRef` — a copy of an identity, an amount, an
account, a period — and hands two sequences to `MatchingService`. Two of the six delegate instead:
M9.14 and M9.15 already own the comparison for their pairs, so `InvoiceReconciliationService` and
`TaxReconciliationService` produce the discrepancies and `DiscrepancyAdapter` lifts them. Writing a
second opinion about a pair somebody already owns is how one divergence gets two severities.

## No scheduler

`OpenFindings` and `ListIntegrityRuns` are reads. Nothing in this package loops, sleeps, polls or
spawns; whoever decides *when* to reconcile lives outside this plane, exactly as it does for M9.17's
`/recovery/cases/due` and M9.18's `/revenue/entries/due`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from billing.foundation import BillingConflict as _Conflict
from billing.foundation import (
    BillingError,
    Currency,
    DivergenceKind,
    FactRef,
    FindingSeverity,
    FindingState,
    FindingTrigger,
    IntegrityFinding,
    IntegrityRun,
    IntegritySearchQuery,
    Money,
    Observation,
    ReconciliationScope,
    RelationshipKind,
    RepairOwner,
    RunState,
    owner_for_divergence,
)
from billing.foundation import BillingNotFound as _NotFound

from .authorization import AuthorizationOrchestrator
from .authorization import BillingPermission as P
from .commands import _Command
from .events import PREFIX_APP_EVENT, ApplicationEvent, ApplicationEventType
from .mapping import MoneyView
from .payment_engine import ReconcilePaymentAttempt
from .queries import _Query
from .revenue_engine import PostRecognitionToLedger
from .unit_of_work import UnitOfWorkFactory

#: **The whole repair surface.** Two entries, both the owning domain's own declared repair command,
#: both dispatched through that domain's Application boundary.
#:
#: `PostJournalEntry` is deliberately absent, and its absence is the point: a reconciliation engine
#: that could post a journal entry would create the financial fact it claims to have observed, and
#: every `MISSING_COUNTERPART` finding would resolve itself into existence.
#:
#: Everything not in this table names an owner and stops. `RepairOwner.OPERATOR` is not a gap in
#: the table — it is the honest answer for divergences no automatic action can fix.
REPAIR_COMMANDS: dict[tuple[RelationshipKind, DivergenceKind], tuple[str, type]] = {
    (RelationshipKind.RECOGNITION_LEDGER, DivergenceKind.MISSING_COUNTERPART): (
        "post_recognition_to_ledger",
        PostRecognitionToLedger,
    ),
    (RelationshipKind.PAYMENT_LEDGER, DivergenceKind.MISSING_COUNTERPART): (
        "reconcile_payment_attempt",
        ReconcilePaymentAttempt,
    ),
    (RelationshipKind.PAYMENT_LEDGER, DivergenceKind.AMOUNT_MISMATCH): (
        "reconcile_payment_attempt",
        ReconcilePaymentAttempt,
    ),
    (RelationshipKind.PAYMENT_LEDGER, DivergenceKind.STALE_EVIDENCE): (
        "reconcile_payment_attempt",
        ReconcilePaymentAttempt,
    ),
}

#: Ledger posting sources that a payment produces, as stored strings. Declared rather than imported
#: as an enum so the counterpart filter reads the same way the row does.
_PAYMENT_SOURCES = ("payment", "refund")
_RECOGNITION_SOURCE = "revenue_recognition"

#: Only reached when an aggregate resolution has counterparts but no source amount to take a
#: currency from — a shape the guards already refuse. Declared rather than defaulted silently.
INR_FALLBACK = Currency.INR


# ── commands ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ReconcilePaymentLedger(_Command):
    """Did every movement of money produce a financial fact, and did every such fact move money?

    Scoped to one account. **Neither side is proof of the other**: a `CAPTURED` attempt is not
    evidence that a ledger entry exists, and a ledger entry with `source=payment` is not evidence
    that a provider moved anything. Both facts are read independently, which is the only way the
    comparison means anything."""

    account_id: str = ""
    period_key: str = ""


@dataclass(frozen=True, slots=True)
class ReconcileInvoiceLedger(_Command):
    """Does the document present what the ledger recorded?

    Delegates the comparison to M9.14's own `InvoiceReconciliationService`, which already owns it —
    including the header-versus-body totals check. **Nothing here recalculates an invoice total**,
    and nothing asks Pricing or Rating to regenerate the document."""

    document_id: str = ""


@dataclass(frozen=True, slots=True)
class ReconcileInvoiceTax(_Command):
    """Does the document present the determination it references?

    Delegates to M9.15's own `TaxReconciliationService`. **No rate is looked up, no jurisdiction is
    resolved, and no current rule is consulted** — the comparison is against the immutable
    determination the document named, not against what the rules say today."""

    document_id: str = ""
    assessment_id: str = ""


@dataclass(frozen=True, slots=True)
class ReconcileInvoiceRecognition(_Command):
    """Is the billed amount fully accounted for by billed = recognized + deferred?

    An aggregate comparison, and the **only relationship where "less" is not a divergence**: a
    twelve-month invoice that has recognized three months is correct, and the other nine are
    deferred revenue. Only recognizing *more* than was billed diverges."""

    document_id: str = ""


@dataclass(frozen=True, slots=True)
class ReconcilePaymentInvoice(_Command):
    """Does every payment answer an obligation that exists?

    An unpaid invoice is **not** a finding — that is a collections question M9.17 already owns, and
    duplicating it here as an integrity error would give one situation two owners."""

    account_id: str = ""


@dataclass(frozen=True, slots=True)
class ReconcileRecognitionLedger(_Command):
    """Did every recognized period produce a financial fact?

    Scoped to one schedule. Recognition owns the decision, the Ledger owns the fact, and this
    verifies the relationship without being allowed to create either."""

    schedule_id: str = ""


@dataclass(frozen=True, slots=True)
class AcknowledgeFinding(_Command):
    """Somebody has seen it. Attributed, because an unattributed acknowledgement is a no-op."""

    finding_id: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class RequestFindingRepair(_Command):
    """Ask the **owning domain** to fix it. M9.19 never fixes anything itself.

    `request_key` makes the ask idempotent: two workers requesting the same repair produce one
    request rather than two, which matters because the dispatched command may move money."""

    finding_id: str = ""
    request_key: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class ResolveFinding(_Command):
    """Close a divergence **with evidence**.

    Re-reads both sides and refuses unless they now agree. This is ADR-0023 PY5 enforced: without
    the re-read the command is `auto_fix()`, and the finding would say resolved while the books
    still disagreed."""

    finding_id: str = ""
    request_key: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class CloseFinding(_Command):
    """Accept a divergence as permanent — a **decision**, not a repair.

    Separate from `ResolveFinding` deliberately: resolved means the books agree, closed means
    somebody decided to live with it. Collapsing the two would let "we gave up" be reported as
    "we fixed it"."""

    finding_id: str = ""
    reason: str = ""


ALL_RECONCILIATION_COMMANDS = (
    ReconcilePaymentLedger,
    ReconcileInvoiceLedger,
    ReconcileInvoiceTax,
    ReconcileInvoiceRecognition,
    ReconcilePaymentInvoice,
    ReconcileRecognitionLedger,
    AcknowledgeFinding,
    RequestFindingRepair,
    ResolveFinding,
    CloseFinding,
)


# ── queries ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class GetIntegrityRun(_Query):
    run_id: str = ""


@dataclass(frozen=True, slots=True)
class ListIntegrityRuns(_Query):
    relationship: str = ""
    subject_id: str = ""
    limit: int = 100


@dataclass(frozen=True, slots=True)
class GetIntegrityFinding(_Query):
    finding_id: str = ""


@dataclass(frozen=True, slots=True)
class ListRunFindings(_Query):
    run_id: str = ""


@dataclass(frozen=True, slots=True)
class OpenFindings(_Query):
    """Unresolved divergences, worst first. **The read an external scheduler polls** — Billing
    runs no scheduler, and whoever decides when to look lives outside this plane."""

    severities: tuple[str, ...] = ()
    limit: int = 200


@dataclass(frozen=True, slots=True)
class FindingsByOwner(_Query):
    owner: str = ""
    limit: int = 200


@dataclass(frozen=True, slots=True)
class FindingsForAccount(_Query):
    account_id: str = ""
    limit: int = 200


@dataclass(frozen=True, slots=True)
class ExplainFinding(_Query):
    """*Why does this finding exist?* — answered from stored evidence, without re-reading a
    domain."""

    finding_id: str = ""


@dataclass(frozen=True, slots=True)
class InspectIntegrityRun(_Query):
    run_id: str = ""


@dataclass(frozen=True, slots=True)
class ListRepairRequests(_Query):
    finding_id: str = ""


@dataclass(frozen=True, slots=True)
class ListFindingResolutions(_Query):
    finding_id: str = ""


@dataclass(frozen=True, slots=True)
class SearchIntegrityFindings(_Query):
    relationships: tuple[str, ...] = ()
    states: tuple[str, ...] = ()
    severities: tuple[str, ...] = ()
    owners: tuple[str, ...] = ()
    account_id: str = ""
    subject: str = ""
    limit: int = 50


@dataclass(frozen=True, slots=True)
class GetIntegrityPosture(_Query):
    """Counts, derived on every call. **There is no stored posture and no monetary total.**"""

    limit: int = 500


@dataclass(frozen=True, slots=True)
class IntegrityTimeline(_Query):
    run_id: str = ""
    limit: int = 200


@dataclass(frozen=True, slots=True)
class GetReconciliationMatrix(_Query):
    """The declared relationships, as data. What Studio renders instead of hard-coding them."""


ALL_RECONCILIATION_QUERIES = (
    GetIntegrityRun,
    ListIntegrityRuns,
    GetIntegrityFinding,
    ListRunFindings,
    OpenFindings,
    FindingsByOwner,
    FindingsForAccount,
    ExplainFinding,
    InspectIntegrityRun,
    ListRepairRequests,
    ListFindingResolutions,
    SearchIntegrityFindings,
    GetIntegrityPosture,
    IntegrityTimeline,
    GetReconciliationMatrix,
)


# ── views ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FactRefView:
    identity: str
    kind: str
    amount: MoneyView | None = None
    account_id: str = ""
    period_key: str = ""
    source: str = ""
    source_id: str = ""
    state: str = ""
    summary: str = ""


@dataclass(frozen=True, slots=True)
class IntegrityRunView:
    id: str
    org_id: str
    relationship: str
    subject_id: str
    period_key: str
    evidence_digest: str
    state: str
    compared: int
    matched: int
    diverged: int
    max_severity: str
    is_clean: bool
    started_at: float
    completed_at: float | None = None
    triggered_by: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class IntegrityFindingView:
    id: str
    org_id: str
    run_id: str
    relationship: str
    subject_key: str
    divergence: str
    severity: str
    owner: str
    state: str
    verdict: str
    expected: str
    observed: str
    detail: str
    account_id: str
    detected_at: float
    source: FactRefView | None = None
    counterpart: FactRefView | None = None
    repair_request_id: str = ""
    resolution_id: str = ""
    resolved_at: float | None = None
    resolved_by: str = ""
    version: int = 0
    allowed_triggers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RepairRequestView:
    id: str
    finding_id: str
    owner: str
    action: str
    request_key: str
    dispatched_command: str
    accepted: bool
    detail: str
    requested_at: float
    requested_by: str


@dataclass(frozen=True, slots=True)
class IntegrityResolutionView:
    id: str
    finding_id: str
    run_id: str
    owner: str
    action: str
    evidence_digest: str
    resolved_at: float
    resolved_by: str
    note: str = ""
    resulting_source: FactRefView | None = None
    resulting_counterpart: FactRefView | None = None


@dataclass(frozen=True, slots=True)
class FindingExplanationView:
    finding_id: str
    relationship: str
    divergence: str
    severity: str
    owner: str
    state: str
    narrative: tuple[str, ...]
    source_fact: str
    counterpart_fact: str
    expected: str
    observed: str
    matching_identity: str
    authoritative_side: str
    evidence_digest: str
    detected_at: float
    resolution: str = ""


@dataclass(frozen=True, slots=True)
class RunInspectionView:
    run_id: str
    relationship: str
    state: str
    compared: int
    matched: int
    diverged: int
    findings_recorded: int
    is_counted_correctly: bool
    is_scoped: bool
    is_complete: bool
    unresolved: int
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IntegrityPostureView:
    org_id: str
    runs: int
    findings_open: int
    findings_acknowledged: int
    findings_repair_requested: int
    findings_resolved: int
    findings_closed: int
    critical: int
    error: int
    warning: int
    is_clean: bool
    by_relationship: dict
    by_owner: dict


@dataclass(frozen=True, slots=True)
class RelationshipView:
    relationship: str
    source_kind: str
    counterpart_kind: str
    authoritative: str
    matching_identity: str
    compares: tuple[str, ...]
    mode: str
    owner: str
    allows_partial: bool
    flags_orphans: bool
    summary: str


@dataclass(frozen=True, slots=True)
class IntegrityActivityView:
    id: str
    run_id: str
    at: float
    kind: str
    summary: str
    actor_id: str
    finding_id: str
    detail: dict


@dataclass(frozen=True, slots=True)
class IntegritySearchView:
    findings: tuple[IntegrityFindingView, ...]
    total: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class ReconciliationReceiptView:
    """What one reconciliation command returns: the run, plus what it found."""

    run: IntegrityRunView
    findings: tuple[IntegrityFindingView, ...]
    is_new: bool


def money_view(amount: Money | None) -> MoneyView | None:
    return MoneyView(minor=amount.minor, currency=amount.currency.value) if amount else None


def fact_view(ref: FactRef | None) -> FactRefView | None:
    from billing.foundation import fact_summary

    if ref is None:
        return None
    return FactRefView(
        identity=ref.identity,
        kind=ref.kind,
        amount=money_view(ref.amount),
        account_id=ref.account_id,
        period_key=ref.period_key,
        source=ref.source,
        source_id=ref.source_id,
        state=ref.state,
        summary=fact_summary(ref),
    )


def integrity_run_view(run: IntegrityRun) -> IntegrityRunView:
    return IntegrityRunView(
        id=run.id,
        org_id=run.org_id,
        relationship=run.relationship.value,
        subject_id=run.scope.subject_id,
        period_key=run.scope.period_key,
        evidence_digest=run.evidence_digest,
        state=run.state.value,
        compared=run.compared,
        matched=run.matched,
        diverged=run.diverged,
        max_severity=run.max_severity.value,
        is_clean=run.is_clean,
        started_at=run.started_at,
        completed_at=run.completed_at,
        triggered_by=run.triggered_by,
        note=run.note,
    )


def finding_view(
    finding: IntegrityFinding, allowed: tuple[FindingTrigger, ...] = ()
) -> IntegrityFindingView:
    return IntegrityFindingView(
        id=finding.id,
        org_id=finding.org_id,
        run_id=finding.run_id,
        relationship=finding.relationship.value,
        subject_key=finding.subject_key,
        divergence=finding.divergence.value,
        severity=finding.severity.value,
        owner=finding.owner.value,
        state=finding.state.value,
        verdict=finding.verdict.value,
        expected=finding.expected,
        observed=finding.observed,
        detail=finding.detail,
        account_id=finding.account_id,
        detected_at=finding.detected_at,
        source=fact_view(finding.source),
        counterpart=fact_view(finding.counterpart),
        repair_request_id=finding.repair_request_id,
        resolution_id=finding.resolution_id,
        resolved_at=finding.resolved_at,
        resolved_by=finding.resolved_by,
        version=finding.version,
        allowed_triggers=tuple(t.value for t in allowed),
    )


def request_view(request) -> RepairRequestView:
    return RepairRequestView(
        id=request.id,
        finding_id=request.finding_id,
        owner=request.owner.value,
        action=request.action,
        request_key=request.request_key,
        dispatched_command=request.dispatched_command,
        accepted=request.accepted,
        detail=request.detail,
        requested_at=request.requested_at,
        requested_by=request.requested_by,
    )


def resolution_view(resolution) -> IntegrityResolutionView:
    return IntegrityResolutionView(
        id=resolution.id,
        finding_id=resolution.finding_id,
        run_id=resolution.run_id,
        owner=resolution.owner.value,
        action=resolution.action,
        evidence_digest=resolution.evidence_digest,
        resolved_at=resolution.resolved_at,
        resolved_by=resolution.resolved_by,
        note=resolution.note,
        resulting_source=fact_view(resolution.resulting_source),
        resulting_counterpart=fact_view(resolution.resulting_counterpart),
    )


def reconciliation_explanation_view(explanation) -> FindingExplanationView:
    return FindingExplanationView(
        finding_id=explanation.finding_id,
        relationship=explanation.relationship.value,
        divergence=explanation.divergence.value,
        severity=explanation.severity.value,
        owner=explanation.owner.value,
        state=explanation.state.value,
        narrative=explanation.narrative,
        source_fact=explanation.source_fact,
        counterpart_fact=explanation.counterpart_fact,
        expected=explanation.expected,
        observed=explanation.observed,
        matching_identity=explanation.matching_identity,
        authoritative_side=explanation.authoritative_side,
        evidence_digest=explanation.evidence_digest,
        detected_at=explanation.detected_at,
        resolution=explanation.resolution,
    )


def reconciliation_activity_view(activity) -> IntegrityActivityView:
    return IntegrityActivityView(
        id=activity.id,
        run_id=activity.run_id,
        at=activity.at,
        kind=activity.kind,
        summary=activity.summary,
        actor_id=activity.actor_id,
        finding_id=activity.finding_id,
        detail=dict(activity.detail),
    )


# ── seams ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ReconciliationRepositories:
    """The five integrity seams, injected. Interfaces only — implementations in persistence.

    Five, and only five. **Nothing here reaches a ledger, invoice, tax, payment, collection or
    recognition table.** Those arrive as *reads* through the context, and the two repairs leave as
    *commands* dispatched into their owning engines."""

    runs: object
    findings: object
    requests: object
    resolutions: object
    activity: object


@dataclass(frozen=True, slots=True)
class ReconciliationServices:
    """The Foundation services this layer coordinates. Composed once, held by reference."""

    matcher: object
    lifecycle: object
    guards: object
    posture: object
    inspector: object
    explainer: object
    search: object
    validator: object
    adapter: object


class _EvidenceReader:
    """Reads immutable facts from the domains that own them and reduces them to `FactRef`.

    **The only place M9.19 touches another domain's storage, and it only ever reads.** Every method
    here returns copies — an identity, an amount, an account, a period — and none of them computes
    anything. An unwired domain is a refusal rather than an empty comparison, because "I found no
    ledger entries" and "there is no ledger" must not produce the same critical finding."""

    def __init__(self, context) -> None:
        self._ctx = context

    # ── seam access ─────────────────────────────────────────────────────────────

    def _engine(self, name: str, why: str):
        engine = getattr(self._ctx, name, None)
        if engine is None:
            raise _Conflict(
                f"the {name.replace('_', ' ')} is not wired; {why} cannot be reconciled against "
                "a domain that is absent"
            )
        return engine

    # ── payment ↔ ledger ────────────────────────────────────────────────────────

    def payment_ledger(self, org_id: str, account_id: str) -> tuple[tuple, tuple]:
        """Successful attempts against the postings that should record them.

        **Both sides read independently.** A `CAPTURED` attempt is not evidence that a ledger entry
        exists, and a ledger entry with `source=payment` is not evidence that money moved. Reading
        one and inferring the other is exactly the mistake this pair exists to catch."""
        payments = self._engine("payment_engine", "payments")
        ledger = self._engine("ledger_engine", "the ledger")
        sources: list[FactRef] = []
        for intent in self._intents_for_account(payments, org_id, account_id):
            for attempt in payments.attempts.for_intent(org_id, intent.id):
                if not self._is_successful(attempt):
                    continue
                sources.append(
                    FactRef(
                        identity=attempt.id,
                        kind="payment_attempt",
                        amount=attempt.amount,
                        account_id=attempt.account_id,
                        state=str(getattr(attempt.state, "value", attempt.state)),
                        detail=f"intent {intent.id}",
                    )
                )
        counterparts = [
            FactRef(
                identity=entry.source_id,
                kind="journal_entry",
                amount=entry.amount,
                account_id=entry.account_id,
                period_key=entry.period_key,
                source=entry.source.value,
                source_id=entry.source_id,
                state=entry.recognition.value,
                detail=entry.id,
            )
            for entry in ledger.entries.for_account(org_id, account_id)
            if entry.source.value in _PAYMENT_SOURCES and entry.source_id
        ]
        return tuple(sources), tuple(counterparts)

    @staticmethod
    def _is_successful(attempt) -> bool:
        """Only a settled success is expected to have produced a financial fact.

        **`AMBIGUOUS` is deliberately excluded** — M9.16's lesson, carried forward. An attempt whose
        outcome nobody knows is not evidence that money moved, so demanding a ledger entry for it
        would manufacture a critical finding out of an honest unknown."""
        state = str(getattr(attempt.state, "value", attempt.state))
        return state in ("captured", "settled", "succeeded")

    @staticmethod
    def _intents_for_account(payments, org_id: str, account_id: str):
        finder = getattr(payments.intents, "for_account", None)
        if finder is not None:
            return finder(org_id, account_id)
        return tuple(
            i for i in payments.intents.all(org_id) if i.account_id == account_id
        )  # pragma: no cover — every implementation provides for_account

    # ── invoice ↔ ledger (delegated to M9.14) ───────────────────────────────────

    def invoice_ledger(self, org_id: str, document_id: str):
        """M9.14 already owns this comparison; this reads the inputs and hands them over."""
        invoices = self._engine("invoice_engine", "invoices")
        ledger = self._engine("ledger_engine", "the ledger")
        document = invoices.documents.get(org_id, document_id)
        if document is None:
            raise _NotFound(f"document {document_id!r} not found")
        snapshot = None
        if document.issued_snapshot_digest:
            snapshot = invoices.snapshots.by_digest(org_id, document.issued_snapshot_digest)
        entries = ledger.entries.for_period(
            org_id, document.account_id, document.accounting_period_key
        )
        pairs = tuple((e.id, e.signed_minor) for e in entries)
        refs = tuple(
            FactRef(
                identity=e.id,
                kind="journal_entry",
                amount=e.amount,
                account_id=e.account_id,
                period_key=e.period_key,
                source=e.source.value,
                source_id=e.source_id,
            )
            for e in entries
        )
        return document, snapshot, pairs, refs

    # ── invoice ↔ tax (delegated to M9.15) ──────────────────────────────────────

    def invoice_tax(self, org_id: str, document_id: str, assessment_id: str):
        """M9.15 already owns this comparison. **No rate lookup, no jurisdiction resolution.**"""
        invoices = self._engine("invoice_engine", "invoices")
        tax = self._engine("tax_engine", "tax determinations")
        document = invoices.documents.get(org_id, document_id)
        if document is None:
            raise _NotFound(f"document {document_id!r} not found")
        snapshot = None
        if document.issued_snapshot_digest:
            snapshot = invoices.snapshots.by_digest(org_id, document.issued_snapshot_digest)
        assessment = None
        if assessment_id:
            assessment = tax.assessments.get(org_id, assessment_id)
        elif hasattr(tax.assessments, "for_subject"):
            found = tax.assessments.for_subject(org_id, document_id)
            assessment = found[-1] if found else None
        return document, snapshot, assessment

    # ── invoice ↔ recognition ───────────────────────────────────────────────────

    def invoice_recognition(self, org_id: str, document_id: str) -> tuple[tuple, tuple]:
        """Billed against recognized-plus-adjusted. An **aggregate** comparison.

        The billed figure is copied from the issued snapshot — `total − tax_total`, exactly the
        figure M9.18 recognizes against, read rather than recomputed. Using a different definition
        here than M9.18 uses would make every schedule diverge by the tax amount."""
        invoices = self._engine("invoice_engine", "invoices")
        revenue = self._engine("revenue_engine", "revenue recognition")
        document = invoices.documents.get(org_id, document_id)
        if document is None:
            raise _NotFound(f"document {document_id!r} not found")
        if not document.issued_snapshot_digest:
            raise _Conflict(
                f"document {document_id!r} has not been issued; only an issued document states an "
                "amount that recognition can be reconciled against"
            )
        snapshot = invoices.snapshots.by_digest(org_id, document.issued_snapshot_digest)
        if snapshot is None:
            raise _NotFound(f"document {document_id!r} has no issued content")
        billed = self._net_of_tax(snapshot)
        sources = (
            FactRef(
                identity=document.id,
                kind="invoice_snapshot",
                amount=billed,
                account_id=document.account_id,
                digest=document.issued_snapshot_digest,
                state=str(getattr(document.state, "value", document.state)),
            ),
        )
        counterparts: list[FactRef] = []
        from billing.foundation import RECOGNIZED_ENTRY_STATES

        recognized_states = {s.value for s in RECOGNIZED_ENTRY_STATES}
        for schedule in revenue.schedules.for_document(org_id, document_id):
            if str(getattr(schedule.state, "value", schedule.state)) == "superseded":
                continue
            for entry in revenue.entries.for_schedule(org_id, schedule.id):
                if str(getattr(entry.state, "value", entry.state)) not in recognized_states:
                    continue
                counterparts.append(
                    FactRef(
                        identity=entry.id,
                        kind="recognition_entry",
                        amount=entry.amount,
                        account_id=entry.account_id,
                        state=str(getattr(entry.state, "value", entry.state)),
                        detail=schedule.id,
                    )
                )
            for adjustment in revenue.adjustments.for_schedule(org_id, schedule.id):
                counterparts.append(
                    FactRef(
                        identity=adjustment.id,
                        kind="recognition_adjustment",
                        amount=adjustment.amount,  # signed; a reversal is negative
                        account_id=adjustment.account_id,
                        state=str(getattr(adjustment.kind, "value", adjustment.kind)),
                        detail=schedule.id,
                    )
                )
        return sources, tuple(counterparts)

    @staticmethod
    def _net_of_tax(snapshot) -> Money:
        """`total − tax_total`. **The same definition M9.18 recognizes against**, read not computed.

        Tax is a liability collected for an authority and is never revenue. M9.15 determined it and
        M9.14 presented it; this subtracts two figures they already agreed on, in integer minor
        units. There is no rate, no jurisdiction and no tax component anywhere near it."""
        totals = getattr(snapshot, "totals", None)
        if totals is None:
            raise _Conflict("this document snapshot states no totals; nothing to reconcile")
        gross = getattr(totals, "total", None)
        tax = getattr(totals, "tax_total", None)
        if isinstance(gross, Money) and isinstance(tax, Money):
            return Money(gross.minor - tax.minor, gross.currency)
        if isinstance(gross, Money):
            return gross
        raise _Conflict("this document snapshot states no comparable amount")

    # ── payment ↔ invoice ───────────────────────────────────────────────────────

    def payment_invoice(self, org_id: str, account_id: str) -> tuple[tuple, tuple]:
        """Does every payment answer an obligation that exists?

        `flags_orphans=False` on this relationship, so an invoice with no payment produces nothing.
        That is a collections question M9.17 owns; reporting it here as an integrity error would
        give one situation two owners and two queues."""
        payments = self._engine("payment_engine", "payments")
        invoices = self._engine("invoice_engine", "invoices")
        sources: list[FactRef] = []
        counterparts: list[FactRef] = []
        seen: set[str] = set()
        for intent in self._intents_for_account(payments, org_id, account_id):
            if not intent.subject_id:
                continue
            sources.append(
                FactRef(
                    identity=intent.subject_id,
                    kind="payment_intent",
                    amount=intent.amount,
                    account_id=intent.account_id,
                    state=str(getattr(intent.state, "value", intent.state)),
                    digest=intent.subject_digest,
                    detail=intent.id,
                )
            )
            if intent.subject_id in seen:
                continue
            seen.add(intent.subject_id)
            document = invoices.documents.get(org_id, intent.subject_id)
            if document is None:
                continue
            counterparts.append(
                FactRef(
                    identity=document.id,
                    kind="commercial_invoice",
                    amount=Money(document.total_minor, document.currency),
                    account_id=document.account_id,
                    period_key=document.accounting_period_key,
                    state=str(getattr(document.state, "value", document.state)),
                    digest=document.issued_snapshot_digest,
                )
            )
        return tuple(sources), tuple(counterparts)

    # ── recognition ↔ ledger ────────────────────────────────────────────────────

    def recognition_ledger(self, org_id: str, schedule_id: str) -> tuple[tuple, tuple]:
        """Every recognized period against the posting that should record it."""
        revenue = self._engine("revenue_engine", "revenue recognition")
        ledger = self._engine("ledger_engine", "the ledger")
        schedule = revenue.schedules.get(org_id, schedule_id)
        if schedule is None:
            raise _NotFound(f"revenue schedule {schedule_id!r} not found")
        from billing.foundation import RECOGNIZED_ENTRY_STATES

        # **M9.18's own declared set, reused rather than restated.** It is deliberately gross of
        # corrections — `recognized`, `posted`, `unposted` and `reversed` — because every one of
        # them means the recognition *decision* was committed and a financial fact is therefore
        # expected.
        #
        # `unposted` is the state that matters most here and the one an intuitive filter drops:
        # it means the Ledger refused the posting, which is precisely the "revenue earned, no
        # financial fact" divergence this relationship exists to catch. Filtering it out as
        # "not recognized yet" makes the engine blind to its own headline case.
        decided = {s.value for s in RECOGNIZED_ENTRY_STATES}
        entries = revenue.entries.for_schedule(org_id, schedule_id)
        sources = tuple(
            FactRef(
                identity=entry.id,
                kind="recognition_entry",
                amount=entry.amount,
                account_id=entry.account_id,
                period_key=entry.ledger_period_key,
                state=str(getattr(entry.state, "value", entry.state)),
                detail=schedule.id,
            )
            for entry in entries
            if str(getattr(entry.state, "value", entry.state)) in decided
        )
        # Scoped to the postings *this* schedule's entries could have produced, plus any posting
        # whose cause resolves to nothing at all. Without the first the account's other schedules
        # read as orphans of this one; without the second a genuinely fabricated financial fact
        # would be filtered out as "somebody else's".
        planned = {e.id for e in entries}
        counterparts: list[FactRef] = []
        for entry in ledger.entries.for_account(org_id, schedule.account_id):
            if entry.source.value != _RECOGNITION_SOURCE or not entry.source_id:
                continue
            if entry.source_id not in planned:
                if revenue.entries.get(org_id, entry.source_id) is not None:
                    continue  # another schedule's posting; not this run's business
                if revenue.adjustments.get(org_id, entry.source_id) is not None:
                    continue  # a correction, posted under its own identity
            counterparts.append(
                FactRef(
                    identity=entry.source_id,
                    kind="journal_entry",
                    amount=entry.amount,
                    account_id=entry.account_id,
                    period_key=entry.period_key,
                    source=entry.source.value,
                    source_id=entry.source_id,
                    state=entry.recognition.value,
                    detail=entry.id,
                )
            )
        return sources, tuple(counterparts)

    def fact_for(self, org_id: str, ref: FactRef | None) -> FactRef | None:
        """Re-read one side of a divergence. **The resolution path's evidence.**

        Returns the fact as it stands *now*, so `guard_evidence_establishes_agreement` compares
        current reality rather than the copy stored on the finding. Returning the stored copy would
        make every resolution trivially succeed."""
        if ref is None:
            return None
        if ref.kind == "journal_entry":
            ledger = self._engine("ledger_engine", "the ledger")
            entry = ledger.entries.get(org_id, ref.detail or ref.identity)
            if entry is None:
                return None
            return replace(ref, amount=entry.amount, period_key=entry.period_key)
        if ref.kind == "payment_attempt":
            payments = self._engine("payment_engine", "payments")
            attempt = payments.attempts.get(org_id, ref.identity)
            if attempt is None:
                return None
            return replace(
                ref,
                amount=attempt.amount,
                state=str(getattr(attempt.state, "value", attempt.state)),
            )
        if ref.kind == "recognition_entry":
            revenue = self._engine("revenue_engine", "revenue recognition")
            entry = revenue.entries.get(org_id, ref.identity)
            if entry is None:
                return None
            return replace(
                ref,
                amount=entry.amount,
                state=str(getattr(entry.state, "value", entry.state)),
                period_key=entry.ledger_period_key,
            )
        return ref


class _ReconciliationSupport:
    """Shared lookups for the command and query sides."""

    def __init__(self, *, context, repos: ReconciliationRepositories, services) -> None:
        self._ctx = context
        self._repos = repos
        self._svc = services
        self._evidence = _EvidenceReader(context)

    def finding_or_404(self, org_id: str, finding_id: str) -> IntegrityFinding:
        found = self._repos.findings.get(org_id, finding_id)
        if found is None:
            raise _NotFound(f"integrity finding {finding_id!r} not found")
        return found

    def run_or_404(self, org_id: str, run_id: str) -> IntegrityRun:
        found = self._repos.runs.get(org_id, run_id)
        if found is None:
            raise _NotFound(f"integrity run {run_id!r} not found")
        return found

    def _finding_view(self, finding: IntegrityFinding) -> IntegrityFindingView:
        return finding_view(finding, self._svc.lifecycle.allowed(finding.state))


class ReconciliationUseCases(_ReconciliationSupport):
    """Reconciliation orchestration. **Observes; never repairs.**"""

    def __init__(
        self,
        *,
        context,
        repos: ReconciliationRepositories,
        factory,
        services: ReconciliationServices,
        authz: AuthorizationOrchestrator,
        uow_factory: UnitOfWorkFactory,
        ids,
        clock,
        dispatch: Callable[..., object] | None = None,
    ) -> None:
        super().__init__(context=context, repos=repos, services=services)
        self._factory = factory
        self._authz = authz
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock
        # **The repair boundary.** A dispatcher onto the shared command bus, so a repair is the
        # owning domain's own command running in the owning domain's own use case. M9.19 writes
        # nothing into any other domain's storage, and `REPAIR_COMMANDS` is the closed whitelist of
        # what may be asked for.
        self._dispatch = dispatch

    # ── the six reconciliations ─────────────────────────────────────────────────

    def reconcile_payment_ledger(
        self, command: ReconcilePaymentLedger
    ) -> ReconciliationReceiptView:
        sources, counterparts = self._evidence.payment_ledger(
            command.org_id, command.account_id
        )
        return self._evaluate(
            command,
            RelationshipKind.PAYMENT_LEDGER,
            subject_id=command.account_id,
            period_key=command.period_key,
            sources=sources,
            counterparts=counterparts,
        )

    def reconcile_invoice_ledger(
        self, command: ReconcileInvoiceLedger
    ) -> ReconciliationReceiptView:
        """**Delegated to M9.14.** Nothing here recalculates an invoice total."""
        document, snapshot, pairs, refs = self._evidence.invoice_ledger(
            command.org_id, command.document_id
        )
        from billing.foundation import InvoiceReconciliationService

        report = InvoiceReconciliationService().reconcile(
            org_id=command.org_id,
            account_id=document.account_id,
            accounting_period_key=document.accounting_period_key,
            entries=pairs,
            snapshot=snapshot,
            at=self._clock.now(),
        )
        spec = self._svc.guards.guard_supported(RelationshipKind.INVOICE_LEDGER)
        observations = self._svc.adapter.lift(
            spec, report.discrepancies, subject_id=document.id
        )
        observations = self._pad_matches(observations, len(pairs))
        return self._record(
            command,
            RelationshipKind.INVOICE_LEDGER,
            subject_id=document.id,
            period_key=document.accounting_period_key,
            evidence=refs,
            observations=observations,
        )

    def reconcile_invoice_tax(self, command: ReconcileInvoiceTax) -> ReconciliationReceiptView:
        """**Delegated to M9.15.** No rate lookup, no jurisdiction resolution, no current rule."""
        document, snapshot, assessment = self._evidence.invoice_tax(
            command.org_id, command.document_id, command.assessment_id
        )
        spec = self._svc.guards.guard_supported(RelationshipKind.INVOICE_TAX)
        if assessment is None:
            # **A determination is expected only when the document presents tax.**
            #
            # Getting this wrong the other way is worse than it looks: demanding an assessment for
            # every document reports a zero-rated invoice, an exempt customer and an installation
            # that does not use tax determination at all as a *critical* integrity failure — and a
            # critical that fires on every correct document is one operators learn to dismiss.
            #
            # A document presenting tax with no determination behind it stays critical, which is
            # the case actually worth alarming on: a tax figure nobody determined.
            presented = self._presented_tax(snapshot)
            if presented == 0:
                observations = (
                    Observation(
                        subject_key=document.id,
                        divergence=None,
                        expected="no tax presented",
                        observed="no determination referenced",
                        detail="nothing to reconcile; the document presents no tax",
                    ),
                )
            else:
                observations = (
                    Observation(
                        subject_key=document.id,
                        divergence=DivergenceKind.MISSING_SOURCE,
                        expected="a finalized tax determination behind the tax it presents",
                        observed=f"{presented} presented, no determination referenced",
                        detail=spec.summary,
                    ),
                )
            evidence: tuple[FactRef, ...] = (
                FactRef(
                    identity=document.id,
                    kind="commercial_invoice",
                    account_id=document.account_id,
                    digest=document.issued_snapshot_digest,
                ),
            )
        else:
            from billing.foundation import TaxReconciliationService

            # The document's taxable lines, as `(line id, taxable base minor)` — **copied from the
            # issued snapshot, never recomputed**. M9.15 compares its own assessment against them.
            subject_lines = tuple(
                (line.id, line.amount.minor)
                for line in (getattr(snapshot, "lines", ()) if snapshot else ())
            )
            report = TaxReconciliationService().reconcile(
                assessment,
                org_id=command.org_id,
                subject_id=document.id,
                subject_lines=subject_lines,
                at=self._clock.now(),
            )
            observations = self._svc.adapter.lift(
                spec, report.discrepancies, subject_id=document.id
            )
            observations = self._pad_matches(
                observations, max(getattr(report, "components_examined", 0), 1)
            )
            evidence = (
                FactRef(
                    identity=assessment.id,
                    kind="tax_assessment",
                    account_id=document.account_id,
                    state=str(getattr(assessment.state, "value", assessment.state)),
                ),
                FactRef(
                    identity=document.id,
                    kind="commercial_invoice",
                    account_id=document.account_id,
                    digest=document.issued_snapshot_digest,
                ),
            )
        return self._record(
            command,
            RelationshipKind.INVOICE_TAX,
            subject_id=document.id,
            period_key=document.accounting_period_key,
            evidence=evidence,
            observations=observations,
        )

    def reconcile_invoice_recognition(
        self, command: ReconcileInvoiceRecognition
    ) -> ReconciliationReceiptView:
        sources, counterparts = self._evidence.invoice_recognition(
            command.org_id, command.document_id
        )
        return self._evaluate(
            command,
            RelationshipKind.INVOICE_RECOGNITION,
            subject_id=command.document_id,
            period_key="",
            sources=sources,
            counterparts=counterparts,
        )

    def reconcile_payment_invoice(
        self, command: ReconcilePaymentInvoice
    ) -> ReconciliationReceiptView:
        sources, counterparts = self._evidence.payment_invoice(
            command.org_id, command.account_id
        )
        return self._evaluate(
            command,
            RelationshipKind.PAYMENT_INVOICE,
            subject_id=command.account_id,
            period_key="",
            sources=sources,
            counterparts=counterparts,
        )

    def reconcile_recognition_ledger(
        self, command: ReconcileRecognitionLedger
    ) -> ReconciliationReceiptView:
        sources, counterparts = self._evidence.recognition_ledger(
            command.org_id, command.schedule_id
        )
        return self._evaluate(
            command,
            RelationshipKind.RECOGNITION_LEDGER,
            subject_id=command.schedule_id,
            period_key="",
            sources=sources,
            counterparts=counterparts,
        )

    # ── the shared evaluation path ──────────────────────────────────────────────

    def _evaluate(
        self,
        command,
        relationship: RelationshipKind,
        *,
        subject_id: str,
        period_key: str,
        sources: tuple[FactRef, ...],
        counterparts: tuple[FactRef, ...],
    ) -> ReconciliationReceiptView:
        spec = self._svc.guards.guard_supported(relationship)
        observations = self._svc.matcher.compare(spec, sources, counterparts)
        return self._record(
            command,
            relationship,
            subject_id=subject_id,
            period_key=period_key,
            evidence=(*sources, *counterparts),
            observations=observations,
        )

    def _record(
        self,
        command,
        relationship: RelationshipKind,
        *,
        subject_id: str,
        period_key: str,
        evidence: tuple[FactRef, ...],
        observations: tuple[Observation, ...],
    ) -> ReconciliationReceiptView:
        """Open the run, record what diverged, close it. **One transaction.**

        Idempotent end to end: the run id is derived from the evidence digest and each finding id
        from `(run, subject, divergence)`, so two workers evaluating the same facts converge on the
        same rows and a re-run over unchanged evidence writes nothing at all."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_READ)
        scope = ReconciliationScope(
            org_id=command.org_id,
            relationship=relationship,
            subject_id=subject_id,
            period_key=period_key,
        )
        self._svc.guards.guard_scoped(scope)
        at = self._clock.now()
        with self._uow.new() as uow:
            opened = self._factory.run(
                scope, evidence, (), at=at, triggered_by=command.actor_id
            )
            stored, is_new = self._repos.runs.create(opened)
            if not is_new and stored.state is RunState.COMPLETED:
                # Same evidence, already evaluated. Return what was found rather than finding it
                # again — this is what makes a duplicate reconciliation command a no-op.
                existing = self._repos.findings.for_run(command.org_id, stored.id)
                uow.stage(
                    self._event(
                        command, stored.id, relationship=relationship.value,
                        disposition="duplicate",
                    )
                )
                return ReconciliationReceiptView(
                    run=integrity_run_view(stored),
                    findings=tuple(self._finding_view(f) for f in existing),
                    is_new=False,
                )
            findings: list[IntegrityFinding] = []
            for observation in observations:
                if not observation.is_divergent:
                    continue
                owner = owner_for_divergence(relationship, observation.divergence)
                built = self._factory.finding(stored, observation, owner=owner, at=at)
                recorded, was_new = self._repos.findings.append(built)
                findings.append(recorded)
                if was_new:
                    self._factory.emit_finding(recorded, actor_id=command.actor_id)
            completed = self._factory.complete(stored, observations, at=at)
            completed = self._repos.runs.complete(completed)
            self._log(
                command, completed,
                kind="reconciled",
                summary=(
                    f"{completed.compared} compared · {completed.matched} matched · "
                    f"{completed.diverged} diverged"
                ),
            )
            uow.stage(
                self._event(
                    command, completed.id,
                    relationship=relationship.value,
                    compared=str(completed.compared),
                    diverged=str(completed.diverged),
                    max_severity=completed.max_severity.value,
                    disposition="new",
                )
            )
            return ReconciliationReceiptView(
                run=integrity_run_view(completed),
                findings=tuple(self._finding_view(f) for f in findings),
                is_new=True,
            )

    @staticmethod
    def _presented_tax(snapshot) -> int:
        """The tax the document presents, in minor units. **Copied from the snapshot, not
        computed** — this module determines no tax and looks up no rate."""
        totals = getattr(snapshot, "totals", None)
        tax = getattr(totals, "tax_total", None) if totals is not None else None
        return tax.minor if isinstance(tax, Money) else 0

    @staticmethod
    def _pad_matches(
        observations: tuple[Observation, ...], compared: int
    ) -> tuple[Observation, ...]:
        """Delegated services report only *discrepancies*, so the matches must be reconstructed.

        Without this a delegated run would report `compared == diverged` and its inspection would
        say the counts disagree — a false integrity failure about the integrity engine itself."""
        matched = max(compared - len(observations), 0)
        return (
            *observations,
            *(
                Observation(subject_key=f"matched:{i}", divergence=None)
                for i in range(matched)
            ),
        )

    # ── the finding lifecycle ───────────────────────────────────────────────────

    def acknowledge(self, command: AcknowledgeFinding) -> IntegrityFindingView:
        return self._transition(
            command,
            command.finding_id,
            FindingTrigger.ACKNOWLEDGE,
            note=command.note,
            permission=P.BILLING_READ,
        )

    def close(self, command: CloseFinding) -> IntegrityFindingView:
        """Accept a divergence as permanent. Requires manage, because it ends the investigation."""
        return self._transition(
            command,
            command.finding_id,
            FindingTrigger.CLOSE,
            note=command.reason,
            permission=P.BILLING_MANAGE,
        )

    def request_repair(self, command: RequestFindingRepair) -> RepairRequestView:
        """Ask the owning domain. **M9.19 performs nothing.**

        The dispatch is whitelisted through `REPAIR_COMMANDS` and goes onto the shared command bus,
        so the repair runs inside the owning domain's own use case with the owning domain's own
        guards. Whether it succeeded is *not* recorded as a resolution — only a later run is
        evidence that the divergence is gone."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        at = self._clock.now()
        with self._uow.new() as uow:
            finding = self.finding_or_404(command.org_id, command.finding_id)
            self._svc.guards.guard_transitionable(finding)
            self._svc.guards.guard_repairable(finding)
            action, command_name, accepted, detail = self._dispatch_repair(
                finding, command.actor_id
            )
            request = self._factory.repair_request(
                finding,
                action=action,
                request_key=command.request_key or finding.id,
                at=at,
                actor_id=command.actor_id,
                dispatched_command=command_name,
                accepted=accepted,
                detail=detail or command.note,
            )
            stored_request, _ = self._repos.requests.append(request)
            moved = self._svc.lifecycle.apply(
                finding,
                FindingTrigger.REQUEST_REPAIR,
                at=at,
                actor_id=command.actor_id,
                repair_request_id=stored_request.id,
            )
            stored = self._repos.findings.advance(moved, expected_version=finding.version)
            self._factory.emit_state(stored, actor_id=command.actor_id)
            self._log_finding(
                command, stored, kind="repair_requested",
                summary=f"{stored.owner.value}: {action}",
            )
            uow.stage(
                self._event(
                    command, stored.id,
                    owner=stored.owner.value,
                    action=action,
                    dispatched=command_name,
                    accepted=str(accepted),
                )
            )
            return request_view(stored_request)

    def _dispatch_repair(
        self, finding: IntegrityFinding, actor_id: str
    ) -> tuple[str, str, bool, str]:
        """Look the repair up in the whitelist and dispatch it, or name the owner and stop.

        A `BillingError` from the owning domain is a **considered refusal** — the repair was
        attempted and declined — and is recorded as such. Anything else propagates, because a bug is
        not a refusal. That is the M9.16-R1 P-6 correction, applied here."""
        key = (finding.relationship, finding.divergence)
        entry = REPAIR_COMMANDS.get(key)
        if entry is None or self._dispatch is None:
            return (
                f"{finding.owner.value}: manual repair required",
                "",
                False,
                "no automatic repair is declared for this divergence; the owner must act",
            )
        action, command_type = entry
        target = self._repair_target(finding, command_type)
        if not target:
            return (
                action, "", False,
                "the finding does not name a subject this repair command can address",
            )
        try:
            self._dispatch(command_type(org_id=finding.org_id, actor_id=actor_id, **target))
        except BillingError as refusal:
            return action, command_type.__name__, False, str(refusal)
        return action, command_type.__name__, True, ""

    @staticmethod
    def _repair_target(finding: IntegrityFinding, command_type: type) -> dict:
        """Which id the owning domain's repair command needs, taken from the finding's own facts."""
        if command_type is PostRecognitionToLedger:
            return {"entry_id": finding.subject_key} if finding.subject_key else {}
        if command_type is ReconcilePaymentAttempt:
            return {"attempt_id": finding.subject_key} if finding.subject_key else {}
        return {}  # pragma: no cover — the whitelist has exactly two entries

    def resolve(self, command: ResolveFinding) -> IntegrityFindingView:
        """**Evidence-based resolution.** The books are re-read and must actually agree.

        `guard_evidence_establishes_agreement` is what keeps this from being `auto_fix()`: the
        source and counterpart are read *again*, from the domains that own them, and a resolution
        whose evidence still shows a disagreement is refused."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        at = self._clock.now()
        with self._uow.new() as uow:
            finding = self.finding_or_404(command.org_id, command.finding_id)
            self._svc.guards.guard_transitionable(finding)
            source, counterpart = self._current_pair(command.org_id, finding)
            self._svc.guards.guard_evidence_establishes_agreement(finding, source, counterpart)
            resolution = self._factory.resolution(
                finding,
                run_id=finding.run_id,
                action=command.note or "verified against current facts",
                request_key=command.request_key or command.finding_id,
                source=source,
                counterpart=counterpart,
                at=at,
                actor_id=command.actor_id,
                repair_request_id=finding.repair_request_id,
                note=command.note,
            )
            stored_resolution, _ = self._repos.resolutions.append(resolution)
            moved = self._svc.lifecycle.apply(
                finding,
                FindingTrigger.RESOLVE,
                at=at,
                actor_id=command.actor_id,
                resolution_id=stored_resolution.id,
            )
            stored = self._repos.findings.advance(moved, expected_version=finding.version)
            self._factory.emit_state(stored, actor_id=command.actor_id)
            self._log_finding(
                command, stored, kind="resolved",
                summary=f"evidence {stored_resolution.evidence_digest[:12]}",
            )
            uow.stage(
                self._event(
                    command, stored.id,
                    resolution_id=stored_resolution.id,
                    evidence_digest=stored_resolution.evidence_digest,
                )
            )
            return self._finding_view(stored)

    def _current_pair(
        self, org_id: str, finding: IntegrityFinding
    ) -> tuple[FactRef | None, FactRef | None]:
        """**Re-read both sides of the divergence, as the books stand now.**

        Not `fact_for(finding.counterpart)`: for the headline divergence — `MISSING_COUNTERPART` —
        the stored counterpart is `None`, and re-reading `None` yields `None`, so the guard refuses
        forever and the one case with a declared repair path can never be closed. The engine would
        detect and repair but never observe the result, which is the whole loop.

        So this re-runs the relationship's evidence read against the *original run's scope* and
        looks the subject up on both sides. If the repair worked the counterpart is there now; if it
        did not, it still is not, and the guard still refuses. Either way the answer comes from the
        owning domains rather than from the copy stored at detection."""
        run = self._repos.runs.get(org_id, finding.run_id)
        if run is None:  # pragma: no cover — a finding always has its run
            return (
                self._evidence.fact_for(org_id, finding.source),
                self._evidence.fact_for(org_id, finding.counterpart),
            )
        try:
            sources, counterparts = self._reread(org_id, run)
        except BillingError:
            # The evidence cannot be re-read — an unwired or unavailable domain. That is not
            # grounds to declare the divergence resolved, so fall back to the stored facts and let
            # the guard refuse on them.
            return (
                self._evidence.fact_for(org_id, finding.source),
                self._evidence.fact_for(org_id, finding.counterpart),
            )
        by_source = {r.identity: r for r in sources}
        by_counterpart = {r.identity: r for r in counterparts}
        key = finding.subject_key
        source = by_source.get(key) or self._evidence.fact_for(org_id, finding.source)
        counterpart = by_counterpart.get(key)
        if counterpart is None and run.scope.relationship is RelationshipKind.INVOICE_RECOGNITION:
            # An aggregate relationship has no per-subject counterpart; the comparison is of
            # totals, so the resolution evidence is the total that is now accounted for.
            counterpart = (
                replace(
                    counterparts[0],
                    identity=key,
                    amount=Money(
                        sum(c.amount.minor for c in counterparts if c.amount is not None),
                        source.amount.currency if source and source.amount else INR_FALLBACK,
                    ),
                )
                if counterparts
                else None
            )
        return source, counterpart

    def _reread(self, org_id: str, run: IntegrityRun) -> tuple[tuple, tuple]:
        """Re-run the evidence read for one run's scope. Reads only; compares nothing."""
        subject = run.scope.subject_id
        relationship = run.scope.relationship
        if relationship is RelationshipKind.RECOGNITION_LEDGER:
            return self._evidence.recognition_ledger(org_id, subject)
        if relationship is RelationshipKind.PAYMENT_LEDGER:
            return self._evidence.payment_ledger(org_id, subject)
        if relationship is RelationshipKind.PAYMENT_INVOICE:
            return self._evidence.payment_invoice(org_id, subject)
        if relationship is RelationshipKind.INVOICE_RECOGNITION:
            return self._evidence.invoice_recognition(org_id, subject)
        if relationship is RelationshipKind.INVOICE_LEDGER:
            _, _, _, refs = self._evidence.invoice_ledger(org_id, subject)
            return refs, refs
        # INVOICE_TAX — M9.15 owns the comparison, so there is no pairwise evidence to re-read.
        # A tax finding is resolved by the operator recording that the determination was corrected.
        return (), ()

    def _transition(
        self, command, finding_id: str, trigger: FindingTrigger, *, note: str, permission
    ) -> IntegrityFindingView:
        self._authz.require(command.actor_id, command.org_id, permission)
        at = self._clock.now()
        with self._uow.new() as uow:
            finding = self.finding_or_404(command.org_id, finding_id)
            self._svc.guards.guard_transitionable(finding)
            moved = self._svc.lifecycle.apply(
                finding, trigger, at=at, actor_id=command.actor_id, note=note
            )
            stored = self._repos.findings.advance(moved, expected_version=finding.version)
            self._factory.emit_state(stored, actor_id=command.actor_id)
            self._log_finding(command, stored, kind=trigger.value, summary=note)
            uow.stage(self._event(command, stored.id, state=stored.state.value))
            return self._finding_view(stored)

    # ── internals ───────────────────────────────────────────────────────────────

    def _log(self, command, run: IntegrityRun, *, kind: str, summary: str = "") -> None:
        self._repos.activity.append(
            self._factory.activity(
                run.id, run.org_id,
                kind=kind, summary=summary, actor_id=command.actor_id, at=self._clock.now(),
            )
        )

    def _log_finding(
        self, command, finding: IntegrityFinding, *, kind: str, summary: str = ""
    ) -> None:
        self._repos.activity.append(
            self._factory.activity(
                finding.run_id, finding.org_id,
                kind=kind, summary=summary, actor_id=command.actor_id,
                finding_id=finding.id, at=self._clock.now(),
            )
        )

    def _event(self, command, subject_id: str, **data: str) -> ApplicationEvent:
        return ApplicationEvent(
            id=self._ids.new(PREFIX_APP_EVENT),
            type=ApplicationEventType.INTEGRITY_RECONCILED,
            ts=self._clock.now(),
            use_case=type(command).__name__,
            outcome="completed",
            org_id=command.org_id,
            actor=command.actor_id,
            subject_id=subject_id,
            data=dict(data),
        )


class ReconciliationQueries(_ReconciliationSupport):
    """Reads. Every one org-scoped, bounded, and index-backed."""

    def __init__(self, *, context, repos, services, authz: AuthorizationOrchestrator) -> None:
        super().__init__(context=context, repos=repos, services=services)
        self._authz = authz

    def _read(self, query) -> None:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)

    def get_run(self, query: GetIntegrityRun) -> IntegrityRunView:
        self._read(query)
        return integrity_run_view(self.run_or_404(query.org_id, query.run_id))

    def list_runs(self, query: ListIntegrityRuns) -> tuple[IntegrityRunView, ...]:
        self._read(query)
        found = (
            self._repos.runs.for_subject(query.org_id, query.subject_id, query.limit)
            if query.subject_id
            else self._repos.runs.recent(query.org_id, query.relationship, query.limit)
        )
        return tuple(integrity_run_view(r) for r in found)

    def get_finding(self, query: GetIntegrityFinding) -> IntegrityFindingView:
        self._read(query)
        return self._finding_view(self.finding_or_404(query.org_id, query.finding_id))

    def run_findings(self, query: ListRunFindings) -> tuple[IntegrityFindingView, ...]:
        self._read(query)
        found = self._repos.findings.for_run(query.org_id, query.run_id)
        return tuple(self._finding_view(f) for f in found)

    def open_findings(self, query: OpenFindings) -> tuple[IntegrityFindingView, ...]:
        self._read(query)
        found = self._repos.findings.open_findings(
            query.org_id, query.severities, query.limit
        )
        return tuple(self._finding_view(f) for f in found)

    def by_owner(self, query: FindingsByOwner) -> tuple[IntegrityFindingView, ...]:
        self._read(query)
        found = self._repos.findings.by_owner(query.org_id, query.owner, query.limit)
        return tuple(self._finding_view(f) for f in found)

    def for_account(self, query: FindingsForAccount) -> tuple[IntegrityFindingView, ...]:
        self._read(query)
        found = self._repos.findings.for_account(query.org_id, query.account_id, query.limit)
        return tuple(self._finding_view(f) for f in found)

    def explain(self, query: ExplainFinding) -> FindingExplanationView:
        self._read(query)
        finding = self.finding_or_404(query.org_id, query.finding_id)
        run = self._repos.runs.get(query.org_id, finding.run_id)
        resolutions = self._repos.resolutions.for_finding(query.org_id, finding.id)
        return reconciliation_explanation_view(
            self._svc.explainer.explain(
                finding, run, resolutions[-1] if resolutions else None
            )
        )

    def inspect(self, query: InspectIntegrityRun) -> RunInspectionView:
        self._read(query)
        run = self.run_or_404(query.org_id, query.run_id)
        findings = self._repos.findings.for_run(query.org_id, run.id)
        result = self._svc.inspector.inspect(run, findings)
        return RunInspectionView(
            run_id=result.run_id,
            relationship=result.relationship.value,
            state=result.state.value,
            compared=result.compared,
            matched=result.matched,
            diverged=result.diverged,
            findings_recorded=result.findings_recorded,
            is_counted_correctly=result.is_counted_correctly,
            is_scoped=result.is_scoped,
            is_complete=result.is_complete,
            unresolved=result.unresolved,
            notes=result.notes,
        )

    def repair_requests(self, query: ListRepairRequests) -> tuple[RepairRequestView, ...]:
        self._read(query)
        found = self._repos.requests.for_finding(query.org_id, query.finding_id)
        return tuple(request_view(r) for r in found)

    def resolutions(self, query: ListFindingResolutions) -> tuple[IntegrityResolutionView, ...]:
        self._read(query)
        found = self._repos.resolutions.for_finding(query.org_id, query.finding_id)
        return tuple(resolution_view(r) for r in found)

    def search(self, query: SearchIntegrityFindings) -> IntegritySearchView:
        self._read(query)
        candidates = (
            self._repos.findings.for_account(
                query.org_id, query.account_id, max(query.limit * 4, 100)
            )
            if query.account_id
            else self._repos.findings.all(query.org_id, max(query.limit * 4, 200))
        )
        result = self._svc.search.search(
            IntegritySearchQuery(
                org_id=query.org_id,
                relationships=tuple(
                    RelationshipKind(r) for r in query.relationships if _is(RelationshipKind, r)
                ),
                states=tuple(FindingState(s) for s in query.states if _is(FindingState, s)),
                severities=tuple(
                    FindingSeverity(s) for s in query.severities if _is(FindingSeverity, s)
                ),
                owners=tuple(RepairOwner(o) for o in query.owners if _is(RepairOwner, o)),
                account_id=query.account_id,
                subject=query.subject,
                limit=query.limit,
            ),
            candidates,
        )
        return IntegritySearchView(
            findings=tuple(self._finding_view(f) for f in result.findings),
            total=result.total,
            truncated=result.truncated,
        )

    def posture(self, query: GetIntegrityPosture) -> IntegrityPostureView:
        """**Counts, computed from the facts on every call.** There is no stored posture."""
        self._read(query)
        runs = self._repos.runs.recent(query.org_id, "", query.limit)
        findings = self._repos.findings.all(query.org_id, query.limit)
        result = self._svc.posture.posture(query.org_id, runs, findings)
        return IntegrityPostureView(
            org_id=result.org_id,
            runs=result.runs,
            findings_open=result.findings_open,
            findings_acknowledged=result.findings_acknowledged,
            findings_repair_requested=result.findings_repair_requested,
            findings_resolved=result.findings_resolved,
            findings_closed=result.findings_closed,
            critical=result.critical,
            error=result.error,
            warning=result.warning,
            is_clean=result.is_clean,
            by_relationship=dict(result.by_relationship),
            by_owner=dict(result.by_owner),
        )

    def timeline(self, query: IntegrityTimeline) -> tuple[IntegrityActivityView, ...]:
        self._read(query)
        found = self._repos.activity.for_run(query.org_id, query.run_id, query.limit)
        return tuple(reconciliation_activity_view(a) for a in found)

    def matrix(self, query: GetReconciliationMatrix) -> tuple[RelationshipView, ...]:
        """The declared relationships, as data — so Studio renders them rather than hard-coding
        six pairs that would drift from the engine the first time one changed."""
        self._read(query)
        from billing.foundation import RECONCILIATION_MATRIX

        return tuple(
            RelationshipView(
                relationship=spec.relationship.value,
                source_kind=spec.source_kind,
                counterpart_kind=spec.counterpart_kind,
                authoritative=spec.authoritative,
                matching_identity=spec.matching_identity,
                compares=spec.compares,
                mode=spec.mode.value,
                owner=spec.owner.value,
                allows_partial=spec.allows_partial,
                flags_orphans=spec.flags_orphans,
                summary=spec.summary,
            )
            for spec in RECONCILIATION_MATRIX.values()
        )


def _is(enum_type, value: str) -> bool:
    return value in {m.value for m in enum_type}


def register_reconciliation_use_cases(
    command_bus, query_bus, uc: ReconciliationUseCases, q: ReconciliationQueries
):
    """Wire the integrity vocabulary onto the shared buses. One handler per command."""
    command_bus.register(ReconcilePaymentLedger, uc.reconcile_payment_ledger)
    command_bus.register(ReconcileInvoiceLedger, uc.reconcile_invoice_ledger)
    command_bus.register(ReconcileInvoiceTax, uc.reconcile_invoice_tax)
    command_bus.register(ReconcileInvoiceRecognition, uc.reconcile_invoice_recognition)
    command_bus.register(ReconcilePaymentInvoice, uc.reconcile_payment_invoice)
    command_bus.register(ReconcileRecognitionLedger, uc.reconcile_recognition_ledger)
    command_bus.register(AcknowledgeFinding, uc.acknowledge)
    command_bus.register(RequestFindingRepair, uc.request_repair)
    command_bus.register(ResolveFinding, uc.resolve)
    command_bus.register(CloseFinding, uc.close)
    query_bus.register(GetIntegrityRun, q.get_run)
    query_bus.register(ListIntegrityRuns, q.list_runs)
    query_bus.register(GetIntegrityFinding, q.get_finding)
    query_bus.register(ListRunFindings, q.run_findings)
    query_bus.register(OpenFindings, q.open_findings)
    query_bus.register(FindingsByOwner, q.by_owner)
    query_bus.register(FindingsForAccount, q.for_account)
    query_bus.register(ExplainFinding, q.explain)
    query_bus.register(InspectIntegrityRun, q.inspect)
    query_bus.register(ListRepairRequests, q.repair_requests)
    query_bus.register(ListFindingResolutions, q.resolutions)
    query_bus.register(SearchIntegrityFindings, q.search)
    query_bus.register(GetIntegrityPosture, q.posture)
    query_bus.register(IntegrityTimeline, q.timeline)
    query_bus.register(GetReconciliationMatrix, q.matrix)


__all__ = [
    "ALL_RECONCILIATION_COMMANDS",
    "ALL_RECONCILIATION_QUERIES",
    "REPAIR_COMMANDS",
    "AcknowledgeFinding",
    "CloseFinding",
    "ExplainFinding",
    "FactRefView",
    "FindingExplanationView",
    "FindingsByOwner",
    "FindingsForAccount",
    "GetIntegrityFinding",
    "GetIntegrityPosture",
    "GetIntegrityRun",
    "GetReconciliationMatrix",
    "InspectIntegrityRun",
    "IntegrityActivityView",
    "IntegrityFindingView",
    "IntegrityPostureView",
    "IntegrityResolutionView",
    "IntegrityRunView",
    "IntegritySearchView",
    "IntegrityTimeline",
    "ListFindingResolutions",
    "ListIntegrityRuns",
    "ListRepairRequests",
    "ListRunFindings",
    "OpenFindings",
    "ReconcileInvoiceLedger",
    "ReconcileInvoiceRecognition",
    "ReconcileInvoiceTax",
    "ReconcilePaymentInvoice",
    "ReconcilePaymentLedger",
    "ReconcileRecognitionLedger",
    "ReconciliationQueries",
    "ReconciliationReceiptView",
    "ReconciliationRepositories",
    "ReconciliationServices",
    "ReconciliationUseCases",
    "RelationshipView",
    "RepairRequestView",
    "RequestFindingRepair",
    "ResolveFinding",
    "RunInspectionView",
    "SearchIntegrityFindings",
    "reconciliation_activity_view",
    "reconciliation_explanation_view",
    "fact_view",
    "finding_view",
    "register_reconciliation_use_cases",
    "request_view",
    "resolution_view",
    "integrity_run_view",
]
