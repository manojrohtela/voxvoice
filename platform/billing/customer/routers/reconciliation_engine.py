"""Reconciliation & Financial Integrity routes (M9.19) — transport only.

Additive to the M9.5–M9.18 transport layer and identical in discipline: parse, take identity from
the verified token, construct a command or query, dispatch, serialize. No reconciliation rule is
evaluated here — what matches, what diverges, how severe it is, who owns the repair and whether
evidence establishes agreement are all Foundation behaviour reached through the Application.

Mounted at **`/integrity`**, chosen by inspection of every existing prefix: `/accounting` and
`/ledger` are M9.13's, `/invoices` and `/documents` are M9.14's, `/taxes` and `/taxation` are
M9.15's, `/payments` and `/collections` are M9.16's, `/recovery` is M9.17's and `/revenue` is
M9.18's. Two routers under one prefix shadow each other by registration order, and the one that
loses does so silently.

`/collections` was the tempting name and is the wrong one twice over: it already belongs to M9.16,
and this domain reconciles rather than collects.

**Eight things no endpoint here does.** None compares two facts — the comparison happens in
`MatchingService`. None decides a severity. None calculates a price, a rate, a tax, an invoice
total, a recognized amount or a balance. **None posts a ledger entry, and none can**: the two
repairs this surface can request are the *owning domains'* own commands, and `PostJournalEntry` is
not reachable from this module. None moves money or touches a provider. None changes a dunning
stage. None resolves a finding because a caller said so — `/findings/{id}/resolve` re-reads both
sides and refuses unless they now agree. And there is **no DELETE anywhere in this file**:
historical divergence is evidence, and evidence is not deleted.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from billing.application import (
    AcknowledgeFinding,
    BillingApplication,
    CloseFinding,
    ExplainFinding,
    FindingsByOwner,
    FindingsForAccount,
    GetIntegrityFinding,
    GetIntegrityPosture,
    GetIntegrityRun,
    GetReconciliationMatrix,
    InspectIntegrityRun,
    IntegrityTimeline,
    ListFindingResolutions,
    ListIntegrityRuns,
    ListRepairRequests,
    ListRunFindings,
    OpenFindings,
    ReconcileInvoiceLedger,
    ReconcileInvoiceRecognition,
    ReconcileInvoiceTax,
    ReconcilePaymentInvoice,
    ReconcilePaymentLedger,
    ReconcileRecognitionLedger,
    RequestFindingRepair,
    ResolveFinding,
    SearchIntegrityFindings,
)

from ..auth import require_org
from ..container import get_application
from ..context import BillingRequestContext
from ..reconciliation_schemas import (
    FindingExplanationResponse,
    FindingNoteBody,
    IntegrityActivityResponse,
    IntegrityFindingResponse,
    IntegrityPostureResponse,
    IntegrityResolutionResponse,
    IntegrityRunResponse,
    IntegritySearchResponse,
    ReconcileAccountBody,
    ReconcileDocumentBody,
    ReconcileScheduleBody,
    ReconciliationReceiptResponse,
    RelationshipResponse,
    RepairRequestBody,
    RepairRequestResponse,
    ResolveFindingBody,
    RunInspectionResponse,
)
from ..schemas import ERROR_RESPONSES

router = APIRouter(prefix="/integrity", tags=["integrity"], responses=ERROR_RESPONSES)


# ── the reconciliation matrix ───────────────────────────────────────────────────


@router.get(
    "/relationships",
    response_model=list[RelationshipResponse],
    summary="The declared reconciliation relationships, as data",
)
def relationships(
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[RelationshipResponse]:
    """What this engine compares, on whose authority, matched on what, and who fixes it.

    Served rather than documented so a client renders the engine's actual configuration instead of
    six pairs it hard-coded and never updated."""
    return [
        RelationshipResponse.of(r)
        for r in app.queries.dispatch(
            GetReconciliationMatrix(actor_id=ctx.actor(), org_id=ctx.organization())
        )
    ]


# ── the six reconciliations ─────────────────────────────────────────────────────


@router.post(
    "/reconcile/payment-ledger",
    response_model=ReconciliationReceiptResponse,
    status_code=201,
    summary="Did every movement of money produce a financial fact?",
)
def reconcile_payment_ledger(
    body: ReconcileAccountBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReconciliationReceiptResponse:
    """Both sides are read independently. A captured attempt is not evidence that a ledger entry
    exists, and a ledger entry is not evidence that a provider moved anything."""
    return ReconciliationReceiptResponse.of(
        app.commands.dispatch(
            ReconcilePaymentLedger(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                account_id=body.account_id,
                period_key=body.period_key,
            )
        )
    )


@router.post(
    "/reconcile/invoice-ledger",
    response_model=ReconciliationReceiptResponse,
    status_code=201,
    summary="Does the document present what the ledger recorded?",
)
def reconcile_invoice_ledger(
    body: ReconcileDocumentBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReconciliationReceiptResponse:
    """Delegated to M9.14's own comparison. **Nothing here recalculates an invoice total.**"""
    return ReconciliationReceiptResponse.of(
        app.commands.dispatch(
            ReconcileInvoiceLedger(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                document_id=body.document_id,
            )
        )
    )


@router.post(
    "/reconcile/invoice-tax",
    response_model=ReconciliationReceiptResponse,
    status_code=201,
    summary="Does the document present the determination it references?",
)
def reconcile_invoice_tax(
    body: ReconcileDocumentBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReconciliationReceiptResponse:
    """Delegated to M9.15's own comparison. **No rate lookup, no jurisdiction, no current rule** —
    the comparison is against the determination the document named, not against today's rules."""
    return ReconciliationReceiptResponse.of(
        app.commands.dispatch(
            ReconcileInvoiceTax(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                document_id=body.document_id,
                assessment_id=body.assessment_id,
            )
        )
    )


@router.post(
    "/reconcile/invoice-recognition",
    response_model=ReconciliationReceiptResponse,
    status_code=201,
    summary="billed = recognized + deferred, and never more than billed",
)
def reconcile_invoice_recognition(
    body: ReconcileDocumentBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReconciliationReceiptResponse:
    """**A deferred remainder is not a mismatch.** A twelve-month invoice that has recognized three
    months is correct; only recognizing more than was billed diverges."""
    return ReconciliationReceiptResponse.of(
        app.commands.dispatch(
            ReconcileInvoiceRecognition(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                document_id=body.document_id,
            )
        )
    )


@router.post(
    "/reconcile/payment-invoice",
    response_model=ReconciliationReceiptResponse,
    status_code=201,
    summary="Does every payment answer an obligation that exists?",
)
def reconcile_payment_invoice(
    body: ReconcileAccountBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReconciliationReceiptResponse:
    """An **unpaid invoice is not a finding** — that is a collections question M9.17 owns."""
    return ReconciliationReceiptResponse.of(
        app.commands.dispatch(
            ReconcilePaymentInvoice(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                account_id=body.account_id,
            )
        )
    )


@router.post(
    "/reconcile/recognition-ledger",
    response_model=ReconciliationReceiptResponse,
    status_code=201,
    summary="Did every recognized period produce a financial fact?",
)
def reconcile_recognition_ledger(
    body: ReconcileScheduleBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReconciliationReceiptResponse:
    """Recognition owns the decision, the Ledger owns the fact, and this verifies the relationship
    without being allowed to create either."""
    return ReconciliationReceiptResponse.of(
        app.commands.dispatch(
            ReconcileRecognitionLedger(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                schedule_id=body.schedule_id,
            )
        )
    )


# ── runs ────────────────────────────────────────────────────────────────────────


@router.get("/runs", response_model=list[IntegrityRunResponse], summary="Recent runs")
def list_runs(
    relationship: str = "",
    subject_id: str = "",
    limit: int = Query(default=100, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[IntegrityRunResponse]:
    return [
        IntegrityRunResponse.of(r)
        for r in app.queries.dispatch(
            ListIntegrityRuns(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                relationship=relationship,
                subject_id=subject_id,
                limit=limit,
            )
        )
    ]


@router.get("/runs/{run_id}", response_model=IntegrityRunResponse, summary="One run")
def get_run(
    run_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> IntegrityRunResponse:
    return IntegrityRunResponse.of(
        app.queries.dispatch(
            GetIntegrityRun(actor_id=ctx.actor(), org_id=ctx.organization(), run_id=run_id)
        )
    )


@router.get(
    "/runs/{run_id}/findings",
    response_model=list[IntegrityFindingResponse],
    summary="What one run found",
)
def run_findings(
    run_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[IntegrityFindingResponse]:
    return [
        IntegrityFindingResponse.of(f)
        for f in app.queries.dispatch(
            ListRunFindings(actor_id=ctx.actor(), org_id=ctx.organization(), run_id=run_id)
        )
    ]


@router.get(
    "/runs/{run_id}/inspect",
    response_model=RunInspectionResponse,
    summary="Are this run's counts self-consistent, scoped, and complete?",
)
def inspect_run(
    run_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RunInspectionResponse:
    return RunInspectionResponse.of(
        app.queries.dispatch(
            InspectIntegrityRun(actor_id=ctx.actor(), org_id=ctx.organization(), run_id=run_id)
        )
    )


@router.get(
    "/runs/{run_id}/timeline",
    response_model=list[IntegrityActivityResponse],
    summary="The audit trail for one run",
)
def timeline(
    run_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[IntegrityActivityResponse]:
    return [
        IntegrityActivityResponse.of(a)
        for a in app.queries.dispatch(
            IntegrityTimeline(
                actor_id=ctx.actor(), org_id=ctx.organization(), run_id=run_id, limit=limit
            )
        )
    ]


# ── findings ────────────────────────────────────────────────────────────────────


@router.get(
    "/findings",
    response_model=list[IntegrityFindingResponse],
    summary="Unresolved divergences, worst first",
)
def open_findings(
    severity: list[str] = Query(default=[]),
    limit: int = Query(default=200, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[IntegrityFindingResponse]:
    """**The read an external scheduler polls.** Billing runs no scheduler — whoever decides when
    to look lives outside this plane, exactly as it does for `/recovery/cases/due`."""
    return [
        IntegrityFindingResponse.of(f)
        for f in app.queries.dispatch(
            OpenFindings(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                severities=tuple(severity),
                limit=limit,
            )
        )
    ]


@router.get(
    "/findings/search", response_model=IntegritySearchResponse, summary="Search findings"
)
def search_findings(
    relationship: list[str] = Query(default=[]),
    state: list[str] = Query(default=[]),
    severity: list[str] = Query(default=[]),
    owner: list[str] = Query(default=[]),
    account_id: str = "",
    subject: str = "",
    limit: int = Query(default=50, ge=1, le=200),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> IntegritySearchResponse:
    return IntegritySearchResponse.of(
        app.queries.dispatch(
            SearchIntegrityFindings(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                relationships=tuple(relationship),
                states=tuple(state),
                severities=tuple(severity),
                owners=tuple(owner),
                account_id=account_id,
                subject=subject,
                limit=limit,
            )
        )
    )


@router.get(
    "/findings/by-owner/{owner}",
    response_model=list[IntegrityFindingResponse],
    summary="One domain's repair queue",
)
def findings_by_owner(
    owner: str,
    limit: int = Query(default=200, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[IntegrityFindingResponse]:
    return [
        IntegrityFindingResponse.of(f)
        for f in app.queries.dispatch(
            FindingsByOwner(
                actor_id=ctx.actor(), org_id=ctx.organization(), owner=owner, limit=limit
            )
        )
    ]


@router.get(
    "/findings/for-account/{account_id}",
    response_model=list[IntegrityFindingResponse],
    summary="Every finding touching one account",
)
def findings_for_account(
    account_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[IntegrityFindingResponse]:
    return [
        IntegrityFindingResponse.of(f)
        for f in app.queries.dispatch(
            FindingsForAccount(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                account_id=account_id,
                limit=limit,
            )
        )
    ]


@router.get(
    "/findings/{finding_id}", response_model=IntegrityFindingResponse, summary="One finding"
)
def get_finding(
    finding_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> IntegrityFindingResponse:
    return IntegrityFindingResponse.of(
        app.queries.dispatch(
            GetIntegrityFinding(
                actor_id=ctx.actor(), org_id=ctx.organization(), finding_id=finding_id
            )
        )
    )


@router.get(
    "/findings/{finding_id}/explain",
    response_model=FindingExplanationResponse,
    summary="Why does this finding exist?",
)
def explain_finding(
    finding_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> FindingExplanationResponse:
    """Answered entirely from stored evidence. **No domain is re-read** — by the time somebody
    opens a two-week-old finding, the payment has settled and the ledger has closed, so an
    explanation that asked them again would answer a different question."""
    return FindingExplanationResponse.of(
        app.queries.dispatch(
            ExplainFinding(
                actor_id=ctx.actor(), org_id=ctx.organization(), finding_id=finding_id
            )
        )
    )


@router.get(
    "/findings/{finding_id}/repair-requests",
    response_model=list[RepairRequestResponse],
    summary="What has been asked of the owning domain",
)
def repair_requests(
    finding_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[RepairRequestResponse]:
    return [
        RepairRequestResponse.of(r)
        for r in app.queries.dispatch(
            ListRepairRequests(
                actor_id=ctx.actor(), org_id=ctx.organization(), finding_id=finding_id
            )
        )
    ]


@router.get(
    "/findings/{finding_id}/resolutions",
    response_model=list[IntegrityResolutionResponse],
    summary="The evidence a divergence was addressed",
)
def resolutions(
    finding_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[IntegrityResolutionResponse]:
    return [
        IntegrityResolutionResponse.of(r)
        for r in app.queries.dispatch(
            ListFindingResolutions(
                actor_id=ctx.actor(), org_id=ctx.organization(), finding_id=finding_id
            )
        )
    ]


@router.post(
    "/findings/{finding_id}/acknowledge",
    response_model=IntegrityFindingResponse,
    summary="Somebody has seen it",
)
def acknowledge(
    finding_id: str,
    body: FindingNoteBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> IntegrityFindingResponse:
    return IntegrityFindingResponse.of(
        app.commands.dispatch(
            AcknowledgeFinding(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                finding_id=finding_id,
                note=body.note,
            )
        )
    )


@router.post(
    "/findings/{finding_id}/request-repair",
    response_model=RepairRequestResponse,
    status_code=201,
    summary="Ask the owning domain to fix it",
)
def request_repair(
    finding_id: str,
    body: RepairRequestBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> RepairRequestResponse:
    """**M9.19 performs nothing.** Where a repair is declared, the owning domain's own command is
    dispatched into the owning domain's own use case; otherwise the owner is named and that is all.
    Acceptance is not evidence the divergence is gone — only a later run is."""
    return RepairRequestResponse.of(
        app.commands.dispatch(
            RequestFindingRepair(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                finding_id=finding_id,
                request_key=body.request_key,
                note=body.note,
            )
        )
    )


@router.post(
    "/findings/{finding_id}/resolve",
    response_model=IntegrityFindingResponse,
    summary="Close a divergence with evidence",
)
def resolve(
    finding_id: str,
    body: ResolveFindingBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> IntegrityFindingResponse:
    """**Not `auto_fix()`.** The server re-reads both sides and refuses unless they now agree —
    ADR-0023 PY5 enforced at a boundary the client cannot reach past. There is no `force` flag."""
    return IntegrityFindingResponse.of(
        app.commands.dispatch(
            ResolveFinding(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                finding_id=finding_id,
                request_key=body.request_key,
                note=body.note,
            )
        )
    )


@router.post(
    "/findings/{finding_id}/close",
    response_model=IntegrityFindingResponse,
    summary="Accept a divergence as permanent — a decision, not a repair",
)
def close(
    finding_id: str,
    body: FindingNoteBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> IntegrityFindingResponse:
    """Separate from resolve deliberately: resolved means the books agree, closed means somebody
    decided to live with it. Collapsing them would let "we gave up" be reported as "we fixed it"."""
    return IntegrityFindingResponse.of(
        app.commands.dispatch(
            CloseFinding(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                finding_id=finding_id,
                reason=body.note,
            )
        )
    )


# ── posture ─────────────────────────────────────────────────────────────────────


@router.get(
    "/posture",
    response_model=IntegrityPostureResponse,
    summary="The org's integrity at a glance — counts, never money",
)
def posture(
    limit: int = Query(default=500, ge=1, le=1000),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> IntegrityPostureResponse:
    """Derived on every call. There is no stored posture and **no amount on this response** — a
    "total divergent value" would be a figure M9.19 computed, and somebody would report it."""
    return IntegrityPostureResponse.of(
        app.queries.dispatch(
            GetIntegrityPosture(actor_id=ctx.actor(), org_id=ctx.organization(), limit=limit)
        )
    )
