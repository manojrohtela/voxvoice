"""Compliance & Regulatory Reporting repositories (M9.22) — additive to the M9.4 persistence layer.

Nine SQL implementations of the M9.22 Foundation contracts, reusing the M9.4 machinery unchanged:
the same `_Repo` session handle, the same codec, the same projection-plus-payload mapping, the same
"never commit, never roll back" rule, and the same org-scoped-by-construction reads (§A11.2).

## The five race-critical writes

`SqlReportDefinitionRepository.publish` inserts on an id derived from `(org, code, version)`.
Publishing the same version twice lands on one row — and the Application refuses first if the
content differs, so a rule cannot be edited under a filing that already used it.

`SqlReportRepository.create` inserts on an id derived from the **pinned inputs**. Two workers
generating the same report over the same unmoved sources therefore file once, not twice.

`SqlReportRepository.advance` is the compare-and-set on `version` — **the stale-worker defence**.
Worker A reads a report as `READY` version 4, worker B finalizes it to version 5, worker A resumes
and writes: zero rows match, and it raises `ReportStale` rather than silently overwriting a filing
somebody else approved.

`SqlReportSnapshotRepository.create` is unique on `(org, report)`: a report finalizes once.

`SqlSubmissionPackageRepository.create` is content-addressed on the report digest, so two workers
packaging one filing produce one package.

## Pagination is a database concern

`page()` issues a `COUNT` and a `LIMIT/OFFSET` against an index, `counts()` issues `GROUP BY`
aggregates, and the manifest's sources live in **their own table** so a filing with three hundred
pinned sources paginates instead of being loaded whole. §34 forbids `all(org_id, 1000)` followed by
a Python filter, and nothing here does it.

## What no repository here does

None reads a source, computes a field, decides completeness or validates anything — those are
Foundation services reached through the Application. And **none writes to a ledger, journal,
invoice, tax, payment, recognition, collection, reconciliation, incident or investigation table**.
An architecture test scans the write targets of the whole Application to prove it.
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from billing.foundation import (
    RegulatoryReport,
    ReportDefinition,
    ReportDefinitionRepository,
    ReportEvent,
    ReportEventRepository,
    ReportException,
    ReportExceptionRepository,
    ReportingPeriod,
    ReportingPeriodRepository,
    ReportInputManifest,
    ReportManifestRepository,
    ReportRepository,
    ReportSearchQuery,
    ReportSnapshot,
    ReportSnapshotRepository,
    ReportSourceRef,
    ReportStale,
    SubmissionPackage,
    SubmissionPackageRepository,
    ValidationResult,
    ValidationResultRepository,
)

from . import reporting_models as rows
from .codec import decode, encode
from .mapping import from_row
from .repositories import _Repo


def _definition_projection(d: ReportDefinition) -> dict:
    return {
        "id": d.id,
        "org_id": d.org_id,
        "report_code": d.report_code,
        "version": d.version,
        "title": d.title,
        "jurisdiction": d.jurisdiction,
        "frequency": d.frequency.value,
        "period_basis": d.period_basis.value,
        "currency_basis": d.currency_basis.value,
        "declared_currency": None if d.declared_currency is None else str(d.declared_currency),
        "integrity_blocks_finalization": d.integrity_blocks_finalization,
        "definition_digest": d.definition_digest,
        "published_at": d.published_at,
        "published_by": d.published_by,
    }


def _period_projection(p: ReportingPeriod) -> dict:
    return {
        "id": p.id,
        "org_id": p.org_id,
        "basis": p.basis.value,
        "period_key": p.period_key,
        "window_start": p.window.start,
        "window_end": p.window.end,
        "state": p.state.value,
        "aligned_source_id": p.aligned_source_id,
        "opened_at": p.opened_at,
        "closed_at": p.closed_at,
        "closed_by": p.closed_by,
        "version": p.version,
    }


def _manifest_projection(m: ReportInputManifest) -> dict:
    return {
        "id": m.id,
        "org_id": m.org_id,
        "definition_id": m.definition_id,
        "definition_version": m.definition_version,
        "period_id": m.period_id,
        "account_id": m.account_id,
        "source_digest": m.source_digest,
        "generator_version": m.generator_version,
        "generated_at": m.generated_at,
        "source_count": len(m.sources),
    }


def _source_projection(manifest_id: str, org_id: str, s: ReportSourceRef) -> dict:
    return {
        "id": f"{manifest_id}:{s.locator}",
        "org_id": org_id,
        "manifest_id": manifest_id,
        "domain": s.domain.value,
        "source_type": s.source_type.value,
        "source_id": s.source_id,
        "locator": s.locator,
        "status": s.status.value,
        "revision": s.revision,
        "digest": s.digest,
        "resolved_at": s.resolved_at,
        "fact_count": s.fact_count,
        "unavailable_reason": s.unavailable_reason,
    }


def _report_projection(r: RegulatoryReport) -> dict:
    return {
        "id": r.id,
        "org_id": r.org_id,
        "report_code": r.report_code,
        "definition_id": r.definition_id,
        "definition_version": r.definition_version,
        "period_id": r.period_id,
        "manifest_id": r.manifest_id,
        "manifest_digest": r.manifest_digest,
        "account_id": r.account_id,
        "status": r.status.value,
        "completeness": r.completeness.value,
        "evidence_completeness": r.evidence_completeness.value,
        "integrity_posture": r.integrity_posture.value,
        "report_digest": r.report_digest,
        "snapshot_id": r.snapshot_id,
        "exception_count": r.exception_count,
        "blocking_exception_count": r.blocking_exception_count,
        "source_count": r.source_count,
        "corrects": r.corrects,
        "corrected_by": r.corrected_by,
        "created_at": r.created_at,
        "created_by": r.created_by,
        "prepared_at": r.prepared_at,
        "validated_at": r.validated_at,
        "finalized_at": r.finalized_at,
        "finalized_by": r.finalized_by,
        "version": r.version,
    }


def _exception_projection(e: ReportException) -> dict:
    return {
        "id": e.id,
        "org_id": e.org_id,
        "report_id": e.report_id,
        "kind": e.kind.value,
        "severity": e.severity.value,
        "subject": e.subject,
        "diagnostic": e.diagnostic,
        "state": e.state.value,
        "field_key": e.field_key,
        "source_locator": e.source_locator,
        "created_at": e.created_at,
        "acknowledged_at": e.acknowledged_at,
        "acknowledged_by": e.acknowledged_by,
        "acknowledgement_reason": e.acknowledgement_reason,
        "version": e.version,
    }


def _snapshot_projection(s: ReportSnapshot) -> dict:
    return {
        "id": s.id,
        "org_id": s.org_id,
        "report_id": s.report_id,
        "definition_id": s.definition_id,
        "definition_version": s.definition_version,
        "manifest_id": s.manifest_id,
        "manifest_digest": s.manifest_digest,
        "report_digest": s.report_digest,
        "completeness": s.completeness.value,
        "evidence_completeness": s.evidence_completeness.value,
        "validation_passed": s.validation_passed,
        "finalized_at": s.finalized_at,
        "finalized_by": s.finalized_by,
    }


def _package_projection(p: SubmissionPackage) -> dict:
    return {
        "id": p.id,
        "org_id": p.org_id,
        "report_id": p.report_id,
        "snapshot_id": p.snapshot_id,
        "definition_id": p.definition_id,
        "definition_version": p.definition_version,
        "report_digest": p.report_digest,
        "manifest_digest": p.manifest_digest,
        "package_digest": p.package_digest,
        "completeness": p.completeness.value,
        "evidence_completeness": p.evidence_completeness.value,
        "validation_passed": p.validation_passed,
        "prepared_at": p.prepared_at,
        "prepared_by": p.prepared_by,
        "submitted_at": p.submitted_at,
        "submission_reference": p.submission_reference,
    }


def _event_projection(e: ReportEvent) -> dict:
    return {
        "id": e.id,
        "org_id": e.org_id,
        "report_id": e.report_id,
        "sequence": e.sequence,
        "kind": e.kind.value,
        "at": e.at,
        "actor_id": e.actor_id,
        "subject": e.subject,
        "summary": e.summary,
    }


class SqlReportDefinitionRepository(_Repo, ReportDefinitionRepository):
    """**Append-only.** A published version is never edited; a changed rule gets a new version."""

    def publish(self, definition: ReportDefinition) -> tuple[ReportDefinition, bool]:
        existing = self._session.get(rows.BillingReportDefinitionRow, definition.id)
        if existing is not None:
            return from_row(ReportDefinition, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingReportDefinitionRow(
                        payload=encode(definition), **_definition_projection(definition)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingReportDefinitionRow, definition.id)
            if winner is None:  # pragma: no cover — the primary key guarantees a row
                raise
            return from_row(ReportDefinition, winner), False
        return definition, True

    def get(self, org_id: str, definition_id: str) -> ReportDefinition | None:
        row = self._one(
            select(rows.BillingReportDefinitionRow).where(
                rows.BillingReportDefinitionRow.org_id == org_id,
                rows.BillingReportDefinitionRow.id == definition_id,
            )
        )
        return from_row(ReportDefinition, row) if row else None

    def by_code(self, org_id: str, report_code: str, version: int) -> ReportDefinition | None:
        row = self._one(
            select(rows.BillingReportDefinitionRow).where(
                rows.BillingReportDefinitionRow.org_id == org_id,
                rows.BillingReportDefinitionRow.report_code == report_code,
                rows.BillingReportDefinitionRow.version == version,
            )
        )
        return from_row(ReportDefinition, row) if row else None

    def latest_version(self, org_id: str, report_code: str) -> ReportDefinition | None:
        """**Used only when opening a new run**, never when reading an existing filing.

        A report stores the version that produced it; nothing reads this to interpret one."""
        table = rows.BillingReportDefinitionRow
        row = self._one(
            select(table)
            .where(table.org_id == org_id, table.report_code == report_code)
            .order_by(table.version.desc())
        )
        return from_row(ReportDefinition, row) if row else None

    def list_definitions(
        self, org_id: str, *, offset: int = 0, limit: int = 50
    ) -> tuple[tuple[ReportDefinition, ...], int]:
        table = rows.BillingReportDefinitionRow
        total = self._session.execute(
            select(func.count()).select_from(table).where(table.org_id == org_id)
        ).scalar_one()
        found = self._all(
            select(table)
            .where(table.org_id == org_id)
            .order_by(table.report_code, table.version.desc())
            .limit(max(1, limit))
            .offset(max(0, offset))
        )
        return tuple(from_row(ReportDefinition, r) for r in found), int(total)


class SqlReportingPeriodRepository(_Repo, ReportingPeriodRepository):
    def create(self, period: ReportingPeriod) -> tuple[ReportingPeriod, bool]:
        existing = self._session.get(rows.BillingReportingPeriodRow, period.id)
        if existing is not None:
            return self._decode(existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingReportingPeriodRow(
                        payload=encode(period), **_period_projection(period)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingReportingPeriodRow, period.id)
            if winner is None:  # pragma: no cover
                raise
            return self._decode(winner), False
        return period, True

    def close(self, period: ReportingPeriod, *, expected_version: int) -> ReportingPeriod:
        """Compare-and-set. **Two workers closing one period converge on one closure**, and the
        loser learns it lost rather than writing a second `closed_by`."""
        table = rows.BillingReportingPeriodRow.__table__
        result = self._session.execute(
            table.update()
            .where(
                table.c.id == period.id,
                table.c.org_id == period.org_id,
                table.c.version == expected_version,
            )
            .values(payload=encode(period), **_period_projection(period))
        )
        if result.rowcount != 1:
            raise ReportStale(
                f"reporting period {period.id!r} moved while this worker was deciding "
                f"(expected version {expected_version}); another worker closed it — re-read "
                "rather than overwrite"
            )
        self._session.expire_all()
        return period

    def get(self, org_id: str, period_id: str) -> ReportingPeriod | None:
        row = self._one(
            select(rows.BillingReportingPeriodRow).where(
                rows.BillingReportingPeriodRow.org_id == org_id,
                rows.BillingReportingPeriodRow.id == period_id,
            )
        )
        return self._decode(row) if row else None

    def by_key(self, org_id: str, basis: str, period_key: str) -> ReportingPeriod | None:
        row = self._one(
            select(rows.BillingReportingPeriodRow).where(
                rows.BillingReportingPeriodRow.org_id == org_id,
                rows.BillingReportingPeriodRow.basis == basis,
                rows.BillingReportingPeriodRow.period_key == period_key,
            )
        )
        return self._decode(row) if row else None

    def list_periods(
        self, org_id: str, *, basis: str = "", offset: int = 0, limit: int = 50
    ) -> tuple[tuple[ReportingPeriod, ...], int]:
        table = rows.BillingReportingPeriodRow
        filters = [table.org_id == org_id]
        if basis:
            filters.append(table.basis == basis)
        total = self._session.execute(
            select(func.count()).select_from(table).where(*filters)
        ).scalar_one()
        found = self._all(
            select(table)
            .where(*filters)
            .order_by(table.window_start.desc(), table.id)
            .limit(max(1, limit))
            .offset(max(0, offset))
        )
        return tuple(self._decode(r) for r in found), int(total)

    @staticmethod
    def _decode(row) -> ReportingPeriod:
        """`BillingPeriod` is a nested frozen dataclass with a validating `__post_init__`, so the
        codec restores it from the payload rather than from the flattened projection columns."""
        return decode(ReportingPeriod, row.payload)


class SqlReportManifestRepository(_Repo, ReportManifestRepository):
    """**Append-only. No update, no delete.** The pinned inputs are the report's identity.

    Sources live in their own table so `sources()` paginates at the database — a manifest over a
    busy period can hold hundreds, and loading them whole to render ten is the §34 failure."""

    def create(self, manifest: ReportInputManifest) -> tuple[ReportInputManifest, bool]:
        existing = self._session.get(rows.BillingReportManifestRow, manifest.id)
        if existing is not None:
            return decode(ReportInputManifest, existing.payload), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingReportManifestRow(
                        payload=encode(manifest), **_manifest_projection(manifest)
                    )
                )
                for source in manifest.sources:
                    self._session.add(
                        rows.BillingReportSourceRow(
                            payload=encode(source),
                            **_source_projection(manifest.id, manifest.org_id, source),
                        )
                    )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingReportManifestRow, manifest.id)
            if winner is None:  # pragma: no cover
                raise
            return decode(ReportInputManifest, winner.payload), False
        return manifest, True

    def get(self, org_id: str, manifest_id: str) -> ReportInputManifest | None:
        row = self._one(
            select(rows.BillingReportManifestRow).where(
                rows.BillingReportManifestRow.org_id == org_id,
                rows.BillingReportManifestRow.id == manifest_id,
            )
        )
        return decode(ReportInputManifest, row.payload) if row else None

    def sources(
        self, org_id: str, manifest_id: str, *, offset: int = 0, limit: int = 200
    ) -> tuple[tuple[ReportSourceRef, ...], int]:
        table = rows.BillingReportSourceRow
        filters = [table.org_id == org_id, table.manifest_id == manifest_id]
        total = self._session.execute(
            select(func.count()).select_from(table).where(*filters)
        ).scalar_one()
        found = self._all(
            select(table)
            .where(*filters)
            .order_by(table.locator)
            .limit(max(1, limit))
            .offset(max(0, offset))
        )
        return tuple(decode(ReportSourceRef, r.payload) for r in found), int(total)


class SqlReportRepository(_Repo, ReportRepository):
    """Reports, arbitrated by their pinned inputs. There is no delete: a filing is a record."""

    def create(self, report: RegulatoryReport) -> tuple[RegulatoryReport, bool]:
        existing = self._session.get(rows.BillingReportRow, report.id)
        if existing is not None:
            return decode(RegulatoryReport, existing.payload), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingReportRow(
                        payload=encode(report), **_report_projection(report)
                    )
                )
                self._session.flush()
        except IntegrityError:
            # Another worker generated the same report from the same pinned inputs. The derived id
            # guarantees they computed the same filing, so this caller reads the winner's rather
            # than filing a second one.
            winner = self._session.get(rows.BillingReportRow, report.id)
            if winner is None:  # pragma: no cover
                raise
            return decode(RegulatoryReport, winner.payload), False
        return report, True

    def advance(self, report: RegulatoryReport, *, expected_version: int) -> RegulatoryReport:
        """Compare-and-set on `version`. **The stale-worker defence.**

        A filtered UPDATE rather than a read followed by a write, because the read would be exactly
        the stale read the check exists to catch. Zero rows is not retryable: the caller must
        re-read, and when it does it may find a filing the FSM refuses to move at all — which is
        correct, because somebody else finalized it."""
        table = rows.BillingReportRow.__table__
        result = self._session.execute(
            table.update()
            .where(
                table.c.id == report.id,
                table.c.org_id == report.org_id,
                table.c.version == expected_version,
            )
            .values(payload=encode(report), **_report_projection(report))
        )
        if result.rowcount != 1:
            stored = self._session.get(rows.BillingReportRow, report.id)
            current = stored.status if stored is not None else "missing"
            raise ReportStale(
                f"report {report.id!r} moved while this worker was deciding (expected version "
                f"{expected_version}, found status {current!r}); another worker settled it — "
                "re-read rather than overwrite a filing somebody else approved"
            )
        self._session.expire_all()
        return report

    def get(self, org_id: str, report_id: str) -> RegulatoryReport | None:
        row = self._one(
            select(rows.BillingReportRow).where(
                rows.BillingReportRow.org_id == org_id,
                rows.BillingReportRow.id == report_id,
            )
        )
        return decode(RegulatoryReport, row.payload) if row else None

    def page(self, query: ReportSearchQuery) -> tuple[tuple[RegulatoryReport, ...], int]:
        """**One page and the total, both from the database.**"""
        table = rows.BillingReportRow
        filters = [table.org_id == query.org_id]
        if query.report_code:
            filters.append(table.report_code == query.report_code)
        if query.definition_version is not None:
            filters.append(table.definition_version == query.definition_version)
        if query.period_id:
            filters.append(table.period_id == query.period_id)
        if query.status is not None:
            filters.append(table.status == query.status.value)
        if query.completeness is not None:
            filters.append(table.completeness == query.completeness.value)
        if query.finalized_only:
            filters.append(table.status == "finalized")
        if query.corrections_only:
            filters.append(table.corrects != "")
        total = self._session.execute(
            select(func.count()).select_from(table).where(*filters)
        ).scalar_one()
        found = self._all(
            select(table)
            .where(*filters)
            .order_by(table.created_at.desc(), table.id)
            .limit(max(1, query.limit))
            .offset(max(0, query.offset))
        )
        return tuple(decode(RegulatoryReport, r.payload) for r in found), int(total)

    def counts(self, org_id: str) -> dict[str, dict[str, int]]:
        """Grouped counts, computed by the database. **Counts only — never a figure.**"""
        table = rows.BillingReportRow
        by_status = {
            str(s): int(n)
            for s, n in self._session.execute(
                select(table.status, func.count())
                .where(table.org_id == org_id)
                .group_by(table.status)
            ).all()
        }
        by_completeness = {
            str(c): int(n)
            for c, n in self._session.execute(
                select(table.completeness, func.count())
                .where(table.org_id == org_id)
                .group_by(table.completeness)
            ).all()
        }
        exc = rows.BillingReportExceptionRow
        open_exceptions = self._session.execute(
            select(func.count())
            .select_from(exc)
            .where(exc.org_id == org_id, exc.state == "open")
        ).scalar_one()
        blocking_exceptions = self._session.execute(
            select(func.count())
            .select_from(exc)
            .where(exc.org_id == org_id, exc.severity == "blocking")
        ).scalar_one()
        pkg = rows.BillingSubmissionPackageRow
        packages = self._session.execute(
            select(func.count()).select_from(pkg).where(pkg.org_id == org_id)
        ).scalar_one()
        return {
            "by_status": by_status,
            "by_completeness": by_completeness,
            "totals": {
                "open_exceptions": int(open_exceptions),
                "blocking_exceptions": int(blocking_exceptions),
                "submission_packages": int(packages),
            },
        }

    def corrections_of(self, org_id: str, report_id: str) -> tuple[RegulatoryReport, ...]:
        table = rows.BillingReportRow
        found = self._all(
            select(table)
            .where(table.org_id == org_id, table.corrects == report_id)
            .order_by(table.created_at)
        )
        return tuple(decode(RegulatoryReport, r.payload) for r in found)


class SqlReportExceptionRepository(_Repo, ReportExceptionRepository):
    """Append-only apart from acknowledgement. **Never deleted, never cleared.**"""

    def append(self, exception: ReportException) -> tuple[ReportException, bool]:
        existing = self._session.get(rows.BillingReportExceptionRow, exception.id)
        if existing is not None:
            return from_row(ReportException, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingReportExceptionRow(
                        payload=encode(exception), **_exception_projection(exception)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingReportExceptionRow, exception.id)
            if winner is None:  # pragma: no cover
                raise
            return from_row(ReportException, winner), False
        return exception, True

    def acknowledge(
        self, exception: ReportException, *, expected_version: int
    ) -> ReportException:
        table = rows.BillingReportExceptionRow.__table__
        result = self._session.execute(
            table.update()
            .where(
                table.c.id == exception.id,
                table.c.org_id == exception.org_id,
                table.c.version == expected_version,
            )
            .values(payload=encode(exception), **_exception_projection(exception))
        )
        if result.rowcount != 1:
            raise ReportStale(
                f"report exception {exception.id!r} was acknowledged by somebody else while this "
                f"worker was deciding (expected version {expected_version}); re-read rather than "
                "overwrite who saw it first and when"
            )
        self._session.expire_all()
        return exception

    def get(self, org_id: str, exception_id: str) -> ReportException | None:
        row = self._one(
            select(rows.BillingReportExceptionRow).where(
                rows.BillingReportExceptionRow.org_id == org_id,
                rows.BillingReportExceptionRow.id == exception_id,
            )
        )
        return from_row(ReportException, row) if row else None

    def for_report(
        self, org_id: str, report_id: str, *, offset: int = 0, limit: int = 200
    ) -> tuple[tuple[ReportException, ...], int]:
        table = rows.BillingReportExceptionRow
        filters = [table.org_id == org_id, table.report_id == report_id]
        total = self._session.execute(
            select(func.count()).select_from(table).where(*filters)
        ).scalar_one()
        found = self._all(
            select(table)
            .where(*filters)
            .order_by(table.created_at, table.id)
            .limit(max(1, limit))
            .offset(max(0, offset))
        )
        return tuple(from_row(ReportException, r) for r in found), int(total)

    def all_for_report(self, org_id: str, report_id: str) -> tuple[ReportException, ...]:
        """Every exception, for the finalization guard. **Bounded by one report**, never by org."""
        table = rows.BillingReportExceptionRow
        found = self._all(
            select(table)
            .where(table.org_id == org_id, table.report_id == report_id)
            .order_by(table.created_at, table.id)
        )
        return tuple(from_row(ReportException, r) for r in found)


class SqlReportSnapshotRepository(_Repo, ReportSnapshotRepository):
    """**Immutable. Append-only, no update, no delete.** A report finalizes once."""

    def create(self, snapshot: ReportSnapshot) -> tuple[ReportSnapshot, bool]:
        existing = self._session.get(rows.BillingReportSnapshotRow, snapshot.id)
        if existing is not None:
            return decode(ReportSnapshot, existing.payload), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingReportSnapshotRow(
                        payload=encode(snapshot), **_snapshot_projection(snapshot)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingReportSnapshotRow, snapshot.id)
            if winner is not None:
                return decode(ReportSnapshot, winner.payload), False
            # The unique constraint on `(org, report)` fired: this report already finalized under
            # a different digest. Read the filing that exists rather than raising — the caller's
            # FSM guard has already refused a second finalization.
            found = self.for_report(snapshot.org_id, snapshot.report_id)
            if found is None:  # pragma: no cover
                raise
            return found, False
        return snapshot, True

    def get(self, org_id: str, snapshot_id: str) -> ReportSnapshot | None:
        row = self._one(
            select(rows.BillingReportSnapshotRow).where(
                rows.BillingReportSnapshotRow.org_id == org_id,
                rows.BillingReportSnapshotRow.id == snapshot_id,
            )
        )
        return decode(ReportSnapshot, row.payload) if row else None

    def for_report(self, org_id: str, report_id: str) -> ReportSnapshot | None:
        row = self._one(
            select(rows.BillingReportSnapshotRow).where(
                rows.BillingReportSnapshotRow.org_id == org_id,
                rows.BillingReportSnapshotRow.report_id == report_id,
            )
        )
        return decode(ReportSnapshot, row.payload) if row else None


class SqlSubmissionPackageRepository(_Repo, SubmissionPackageRepository):
    """**Immutable**, content-addressed on the report digest."""

    def create(self, package: SubmissionPackage) -> tuple[SubmissionPackage, bool]:
        existing = self._session.get(rows.BillingSubmissionPackageRow, package.id)
        if existing is not None:
            return decode(SubmissionPackage, existing.payload), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingSubmissionPackageRow(
                        payload=encode(package), **_package_projection(package)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingSubmissionPackageRow, package.id)
            if winner is None:  # pragma: no cover
                raise
            return decode(SubmissionPackage, winner.payload), False
        return package, True

    def get(self, org_id: str, package_id: str) -> SubmissionPackage | None:
        row = self._one(
            select(rows.BillingSubmissionPackageRow).where(
                rows.BillingSubmissionPackageRow.org_id == org_id,
                rows.BillingSubmissionPackageRow.id == package_id,
            )
        )
        return decode(SubmissionPackage, row.payload) if row else None

    def for_report(self, org_id: str, report_id: str) -> SubmissionPackage | None:
        table = rows.BillingSubmissionPackageRow
        row = self._one(
            select(table)
            .where(table.org_id == org_id, table.report_id == report_id)
            .order_by(table.prepared_at.desc(), table.id)
        )
        return decode(SubmissionPackage, row.payload) if row else None


class SqlReportValidationRepository(_Repo, ValidationResultRepository):
    """The current verdict on a draft. Replaced by a later validation of the same report."""

    def save(self, result: ValidationResult) -> ValidationResult:
        existing = self._session.get(rows.BillingReportValidationRow, result.report_id)
        projection = {
            "report_id": result.report_id,
            "org_id": result.org_id,
            "passed": result.passed,
            "definition_version": result.definition_version,
            "check_count": len(result.checks),
            "failure_count": len(result.failures),
            "validated_at": result.validated_at,
            "validated_by": result.validated_by,
        }
        if existing is None:
            self._session.add(
                rows.BillingReportValidationRow(payload=encode(result), **projection)
            )
        else:
            for key, value in projection.items():
                setattr(existing, key, value)
            existing.payload = encode(result)
        self._session.flush()
        return result

    def get(self, org_id: str, report_id: str) -> ValidationResult | None:
        row = self._one(
            select(rows.BillingReportValidationRow).where(
                rows.BillingReportValidationRow.org_id == org_id,
                rows.BillingReportValidationRow.report_id == report_id,
            )
        )
        return decode(ValidationResult, row.payload) if row else None


class SqlReportEventRepository(_Repo, ReportEventRepository):
    """**Append-only. No update, no delete.** The trail is the record, not a log of it."""

    def append(self, event: ReportEvent) -> tuple[ReportEvent, bool]:
        existing = self._session.get(rows.BillingReportEventRow, event.id)
        if existing is not None:
            return from_row(ReportEvent, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingReportEventRow(
                        payload=encode(event), **_event_projection(event)
                    )
                )
                self._session.flush()
        except IntegrityError:
            # Two workers computed the same sequence. The unique constraint arbitrates; the loser
            # reads the winner's line rather than claiming the same position in the trail.
            winner = self._session.get(rows.BillingReportEventRow, event.id)
            if winner is not None:
                return from_row(ReportEvent, winner), False
            same_seq = self._one(
                select(rows.BillingReportEventRow).where(
                    rows.BillingReportEventRow.org_id == event.org_id,
                    rows.BillingReportEventRow.report_id == event.report_id,
                    rows.BillingReportEventRow.sequence == event.sequence,
                )
            )
            if same_seq is None:  # pragma: no cover
                raise
            return from_row(ReportEvent, same_seq), False
        return event, True

    def for_report(
        self, org_id: str, report_id: str, *, offset: int = 0, limit: int = 200
    ) -> tuple[tuple[ReportEvent, ...], int]:
        table = rows.BillingReportEventRow
        filters = [table.org_id == org_id, table.report_id == report_id]
        total = self._session.execute(
            select(func.count()).select_from(table).where(*filters)
        ).scalar_one()
        found = self._all(
            select(table)
            .where(*filters)
            .order_by(table.sequence)
            .limit(max(1, limit))
            .offset(max(0, offset))
        )
        return tuple(from_row(ReportEvent, r) for r in found), int(total)

    def next_sequence(self, org_id: str, report_id: str) -> int:
        table = rows.BillingReportEventRow
        highest = self._session.execute(
            select(func.max(table.sequence)).where(
                table.org_id == org_id, table.report_id == report_id
            )
        ).scalar_one()
        return int(highest or 0) + 1


__all__ = [
    "SqlReportDefinitionRepository",
    "SqlReportEventRepository",
    "SqlReportExceptionRepository",
    "SqlReportManifestRepository",
    "SqlReportRepository",
    "SqlReportSnapshotRepository",
    "SqlReportValidationRepository",
    "SqlReportingPeriodRepository",
    "SqlSubmissionPackageRepository",
]
