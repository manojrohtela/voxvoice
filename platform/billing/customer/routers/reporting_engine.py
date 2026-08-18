"""Compliance & Regulatory Reporting routes (M9.22) — transport only.

Additive to the M9.5–M9.21 transport layer and identical in discipline: parse, take identity from
the verified token, construct a command or query, dispatch, serialize. No reporting rule is
evaluated here — what a field means, which transformation applies, whether a source resolved,
whether a filing may finalize and what a package contains are all Foundation behaviour reached
through the Application.

Mounted at **`/reports`**, its own prefix, proven collision-free against all 425 existing paths.

**Twelve things no endpoint here does.** None posts a ledger entry, issues an invoice, determines
tax, executes a payment or recognizes revenue — and none can, because no repository that could is
reachable. None reconciles (M9.19), correlates (M9.20) or investigates (M9.21). None calculates a
price, a rate, a tax component or a balance. **None converts a currency**: a field whose sources
span currencies comes back unsupported with the reason, never a total. None reports a missing
source as zero — `/prepare` records an `UNAVAILABLE` reference and a named exception. None
finalizes around validation: `/finalize` re-reads the verdict, the exceptions and the completeness
inside the transaction, and the lifecycle has no edge to `FINALIZED` except from `READY`. None
edits a filed report — `/correction` opens a new one beside it. **None submits anything to a
regulator**: there is no network client, no endpoint and no credential in this milestone. And there
is **no DELETE, PUT or PATCH anywhere in this file** — a filing, its inputs, its exceptions, its
snapshot and its package are not mutated.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from billing.application import (
    AcknowledgeReportException,
    BillingApplication,
    CloseReportingPeriod,
    CorrectReport,
    CreateReport,
    CreateSubmissionPackage,
    DiffReports,
    FinalizeReport,
    GetReport,
    GetReportDefinition,
    GetReportEvidence,
    GetReportExceptions,
    GetReportingPosture,
    GetReportProvenance,
    GetReportSnapshot,
    GetReportSources,
    GetReportValidation,
    GetSubmissionPackage,
    ListReportDefinitions,
    ListReportingPeriods,
    ListReports,
    OpenReportingPeriod,
    PrepareReport,
    PublishReportDefinition,
    RecordSourceUnavailable,
    ValidateReport,
)
from billing.foundation import Currency, ReportFieldSpec, ReportStatus

from ..auth import require_org
from ..container import get_application
from ..context import BillingRequestContext
from ..reporting_schemas import (
    AcknowledgeExceptionBody,
    CorrectReportBody,
    CreateReportBody,
    OpenReportingPeriodBody,
    PublishReportDefinitionBody,
    RecordSourceUnavailableBody,
    ReportDefinitionResponse,
    ReportDiffResponse,
    ReportExceptionPageResponse,
    ReportExceptionResponse,
    ReportingPostureResponse,
    ReportPageResponse,
    ReportPeriodResponse,
    ReportProvenanceResponse,
    ReportResponse,
    ReportSnapshotResponse,
    ReportSourcePageResponse,
    ReportSourceResponse,
    ReportValidationResponse,
    SubmissionPackageResponse,
)
from ..schemas import ERROR_RESPONSES

router = APIRouter(prefix="/reports", tags=["reports"], responses=ERROR_RESPONSES)


# ── definitions ─────────────────────────────────────────────────────────────────


@router.post(
    "/definitions",
    response_model=ReportDefinitionResponse,
    status_code=201,
    summary="Publish a reporting rule at a version",
)
def publish_definition(
    body: PublishReportDefinitionBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReportDefinitionResponse:
    """**Immutable once published.** Re-publishing the same version with different content is
    refused: reports already bound to it must keep meaning what they meant, and a rule that changes
    gets a new version."""
    return ReportDefinitionResponse.of(
        app.commands.dispatch(
            PublishReportDefinition(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                report_code=body.report_code,
                version=body.version,
                title=body.title,
                jurisdiction=body.jurisdiction,
                frequency=body.frequency,
                period_basis=body.period_basis,
                fields=tuple(
                    ReportFieldSpec(
                        key=f.key,
                        label=f.label or f.key,
                        domain=f.domain,
                        source_type=f.source_type,
                        transformation=f.transformation,
                        mandatory=f.mandatory,
                        source_attribute=f.source_attribute,
                        classification_map=tuple(
                            (str(a), str(b)) for a, b in f.classification_map
                        ),
                        note=f.note,
                    )
                    for f in body.fields
                ),
                required_sources=tuple(body.required_sources),
                currency_basis=body.currency_basis,
                declared_currency=(
                    Currency(body.declared_currency) if body.declared_currency else None
                ),
                integrity_blocks_finalization=body.integrity_blocks_finalization,
            )
        )
    )


@router.get(
    "/definitions",
    response_model=list[ReportDefinitionResponse],
    summary="Published reporting rules, newest version first",
)
def list_definitions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[ReportDefinitionResponse]:
    return [
        ReportDefinitionResponse.of(d)
        for d in app.queries.dispatch(
            ListReportDefinitions(
                actor_id=ctx.actor(), org_id=ctx.organization(), limit=limit, offset=offset
            )
        )
    ]


@router.get(
    "/definitions/{definition_id}",
    response_model=ReportDefinitionResponse,
    summary="One reporting rule, at one version",
)
def get_definition(
    definition_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReportDefinitionResponse:
    return ReportDefinitionResponse.of(
        app.queries.dispatch(
            GetReportDefinition(
                actor_id=ctx.actor(), org_id=ctx.organization(), definition_id=definition_id
            )
        )
    )


# ── periods ─────────────────────────────────────────────────────────────────────


@router.post(
    "/periods",
    response_model=ReportPeriodResponse,
    status_code=201,
    summary="Open a reporting period under a declared basis",
)
def open_period(
    body: OpenReportingPeriodBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReportPeriodResponse:
    """`basis` is part of the identity. A calendar month is **not** an accounting period is not a
    tax period — Billing owns four of these, and filing one as another is wrong in a way nobody
    notices until an audit."""
    return ReportPeriodResponse.of(
        app.commands.dispatch(
            OpenReportingPeriod(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                basis=body.basis,
                period_key=body.period_key,
                start=body.start,
                end=body.end,
                aligned_source_id=body.aligned_source_id,
            )
        )
    )


@router.get(
    "/periods",
    response_model=list[ReportPeriodResponse],
    summary="Reporting periods, newest first",
)
def list_periods(
    basis: str = "",
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[ReportPeriodResponse]:
    return [
        ReportPeriodResponse.of(p)
        for p in app.queries.dispatch(
            ListReportingPeriods(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                basis=basis,
                limit=limit,
                offset=offset,
            )
        )
    ]


@router.post(
    "/periods/{period_id}/close",
    response_model=ReportPeriodResponse,
    summary="Close a reporting period for filing",
)
def close_period(
    period_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReportPeriodResponse:
    """Closing a **reporting** period is not closing the ledger's accounting period — they are
    separate concepts with separate owners, and this endpoint touches only the former."""
    return ReportPeriodResponse.of(
        app.commands.dispatch(
            CloseReportingPeriod(
                actor_id=ctx.actor(), org_id=ctx.organization(), period_id=period_id
            )
        )
    )


# ── reports (collections before `{report_id}` so literals are not swallowed) ────


@router.post(
    "",
    response_model=ReportResponse,
    status_code=201,
    summary="Open a report run for one definition version over one period",
)
def create_report(
    body: CreateReportBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReportResponse:
    return ReportResponse.of(
        app.commands.dispatch(
            CreateReport(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                report_code=body.report_code,
                definition_version=body.definition_version,
                period_id=body.period_id,
                account_id=body.account_id,
            )
        )
    )


@router.get("", response_model=ReportPageResponse, summary="Reports, paginated at the database")
def list_reports(
    report_code: str = "",
    status: ReportStatus | None = None,
    period_id: str = "",
    finalized_only: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReportPageResponse:
    """`total` comes from a database count; nothing is filtered in Python after loading."""
    return ReportPageResponse.of(
        app.queries.dispatch(
            ListReports(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                report_code=report_code,
                status=status,
                period_id=period_id,
                finalized_only=finalized_only,
                limit=limit,
                offset=offset,
            )
        )
    )


@router.get(
    "/posture",
    response_model=ReportingPostureResponse,
    summary="The org's compliance picture — counts, never money",
)
def posture(
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReportingPostureResponse:
    """Grouped counts computed by the database. **No amount on this response.**"""
    return ReportingPostureResponse.of(
        app.queries.dispatch(
            GetReportingPosture(actor_id=ctx.actor(), org_id=ctx.organization())
        )
    )


@router.get(
    "/diff",
    response_model=ReportDiffResponse,
    summary="What changed between a filing and its correction",
)
def diff(
    earlier_report_id: str,
    later_report_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReportDiffResponse:
    """Compared by canonical digest, so a field is *changed* only when its content genuinely
    differs — not because two equal amounts were constructed differently."""
    return ReportDiffResponse.of(
        app.queries.dispatch(
            DiffReports(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                earlier_report_id=earlier_report_id,
                later_report_id=later_report_id,
            )
        )
    )


# ── one report ──────────────────────────────────────────────────────────────────


@router.get(
    "/{report_id}",
    response_model=ReportResponse,
    summary="One report, with the triggers the server will accept",
)
def get_report(
    report_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReportResponse:
    """`allowed_triggers` is the server's answer to *what can I do with this*. A finalized report
    returns an empty list — the lifecycle has no edge out of it."""
    return ReportResponse.of(
        app.queries.dispatch(
            GetReport(actor_id=ctx.actor(), org_id=ctx.organization(), report_id=report_id)
        )
    )


@router.get(
    "/{report_id}/sources",
    response_model=ReportSourcePageResponse,
    summary="The pinned input manifest, paginated",
)
def sources(
    report_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReportSourcePageResponse:
    """Provenance, not content: each row names a source, the revision it was read at, and its
    digest — so an examiner can tell whether it has moved since."""
    return ReportSourcePageResponse.of(
        app.queries.dispatch(
            GetReportSources(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                report_id=report_id,
                limit=limit,
                offset=offset,
            )
        )
    )


@router.get(
    "/{report_id}/evidence",
    response_model=list[ReportSourceResponse],
    summary="The evidence manifest — every source with its status",
)
def evidence(
    report_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> list[ReportSourceResponse]:
    """An unavailable source is **present and marked**, never omitted. A manifest that dropped what
    it could not read would present as complete exactly when it is least trustworthy."""
    return [
        ReportSourceResponse.of(s)
        for s in app.queries.dispatch(
            GetReportEvidence(
                actor_id=ctx.actor(), org_id=ctx.organization(), report_id=report_id
            )
        )
    ]


@router.get(
    "/{report_id}/exceptions",
    response_model=ReportExceptionPageResponse,
    summary="Named problems with this filing",
)
def exceptions(
    report_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReportExceptionPageResponse:
    """Each names what an operator must go and do. There is no generic "report failed"."""
    return ReportExceptionPageResponse.of(
        app.queries.dispatch(
            GetReportExceptions(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                report_id=report_id,
                limit=limit,
                offset=offset,
            )
        )
    )


@router.get(
    "/{report_id}/validation",
    response_model=ReportValidationResponse,
    summary="The validation verdict, check by check",
)
def validation(
    report_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReportValidationResponse:
    """**Validation is not reconciliation.** Every check asks about the report; none compares two
    financial domains against each other."""
    return ReportValidationResponse.of(
        app.queries.dispatch(
            GetReportValidation(
                actor_id=ctx.actor(), org_id=ctx.organization(), report_id=report_id
            )
        )
    )


@router.get(
    "/{report_id}/snapshot",
    response_model=ReportSnapshotResponse,
    summary="The finalized, content-addressed filing",
)
def snapshot(
    report_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReportSnapshotResponse:
    """Requires export permission. `report_digest` is recomputable from `representation`, which is
    what makes the filing defensible rather than merely archived."""
    return ReportSnapshotResponse.of(
        app.queries.dispatch(
            GetReportSnapshot(
                actor_id=ctx.actor(), org_id=ctx.organization(), report_id=report_id
            )
        )
    )


@router.get(
    "/{report_id}/provenance",
    response_model=ReportProvenanceResponse,
    summary="Which definition, which sources, what was unavailable, who finalized it",
)
def provenance(
    report_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReportProvenanceResponse:
    """Assembled from **stored rows alone** — the pinned manifest, the version the run recorded and
    the snapshot. It reads no current source, so the answer is stable after every source has
    moved, and it depends on no application log."""
    return ReportProvenanceResponse.of(
        app.queries.dispatch(
            GetReportProvenance(
                actor_id=ctx.actor(), org_id=ctx.organization(), report_id=report_id
            )
        )
    )


@router.get(
    "/{report_id}/submission-package",
    response_model=SubmissionPackageResponse,
    summary="The bundle prepared for an external submission this platform never performs",
)
def submission_package(
    report_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubmissionPackageResponse:
    return SubmissionPackageResponse.of(
        app.queries.dispatch(
            GetSubmissionPackage(
                actor_id=ctx.actor(), org_id=ctx.organization(), report_id=report_id
            )
        )
    )


# ── the run ─────────────────────────────────────────────────────────────────────


@router.post(
    "/{report_id}/prepare",
    response_model=ReportResponse,
    summary="Read every required source, pin it, and compute the declared fields",
)
def prepare(
    report_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReportResponse:
    """**The endpoint this milestone exists for.**

    Each source is pinned at the revision it was read, and the report's identity derives from the
    resulting digest — so re-preparing unmoved inputs rewrites nothing, and moved inputs produce a
    different report beside the old one. A source that could not be read becomes an `UNAVAILABLE`
    reference and a named exception; **it never becomes zero.**"""
    return ReportResponse.of(
        app.commands.dispatch(
            PrepareReport(actor_id=ctx.actor(), org_id=ctx.organization(), report_id=report_id)
        )
    )


@router.post(
    "/{report_id}/validate",
    response_model=ReportValidationResponse,
    summary="Check the report against its definition's contract",
)
def validate(
    report_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReportValidationResponse:
    return ReportValidationResponse.of(
        app.commands.dispatch(
            ValidateReport(actor_id=ctx.actor(), org_id=ctx.organization(), report_id=report_id)
        )
    )


@router.post(
    "/{report_id}/finalize",
    response_model=ReportResponse,
    summary="Take the immutable snapshot",
)
def finalize(
    report_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReportResponse:
    """Requires elevated permission, and refuses on a validation that did not pass, on any blocking
    exception, and on blocked completeness — all re-read inside the transaction. The lifecycle has
    no edge to `FINALIZED` except from `READY`, so **a filing cannot skip validation.**"""
    return ReportResponse.of(
        app.commands.dispatch(
            FinalizeReport(actor_id=ctx.actor(), org_id=ctx.organization(), report_id=report_id)
        )
    )


@router.post(
    "/{report_id}/correction",
    response_model=ReportResponse,
    status_code=201,
    summary="File a correction — a new report beside the original",
)
def correct(
    report_id: str,
    body: CorrectReportBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReportResponse:
    """Requires elevated permission. **The original is never edited** — it gains only a
    back-reference, so an examiner reading the filed report sees there is a later one and can diff
    the two. Refused when the inputs have not moved and the definition version has not changed:
    a correction that corrects nothing is not a filing."""
    return ReportResponse.of(
        app.commands.dispatch(
            CorrectReport(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                report_id=report_id,
                definition_version=body.definition_version,
                reason=body.reason,
            )
        )
    )


@router.post(
    "/{report_id}/exceptions/acknowledge",
    response_model=ReportExceptionResponse,
    summary="Record that somebody saw a problem",
)
def acknowledge(
    report_id: str,
    body: AcknowledgeExceptionBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReportExceptionResponse:
    """**This does not unblock a filing.** Acknowledgement records that somebody saw the problem,
    which is a different fact from the problem being gone — only a re-prepared run that no longer
    observes it clears the path."""
    return ReportExceptionResponse.of(
        app.commands.dispatch(
            AcknowledgeReportException(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                report_id=report_id,
                exception_id=body.exception_id,
                reason=body.reason,
            )
        )
    )


@router.post(
    "/{report_id}/source-unavailable",
    response_model=ReportResponse,
    summary="Declare a source unreadable, so the gap is on the record",
)
def source_unavailable(
    report_id: str,
    body: RecordSourceUnavailableBody,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> ReportResponse:
    return ReportResponse.of(
        app.commands.dispatch(
            RecordSourceUnavailable(
                actor_id=ctx.actor(),
                org_id=ctx.organization(),
                report_id=report_id,
                domain=body.domain,
                reason=body.reason,
            )
        )
    )


@router.post(
    "/{report_id}/submission-package",
    response_model=SubmissionPackageResponse,
    status_code=201,
    summary="Bundle a finalized filing for submission",
)
def create_package(
    report_id: str,
    ctx: BillingRequestContext = Depends(require_org),
    app: BillingApplication = Depends(get_application),
) -> SubmissionPackageResponse:
    """**Nothing here transmits anything.** The package is content-addressed on the report digest,
    so preparing the same finalized filing twice returns one package — and `submitted_at` stays
    empty, because §24 defers transmission to a future adapter that does not exist yet."""
    return SubmissionPackageResponse.of(
        app.commands.dispatch(
            CreateSubmissionPackage(
                actor_id=ctx.actor(), org_id=ctx.organization(), report_id=report_id
            )
        )
    )
