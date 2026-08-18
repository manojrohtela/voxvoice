"""Reporting factories + repository contracts (M9.22).

`ReportingFactory` is the one place a report definition, a reporting period, an input manifest, a
report run, an exception, a snapshot, a submission package or a trail entry is constructed. Same
discipline as every milestone since M9.2: validate structurally, fail fast on error-severity
findings (`BillingValidationError`, §A2.6), compose an immutable aggregate from the injected id and
clock seams, emit the corresponding `billing.*` fact through the injected emitter. No I/O, no
persistence, no transport, no provider, no scheduler.

## Exactly one event per business fact, and what is deliberately absent

Five `billing.*` facts are published: a definition published, a report finalized, a correction
opened, a submission package created, and a *blocking* exception raised. The other eleven trail
kinds are not on the bus, for the reason M9.20 and M9.21 both gave — the `billing.*` stream is the
platform's business-fact feed, not a mirror of an audit trail. An external consumer needs to know
that a filing was made, corrected, packaged, or stopped by a blocking problem. It does not need one
message per field computation. The full sequence lives in `cp_billing_report_event`.

There is no event for a rejected command, none for a reconciliation, none for a correlation and
none for an investigation — republishing another milestone's fact is how one occurrence comes to be
counted twice.

## Why every identity is derived

* a **definition** from `(org, code, version)` — so publishing a version twice lands on the same
  row and a rule cannot be edited under a report that already used it;
* a **period** from `(org, basis, key)` — with the basis inside, because a calendar month is not an
  accounting period and the two must not collide;
* a **manifest** from `(org, definition, period, source digest)`;
* a **report** from `(org, definition, period, manifest digest, corrects)` — **the pinning
  guarantee as an identity**: same inputs, same report; any input moved, a different report beside
  the old one;
* a **snapshot** from `(report, report digest)`, a **submission package** from
  `(org, report, report digest)` — so two workers packaging one filing produce one package;
* an **exception** from `(report, kind, subject)`, so the same problem observed twice is recorded
  once and an exception list does not double-count how bad a filing is;
* a **trail entry** from `(report, kind, subject, sequence)`.

## What no repository here can do

There is **no update path on the manifest store, the snapshot store, the submission store or the
trail**, and no delete on anything. A filing is evidence, and evidence is not edited: a correction
opens a new report, and both remain readable.

`ReportRepository.advance` takes the version the caller read and is a compare-and-set, so a worker
deciding from a stale view fails loudly rather than overwriting a filing somebody else approved.

And there is **no repository here for a ledger entry, a journal entry, an invoice, a tax
assessment, a payment, a recognition schedule, a reconciliation finding, an integrity incident or
an investigation.** M9.22 reads those through the owning domain's own seams, supplied by the
Application, and writes to none of them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .commercial_investigations import canonical_digest
from .commercial_reporting import (
    ORG_SCOPE,
    CurrencyBasis,
    ExceptionSeverity,
    RegulatoryReport,
    ReportDefinition,
    ReportEvent,
    ReportEventKind,
    ReportException,
    ReportExceptionKind,
    ReportFieldSpec,
    ReportFrequency,
    ReportingPeriod,
    ReportInputManifest,
    ReportSearchQuery,
    ReportSnapshot,
    ReportSourceRef,
    SubmissionPackage,
    ValidationResult,
    definition_digest,
    derive_definition_id,
    derive_exception_id,
    derive_manifest_id,
    derive_period_id,
    derive_report_event_id,
    derive_report_id,
    derive_report_snapshot_id,
    derive_submission_id,
    report_representation,
    source_manifest_digest,
)
from .diagnostics import BillingDiagnostics, Severity
from .domain import BillingPeriod
from .errors import BillingValidationError
from .events import BillingEvent, BillingEventType, EventEmitter, NullEventEmitter
from .money import Currency
from .reporting_rules import ReportExceptionService, ReportingValidator
from .seams import Clock, IdGenerator, SequentialIdGenerator, SystemClock

#: Which `billing.*` fact each trail kind publishes. A table, so "exactly one event per business
#: fact" is checkable by iteration rather than asserted in prose. Most kinds are absent on purpose —
#: see the module docstring.
REPORT_EVENTS: dict[ReportEventKind, BillingEventType] = {
    ReportEventKind.DEFINITION_PUBLISHED: BillingEventType.REPORT_DEFINITION_PUBLISHED,
    ReportEventKind.EXCEPTION_RAISED: BillingEventType.REPORT_EXCEPTION_RAISED,
    ReportEventKind.REPORT_FINALIZED: BillingEventType.REPORT_FINALIZED,
    ReportEventKind.CORRECTION_OPENED: BillingEventType.REPORT_CORRECTION_OPENED,
    ReportEventKind.SUBMISSION_PACKAGE_CREATED: BillingEventType.SUBMISSION_PACKAGE_CREATED,
}

#: The version stamped into every manifest this build produces. Part of the manifest so an examiner
#: can tell which generator computed a filing — a report reproduced by different code is a
#: different fact even when the inputs match.
GENERATOR_VERSION = "m9.22.1"


@dataclass(frozen=True, slots=True)
class ReportingFactory:
    """Constructs every reporting aggregate. Seams injected; nothing ambient."""

    ids: IdGenerator = SequentialIdGenerator()
    clock: Clock = SystemClock()
    emitter: EventEmitter = NullEventEmitter()
    validator: ReportingValidator = ReportingValidator()
    exceptions: ReportExceptionService = ReportExceptionService()

    # ── definitions ─────────────────────────────────────────────────────────────

    def definition(
        self,
        *,
        org_id: str,
        report_code: str,
        version: int,
        title: str,
        jurisdiction: str,
        frequency: ReportFrequency,
        period_basis,
        fields: tuple[ReportFieldSpec, ...],
        required_sources: tuple = (),
        currency_basis: CurrencyBasis = CurrencyBasis.NOT_APPLICABLE,
        declared_currency: Currency | None = None,
        integrity_blocks_finalization: bool = False,
        published_by: str = "",
        at: float = 0.0,
    ) -> ReportDefinition:
        """Publish a reporting rule **at a version**. Immutable thereafter.

        `definition_digest` is computed here from the declared content, so re-publishing the same
        version can be *proven* identical rather than assumed to be — which is what lets a report
        filed months ago still mean what it meant."""
        built = ReportDefinition(
            id=derive_definition_id(org_id, report_code, version),
            org_id=org_id,
            report_code=report_code,
            version=version,
            title=title,
            jurisdiction=jurisdiction,
            frequency=frequency,
            period_basis=period_basis,
            currency_basis=currency_basis,
            declared_currency=declared_currency,
            fields=fields,
            required_sources=tuple(required_sources),
            integrity_blocks_finalization=integrity_blocks_finalization,
            published_at=at or self.clock.now(),
            published_by=published_by,
        )
        built = ReportDefinition(
            **{**_as_dict(built), "definition_digest": definition_digest(built)}
        )
        self._fail_fast(self.validator.validate_definition(built))
        return built

    def emit_definition(self, definition: ReportDefinition) -> None:
        self._emit(
            BillingEventType.REPORT_DEFINITION_PUBLISHED,
            definition.org_id,
            definition.id,
            report_code=definition.report_code,
            version=str(definition.version),
            jurisdiction=definition.jurisdiction,
        )

    # ── periods ─────────────────────────────────────────────────────────────────

    def period(
        self,
        *,
        org_id: str,
        basis,
        period_key: str,
        window: BillingPeriod,
        aligned_source_id: str = "",
        at: float = 0.0,
    ) -> ReportingPeriod:
        built = ReportingPeriod(
            id=derive_period_id(org_id, basis.value, period_key),
            org_id=org_id,
            basis=basis,
            period_key=period_key,
            window=window,
            aligned_source_id=aligned_source_id,
            opened_at=at or self.clock.now(),
        )
        self._fail_fast(self.validator.validate_period(built))
        return built

    # ── the input manifest ──────────────────────────────────────────────────────

    def manifest(
        self,
        definition: ReportDefinition,
        period: ReportingPeriod,
        sources: tuple[ReportSourceRef, ...],
        *,
        account_id: str = "",
        query_scope: dict[str, str] | None = None,
        at: float = 0.0,
    ) -> ReportInputManifest:
        """**Immutable, and the report's identity depends on it.**

        `source_digest` is a canonical digest over every pinned reference, so any source moving to a
        new revision produces a different manifest — and therefore a different report — rather than
        mutating a filing that already exists."""
        for source in sources:
            self._fail_fast(self.validator.validate_source(source))
        digest = source_manifest_digest(sources)
        scope_key = account_id or ORG_SCOPE
        return ReportInputManifest(
            id=derive_manifest_id(
                definition.org_id, definition.id, period.id, scope_key, digest
            ),
            org_id=definition.org_id,
            definition_id=definition.id,
            definition_version=definition.version,
            period_id=period.id,
            account_id=account_id,
            sources=sources,
            source_digest=digest,
            query_scope=dict(query_scope or {}),
            generated_at=at or self.clock.now(),
            generator_version=GENERATOR_VERSION,
        )

    # ── the report ──────────────────────────────────────────────────────────────

    def report(
        self,
        definition: ReportDefinition,
        period: ReportingPeriod,
        manifest: ReportInputManifest,
        *,
        corrects: str = "",
        created_by: str = "",
        at: float = 0.0,
    ) -> RegulatoryReport:
        """Open a run against **pinned** inputs.

        `definition_version` is copied here and never recomputed: a later version of the reporting
        rule must not reinterpret this filing, and a report that reached for `latest()` would do
        exactly that."""
        built = RegulatoryReport(
            id=derive_report_id(
                definition.org_id,
                definition.id,
                period.id,
                manifest.account_id or ORG_SCOPE,
                manifest.source_digest,
                corrects,
            ),
            org_id=definition.org_id,
            definition_id=definition.id,
            definition_version=definition.version,
            report_code=definition.report_code,
            period_id=period.id,
            manifest_id=manifest.id,
            manifest_digest=manifest.source_digest,
            account_id=manifest.account_id,
            source_count=len(manifest.sources),
            corrects=corrects,
            created_at=at or self.clock.now(),
            created_by=created_by,
        )
        self._fail_fast(self.validator.validate_report(built))
        return built

    def emit_finalized(self, report: RegulatoryReport) -> None:
        self._emit(
            BillingEventType.REPORT_FINALIZED,
            report.org_id,
            report.id,
            report_code=report.report_code,
            definition_version=str(report.definition_version),
            completeness=report.completeness.value,
            report_digest=report.report_digest,
        )

    def emit_correction(self, correction: RegulatoryReport) -> None:
        self._emit(
            BillingEventType.REPORT_CORRECTION_OPENED,
            correction.org_id,
            correction.id,
            report_code=correction.report_code,
            corrects=correction.corrects,
        )

    # ── exceptions ──────────────────────────────────────────────────────────────

    def exception(
        self,
        report: RegulatoryReport,
        *,
        kind: ReportExceptionKind,
        subject: str,
        diagnostic: str,
        field_key: str = "",
        source_locator: str = "",
        at: float = 0.0,
    ) -> ReportException:
        """A **named** problem. Severity comes from the declared table, never from the caller."""
        return ReportException(
            id=derive_exception_id(report.id, kind.value, subject),
            org_id=report.org_id,
            report_id=report.id,
            kind=kind,
            severity=self.exceptions.severity(kind),
            subject=subject,
            diagnostic=diagnostic,
            field_key=field_key,
            source_locator=source_locator,
            created_at=at or self.clock.now(),
        )

    def emit_exception(self, exception: ReportException) -> None:
        """Only a **blocking** exception reaches the bus.

        An advisory one is recorded and filed alongside; putting every one on the platform's
        business-fact feed would train consumers to ignore the feed."""
        if exception.severity is not ExceptionSeverity.BLOCKING:
            return
        self._emit(
            BillingEventType.REPORT_EXCEPTION_RAISED,
            exception.org_id,
            exception.report_id,
            kind=exception.kind.value,
            on=exception.subject,
        )

    # ── the snapshot ────────────────────────────────────────────────────────────

    def snapshot(
        self,
        report: RegulatoryReport,
        definition: ReportDefinition,
        period: ReportingPeriod,
        manifest: ReportInputManifest,
        *,
        validation_passed: bool,
        finalized_by: str = "",
        at: float = 0.0,
    ) -> ReportSnapshot:
        """**Content-addressed, and taken from pinned state only.**

        `report_representation` reaches for nothing current — the definition version this run used,
        the period, the manifest digest and the values as computed. A source moving next week
        changes nothing about what this says, which is the whole point of a filing."""
        representation = report_representation(report, definition, period, manifest)
        digest = canonical_digest(representation)
        return ReportSnapshot(
            id=derive_report_snapshot_id(report.id, digest),
            org_id=report.org_id,
            report_id=report.id,
            definition_id=definition.id,
            definition_version=definition.version,
            manifest_id=manifest.id,
            manifest_digest=manifest.source_digest,
            report_digest=digest,
            representation=representation,
            completeness=report.completeness,
            evidence_completeness=report.evidence_completeness,
            validation_passed=validation_passed,
            finalized_at=at or self.clock.now(),
            finalized_by=finalized_by,
        )

    # ── the submission package ──────────────────────────────────────────────────

    def submission(
        self,
        report: RegulatoryReport,
        snapshot: ReportSnapshot,
        definition: ReportDefinition,
        *,
        evidence: tuple[ReportSourceRef, ...],
        exceptions: tuple[ReportException, ...],
        validation_passed: bool,
        prepared_by: str = "",
        at: float = 0.0,
    ) -> SubmissionPackage:
        """A bundle prepared **for** submission. **Nothing here submits anything.**

        `submitted_at` and `submission_reference` are declared and left empty, always: §24 defers
        transmission to a future adapter, and there is no network client, regulator endpoint or
        credential anywhere in this milestone."""
        artifact = {
            "report": snapshot.representation,
            "report_digest": snapshot.report_digest,
            "definition": {
                "id": definition.id,
                "report_code": definition.report_code,
                "version": definition.version,
                "digest": definition.definition_digest,
                "jurisdiction": definition.jurisdiction,
            },
            "evidence": [
                {
                    "locator": s.locator,
                    "status": s.status.value,
                    "revision": s.revision,
                    "digest": s.digest,
                    "fact_count": s.fact_count,
                    "unavailable_reason": s.unavailable_reason,
                }
                for s in sorted(evidence, key=lambda s: s.locator)
            ],
            "exceptions": [
                {
                    "kind": e.kind.value,
                    "severity": e.severity.value,
                    "subject": e.subject,
                    "state": e.state.value,
                    "diagnostic": e.diagnostic,
                }
                for e in sorted(exceptions, key=lambda e: e.id)
            ],
            "validation_passed": validation_passed,
            "completeness": report.completeness.value,
            "evidence_completeness": report.evidence_completeness.value,
        }
        return SubmissionPackage(
            id=derive_submission_id(report.org_id, report.id, snapshot.report_digest),
            org_id=report.org_id,
            report_id=report.id,
            snapshot_id=snapshot.id,
            definition_id=definition.id,
            definition_version=definition.version,
            report_digest=snapshot.report_digest,
            manifest_digest=snapshot.manifest_digest,
            package_digest=canonical_digest(artifact),
            completeness=report.completeness,
            evidence_completeness=report.evidence_completeness,
            validation_passed=validation_passed,
            artifact=artifact,
            prepared_at=at or self.clock.now(),
            prepared_by=prepared_by,
        )

    def emit_submission(self, package: SubmissionPackage) -> None:
        self._emit(
            BillingEventType.SUBMISSION_PACKAGE_CREATED,
            package.org_id,
            package.report_id,
            package_id=package.id,
            report_digest=package.report_digest,
        )

    # ── the trail ───────────────────────────────────────────────────────────────

    def event(
        self,
        report: RegulatoryReport,
        *,
        kind: ReportEventKind,
        sequence: int,
        at: float = 0.0,
        actor_id: str = "",
        subject: str = "",
        summary: str = "",
        detail: dict[str, str] | None = None,
    ) -> ReportEvent:
        return ReportEvent(
            id=derive_report_event_id(report.id, kind.value, subject, sequence),
            org_id=report.org_id,
            report_id=report.id,
            sequence=sequence,
            kind=kind,
            at=at or self.clock.now(),
            actor_id=actor_id,
            subject=subject,
            summary=summary,
            detail=dict(detail or {}),
        )

    # ── internals ───────────────────────────────────────────────────────────────

    def _fail_fast(self, diagnostics: tuple) -> None:
        errors = [d for d in diagnostics if d.severity is Severity.ERROR]
        if errors:
            raise BillingValidationError(BillingDiagnostics(tuple(errors)))

    def _emit(
        self, event_type: BillingEventType, org_id: str, subject: str, **data: str
    ) -> None:
        self.emitter.emit(
            BillingEvent(
                id=self.ids.new("bev"),
                type=event_type,
                ts=self.clock.now(),
                org_id=org_id,
                subject_id=subject,
                data={k: v for k, v in data.items() if v not in ("", None)},
            )
        )


def _as_dict(definition: ReportDefinition) -> dict:
    return {
        name: getattr(definition, name) for name in type(definition).__dataclass_fields__
    }


# ── repository contracts ────────────────────────────────────────────────────────


class ReportDefinitionRepository(ABC):
    """**Append-only.** A published version is never edited; a changed rule gets a new version."""

    @abstractmethod
    def publish(self, definition: ReportDefinition) -> tuple[ReportDefinition, bool]: ...

    @abstractmethod
    def get(self, org_id: str, definition_id: str) -> ReportDefinition | None: ...

    @abstractmethod
    def by_code(
        self, org_id: str, report_code: str, version: int
    ) -> ReportDefinition | None: ...

    @abstractmethod
    def latest_version(self, org_id: str, report_code: str) -> ReportDefinition | None:
        """The newest published version of a code.

        Used **only** when opening a new run and only when the caller did not pin one. A report
        that already exists never consults this — its `definition_version` is stored."""

    @abstractmethod
    def list_definitions(
        self, org_id: str, *, offset: int = 0, limit: int = 50
    ) -> tuple[tuple[ReportDefinition, ...], int]: ...


class ReportingPeriodRepository(ABC):
    @abstractmethod
    def create(self, period: ReportingPeriod) -> tuple[ReportingPeriod, bool]: ...

    @abstractmethod
    def close(self, period: ReportingPeriod, *, expected_version: int) -> ReportingPeriod:
        """Compare-and-set. Two workers closing one period converge on one closure."""

    @abstractmethod
    def get(self, org_id: str, period_id: str) -> ReportingPeriod | None: ...

    @abstractmethod
    def by_key(self, org_id: str, basis: str, period_key: str) -> ReportingPeriod | None: ...

    @abstractmethod
    def list_periods(
        self, org_id: str, *, basis: str = "", offset: int = 0, limit: int = 50
    ) -> tuple[tuple[ReportingPeriod, ...], int]: ...


class ReportManifestRepository(ABC):
    """**Append-only. No update, no delete.** The pinned inputs are the report's identity."""

    @abstractmethod
    def create(self, manifest: ReportInputManifest) -> tuple[ReportInputManifest, bool]: ...

    @abstractmethod
    def get(self, org_id: str, manifest_id: str) -> ReportInputManifest | None: ...

    @abstractmethod
    def sources(
        self, org_id: str, manifest_id: str, *, offset: int = 0, limit: int = 200
    ) -> tuple[tuple[ReportSourceRef, ...], int]:
        """Paginated at the database — a manifest over a busy period has many sources."""


class ReportRepository(ABC):
    """The one mutable aggregate, and only through a compare-and-set."""

    @abstractmethod
    def create(self, report: RegulatoryReport) -> tuple[RegulatoryReport, bool]: ...

    @abstractmethod
    def advance(self, report: RegulatoryReport, *, expected_version: int) -> RegulatoryReport:
        """Compare-and-set on `version`. Raises `ReportStale` when the row moved."""

    @abstractmethod
    def get(self, org_id: str, report_id: str) -> RegulatoryReport | None: ...

    @abstractmethod
    def page(self, query: ReportSearchQuery) -> tuple[tuple[RegulatoryReport, ...], int]:
        """Database-side pagination: a `COUNT` and a `LIMIT/OFFSET` against an index."""

    @abstractmethod
    def counts(self, org_id: str) -> dict[str, dict[str, int]]:
        """Grouped counts for the compliance posture, computed by the database."""

    @abstractmethod
    def corrections_of(self, org_id: str, report_id: str) -> tuple[RegulatoryReport, ...]: ...


class ReportExceptionRepository(ABC):
    """Append-only apart from acknowledgement. **An exception is never deleted or cleared.**"""

    @abstractmethod
    def append(self, exception: ReportException) -> tuple[ReportException, bool]: ...

    @abstractmethod
    def acknowledge(
        self, exception: ReportException, *, expected_version: int
    ) -> ReportException: ...

    @abstractmethod
    def get(self, org_id: str, exception_id: str) -> ReportException | None: ...

    @abstractmethod
    def for_report(
        self, org_id: str, report_id: str, *, offset: int = 0, limit: int = 200
    ) -> tuple[tuple[ReportException, ...], int]: ...

    @abstractmethod
    def all_for_report(self, org_id: str, report_id: str) -> tuple[ReportException, ...]:
        """Every exception, for the finalization guard and the submission artifact."""


class ReportSnapshotRepository(ABC):
    """**Immutable. Append-only, no update, no delete.**"""

    @abstractmethod
    def create(self, snapshot: ReportSnapshot) -> tuple[ReportSnapshot, bool]: ...

    @abstractmethod
    def get(self, org_id: str, snapshot_id: str) -> ReportSnapshot | None: ...

    @abstractmethod
    def for_report(self, org_id: str, report_id: str) -> ReportSnapshot | None: ...


class SubmissionPackageRepository(ABC):
    """**Immutable.** `create` is idempotent on the content-addressed id."""

    @abstractmethod
    def create(self, package: SubmissionPackage) -> tuple[SubmissionPackage, bool]: ...

    @abstractmethod
    def get(self, org_id: str, package_id: str) -> SubmissionPackage | None: ...

    @abstractmethod
    def for_report(self, org_id: str, report_id: str) -> SubmissionPackage | None: ...


class ReportEventRepository(ABC):
    """Append-only, totally ordered per report. No update, no delete."""

    @abstractmethod
    def append(self, event: ReportEvent) -> tuple[ReportEvent, bool]: ...

    @abstractmethod
    def for_report(
        self, org_id: str, report_id: str, *, offset: int = 0, limit: int = 200
    ) -> tuple[tuple[ReportEvent, ...], int]: ...

    @abstractmethod
    def next_sequence(self, org_id: str, report_id: str) -> int: ...


class ValidationResultRepository(ABC):
    """One validation result per report, replaced only by a later validation of the same report.

    Not append-only, and deliberately so: a validation result is a *current* verdict about a draft,
    not a historical filing. What is permanent is the snapshot, which pins the verdict that let a
    report finalize — and the trail, which records every validation that ran."""

    @abstractmethod
    def save(self, result: ValidationResult) -> ValidationResult: ...

    @abstractmethod
    def get(self, org_id: str, report_id: str) -> ValidationResult | None: ...


def blocking(exceptions: tuple[ReportException, ...]) -> tuple[ReportException, ...]:
    """The exceptions that stop a filing. **Acknowledgement does not remove one from this list.**"""
    return tuple(e for e in exceptions if e.blocks_finalization)


def unresolved_sources(manifest: ReportInputManifest) -> tuple[str, ...]:
    return tuple(sorted(s.locator for s in manifest.unavailable))


__all__ = [
    "GENERATOR_VERSION",
    "REPORT_EVENTS",
    "ReportDefinitionRepository",
    "ReportEventRepository",
    "ReportExceptionRepository",
    "ReportManifestRepository",
    "ReportRepository",
    "ReportSnapshotRepository",
    "ReportingFactory",
    "ReportingPeriodRepository",
    "SubmissionPackageRepository",
    "ValidationResultRepository",
    "blocking",
    "unresolved_sources",
]
