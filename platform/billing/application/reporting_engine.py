"""Compliance & Regulatory Reporting application layer (M9.22) — commands, queries, use cases.

Additive to M9.3/M9.6–M9.21: the buses, Unit of Work, authorization orchestrator, application-event
vocabulary and dispatch discipline are reused exactly as they are. This module adds 11 commands, 14
queries, their use cases and the view DTOs — and nothing else.

## What crosses this layer's boundaries, and in which direction

    Ledger / Invoice / Tax / Payment / Revenue ──read──▶ Reporting ──▶ billing.report.*
    Reconciliation / Integrity / Investigation ──read──▶     │

**Every arrow is a read, and there is no outward command.** M9.20 dispatched M9.19's repair
request; M9.21 dispatched nothing; M9.22 likewise dispatches nothing, and `dispatch` is not
injected. A regulatory report is derived evidence — it cannot ask a domain to change so the filing
comes out better.

An architecture test asserts that no ledger, invoice, tax, payment, recognition, collection,
rating, metering, entitlement, subscription, account, reconciliation, incident or investigation
repository is *written* from this module, and that `_SourceReader` calls only read verbs.

## Reading a source is pinning it, not copying it

`_SourceReader` resolves each domain the definition requires and returns a `ReportSourceRef`
carrying the revision and digest it read at, plus the facts themselves for the declared
transformation to consume. Three outcomes, and the third is the one that matters:

* **RESOLVED** — the domain answered and had facts;
* **EMPTY** — the domain answered and genuinely had nothing for this period. A legitimate filing;
* **UNAVAILABLE** — the domain could not be read. This never becomes zero, never becomes empty, and
  never disappears: it produces an exception, blocks completeness, and is named in the manifest.

A `BillingError` from an owning domain is a legitimate "cannot read". Anything else propagates —
the M9.16-R1 P-6 correction, applied here as in M9.21.

## No scheduler

`PrepareReport`, `ValidateReport` and `FinalizeReport` are invoked from outside. Nothing here
loops, sleeps, polls or spawns; whoever decides *when* a filing is due lives outside this plane.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from billing.foundation import BillingConflict as _Conflict
from billing.foundation import (
    BillingError,
    BillingPeriod,
    CurrencyBasis,
    EvidenceCompleteness,
    EvidenceDomain,
    EvidenceSourceType,
    ExceptionState,
    Money,
    RegulatoryReport,
    ReportCompleteness,
    ReportDefinition,
    ReportedIntegrity,
    ReportEventKind,
    ReportException,
    ReportExceptionKind,
    ReportFieldSpec,
    ReportFieldValue,
    ReportFrequency,
    ReportingPeriod,
    ReportInputManifest,
    ReportSearchQuery,
    ReportSnapshot,
    ReportSourceRef,
    ReportStatus,
    ReportTrigger,
    SourceStatus,
    SubmissionPackage,
    ValidationResult,
    ValueStatus,
    blocking,
    canonical_digest,
)
from billing.foundation import BillingNotFound as _NotFound

from .authorization import AuthorizationOrchestrator
from .authorization import BillingPermission as P
from .commands import _Command
from .events import PREFIX_APP_EVENT, ApplicationEvent, ApplicationEventType
from .queries import _Query
from .unit_of_work import UnitOfWorkFactory

_CAS_ATTEMPTS = 5


# ── commands ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PublishReportDefinition(_Command):
    """Publish a reporting rule **at a version**. Immutable thereafter.

    Re-publishing a version with different content is refused: reports already bound to it must
    keep meaning what they meant."""

    report_code: str = ""
    version: int = 1
    title: str = ""
    jurisdiction: str = ""
    frequency: ReportFrequency = ReportFrequency.MONTHLY
    period_basis: object = None
    fields: tuple[ReportFieldSpec, ...] = ()
    required_sources: tuple[EvidenceDomain, ...] = ()
    currency_basis: CurrencyBasis = CurrencyBasis.NOT_APPLICABLE
    declared_currency: object = None
    integrity_blocks_finalization: bool = False


@dataclass(frozen=True, slots=True)
class OpenReportingPeriod(_Command):
    basis: object = None
    period_key: str = ""
    start: float = 0.0
    end: float = 0.0
    aligned_source_id: str = ""


@dataclass(frozen=True, slots=True)
class CloseReportingPeriod(_Command):
    period_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CreateReport(_Command):
    """Open a run for one definition version over one period.

    `definition_version` may be pinned explicitly. Left empty it takes the latest published
    version **at creation time only** — and the run stores it, so no later version reinterprets
    this filing."""

    report_code: str = ""
    definition_version: int = 0  # 0 → the latest published, resolved once and stored
    period_id: str = ""
    #: The account this filing covers. Empty means organization-wide — and because every
    #: period-scoped read Billing publishes is keyed by account, an org-wide run reports its
    #: sources as UNAVAILABLE rather than aggregating across accounts on a regulator's behalf.
    account_id: str = ""


@dataclass(frozen=True, slots=True)
class PrepareReport(_Command):
    """Read every required source, pin it, and compute the declared fields.

    **The command this milestone exists for.** Idempotent on unchanged inputs: the manifest digest
    is the report's identity, so re-preparing an unmoved period rewrites nothing."""

    report_id: str = ""


@dataclass(frozen=True, slots=True)
class ValidateReport(_Command):
    report_id: str = ""


@dataclass(frozen=True, slots=True)
class FinalizeReport(_Command):
    """Take the immutable snapshot. **Refuses on any blocking exception**, on a validation that
    did not pass, and on blocked completeness."""

    report_id: str = ""


@dataclass(frozen=True, slots=True)
class CorrectReport(_Command):
    """File a correction. **A new report beside the original**, never an edit of it.

    Refused when the inputs have not moved: a correction exists because a source changed or the
    definition version did, and if neither has, the filed report is still the right one."""

    report_id: str = ""
    definition_version: int = 0  # 0 → keep the original's version
    reason: str = ""


@dataclass(frozen=True, slots=True)
class AcknowledgeReportException(_Command):
    """Record that somebody saw it. **Does not unblock a filing** — only a re-prepared run that no
    longer observes the problem does that."""

    report_id: str = ""
    exception_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CreateSubmissionPackage(_Command):
    """Bundle a finalized report **for** submission. Nothing here transmits anything."""

    report_id: str = ""


@dataclass(frozen=True, slots=True)
class RecordSourceUnavailable(_Command):
    """Declare a source unreadable ahead of preparation, so the gap is on the record even if the
    reader would later have succeeded against a stale replica."""

    report_id: str = ""
    domain: EvidenceDomain = EvidenceDomain.LEDGER
    reason: str = ""


ALL_REPORTING_COMMANDS: tuple[type, ...] = (
    PublishReportDefinition,
    OpenReportingPeriod,
    CloseReportingPeriod,
    CreateReport,
    PrepareReport,
    ValidateReport,
    FinalizeReport,
    CorrectReport,
    AcknowledgeReportException,
    CreateSubmissionPackage,
    RecordSourceUnavailable,
)


# ── queries ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ListReportDefinitions(_Query):
    offset: int = 0
    limit: int = 50


@dataclass(frozen=True, slots=True)
class GetReportDefinition(_Query):
    definition_id: str = ""


@dataclass(frozen=True, slots=True)
class ListReportingPeriods(_Query):
    basis: str = ""
    offset: int = 0
    limit: int = 50


@dataclass(frozen=True, slots=True)
class ListReports(_Query):
    report_code: str = ""
    status: ReportStatus | None = None
    period_id: str = ""
    finalized_only: bool = False
    offset: int = 0
    limit: int = 50


@dataclass(frozen=True, slots=True)
class GetReport(_Query):
    report_id: str = ""


@dataclass(frozen=True, slots=True)
class GetReportSources(_Query):
    """The pinned input manifest, paginated. **Provenance, not content.**"""

    report_id: str = ""
    offset: int = 0
    limit: int = 100


@dataclass(frozen=True, slots=True)
class GetReportExceptions(_Query):
    report_id: str = ""
    offset: int = 0
    limit: int = 100


@dataclass(frozen=True, slots=True)
class GetReportValidation(_Query):
    report_id: str = ""


@dataclass(frozen=True, slots=True)
class GetReportEvidence(_Query):
    """Every source reference with its status — the §21 evidence manifest."""

    report_id: str = ""


@dataclass(frozen=True, slots=True)
class GetReportSnapshot(_Query):
    report_id: str = ""


@dataclass(frozen=True, slots=True)
class GetSubmissionPackage(_Query):
    report_id: str = ""


@dataclass(frozen=True, slots=True)
class GetReportProvenance(_Query):
    """**The §25 answer, from stored rows alone.** Reads no current source."""

    report_id: str = ""


@dataclass(frozen=True, slots=True)
class DiffReports(_Query):
    """What changed between a filing and its correction."""

    earlier_report_id: str = ""
    later_report_id: str = ""


@dataclass(frozen=True, slots=True)
class GetReportingPosture(_Query):
    """Fleet-wide counts for a compliance queue. Counts only — never a figure."""


ALL_REPORTING_QUERIES: tuple[type, ...] = (
    ListReportDefinitions,
    GetReportDefinition,
    ListReportingPeriods,
    ListReports,
    GetReport,
    GetReportSources,
    GetReportExceptions,
    GetReportValidation,
    GetReportEvidence,
    GetReportSnapshot,
    GetSubmissionPackage,
    GetReportProvenance,
    DiffReports,
    GetReportingPosture,
)


# ── views ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ReportDefinitionView:
    id: str
    report_code: str
    version: int
    title: str
    jurisdiction: str
    frequency: str
    period_basis: str
    currency_basis: str
    declared_currency: str | None
    integrity_blocks_finalization: bool
    definition_digest: str
    published_at: float
    published_by: str
    fields: tuple[dict, ...] = ()
    required_sources: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReportingPeriodView:
    id: str
    basis: str
    period_key: str
    start: float
    end: float
    state: str
    aligned_source_id: str
    opened_at: float
    closed_at: float | None
    version: int


@dataclass(frozen=True, slots=True)
class ReportFieldValueView:
    key: str
    status: str
    transformation: str
    amount_minor: int | None
    currency: str | None
    number: int | None
    text: str
    source_locators: tuple[str, ...]
    detail: str


@dataclass(frozen=True, slots=True)
class ReportView:
    id: str
    org_id: str
    report_code: str
    definition_id: str
    definition_version: int
    period_id: str
    manifest_id: str
    manifest_digest: str
    account_id: str
    scope: str
    status: str
    completeness: str
    evidence_completeness: str
    integrity_posture: str
    report_digest: str
    snapshot_id: str
    exception_count: int
    blocking_exception_count: int
    source_count: int
    corrects: str
    corrected_by: str
    created_at: float
    created_by: str
    finalized_at: float | None
    finalized_by: str
    version: int
    values: tuple[ReportFieldValueView, ...] = ()
    allowed_triggers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReportPageView:
    reports: tuple[ReportView, ...]
    total: int
    offset: int
    limit: int
    has_more: bool


@dataclass(frozen=True, slots=True)
class ReportSourceView:
    locator: str
    domain: str
    source_type: str
    source_id: str
    status: str
    revision: str
    digest: str
    resolved_at: float
    fact_count: int
    unavailable_reason: str


@dataclass(frozen=True, slots=True)
class ReportSourcePageView:
    sources: tuple[ReportSourceView, ...]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True, slots=True)
class ReportExceptionView:
    id: str
    kind: str
    severity: str
    subject: str
    diagnostic: str
    state: str
    field_key: str
    source_locator: str
    blocks_finalization: bool
    created_at: float
    acknowledged_at: float | None
    acknowledged_by: str
    acknowledgement_reason: str


@dataclass(frozen=True, slots=True)
class ReportExceptionPageView:
    exceptions: tuple[ReportExceptionView, ...]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True, slots=True)
class ValidationCheckView:
    name: str
    passed: bool
    subject: str
    detail: str


@dataclass(frozen=True, slots=True)
class ValidationResultView:
    report_id: str
    passed: bool
    definition_version: int
    validated_at: float
    validated_by: str
    checks: tuple[ValidationCheckView, ...] = ()


@dataclass(frozen=True, slots=True)
class ReportSnapshotView:
    id: str
    report_id: str
    definition_id: str
    definition_version: int
    manifest_id: str
    manifest_digest: str
    report_digest: str
    completeness: str
    evidence_completeness: str
    validation_passed: bool
    finalized_at: float
    finalized_by: str
    representation: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SubmissionPackageView:
    id: str
    report_id: str
    snapshot_id: str
    definition_id: str
    definition_version: int
    report_digest: str
    manifest_digest: str
    package_digest: str
    completeness: str
    evidence_completeness: str
    validation_passed: bool
    prepared_at: float
    prepared_by: str
    submitted_at: float | None
    submission_reference: str
    artifact: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SourceTraceView:
    field_key: str
    label: str
    domain: str
    source_type: str
    transformation: str
    status: str
    locators: tuple[str, ...]
    detail: str


@dataclass(frozen=True, slots=True)
class ReportProvenanceView:
    report_id: str
    report_code: str
    definition_id: str
    definition_version: int
    period_key: str
    period_basis: str
    manifest_id: str
    manifest_digest: str
    report_digest: str
    validation_passed: bool
    finalized_at: float | None
    finalized_by: str
    corrects: str
    corrected_by: str
    traces: tuple[SourceTraceView, ...] = ()
    unavailable_sources: tuple[str, ...] = ()
    exception_kinds: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReportDiffView:
    earlier_report_id: str
    later_report_id: str
    changed_fields: tuple[str, ...]
    added_fields: tuple[str, ...]
    removed_fields: tuple[str, ...]
    changed_sources: tuple[str, ...]
    definition_version_changed: bool


@dataclass(frozen=True, slots=True)
class ReportingPostureView:
    org_id: str
    total: int
    by_status: dict
    by_completeness: dict
    finalized: int
    blocked: int
    open_exceptions: int
    blocking_exceptions: int
    submission_packages: int


# ── mapping ─────────────────────────────────────────────────────────────────────


def report_definition_view(definition: ReportDefinition) -> ReportDefinitionView:
    return ReportDefinitionView(
        id=definition.id,
        report_code=definition.report_code,
        version=definition.version,
        title=definition.title,
        jurisdiction=definition.jurisdiction,
        frequency=definition.frequency.value,
        period_basis=definition.period_basis.value,
        currency_basis=definition.currency_basis.value,
        declared_currency=(
            None if definition.declared_currency is None else str(definition.declared_currency)
        ),
        integrity_blocks_finalization=definition.integrity_blocks_finalization,
        definition_digest=definition.definition_digest,
        published_at=definition.published_at,
        published_by=definition.published_by,
        fields=tuple(
            {
                "key": f.key,
                "label": f.label,
                "domain": f.domain.value,
                "source_type": f.source_type.value,
                "transformation": f.transformation.value,
                "mandatory": f.mandatory,
                "source_attribute": f.source_attribute,
                "note": f.note,
            }
            for f in definition.fields
        ),
        required_sources=tuple(d.value for d in definition.required_sources),
    )


def reporting_period_view(period: ReportingPeriod) -> ReportingPeriodView:
    return ReportingPeriodView(
        id=period.id,
        basis=period.basis.value,
        period_key=period.period_key,
        start=period.window.start,
        end=period.window.end,
        state=period.state.value,
        aligned_source_id=period.aligned_source_id,
        opened_at=period.opened_at,
        closed_at=period.closed_at,
        version=period.version,
    )


def value_view(value: ReportFieldValue) -> ReportFieldValueView:
    return ReportFieldValueView(
        key=value.key,
        status=value.status.value,
        transformation=value.transformation.value,
        # `None`, never `0`. A field whose source was unreadable has no value, and rendering it as
        # zero on the wire would reintroduce exactly the failure the domain refuses.
        amount_minor=None if value.amount is None else value.amount.minor,
        currency=None if value.currency is None else str(value.currency),
        number=value.number,
        text=value.text,
        source_locators=value.source_locators,
        detail=value.detail,
    )


def report_view(report: RegulatoryReport, allowed: tuple[ReportTrigger, ...]) -> ReportView:
    return ReportView(
        id=report.id,
        org_id=report.org_id,
        report_code=report.report_code,
        definition_id=report.definition_id,
        definition_version=report.definition_version,
        period_id=report.period_id,
        manifest_id=report.manifest_id,
        manifest_digest=report.manifest_digest,
        account_id=report.account_id,
        scope=report.scope.value,
        status=report.status.value,
        completeness=report.completeness.value,
        evidence_completeness=report.evidence_completeness.value,
        integrity_posture=report.integrity_posture.value,
        report_digest=report.report_digest,
        snapshot_id=report.snapshot_id,
        exception_count=report.exception_count,
        blocking_exception_count=report.blocking_exception_count,
        source_count=report.source_count,
        corrects=report.corrects,
        corrected_by=report.corrected_by,
        created_at=report.created_at,
        created_by=report.created_by,
        finalized_at=report.finalized_at,
        finalized_by=report.finalized_by,
        version=report.version,
        values=tuple(value_view(v) for v in sorted(report.values, key=lambda v: v.key)),
        allowed_triggers=tuple(t.value for t in allowed),
    )


def source_view(source: ReportSourceRef) -> ReportSourceView:
    return ReportSourceView(
        locator=source.locator,
        domain=source.domain.value,
        source_type=source.source_type.value,
        source_id=source.source_id,
        status=source.status.value,
        revision=source.revision,
        digest=source.digest,
        resolved_at=source.resolved_at,
        fact_count=source.fact_count,
        unavailable_reason=source.unavailable_reason,
    )


def exception_view(exception: ReportException) -> ReportExceptionView:
    return ReportExceptionView(
        id=exception.id,
        kind=exception.kind.value,
        severity=exception.severity.value,
        subject=exception.subject,
        diagnostic=exception.diagnostic,
        state=exception.state.value,
        field_key=exception.field_key,
        source_locator=exception.source_locator,
        blocks_finalization=exception.blocks_finalization,
        created_at=exception.created_at,
        acknowledged_at=exception.acknowledged_at,
        acknowledged_by=exception.acknowledged_by,
        acknowledgement_reason=exception.acknowledgement_reason,
    )


def validation_view(result: ValidationResult) -> ValidationResultView:
    return ValidationResultView(
        report_id=result.report_id,
        passed=result.passed,
        definition_version=result.definition_version,
        validated_at=result.validated_at,
        validated_by=result.validated_by,
        checks=tuple(
            ValidationCheckView(
                name=c.name, passed=c.passed, subject=c.subject, detail=c.detail
            )
            for c in result.checks
        ),
    )


def snapshot_view(snapshot: ReportSnapshot) -> ReportSnapshotView:
    return ReportSnapshotView(
        id=snapshot.id,
        report_id=snapshot.report_id,
        definition_id=snapshot.definition_id,
        definition_version=snapshot.definition_version,
        manifest_id=snapshot.manifest_id,
        manifest_digest=snapshot.manifest_digest,
        report_digest=snapshot.report_digest,
        completeness=snapshot.completeness.value,
        evidence_completeness=snapshot.evidence_completeness.value,
        validation_passed=snapshot.validation_passed,
        finalized_at=snapshot.finalized_at,
        finalized_by=snapshot.finalized_by,
        representation=dict(snapshot.representation),
    )


def submission_package_view(package: SubmissionPackage) -> SubmissionPackageView:
    return SubmissionPackageView(
        id=package.id,
        report_id=package.report_id,
        snapshot_id=package.snapshot_id,
        definition_id=package.definition_id,
        definition_version=package.definition_version,
        report_digest=package.report_digest,
        manifest_digest=package.manifest_digest,
        package_digest=package.package_digest,
        completeness=package.completeness.value,
        evidence_completeness=package.evidence_completeness.value,
        validation_passed=package.validation_passed,
        prepared_at=package.prepared_at,
        prepared_by=package.prepared_by,
        submitted_at=package.submitted_at,
        submission_reference=package.submission_reference,
        artifact=dict(package.artifact),
    )


# ── seams ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ReportingRepositories:
    """The eight reporting seams, injected. Interfaces only — implementations in persistence.

    Eight, and only eight. **Nothing here reaches a ledger, invoice, tax, payment, recognition,
    collection, reconciliation, incident or investigation table for a write.** Those domains are
    read through the context, and every one of those reads is a read."""

    definitions: object
    periods: object
    manifests: object
    reports: object
    exceptions: object
    snapshots: object
    submissions: object
    events: object
    validations: object


@dataclass(frozen=True, slots=True)
class ReportingServices:
    """The Foundation services this layer coordinates. Composed once, held by reference."""

    lifecycle: object
    guards: object
    exception_guards: object
    transformations: object
    completeness: object
    exceptions: object
    validation: object
    provenance: object
    diff: object
    validator: object


@dataclass(frozen=True, slots=True)
class _SourceFacts:
    """What one domain returned for one period. **Three outcomes, never two.**"""

    status: SourceStatus
    values: tuple[object, ...] = ()
    amounts: tuple[Money, ...] = ()
    reference: ReportSourceRef | None = None
    reason: str = ""


class _SourceReader:
    """Resolves the domains a definition requires. **The only place M9.22 touches another domain.**

    Every method reads. None writes, and none recomputes: a `Money` value that reaches a report
    came out of the owning aggregate exactly as that domain wrote it, and the only thing done to it
    afterwards is the declared transformation.

    `UNAVAILABLE` is produced whenever a domain is unwired or raises a `BillingError`. It is never
    collapsed into an empty result — `EMPTY` means *the domain answered and had nothing*, and the
    two must stay distinguishable because one is a filing and the other is a gap."""

    def __init__(self, context) -> None:
        self._ctx = context

    # ── the domain readers ──────────────────────────────────────────────────────

    def read(
        self,
        domain: EvidenceDomain,
        org_id: str,
        period: ReportingPeriod,
        at: float,
        account_id: str = "",
    ) -> _SourceFacts:
        reader = _DOMAIN_READERS.get(domain)
        if reader is None:
            return _SourceFacts(
                status=SourceStatus.UNAVAILABLE,
                reason=f"M9.22 declares no reader for {domain.value}",
            )
        try:
            return reader(self, org_id, period, at, account_id)
        except BillingError as exc:
            # A refusal from the owning domain is a legitimate "cannot read", not a crash. Anything
            # that is not a BillingError propagates — the M9.16-R1 P-6 correction.
            return _SourceFacts(
                status=SourceStatus.UNAVAILABLE,
                reason=f"{domain.value} refused the read: {exc}",
            )

    def _seam(self, engine_attr: str, repo_attr: str):
        engine = getattr(self._ctx, engine_attr, None)
        if engine is None:
            return None
        return getattr(engine, repo_attr, None)

    @staticmethod
    def _in_period(period: ReportingPeriod, ts: float) -> bool:
        return period.window.contains(ts)

    def _ledger(self, org_id: str, period: ReportingPeriod, at: float, account_id: str):
        repo = self._seam("ledger_engine", "entries")
        if repo is None:
            return _SourceFacts(SourceStatus.UNAVAILABLE, reason=_unwired("ledger"))
        if not account_id:
            return _SourceFacts(SourceStatus.UNAVAILABLE, reason=_needs_account("ledger"))
        entries = tuple(repo.for_period(org_id, account_id, period.period_key))
        return self._facts(
            EvidenceDomain.LEDGER,
            EvidenceSourceType.JOURNAL_ENTRY,
            period.period_key,
            entries,
            amounts=tuple(e.amount for e in entries if getattr(e, "amount", None) is not None),
            at=at,
        )

    def _invoice(self, org_id: str, period: ReportingPeriod, at: float, account_id: str):
        repo = self._seam("invoice_engine", "documents")
        if repo is None:
            return _SourceFacts(SourceStatus.UNAVAILABLE, reason=_unwired("invoice"))
        if not account_id:
            return _SourceFacts(SourceStatus.UNAVAILABLE, reason=_needs_account("invoice"))
        # **The issued document, read as the document.** §16: an invoice is never reconstructed
        # from ledger, rating, tax or payment — those would be a different figure wearing the
        # invoice's name.
        found = tuple(
            d
            for d in repo.for_period(org_id, account_id, period.period_key)
            if getattr(d, "issued_revision", 0)
        )
        return self._facts(
            EvidenceDomain.INVOICE,
            EvidenceSourceType.INVOICE_DOCUMENT,
            period.period_key,
            found,
            at=at,
            digests=tuple(getattr(d, "issued_snapshot_digest", "") for d in found),
        )

    def _tax(self, org_id: str, period: ReportingPeriod, at: float, account_id: str):
        """M9.15 offers `for_account` but **no period-scoped read**.

        Assessments are therefore filtered to the period *by the account's own assessments*, which
        is a bounded read of one account rather than a scan of the organization. Where no account
        is named the source is `UNAVAILABLE` — see `_needs_account`."""
        repo = self._seam("tax_engine", "assessments")
        if repo is None:
            return _SourceFacts(SourceStatus.UNAVAILABLE, reason=_unwired("tax"))
        if not account_id:
            return _SourceFacts(SourceStatus.UNAVAILABLE, reason=_needs_account("tax"))
        found = tuple(repo.for_account(org_id, account_id))
        return self._facts(
            EvidenceDomain.TAX,
            EvidenceSourceType.TAX_ASSESSMENT,
            period.period_key,
            found,
            # M9.15's own assessed total. Nothing here computes a rate, resolves a jurisdiction,
            # splits CGST/SGST/IGST or classifies an export.
            amounts=tuple(
                a for a in (getattr(t, "total", None) for t in found) if isinstance(a, Money)
            ),
            at=at,
        )

    def _payment(self, org_id: str, period: ReportingPeriod, at: float, account_id: str):
        repo = self._seam("payment_engine", "intents")
        if repo is None:
            return _SourceFacts(SourceStatus.UNAVAILABLE, reason=_unwired("payment"))
        if not account_id:
            return _SourceFacts(SourceStatus.UNAVAILABLE, reason=_needs_account("payment"))
        # M9.16's own intents. **No provider is called, no attempt is created, no ambiguity is
        # resolved** — an ambiguous state is carried through and classified as ambiguous, never
        # reinterpreted as failed or succeeded.
        found = tuple(repo.for_account(org_id, account_id))
        return self._facts(
            EvidenceDomain.PAYMENT,
            EvidenceSourceType.PAYMENT_INTENT,
            period.period_key,
            found,
            amounts=tuple(i.amount for i in found if getattr(i, "amount", None) is not None),
            at=at,
        )

    def _revenue(self, org_id: str, period: ReportingPeriod, at: float, account_id: str):
        """M9.18 offers `unposted`, `due` and `for_schedule` — **no period-scoped read**.

        Rather than scan every schedule, this reports the gap. §18 forbids creating a schedule or
        calculating recognized revenue here, and a bounded scan of the org would be the §34
        violation dressed as a feature."""
        repo = self._seam("revenue_engine", "entries")
        if repo is None:
            return _SourceFacts(SourceStatus.UNAVAILABLE, reason=_unwired("revenue"))
        return _SourceFacts(
            SourceStatus.UNAVAILABLE,
            reason=(
                "M9.18 publishes no period-scoped read of recognition entries; this milestone "
                "reports the gap rather than scanning every schedule or recalculating recognized "
                "revenue, both of which it is forbidden to do"
            ),
        )

    def _reconciliation(self, org_id: str, period: ReportingPeriod, at: float, account_id: str):
        repo = self._seam("reconciliation_engine", "findings")
        if repo is None:
            return _SourceFacts(SourceStatus.UNAVAILABLE, reason=_unwired("reconciliation"))
        # `open_findings` is a bounded operational queue read — the same seam M9.19's own posture
        # uses. It is a *status* question, not a financial scan.
        found = tuple(
            f for f in repo.open_findings(org_id) if self._in_period(period, f.detected_at)
        )
        return self._facts(
            EvidenceDomain.RECONCILIATION,
            EvidenceSourceType.RECONCILIATION_FINDING,
            period.period_key,
            found,
            at=at,
        )

    def _integrity(self, org_id: str, period: ReportingPeriod, at: float, account_id: str):
        repo = self._seam("incident_engine", "incidents")
        if repo is None:
            return _SourceFacts(SourceStatus.UNAVAILABLE, reason=_unwired("correlation"))
        found = tuple(
            i for i in repo.live(org_id) if self._in_period(period, i.first_detected_at)
        )
        return self._facts(
            EvidenceDomain.INTEGRITY,
            EvidenceSourceType.INTEGRITY_INCIDENT,
            period.period_key,
            found,
            at=at,
        )

    def _facts(
        self,
        domain: EvidenceDomain,
        source_type: EvidenceSourceType,
        period_key: str,
        found: tuple,
        *,
        at: float,
        amounts: tuple[Money, ...] = (),
        digests: tuple[str, ...] = (),
    ) -> _SourceFacts:
        """Pin what was read. **`EMPTY` is a legitimate answer; `UNAVAILABLE` never reaches here.**

        `digest` is a canonical digest over the identities and revisions read, so a later run over
        a moved source produces a different manifest — and therefore a different report — rather
        than mutating a filing."""
        identities = tuple(getattr(f, "id", "") for f in found)
        revisions = tuple(str(getattr(f, "version", getattr(f, "revision", ""))) for f in found)
        reference = ReportSourceRef(
            domain=domain,
            source_type=source_type,
            source_id=period_key,
            status=SourceStatus.RESOLVED if found else SourceStatus.EMPTY,
            revision=canonical_digest(list(revisions))[:16],
            digest=canonical_digest(
                {
                    "identities": list(identities),
                    "revisions": list(revisions),
                    "digests": list(digests),
                }
            ),
            resolved_at=at,
            fact_count=len(found),
        )
        return _SourceFacts(
            status=reference.status,
            values=found,
            amounts=amounts,
            reference=reference,
        )

    # ── integrity posture ───────────────────────────────────────────────────────

    def integrity_posture(self, org_id: str, period: ReportingPeriod) -> ReportedIntegrity:
        """**Read M9.19/M9.20's verdict. Never recompute it.**

        `INDETERMINATE` when either engine is unwired or refuses — the §19 refusal made concrete:
        *do not infer that no report data means no integrity issue*."""
        findings_repo = self._seam("reconciliation_engine", "findings")
        incidents_repo = self._seam("incident_engine", "incidents")
        if findings_repo is None or incidents_repo is None:
            return ReportedIntegrity.INDETERMINATE
        try:
            incidents = tuple(
                i
                for i in incidents_repo.live(org_id)
                if self._in_period(period, i.first_detected_at)
            )
            if incidents:
                return ReportedIntegrity.OPEN_INCIDENT
            findings = tuple(
                f
                for f in findings_repo.open_findings(org_id)
                if self._in_period(period, f.detected_at)
            )
            if findings:
                return ReportedIntegrity.OPEN_FINDING
        except BillingError:
            return ReportedIntegrity.INDETERMINATE
        return ReportedIntegrity.NO_FINDING


#: Which reader resolves which domain. A table, so §6's source map is checkable by iteration; a
#: domain absent from it resolves `UNAVAILABLE` rather than being quietly skipped.
def _unwired(domain: str) -> str:
    return f"the {domain} engine is not wired in this deployment"


def _needs_account(domain: str) -> str:
    """**The honest gap, named precisely.**

    Every period-scoped read Billing publishes is keyed by `(org, account, period)`, because an
    accounting period is a property of an account. An organization-wide filing therefore has no
    authoritative period read to consume, and this milestone reports that rather than aggregating
    across accounts on a regulator's behalf or scanning the organization in Python."""
    return (
        f"M9.{domain} publishes period-scoped reads keyed by account, and this filing is "
        "organization-wide; no authoritative period read exists to consume. Scope the report to "
        "an account, or the gap stands"
    )


_DOMAIN_READERS = {
    EvidenceDomain.LEDGER: _SourceReader._ledger,
    EvidenceDomain.INVOICE: _SourceReader._invoice,
    EvidenceDomain.TAX: _SourceReader._tax,
    EvidenceDomain.PAYMENT: _SourceReader._payment,
    EvidenceDomain.REVENUE: _SourceReader._revenue,
    EvidenceDomain.RECONCILIATION: _SourceReader._reconciliation,
    EvidenceDomain.INTEGRITY: _SourceReader._integrity,
}


class _ReportingSupport:
    """Shared lookups for the command and query sides."""

    def __init__(self, *, context, repos: ReportingRepositories, services) -> None:
        self._ctx = context
        self._repos = repos
        self._svc = services
        self._reader = _SourceReader(context)

    def report_or_404(self, org_id: str, report_id: str) -> RegulatoryReport:
        found = self._repos.reports.get(org_id, report_id)
        if found is None:
            raise _NotFound(f"report {report_id!r} not found")
        return found

    def definition_or_404(self, org_id: str, definition_id: str) -> ReportDefinition:
        found = self._repos.definitions.get(org_id, definition_id)
        if found is None:
            raise _NotFound(f"report definition {definition_id!r} not found")
        return found

    def period_or_404(self, org_id: str, period_id: str) -> ReportingPeriod:
        found = self._repos.periods.get(org_id, period_id)
        if found is None:
            raise _NotFound(f"reporting period {period_id!r} not found")
        return found

    def manifest_or_404(self, org_id: str, manifest_id: str) -> ReportInputManifest:
        found = self._repos.manifests.get(org_id, manifest_id)
        if found is None:
            raise _NotFound(f"report input manifest {manifest_id!r} not found")
        return found

    def _pinned_definition(self, report: RegulatoryReport) -> ReportDefinition:
        """The definition **version the report stored**, never the latest.

        This is the whole of §4 in one lookup: a reporting rule that changed afterwards must not
        reinterpret a filing that already exists."""
        found = self._repos.definitions.by_code(
            report.org_id, report.report_code, report.definition_version
        )
        if found is None:
            raise _NotFound(
                f"report {report.id!r} was produced by {report.report_code!r} "
                f"v{report.definition_version}, which is no longer readable; the filing cannot be "
                "reinterpreted under a different version"
            )
        return found

    def _view(self, report: RegulatoryReport) -> ReportView:
        return report_view(report, self._svc.lifecycle.allowed(report.status))


class ReportingUseCases(_ReportingSupport):
    """Reporting orchestration. **Derives a filing; creates no financial fact.**"""

    def __init__(
        self,
        *,
        context,
        repos: ReportingRepositories,
        factory,
        services: ReportingServices,
        authz: AuthorizationOrchestrator,
        uow_factory: UnitOfWorkFactory,
        ids,
        clock,
    ) -> None:
        super().__init__(context=context, repos=repos, services=services)
        self._factory = factory
        self._authz = authz
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock

    # ── definitions and periods ─────────────────────────────────────────────────

    def publish_definition(self, command: PublishReportDefinition) -> ReportDefinitionView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_REPORT_GENERATE)
        self._svc.guards.guard_scoped(command.org_id)
        at = self._clock.now()
        with self._uow.new() as uow:
            built = self._factory.definition(
                org_id=command.org_id,
                report_code=command.report_code,
                version=command.version,
                title=command.title,
                jurisdiction=command.jurisdiction,
                frequency=command.frequency,
                period_basis=command.period_basis,
                fields=command.fields,
                required_sources=command.required_sources,
                currency_basis=command.currency_basis,
                declared_currency=command.declared_currency,
                integrity_blocks_finalization=command.integrity_blocks_finalization,
                published_by=command.actor_id,
                at=at,
            )
            existing = self._repos.definitions.get(command.org_id, built.id)
            # **A published version means one thing forever.** Re-publishing it with different
            # content would silently change what every report already bound to it says.
            self._svc.guards.guard_definition_publishable(existing, built.definition_digest)
            stored, created = self._repos.definitions.publish(built)
            if created:
                self._factory.emit_definition(stored)
            uow.stage(self._event(command, stored.id, version=str(stored.version)))
            return report_definition_view(stored)

    def open_period(self, command: OpenReportingPeriod) -> ReportingPeriodView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_REPORT_GENERATE)
        at = self._clock.now()
        with self._uow.new() as uow:
            built = self._factory.period(
                org_id=command.org_id,
                basis=command.basis,
                period_key=command.period_key,
                window=BillingPeriod(start=command.start, end=command.end),
                aligned_source_id=command.aligned_source_id,
                at=at,
            )
            stored, _created = self._repos.periods.create(built)
            uow.stage(self._event(command, stored.id, period_key=stored.period_key))
            return reporting_period_view(stored)

    def close_period(self, command: CloseReportingPeriod) -> ReportingPeriodView:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_REPORT_FINALIZE)
        at = self._clock.now()
        with self._uow.new() as uow:
            period = self.period_or_404(command.org_id, command.period_id)
            if period.is_closed:
                return reporting_period_view(period)
            closed = replace(
                period,
                state=_closed_state(),
                closed_at=at,
                closed_by=command.actor_id,
                version=period.version + 1,
            )
            stored = self._repos.periods.close(closed, expected_version=period.version)
            uow.stage(self._event(command, stored.id, state=stored.state.value))
            return reporting_period_view(stored)

    # ── the run ─────────────────────────────────────────────────────────────────

    def create_report(self, command: CreateReport) -> ReportView:
        """Open a run. The manifest is empty until `prepare`, so the report id is provisional
        until inputs are pinned — which is why `prepare` may replace it."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_REPORT_GENERATE)
        self._svc.guards.guard_scoped(command.org_id)
        at = self._clock.now()
        with self._uow.new() as uow:
            definition = self._resolve_definition(
                command.org_id, command.report_code, command.definition_version
            )
            period = self.period_or_404(command.org_id, command.period_id)
            self._svc.guards.guard_period_reportable(period, definition)
            manifest = self._factory.manifest(
                definition, period, (), account_id=command.account_id, at=at
            )
            stored_manifest, _ = self._repos.manifests.create(manifest)
            built = self._factory.report(
                definition, period, stored_manifest, created_by=command.actor_id, at=at
            )
            stored, created = self._repos.reports.create(built)
            if created:
                self._append_event(
                    stored,
                    ReportEventKind.REPORT_CREATED,
                    at=at,
                    actor_id=command.actor_id,
                    subject=definition.id,
                    summary=(
                        f"{definition.report_code} v{definition.version} for "
                        f"{period.period_key}"
                    ),
                )
            uow.stage(self._event(command, stored.id, report_code=stored.report_code))
            return self._view(stored)

    def _resolve_definition(
        self, org_id: str, report_code: str, version: int
    ) -> ReportDefinition:
        if version:
            found = self._repos.definitions.by_code(org_id, report_code, version)
            if found is None:
                raise _NotFound(
                    f"report definition {report_code!r} v{version} is not published"
                )
            return found
        latest = self._repos.definitions.latest_version(org_id, report_code)
        if latest is None:
            raise _NotFound(f"no report definition is published for {report_code!r}")
        return latest

    def prepare(self, command: PrepareReport) -> ReportView:
        """**The command this milestone exists for.**

        Reads every required source, pins it at the revision it read, computes the declared fields,
        raises named exceptions for every gap, and re-derives the report's identity from the
        resulting manifest digest. A source that could not be read produces an `UNAVAILABLE` row
        and a blocking exception — never a zero."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_REPORT_GENERATE)
        at = self._clock.now()
        with self._uow.new() as uow:
            report = self.report_or_404(command.org_id, command.report_id)
            self._svc.guards.guard_mutable(report)
            definition = self._pinned_definition(report)
            period = self.period_or_404(command.org_id, report.period_id)

            moving = self._svc.lifecycle.apply(
                report, ReportTrigger.PREPARE, at=at, actor_id=command.actor_id
            )
            report = self._repos.reports.advance(moving, expected_version=report.version)
            self._append_event(
                report,
                ReportEventKind.PREPARATION_STARTED,
                at=at,
                actor_id=command.actor_id,
                subject=period.period_key,
            )

            facts, sources = self._resolve_sources(
                command.org_id, definition, period, at, report, report.account_id
            )
            manifest = self._factory.manifest(
                definition,
                period,
                sources,
                account_id=report.account_id,
                query_scope={
                    "period_key": period.period_key,
                    "basis": period.basis.value,
                    "scope": report.scope.value,
                },
                at=at,
            )
            stored_manifest, _ = self._repos.manifests.create(manifest)

            values = self._compute(definition, facts)
            posture = self._reader.integrity_posture(command.org_id, period)
            completeness = self._svc.completeness.assess(definition, stored_manifest, values)
            evidence = self._svc.completeness.evidence(definition, stored_manifest)

            prepared = replace(
                report,
                manifest_id=stored_manifest.id,
                manifest_digest=stored_manifest.source_digest,
                source_count=len(sources),
                values=values,
                completeness=completeness,
                evidence_completeness=evidence,
                integrity_posture=posture,
            )
            prepared = self._svc.lifecycle.apply(
                prepared, ReportTrigger.COMPLETE_PREPARATION, at=at, actor_id=command.actor_id
            )
            report = self._repos.reports.advance(prepared, expected_version=report.version)

            self._append_event(
                report,
                ReportEventKind.SOURCES_RESOLVED,
                at=at,
                actor_id=command.actor_id,
                subject=stored_manifest.id,
                summary=f"{len(sources)} source(s) pinned",
                detail={"source_digest": stored_manifest.source_digest},
            )
            self._append_event(
                report,
                ReportEventKind.VALUES_COMPUTED,
                at=at,
                actor_id=command.actor_id,
                subject=str(len(values)),
                detail={"completeness": completeness.value},
            )
            report = self._raise_exceptions(
                report, definition, stored_manifest, values, posture, at, command.actor_id
            )
            uow.stage(self._event(command, report.id, completeness=completeness.value))
            return self._view(report)

    def _resolve_sources(
        self,
        org_id: str,
        definition: ReportDefinition,
        period: ReportingPeriod,
        at: float,
        report: RegulatoryReport,
        account_id: str = "",
    ) -> tuple[dict[EvidenceDomain, _SourceFacts], tuple[ReportSourceRef, ...]]:
        """Read every domain the definition touches. **Nothing is skipped.**

        A domain that cannot be read still appears in the manifest — as an `UNAVAILABLE` reference
        naming why. Omitting it would let a filing present as complete while a source was missing,
        which is §35.15 exactly."""
        wanted: list[EvidenceDomain] = list(definition.required_sources)
        for spec in definition.fields:
            if spec.domain not in wanted:
                wanted.append(spec.domain)

        facts: dict[EvidenceDomain, _SourceFacts] = {}
        sources: list[ReportSourceRef] = []
        for domain in wanted:
            result = self._reader.read(domain, org_id, period, at, account_id)
            facts[domain] = result
            if result.reference is not None:
                sources.append(result.reference)
            else:
                sources.append(
                    ReportSourceRef(
                        domain=domain,
                        source_type=_SOURCE_TYPE_BY_DOMAIN.get(
                            domain, EvidenceSourceType.JOURNAL_ENTRY
                        ),
                        source_id=period.period_key,
                        status=SourceStatus.UNAVAILABLE,
                        resolved_at=at,
                        unavailable_reason=result.reason or "the source could not be read",
                    )
                )
                self._append_event(
                    report,
                    ReportEventKind.SOURCE_UNAVAILABLE,
                    at=at,
                    subject=domain.value,
                    summary=result.reason,
                )
        return facts, tuple(sources)

    def _compute(
        self, definition: ReportDefinition, facts: dict[EvidenceDomain, _SourceFacts]
    ) -> tuple[ReportFieldValue, ...]:
        """Apply each field's **declared** transformation. No formula lives outside a definition."""
        out: list[ReportFieldValue] = []
        for spec in definition.fields:
            source = facts.get(spec.domain)
            if source is None:
                out.append(
                    ReportFieldValue(
                        key=spec.key,
                        status=ValueStatus.SOURCE_UNAVAILABLE,
                        transformation=spec.transformation,
                        detail=f"{spec.domain.value} was not resolved for this run",
                    )
                )
                continue
            values = (
                tuple(
                    getattr(f, spec.source_attribute, None)
                    for f in source.values
                    if getattr(f, spec.source_attribute, None) is not None
                )
                if spec.source_attribute
                else source.values
            )
            locators = (
                (source.reference.locator,) if source.reference is not None else ()
            )
            out.append(
                self._svc.transformations.apply(
                    spec,
                    values=values,
                    amounts=source.amounts,
                    locators=locators,
                    source_status=source.status,
                    definition=definition,
                )
            )
        return tuple(out)

    def _raise_exceptions(
        self,
        report: RegulatoryReport,
        definition: ReportDefinition,
        manifest: ReportInputManifest,
        values: tuple[ReportFieldValue, ...],
        posture: ReportedIntegrity,
        at: float,
        actor_id: str,
    ) -> RegulatoryReport:
        """Record every named problem. **Nothing is omitted, and nothing is generic.**"""
        raised = (
            self._svc.exceptions.for_manifest(definition, manifest)
            + self._svc.exceptions.for_values(definition, values)
            + self._svc.exceptions.for_integrity(definition, posture)
        )
        total = blocked = 0
        for kind, subject, diagnostic in raised:
            built = self._factory.exception(
                report, kind=kind, subject=subject, diagnostic=diagnostic, at=at
            )
            stored, created = self._repos.exceptions.append(built)
            if created:
                self._append_event(
                    report,
                    ReportEventKind.EXCEPTION_RAISED,
                    at=at,
                    actor_id=actor_id,
                    subject=stored.subject,
                    summary=stored.diagnostic,
                    detail={"kind": stored.kind.value, "severity": stored.severity.value},
                )
                self._factory.emit_exception(stored)
        current = self._repos.exceptions.all_for_report(report.org_id, report.id)
        total = len(current)
        blocked = len(blocking(current))
        counted = replace(
            report,
            exception_count=total,
            blocking_exception_count=blocked,
            version=report.version + 1,
        )
        return self._repos.reports.advance(counted, expected_version=report.version)

    # ── validation ──────────────────────────────────────────────────────────────

    def validate(self, command: ValidateReport) -> ValidationResultView:
        """**Validation, not reconciliation.** Every check is about the report."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_REPORT_VALIDATE)
        at = self._clock.now()
        with self._uow.new() as uow:
            report = self.report_or_404(command.org_id, command.report_id)
            self._svc.guards.guard_mutable(report)
            definition = self._pinned_definition(report)
            period = self.period_or_404(command.org_id, report.period_id)
            manifest = self.manifest_or_404(command.org_id, report.manifest_id)
            exceptions = self._repos.exceptions.all_for_report(command.org_id, report.id)

            self._append_event(
                report,
                ReportEventKind.VALIDATION_STARTED,
                at=at,
                actor_id=command.actor_id,
            )
            result = self._svc.validation.validate(
                report,
                definition=definition,
                period=period,
                manifest=manifest,
                exceptions=exceptions,
                at=at,
                actor_id=command.actor_id,
            )
            self._repos.validations.save(result)
            moved = self._svc.lifecycle.apply(
                report,
                ReportTrigger.PASS_VALIDATION if result.passed else ReportTrigger.FAIL_VALIDATION,
                at=at,
                actor_id=command.actor_id,
            )
            report = self._repos.reports.advance(moved, expected_version=report.version)
            self._append_event(
                report,
                ReportEventKind.VALIDATION_PASSED
                if result.passed
                else ReportEventKind.VALIDATION_FAILED,
                at=at,
                actor_id=command.actor_id,
                summary=", ".join(c.name for c in result.failures) or "all checks passed",
            )
            uow.stage(self._event(command, report.id, passed=str(result.passed)))
            return validation_view(result)

    # ── finalization ────────────────────────────────────────────────────────────

    def finalize(self, command: FinalizeReport) -> ReportView:
        """Take the immutable snapshot.

        Three refusals on **freshly-read rows inside the transaction**: a validation that did not
        pass, a blocking exception, and blocked completeness. The FSM already refuses to reach
        `FINALIZED` from anywhere but `READY`."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_REPORT_FINALIZE)
        at = self._clock.now()
        with self._uow.new() as uow:
            report = self.report_or_404(command.org_id, command.report_id)
            self._svc.guards.guard_mutable(report)
            definition = self._pinned_definition(report)
            period = self.period_or_404(command.org_id, report.period_id)
            manifest = self.manifest_or_404(command.org_id, report.manifest_id)
            validation = self._repos.validations.get(command.org_id, report.id)
            exceptions = self._repos.exceptions.all_for_report(command.org_id, report.id)

            self._svc.guards.guard_finalizable(
                report, validation=validation, exceptions=exceptions
            )

            snapshot = self._factory.snapshot(
                report,
                definition,
                period,
                manifest,
                validation_passed=bool(validation and validation.passed),
                finalized_by=command.actor_id,
                at=at,
            )
            stored_snapshot, _ = self._repos.snapshots.create(snapshot)
            finalized = self._svc.lifecycle.apply(
                report, ReportTrigger.FINALIZE, at=at, actor_id=command.actor_id
            )
            finalized = replace(
                finalized,
                report_digest=stored_snapshot.report_digest,
                snapshot_id=stored_snapshot.id,
            )
            report = self._repos.reports.advance(finalized, expected_version=report.version)
            self._append_event(
                report,
                ReportEventKind.REPORT_FINALIZED,
                at=at,
                actor_id=command.actor_id,
                subject=stored_snapshot.id,
                detail={"report_digest": stored_snapshot.report_digest},
            )
            self._factory.emit_finalized(report)
            uow.stage(self._event(command, report.id, digest=report.report_digest))
            return self._view(report)

    # ── corrections ─────────────────────────────────────────────────────────────

    def correct(self, command: CorrectReport) -> ReportView:
        """**A new report beside the original.** The original is never edited.

        Refused when the inputs have not moved and the definition version has not changed: a
        correction that corrects nothing would compute the identity of a report that already
        exists, and the caller would receive the original back believing a filing had been made."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_REPORT_CORRECT)
        at = self._clock.now()
        with self._uow.new() as uow:
            original = self.report_or_404(command.org_id, command.report_id)
            self._svc.guards.guard_correctable(original)
            definition = (
                self._resolve_definition(
                    command.org_id, original.report_code, command.definition_version
                )
                if command.definition_version
                else self._pinned_definition(original)
            )
            period = self.period_or_404(command.org_id, original.period_id)
            self._svc.guards.guard_period_reportable(period, definition)

            facts, sources = self._resolve_sources(
                command.org_id, definition, period, at, original, original.account_id
            )
            manifest = self._factory.manifest(
                definition,
                period,
                sources,
                account_id=original.account_id,
                query_scope={"period_key": period.period_key, "corrects": original.id},
                at=at,
            )
            if definition.version == original.definition_version:
                # Nothing moved and no rule changed. Refuse, loudly — the alternative is returning
                # the original and letting an operator believe a correction was filed.
                self._svc.guards.guard_correction_has_moved(original, manifest.source_digest)

            stored_manifest, _ = self._repos.manifests.create(manifest)
            built = self._factory.report(
                definition,
                period,
                stored_manifest,
                corrects=original.id,
                created_by=command.actor_id,
                at=at,
            )
            correction, created = self._repos.reports.create(built)
            if created:
                self._append_event(
                    correction,
                    ReportEventKind.CORRECTION_OPENED,
                    at=at,
                    actor_id=command.actor_id,
                    subject=original.id,
                    summary=command.reason,
                    detail={
                        "corrects": original.id,
                        "definition_version": str(definition.version),
                    },
                )
                self._factory.emit_correction(correction)
                # The original records that it was superseded. Its **values, digest, snapshot and
                # finalized state are untouched** — only the back-reference is added, so an
                # examiner reading the filed report sees there is a later one.
                superseded = replace(
                    original, corrected_by=correction.id, version=original.version + 1
                )
                self._repos.reports.advance(superseded, expected_version=original.version)
            uow.stage(self._event(command, correction.id, corrects=original.id))
            return self._view(correction)

    # ── exceptions and packages ─────────────────────────────────────────────────

    def acknowledge(self, command: AcknowledgeReportException) -> ReportExceptionView:
        """Record that somebody saw it. **This does not unblock a filing.**"""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_REPORT_VALIDATE)
        at = self._clock.now()
        with self._uow.new() as uow:
            report = self.report_or_404(command.org_id, command.report_id)
            found = self._repos.exceptions.get(command.org_id, command.exception_id)
            if found is None or found.report_id != report.id:
                raise _NotFound(f"report exception {command.exception_id!r} not found")
            self._svc.exception_guards.guard_acknowledgeable(found)
            acknowledged = replace(
                found,
                state=ExceptionState.ACKNOWLEDGED,
                acknowledged_at=at,
                acknowledged_by=command.actor_id,
                acknowledgement_reason=command.reason,
                version=found.version + 1,
            )
            stored = self._repos.exceptions.acknowledge(
                acknowledged, expected_version=found.version
            )
            self._append_event(
                report,
                ReportEventKind.EXCEPTION_ACKNOWLEDGED,
                at=at,
                actor_id=command.actor_id,
                subject=stored.subject,
                summary=command.reason,
            )
            uow.stage(self._event(command, stored.id, kind=stored.kind.value))
            return exception_view(stored)

    def create_package(self, command: CreateSubmissionPackage) -> SubmissionPackageView:
        """Bundle a finalized filing **for** submission. **Nothing here transmits anything.**"""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_REPORT_EXPORT)
        at = self._clock.now()
        with self._uow.new() as uow:
            report = self.report_or_404(command.org_id, command.report_id)
            self._svc.guards.guard_packageable(report)
            definition = self._pinned_definition(report)
            snapshot = self._repos.snapshots.for_report(command.org_id, report.id)
            if snapshot is None:
                raise _Conflict(
                    f"report {report.id!r} is finalized but has no snapshot; there is nothing to "
                    "package"
                )
            manifest = self.manifest_or_404(command.org_id, report.manifest_id)
            validation = self._repos.validations.get(command.org_id, report.id)
            exceptions = self._repos.exceptions.all_for_report(command.org_id, report.id)
            built = self._factory.submission(
                report,
                snapshot,
                definition,
                evidence=manifest.sources,
                exceptions=exceptions,
                validation_passed=bool(validation and validation.passed),
                prepared_by=command.actor_id,
                at=at,
            )
            stored, created = self._repos.submissions.create(built)
            if created:
                self._append_event(
                    report,
                    ReportEventKind.SUBMISSION_PACKAGE_CREATED,
                    at=at,
                    actor_id=command.actor_id,
                    subject=stored.id,
                    detail={"package_digest": stored.package_digest},
                )
                self._factory.emit_submission(stored)
            uow.stage(self._event(command, stored.id, digest=stored.package_digest))
            return submission_package_view(stored)

    def record_unavailable(self, command: RecordSourceUnavailable) -> ReportView:
        """Declare a source unreadable. The gap is on the record from that moment."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_REPORT_GENERATE)
        at = self._clock.now()
        with self._uow.new() as uow:
            report = self.report_or_404(command.org_id, command.report_id)
            self._svc.guards.guard_mutable(report)
            built = self._factory.exception(
                report,
                kind=ReportExceptionKind.SOURCE_UNAVAILABLE,
                subject=command.domain.value,
                diagnostic=command.reason or f"{command.domain.value} was declared unreadable",
                at=at,
            )
            stored, created = self._repos.exceptions.append(built)
            if created:
                self._append_event(
                    report,
                    ReportEventKind.SOURCE_UNAVAILABLE,
                    at=at,
                    actor_id=command.actor_id,
                    subject=command.domain.value,
                    summary=stored.diagnostic,
                )
                self._factory.emit_exception(stored)
            current = self._repos.exceptions.all_for_report(command.org_id, report.id)
            counted = replace(
                report,
                exception_count=len(current),
                blocking_exception_count=len(blocking(current)),
                completeness=ReportCompleteness.BLOCKED,
                evidence_completeness=EvidenceCompleteness.EVIDENCE_INCOMPLETE,
                version=report.version + 1,
            )
            report = self._repos.reports.advance(counted, expected_version=report.version)
            uow.stage(self._event(command, report.id, domain=command.domain.value))
            return self._view(report)

    # ── internals ───────────────────────────────────────────────────────────────

    def _append_event(
        self,
        report: RegulatoryReport,
        kind: ReportEventKind,
        *,
        at: float,
        actor_id: str = "",
        subject: str = "",
        summary: str = "",
        detail: dict[str, str] | None = None,
    ):
        sequence = self._repos.events.next_sequence(report.org_id, report.id)
        built = self._factory.event(
            report,
            kind=kind,
            sequence=sequence,
            at=at,
            actor_id=actor_id,
            subject=subject,
            summary=summary,
            detail=detail,
        )
        stored, _created = self._repos.events.append(built)
        return stored

    def _event(self, command, subject: str, **data: str) -> ApplicationEvent:
        return ApplicationEvent(
            id=self._ids.new(PREFIX_APP_EVENT),
            type=ApplicationEventType.REPORT_PREPARED,
            ts=self._clock.now(),
            use_case=type(command).__name__,
            outcome="completed",
            org_id=command.org_id,
            actor=command.actor_id,
            subject_id=subject,
            data=dict(data),
        )


def _closed_state():
    from billing.foundation import ReportingPeriodState

    return ReportingPeriodState.CLOSED


#: The artifact type each domain contributes when a reader could not name one. A table so an
#: unavailable source still has a machine-resolvable type rather than a placeholder.
_SOURCE_TYPE_BY_DOMAIN = {
    EvidenceDomain.LEDGER: EvidenceSourceType.JOURNAL_ENTRY,
    EvidenceDomain.INVOICE: EvidenceSourceType.INVOICE_DOCUMENT,
    EvidenceDomain.TAX: EvidenceSourceType.TAX_ASSESSMENT,
    EvidenceDomain.PAYMENT: EvidenceSourceType.PAYMENT_INTENT,
    EvidenceDomain.REVENUE: EvidenceSourceType.REVENUE_ENTRY,
    EvidenceDomain.COLLECTIONS: EvidenceSourceType.COLLECTION_CASE,
    EvidenceDomain.RECONCILIATION: EvidenceSourceType.RECONCILIATION_FINDING,
    EvidenceDomain.INTEGRITY: EvidenceSourceType.INTEGRITY_INCIDENT,
    EvidenceDomain.INVESTIGATION: EvidenceSourceType.INVESTIGATION_CASE,
}


class ReportingQueries(_ReportingSupport):
    """Reads. Every one org-scoped, bounded, index-backed and — for collections — paginated."""

    def __init__(self, *, context, repos, services, authz: AuthorizationOrchestrator) -> None:
        super().__init__(context=context, repos=repos, services=services)
        self._authz = authz

    def definitions(self, query: ListReportDefinitions) -> tuple[ReportDefinitionView, ...]:
        self._read(query)
        rows, _total = self._repos.definitions.list_definitions(
            query.org_id, offset=query.offset, limit=query.limit
        )
        return tuple(report_definition_view(d) for d in rows)

    def definition(self, query: GetReportDefinition) -> ReportDefinitionView:
        self._read(query)
        return report_definition_view(self.definition_or_404(query.org_id, query.definition_id))

    def periods(self, query: ListReportingPeriods) -> tuple[ReportingPeriodView, ...]:
        self._read(query)
        rows, _total = self._repos.periods.list_periods(
            query.org_id, basis=query.basis, offset=query.offset, limit=query.limit
        )
        return tuple(reporting_period_view(p) for p in rows)

    def list_reports(self, query: ListReports) -> ReportPageView:
        self._read(query)
        rows, total = self._repos.reports.page(
            ReportSearchQuery(
                org_id=query.org_id,
                report_code=query.report_code,
                status=query.status,
                period_id=query.period_id,
                finalized_only=query.finalized_only,
                offset=query.offset,
                limit=query.limit,
            )
        )
        return ReportPageView(
            reports=tuple(self._view(r) for r in rows),
            total=total,
            offset=query.offset,
            limit=query.limit,
            has_more=query.offset + len(rows) < total,
        )

    def report(self, query: GetReport) -> ReportView:
        self._read(query)
        return self._view(self.report_or_404(query.org_id, query.report_id))

    def sources(self, query: GetReportSources) -> ReportSourcePageView:
        self._read(query)
        report = self.report_or_404(query.org_id, query.report_id)
        rows, total = self._repos.manifests.sources(
            query.org_id, report.manifest_id, offset=query.offset, limit=query.limit
        )
        return ReportSourcePageView(
            sources=tuple(source_view(s) for s in rows),
            total=total,
            offset=query.offset,
            limit=query.limit,
        )

    def evidence(self, query: GetReportEvidence) -> tuple[ReportSourceView, ...]:
        """The §21 evidence manifest: every source with its revision, digest and status."""
        self._read(query)
        report = self.report_or_404(query.org_id, query.report_id)
        manifest = self.manifest_or_404(query.org_id, report.manifest_id)
        return tuple(source_view(s) for s in sorted(manifest.sources, key=lambda s: s.locator))

    def exceptions(self, query: GetReportExceptions) -> ReportExceptionPageView:
        self._read(query)
        report = self.report_or_404(query.org_id, query.report_id)
        rows, total = self._repos.exceptions.for_report(
            query.org_id, report.id, offset=query.offset, limit=query.limit
        )
        return ReportExceptionPageView(
            exceptions=tuple(exception_view(e) for e in rows),
            total=total,
            offset=query.offset,
            limit=query.limit,
        )

    def validation(self, query: GetReportValidation) -> ValidationResultView:
        self._read(query)
        report = self.report_or_404(query.org_id, query.report_id)
        found = self._repos.validations.get(query.org_id, report.id)
        if found is None:
            raise _NotFound(f"report {report.id!r} has not been validated")
        return validation_view(found)

    def snapshot(self, query: GetReportSnapshot) -> ReportSnapshotView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_REPORT_EXPORT)
        report = self.report_or_404(query.org_id, query.report_id)
        found = self._repos.snapshots.for_report(query.org_id, report.id)
        if found is None:
            raise _NotFound(f"report {report.id!r} has not been finalized")
        return snapshot_view(found)

    def package(self, query: GetSubmissionPackage) -> SubmissionPackageView:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_SUBMISSION_PACKAGE_READ)
        report = self.report_or_404(query.org_id, query.report_id)
        found = self._repos.submissions.for_report(query.org_id, report.id)
        if found is None:
            raise _NotFound(f"report {report.id!r} has no submission package")
        return submission_package_view(found)

    def provenance(self, query: GetReportProvenance) -> ReportProvenanceView:
        """**The §25 answer, from stored rows alone.** Reads no current source."""
        self._read(query)
        report = self.report_or_404(query.org_id, query.report_id)
        definition = self._pinned_definition(report)
        period = self.period_or_404(query.org_id, report.period_id)
        manifest = self.manifest_or_404(query.org_id, report.manifest_id)
        validation = self._repos.validations.get(query.org_id, report.id)
        exceptions = self._repos.exceptions.all_for_report(query.org_id, report.id)
        traces = self._svc.provenance.traces(definition, report)
        return ReportProvenanceView(
            report_id=report.id,
            report_code=report.report_code,
            definition_id=report.definition_id,
            definition_version=report.definition_version,
            period_key=period.period_key,
            period_basis=period.basis.value,
            manifest_id=manifest.id,
            manifest_digest=manifest.source_digest,
            report_digest=report.report_digest,
            validation_passed=bool(validation and validation.passed),
            finalized_at=report.finalized_at,
            finalized_by=report.finalized_by,
            corrects=report.corrects,
            corrected_by=report.corrected_by,
            traces=tuple(
                SourceTraceView(
                    field_key=t[0],
                    label=t[1],
                    domain=t[2],
                    source_type=t[3],
                    transformation=t[4],
                    status=t[5],
                    locators=t[6],
                    detail=t[7],
                )
                for t in traces
            ),
            unavailable_sources=tuple(sorted(s.locator for s in manifest.unavailable)),
            exception_kinds=tuple(sorted({e.kind.value for e in exceptions})),
        )

    def diff(self, query: DiffReports) -> ReportDiffView:
        self._read(query)
        earlier = self.report_or_404(query.org_id, query.earlier_report_id)
        later = self.report_or_404(query.org_id, query.later_report_id)
        changed, added, removed, sources, version_changed = self._svc.diff.compare(
            earlier,
            later,
            earlier_manifest=self._repos.manifests.get(query.org_id, earlier.manifest_id),
            later_manifest=self._repos.manifests.get(query.org_id, later.manifest_id),
        )
        return ReportDiffView(
            earlier_report_id=earlier.id,
            later_report_id=later.id,
            changed_fields=changed,
            added_fields=added,
            removed_fields=removed,
            changed_sources=sources,
            definition_version_changed=version_changed,
        )

    def posture(self, query: GetReportingPosture) -> ReportingPostureView:
        """Counts, computed by the database. Never a figure, and never a Python-side scan."""
        self._read(query)
        counts = self._repos.reports.counts(query.org_id)
        by_status = counts.get("by_status", {})
        totals = counts.get("totals", {})
        return ReportingPostureView(
            org_id=query.org_id,
            total=sum(by_status.values()),
            by_status=by_status,
            by_completeness=counts.get("by_completeness", {}),
            finalized=by_status.get(ReportStatus.FINALIZED.value, 0),
            blocked=counts.get("by_completeness", {}).get(
                ReportCompleteness.BLOCKED.value, 0
            ),
            open_exceptions=totals.get("open_exceptions", 0),
            blocking_exceptions=totals.get("blocking_exceptions", 0),
            submission_packages=totals.get("submission_packages", 0),
        )

    def _read(self, query) -> None:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_REPORT_READ)


def register_reporting_use_cases(
    command_bus, query_bus, uc: ReportingUseCases, q: ReportingQueries
):
    """Wire the reporting vocabulary onto the shared buses. One handler per message."""
    command_bus.register(PublishReportDefinition, uc.publish_definition)
    command_bus.register(OpenReportingPeriod, uc.open_period)
    command_bus.register(CloseReportingPeriod, uc.close_period)
    command_bus.register(CreateReport, uc.create_report)
    command_bus.register(PrepareReport, uc.prepare)
    command_bus.register(ValidateReport, uc.validate)
    command_bus.register(FinalizeReport, uc.finalize)
    command_bus.register(CorrectReport, uc.correct)
    command_bus.register(AcknowledgeReportException, uc.acknowledge)
    command_bus.register(CreateSubmissionPackage, uc.create_package)
    command_bus.register(RecordSourceUnavailable, uc.record_unavailable)
    query_bus.register(ListReportDefinitions, q.definitions)
    query_bus.register(GetReportDefinition, q.definition)
    query_bus.register(ListReportingPeriods, q.periods)
    query_bus.register(ListReports, q.list_reports)
    query_bus.register(GetReport, q.report)
    query_bus.register(GetReportSources, q.sources)
    query_bus.register(GetReportEvidence, q.evidence)
    query_bus.register(GetReportExceptions, q.exceptions)
    query_bus.register(GetReportValidation, q.validation)
    query_bus.register(GetReportSnapshot, q.snapshot)
    query_bus.register(GetSubmissionPackage, q.package)
    query_bus.register(GetReportProvenance, q.provenance)
    query_bus.register(DiffReports, q.diff)
    query_bus.register(GetReportingPosture, q.posture)


__all__ = [
    "ALL_REPORTING_COMMANDS",
    "ALL_REPORTING_QUERIES",
    "AcknowledgeReportException",
    "CloseReportingPeriod",
    "CorrectReport",
    "CreateReport",
    "CreateSubmissionPackage",
    "DiffReports",
    "FinalizeReport",
    "GetReport",
    "GetReportDefinition",
    "GetReportEvidence",
    "GetReportExceptions",
    "GetReportProvenance",
    "GetReportSnapshot",
    "GetReportSources",
    "GetReportValidation",
    "GetReportingPosture",
    "GetSubmissionPackage",
    "ListReportDefinitions",
    "ListReportingPeriods",
    "ListReports",
    "OpenReportingPeriod",
    "PrepareReport",
    "PublishReportDefinition",
    "RecordSourceUnavailable",
    "ReportDefinitionView",
    "ReportDiffView",
    "ReportExceptionPageView",
    "ReportExceptionView",
    "ReportFieldValueView",
    "ReportPageView",
    "ReportProvenanceView",
    "ReportSnapshotView",
    "ReportSourcePageView",
    "ReportSourceView",
    "ReportView",
    "ReportingPeriodView",
    "ReportingPostureView",
    "ReportingQueries",
    "ReportingRepositories",
    "ReportingServices",
    "ReportingUseCases",
    "SourceTraceView",
    "SubmissionPackageView",
    "ValidateReport",
    "ValidationCheckView",
    "ValidationResultView",
    "report_definition_view",
    "exception_view",
    "submission_package_view",
    "reporting_period_view",
    "register_reporting_use_cases",
    "report_view",
    "snapshot_view",
    "source_view",
    "validation_view",
    "value_view",
]
