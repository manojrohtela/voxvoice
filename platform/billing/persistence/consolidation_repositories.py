"""Period Scope & Consolidation repositories (M9.23) — additive to the M9.4 persistence layer.

Ten SQL implementations of the M9.23 Foundation contracts, reusing the M9.4 machinery unchanged:
the same `_Repo` session handle, the same codec, the same projection-plus-payload mapping, the same
"never commit, never roll back" rule, and the same org-scoped-by-construction reads (§A11.2).

## The six race-critical writes

`SqlScopeDefinitionRepository.publish` inserts on an id derived from `(org, code, version)`.
Publishing the same version twice lands on one row — and the Application refuses first if the
content differs, so a scope cannot be edited under a consolidation that already used it.

`SqlScopeSnapshotRepository.create` inserts on an id derived from the **membership digest**. Two
workers resolving the same scope against an unchanged hierarchy write one snapshot, not two — and
an account that moved parent produces a different id rather than editing the snapshot a finalized
consolidation references.

`SqlConsolidationRepository.create` inserts on an id derived from `(org, snapshot, period,
corrects)`. Two workers opening the same consolidation therefore consolidate once.

`SqlConsolidationRepository.advance` is the compare-and-set on `version` — **the stale-worker
defence**. Worker A reads a run as `READY` version 4, worker B finalizes it to version 5, worker A
resumes and writes: zero rows match, and it raises `ConsolidationStale` rather than silently
overwriting a consolidation somebody else finalized.

`SqlSourceBindingRepository.bind` inserts on `(run, account, domain, artifact)`. Two workers
collecting one batch concurrently write one row per source — the race is a no-op rather than a
double count, which is the difference between a correct total and a doubled one.

`SqlConsolidationResultRepository.create` is unique on `(org, consolidation)`: a run finalizes once.

## Pagination and aggregation are database concerns

`members()` and `page()` issue a `COUNT` and a `LIMIT/OFFSET` against an index. `member_ids()` is a
**keyset** read — `WHERE account_id > cursor ORDER BY account_id LIMIT n` — so batched collection
over a hundred thousand accounts stays an indexed range scan rather than a growing offset.
`counts()` and `posture()` issue `GROUP BY` aggregates.

§25 forbids `all(org_id, 1000)` followed by a Python filter, and nothing here does it.

## What no repository here does

None resolves a scope, reads a source, aggregates money, decides completeness or validates
anything — those are Foundation services reached through the Application. And **none writes to an
account, ledger, journal, invoice, tax, payment, recognition, collection, reconciliation, incident,
investigation or reporting table**. An architecture test scans the write targets of the whole
Application to prove it.
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from billing.foundation import (
    ConsolidationEvent,
    ConsolidationEventRepository,
    ConsolidationException,
    ConsolidationExceptionRepository,
    ConsolidationPeriod,
    ConsolidationPeriodRepository,
    ConsolidationPosture,
    ConsolidationRepository,
    ConsolidationRun,
    ConsolidationSearchQuery,
    ConsolidationSearchResult,
    ConsolidationSnapshot,
    ConsolidationSnapshotRepository,
    ConsolidationStale,
    ConsolidationValidation,
    ConsolidationValidationRepository,
    PeriodBasis,
    ScopeDefinition,
    ScopeDefinitionRepository,
    ScopeMember,
    ScopeSnapshot,
    ScopeSnapshotRepository,
    SourceBinding,
    SourceBindingRepository,
)

from . import consolidation_models as rows
from .codec import decode, encode
from .repositories import _Repo


def _definition_projection(d: ScopeDefinition) -> dict:
    return {
        "id": d.id,
        "org_id": d.org_id,
        "scope_code": d.scope_code,
        "version": d.version,
        "mode": d.mode.value,
        "title": d.title,
        "anchor_account_id": d.anchor_account_id,
        "currency_mode": d.currency_mode.value,
        "declared_currency": str(d.declared_currency) if d.declared_currency else None,
        "period_basis": d.period_basis.value,
        "integrity_blocks_finalization": d.integrity_blocks_finalization,
        "definition_digest": d.definition_digest,
        "published_at": d.published_at,
        "published_by": d.published_by,
    }


def _snapshot_projection(s: ScopeSnapshot) -> dict:
    return {
        "id": s.id,
        "org_id": s.org_id,
        "scope_definition_id": s.scope_definition_id,
        "scope_version": s.scope_version,
        "mode": s.mode.value,
        "hierarchy": s.hierarchy.value,
        "member_count": s.member_count,
        "excluded_count": len(s.excluded),
        "currency_mode": s.currency_mode.value,
        "declared_currency": str(s.declared_currency) if s.declared_currency else None,
        "membership_digest": s.membership_digest,
        "snapshot_digest": s.snapshot_digest,
        "effective_at": s.effective_at,
        "resolved_by": s.resolved_by,
    }


def _run_projection(r: ConsolidationRun) -> dict:
    return {
        "id": r.id,
        "org_id": r.org_id,
        "scope_snapshot_id": r.scope_snapshot_id,
        "scope_definition_id": r.scope_definition_id,
        "scope_version": r.scope_version,
        "period_id": r.period_id,
        "period_basis": r.period_basis.value,
        "period_key": r.period_key,
        "status": r.status.value,
        "completeness": r.completeness.value,
        "currency_mode": r.currency_mode.value,
        "declared_currency": str(r.declared_currency) if r.declared_currency else None,
        "member_count": r.member_count,
        "collected_count": r.collected_count,
        "collection_cursor": r.collection_cursor,
        "source_count": r.source_count,
        "unavailable_source_count": r.unavailable_source_count,
        "exception_count": r.exception_count,
        "blocking_exception_count": r.blocking_exception_count,
        "consolidation_digest": r.consolidation_digest,
        "corrects": r.corrects,
        "superseded_by": r.superseded_by,
        "correction_reason": r.correction_reason,
        "opened_at": r.opened_at,
        "opened_by": r.opened_by,
        "finalized_at": r.finalized_at,
        "finalized_by": r.finalized_by,
        "version": r.version,
    }


def _binding_projection(b: SourceBinding) -> dict:
    return {
        "id": b.id,
        "org_id": b.org_id,
        "consolidation_id": b.consolidation_id,
        "account_id": b.account_id,
        "domain": b.domain.value,
        "artifact": b.artifact.value,
        "read_name": b.read_name,
        "status": b.status.value,
        "completeness": b.completeness.value,
        "period_key": b.period_key,
        "period_applied": b.period_applied,
        "source_revision": b.source_revision,
        "source_digest": b.source_digest,
        "record_count": b.record_count,
        # **Null, not zero, for a gap.** The projection copies the domain object faithfully; an
        # unavailable source has no figure and stores none.
        "amount_minor": b.amount_minor,
        "currency": str(b.currency) if b.currency else None,
        "detail": b.detail,
        "bound_at": b.bound_at,
    }


def _exception_projection(e: ConsolidationException) -> dict:
    return {
        "id": e.id,
        "org_id": e.org_id,
        "consolidation_id": e.consolidation_id,
        "kind": e.kind.value,
        "severity": e.severity.value,
        "state": e.state.value,
        "subject": e.subject,
        "diagnostic": e.diagnostic,
        "account_id": e.account_id,
        "domain": e.domain.value if e.domain else None,
        "period_key": e.period_key,
        "created_at": e.created_at,
        "acknowledged_at": e.acknowledged_at,
        "acknowledged_by": e.acknowledged_by,
        "acknowledgement_note": e.acknowledgement_note,
    }


class SqlScopeDefinitionRepository(_Repo, ScopeDefinitionRepository):
    """Published scope rules. There is no update method — a version is published once."""

    def publish(self, definition: ScopeDefinition) -> ScopeDefinition:
        existing = self._session.get(rows.BillingScopeDefinitionRow, definition.id)
        if existing is not None:
            return decode(ScopeDefinition, existing.payload)
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingScopeDefinitionRow(
                        payload=encode(definition), **_definition_projection(definition)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingScopeDefinitionRow, definition.id)
            if winner is None:  # pragma: no cover
                raise
            return decode(ScopeDefinition, winner.payload)
        return definition

    def get(self, org_id: str, definition_id: str) -> ScopeDefinition | None:
        row = self._one(
            select(rows.BillingScopeDefinitionRow).where(
                rows.BillingScopeDefinitionRow.org_id == org_id,
                rows.BillingScopeDefinitionRow.id == definition_id,
            )
        )
        return decode(ScopeDefinition, row.payload) if row else None

    def by_code(self, org_id: str, scope_code: str, version: int) -> ScopeDefinition | None:
        row = self._one(
            select(rows.BillingScopeDefinitionRow).where(
                rows.BillingScopeDefinitionRow.org_id == org_id,
                rows.BillingScopeDefinitionRow.scope_code == scope_code,
                rows.BillingScopeDefinitionRow.version == version,
            )
        )
        return decode(ScopeDefinition, row.payload) if row else None

    def latest_version(self, org_id: str, scope_code: str) -> ScopeDefinition | None:
        """**The one `latest()` in this milestone**, and it is a database `ORDER BY … LIMIT 1`."""
        row = self._one(
            select(rows.BillingScopeDefinitionRow)
            .where(
                rows.BillingScopeDefinitionRow.org_id == org_id,
                rows.BillingScopeDefinitionRow.scope_code == scope_code,
            )
            .order_by(rows.BillingScopeDefinitionRow.version.desc())
            .limit(1)
        )
        return decode(ScopeDefinition, row.payload) if row else None

    def all(self, org_id: str, limit: int = 200) -> tuple[ScopeDefinition, ...]:
        found = self._all(
            select(rows.BillingScopeDefinitionRow)
            .where(rows.BillingScopeDefinitionRow.org_id == org_id)
            .order_by(
                rows.BillingScopeDefinitionRow.scope_code,
                rows.BillingScopeDefinitionRow.version.desc(),
            )
            .limit(limit)
        )
        return tuple(decode(ScopeDefinition, r.payload) for r in found)


class SqlScopeSnapshotRepository(_Repo, ScopeSnapshotRepository):
    """Immutable memberships, with members in their own table so they paginate."""

    def create(self, snapshot: ScopeSnapshot) -> ScopeSnapshot:
        existing = self._session.get(rows.BillingScopeSnapshotRow, snapshot.id)
        if existing is not None:
            # The id derives from the membership digest, so an existing row *is* this membership.
            return decode(ScopeSnapshot, existing.payload)
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingScopeSnapshotRow(
                        payload=encode(snapshot), **_snapshot_projection(snapshot)
                    )
                )
                for member in snapshot.members:
                    self._session.add(
                        rows.BillingScopeMemberRow(
                            id=f"{snapshot.id}:{member.account_id}",
                            org_id=snapshot.org_id,
                            snapshot_id=snapshot.id,
                            account_id=member.account_id,
                            inclusion=member.inclusion.value,
                            account_revision=member.account_revision,
                            currency=str(member.currency) if member.currency else None,
                            depth=member.depth,
                            payload=encode(member),
                        )
                    )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingScopeSnapshotRow, snapshot.id)
            if winner is None:  # pragma: no cover
                raise
            return decode(ScopeSnapshot, winner.payload)
        return snapshot

    def get(self, org_id: str, snapshot_id: str) -> ScopeSnapshot | None:
        row = self._one(
            select(rows.BillingScopeSnapshotRow).where(
                rows.BillingScopeSnapshotRow.org_id == org_id,
                rows.BillingScopeSnapshotRow.id == snapshot_id,
            )
        )
        return decode(ScopeSnapshot, row.payload) if row else None

    def members(
        self, org_id: str, snapshot_id: str, page: int = 1, page_size: int = 100
    ) -> tuple[tuple[ScopeMember, ...], int]:
        table = rows.BillingScopeMemberRow
        total = (
            self._session.execute(
                select(func.count())
                .select_from(table)
                .where(table.org_id == org_id, table.snapshot_id == snapshot_id)
            ).scalar_one()
            or 0
        )
        found = self._all(
            select(table)
            .where(table.org_id == org_id, table.snapshot_id == snapshot_id)
            .order_by(table.account_id)
            .offset(max(0, (max(1, page) - 1) * page_size))
            .limit(page_size)
        )
        return tuple(decode(ScopeMember, r.payload) for r in found), total

    def member_ids(
        self, org_id: str, snapshot_id: str, after: str = "", limit: int = 100
    ) -> tuple[str, ...]:
        """**A keyset read, not an offset.** `WHERE account_id > cursor ORDER BY account_id` stays
        an indexed range scan at any depth, which is what makes collection over a hundred thousand
        accounts finish in bounded time per batch instead of degrading."""
        table = rows.BillingScopeMemberRow
        filters = [table.org_id == org_id, table.snapshot_id == snapshot_id]
        if after:
            filters.append(table.account_id > after)
        found = self._all(
            select(table).where(*filters).order_by(table.account_id).limit(max(1, limit))
        )
        return tuple(r.account_id for r in found)

    def for_definition(
        self, org_id: str, definition_id: str, limit: int = 50
    ) -> tuple[ScopeSnapshot, ...]:
        found = self._all(
            select(rows.BillingScopeSnapshotRow)
            .where(
                rows.BillingScopeSnapshotRow.org_id == org_id,
                rows.BillingScopeSnapshotRow.scope_definition_id == definition_id,
            )
            .order_by(rows.BillingScopeSnapshotRow.effective_at.desc())
            .limit(limit)
        )
        return tuple(decode(ScopeSnapshot, r.payload) for r in found)


class SqlConsolidationPeriodRepository(_Repo, ConsolidationPeriodRepository):
    def create(self, period: ConsolidationPeriod) -> ConsolidationPeriod:
        existing = self._session.get(rows.BillingConsolidationPeriodRow, period.id)
        if existing is not None:
            return decode(ConsolidationPeriod, existing.payload)
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingConsolidationPeriodRow(
                        id=period.id,
                        org_id=period.org_id,
                        basis=period.basis.value,
                        period_key=period.period_key,
                        window_start=period.window.start,
                        window_end=period.window.end,
                        aligned_source_id=period.aligned_source_id,
                        payload=encode(period),
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingConsolidationPeriodRow, period.id)
            if winner is None:  # pragma: no cover
                raise
            return decode(ConsolidationPeriod, winner.payload)
        return period

    def get(self, org_id: str, period_id: str) -> ConsolidationPeriod | None:
        row = self._one(
            select(rows.BillingConsolidationPeriodRow).where(
                rows.BillingConsolidationPeriodRow.org_id == org_id,
                rows.BillingConsolidationPeriodRow.id == period_id,
            )
        )
        return decode(ConsolidationPeriod, row.payload) if row else None

    def by_key(
        self, org_id: str, basis: PeriodBasis, period_key: str
    ) -> ConsolidationPeriod | None:
        row = self._one(
            select(rows.BillingConsolidationPeriodRow).where(
                rows.BillingConsolidationPeriodRow.org_id == org_id,
                rows.BillingConsolidationPeriodRow.basis == basis.value,
                rows.BillingConsolidationPeriodRow.period_key == period_key,
            )
        )
        return decode(ConsolidationPeriod, row.payload) if row else None

    def all(self, org_id: str, limit: int = 100) -> tuple[ConsolidationPeriod, ...]:
        found = self._all(
            select(rows.BillingConsolidationPeriodRow)
            .where(rows.BillingConsolidationPeriodRow.org_id == org_id)
            .order_by(rows.BillingConsolidationPeriodRow.window_start.desc())
            .limit(limit)
        )
        return tuple(decode(ConsolidationPeriod, r.payload) for r in found)


class SqlConsolidationRepository(_Repo, ConsolidationRepository):
    """Consolidation runs. There is no delete: a consolidation is a record."""

    def create(self, run: ConsolidationRun) -> ConsolidationRun:
        existing = self._session.get(rows.BillingConsolidationRow, run.id)
        if existing is not None:
            return decode(ConsolidationRun, existing.payload)
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingConsolidationRow(payload=encode(run), **_run_projection(run))
                )
                self._session.flush()
        except IntegrityError:
            # Another worker opened the same consolidation over the same snapshot and period. The
            # derived id guarantees it is the same request, so this caller reads the winner's row
            # rather than consolidating twice.
            winner = self._session.get(rows.BillingConsolidationRow, run.id)
            if winner is None:  # pragma: no cover
                raise
            return decode(ConsolidationRun, winner.payload)
        return run

    def get(self, org_id: str, consolidation_id: str) -> ConsolidationRun | None:
        row = self._one(
            select(rows.BillingConsolidationRow).where(
                rows.BillingConsolidationRow.org_id == org_id,
                rows.BillingConsolidationRow.id == consolidation_id,
            )
        )
        return decode(ConsolidationRun, row.payload) if row else None

    def advance(self, run: ConsolidationRun, expected_version: int) -> ConsolidationRun:
        """Compare-and-set on `version`. **The stale-worker defence.**

        A filtered UPDATE rather than a read followed by a write, because the read would be exactly
        the stale read the check exists to catch. Zero rows is not retryable: the caller must
        re-read, and when it does it may find a run the FSM refuses to move at all — which is
        correct, because somebody else finalized it."""
        table = rows.BillingConsolidationRow.__table__
        result = self._session.execute(
            table.update()
            .where(
                table.c.id == run.id,
                table.c.org_id == run.org_id,
                table.c.version == expected_version,
            )
            .values(payload=encode(run), **_run_projection(run))
        )
        if result.rowcount != 1:
            stored = self._session.get(rows.BillingConsolidationRow, run.id)
            current = stored.status if stored is not None else "missing"
            raise ConsolidationStale(
                f"consolidation {run.id!r} moved while this worker was deciding (expected version "
                f"{expected_version}, found status {current!r}); another worker settled it — "
                "re-read rather than overwrite a consolidation somebody else finalized"
            )
        self._session.expire_all()
        return run

    def search(self, query: ConsolidationSearchQuery) -> ConsolidationSearchResult:
        """**One page and the total, both from the database.**"""
        table = rows.BillingConsolidationRow
        filters = [table.org_id == query.org_id]
        if query.scope_definition_id:
            filters.append(table.scope_definition_id == query.scope_definition_id)
        if query.period_key:
            filters.append(table.period_key == query.period_key)
        if query.statuses:
            filters.append(table.status.in_([s.value for s in query.statuses]))
        if query.completeness:
            filters.append(table.completeness.in_([c.value for c in query.completeness]))
        if query.corrections_only:
            filters.append(table.corrects != "")
        total = (
            self._session.execute(
                select(func.count()).select_from(table).where(*filters)
            ).scalar_one()
            or 0
        )
        page_size = max(1, query.page_size)
        found = self._all(
            select(table)
            .where(*filters)
            .order_by(table.opened_at.desc(), table.id)
            .offset(max(0, (max(1, query.page) - 1) * page_size))
            .limit(page_size)
        )
        return ConsolidationSearchResult(
            runs=tuple(decode(ConsolidationRun, r.payload) for r in found),
            total=total,
            page=max(1, query.page),
            page_size=page_size,
        )

    def for_snapshot(
        self, org_id: str, snapshot_id: str, limit: int = 50
    ) -> tuple[ConsolidationRun, ...]:
        found = self._all(
            select(rows.BillingConsolidationRow)
            .where(
                rows.BillingConsolidationRow.org_id == org_id,
                rows.BillingConsolidationRow.scope_snapshot_id == snapshot_id,
            )
            .order_by(rows.BillingConsolidationRow.opened_at.desc())
            .limit(limit)
        )
        return tuple(decode(ConsolidationRun, r.payload) for r in found)

    def posture(self, org_id: str) -> ConsolidationPosture:
        """`GROUP BY` aggregates. **Counts and labels, never money.**"""
        table = rows.BillingConsolidationRow
        by_status = {
            status: count
            for status, count in self._session.execute(
                select(table.status, func.count())
                .where(table.org_id == org_id)
                .group_by(table.status)
            ).all()
        }
        by_completeness = {
            value: count
            for value, count in self._session.execute(
                select(table.completeness, func.count())
                .where(table.org_id == org_id)
                .group_by(table.completeness)
            ).all()
        }
        exceptions = rows.BillingConsolidationExceptionRow
        open_exceptions = (
            self._session.execute(
                select(func.count())
                .select_from(exceptions)
                .where(exceptions.org_id == org_id, exceptions.state == "open")
            ).scalar_one()
            or 0
        )
        blocking = (
            self._session.execute(
                select(func.count())
                .select_from(exceptions)
                .where(exceptions.org_id == org_id, exceptions.severity == "blocking")
            ).scalar_one()
            or 0
        )
        definitions = (
            self._session.execute(
                select(func.count())
                .select_from(rows.BillingScopeDefinitionRow)
                .where(rows.BillingScopeDefinitionRow.org_id == org_id)
            ).scalar_one()
            or 0
        )
        return ConsolidationPosture(
            org_id=org_id,
            total_runs=sum(by_status.values()),
            by_status=by_status,
            by_completeness=by_completeness,
            open_exceptions=open_exceptions,
            blocking_exceptions=blocking,
            scope_definitions=definitions,
        )


class SqlSourceBindingRepository(_Repo, SourceBindingRepository):
    """Pinned sources. Append-only — there is no update method and no delete."""

    def bind(self, binding: SourceBinding) -> SourceBinding:
        existing = self._session.get(rows.BillingConsolidationSourceRow, binding.id)
        if existing is not None:
            return decode(SourceBinding, existing.payload)
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingConsolidationSourceRow(
                        payload=encode(binding), **_binding_projection(binding)
                    )
                )
                self._session.flush()
        except IntegrityError:
            # Two workers collected the same batch. The derived id means they read the same source,
            # so one row stands — and the aggregate counts it once rather than twice.
            winner = self._session.get(rows.BillingConsolidationSourceRow, binding.id)
            if winner is None:  # pragma: no cover
                raise
            return decode(SourceBinding, winner.payload)
        return binding

    def get(self, org_id: str, binding_id: str) -> SourceBinding | None:
        row = self._one(
            select(rows.BillingConsolidationSourceRow).where(
                rows.BillingConsolidationSourceRow.org_id == org_id,
                rows.BillingConsolidationSourceRow.id == binding_id,
            )
        )
        return decode(SourceBinding, row.payload) if row else None

    def page(
        self, org_id: str, consolidation_id: str, page: int = 1, page_size: int = 100
    ) -> tuple[tuple[SourceBinding, ...], int]:
        table = rows.BillingConsolidationSourceRow
        filters = [table.org_id == org_id, table.consolidation_id == consolidation_id]
        total = (
            self._session.execute(
                select(func.count()).select_from(table).where(*filters)
            ).scalar_one()
            or 0
        )
        found = self._all(
            select(table)
            .where(*filters)
            .order_by(table.account_id, table.domain)
            .offset(max(0, (max(1, page) - 1) * page_size))
            .limit(page_size)
        )
        return tuple(decode(SourceBinding, r.payload) for r in found), total

    def for_consolidation(
        self, org_id: str, consolidation_id: str, limit: int = 2000
    ) -> tuple[SourceBinding, ...]:
        found = self._all(
            select(rows.BillingConsolidationSourceRow)
            .where(
                rows.BillingConsolidationSourceRow.org_id == org_id,
                rows.BillingConsolidationSourceRow.consolidation_id == consolidation_id,
            )
            .order_by(
                rows.BillingConsolidationSourceRow.account_id,
                rows.BillingConsolidationSourceRow.domain,
            )
            .limit(limit)
        )
        return tuple(decode(SourceBinding, r.payload) for r in found)

    def counts(self, org_id: str, consolidation_id: str) -> dict[str, int]:
        table = rows.BillingConsolidationSourceRow
        return {
            status: count
            for status, count in self._session.execute(
                select(table.status, func.count())
                .where(table.org_id == org_id, table.consolidation_id == consolidation_id)
                .group_by(table.status)
            ).all()
        }

    def bound_accounts(self, org_id: str, consolidation_id: str) -> int:
        table = rows.BillingConsolidationSourceRow
        return (
            self._session.execute(
                select(func.count(func.distinct(table.account_id))).where(
                    table.org_id == org_id, table.consolidation_id == consolidation_id
                )
            ).scalar_one()
            or 0
        )


class SqlConsolidationExceptionRepository(_Repo, ConsolidationExceptionRepository):
    """Named problems. **No delete method exists**, and acknowledgement writes four columns."""

    def raise_exception(self, exception: ConsolidationException) -> ConsolidationException:
        existing = self._session.get(rows.BillingConsolidationExceptionRow, exception.id)
        if existing is not None:
            return decode(ConsolidationException, existing.payload)
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingConsolidationExceptionRow(
                        payload=encode(exception), **_exception_projection(exception)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingConsolidationExceptionRow, exception.id)
            if winner is None:  # pragma: no cover
                raise
            return decode(ConsolidationException, winner.payload)
        return exception

    def get(self, org_id: str, exception_id: str) -> ConsolidationException | None:
        row = self._one(
            select(rows.BillingConsolidationExceptionRow).where(
                rows.BillingConsolidationExceptionRow.org_id == org_id,
                rows.BillingConsolidationExceptionRow.id == exception_id,
            )
        )
        return decode(ConsolidationException, row.payload) if row else None

    def acknowledge(self, exception: ConsolidationException) -> ConsolidationException:
        """Writes the acknowledgement columns and the payload. **The kind, severity, subject and
        diagnostic are never amended**, and the row is never removed."""
        table = rows.BillingConsolidationExceptionRow.__table__
        self._session.execute(
            table.update()
            .where(table.c.id == exception.id, table.c.org_id == exception.org_id)
            .values(
                payload=encode(exception),
                state=exception.state.value,
                acknowledged_at=exception.acknowledged_at,
                acknowledged_by=exception.acknowledged_by,
                acknowledgement_note=exception.acknowledgement_note,
            )
        )
        self._session.expire_all()
        return exception

    def for_consolidation(
        self, org_id: str, consolidation_id: str, limit: int = 500
    ) -> tuple[ConsolidationException, ...]:
        found = self._all(
            select(rows.BillingConsolidationExceptionRow)
            .where(
                rows.BillingConsolidationExceptionRow.org_id == org_id,
                rows.BillingConsolidationExceptionRow.consolidation_id == consolidation_id,
            )
            .order_by(
                rows.BillingConsolidationExceptionRow.severity,
                rows.BillingConsolidationExceptionRow.created_at,
            )
            .limit(limit)
        )
        return tuple(decode(ConsolidationException, r.payload) for r in found)

    def blocking_count(self, org_id: str, consolidation_id: str) -> int:
        """**Acknowledged blocking exceptions are counted.** Somebody having looked at a gap is a
        different fact from the gap being closed, and only the second may unblock a run."""
        table = rows.BillingConsolidationExceptionRow
        return (
            self._session.execute(
                select(func.count()).where(
                    table.org_id == org_id,
                    table.consolidation_id == consolidation_id,
                    table.severity == "blocking",
                )
            ).scalar_one()
            or 0
        )


class SqlConsolidationValidationRepository(_Repo, ConsolidationValidationRepository):
    def record(self, validation: ConsolidationValidation) -> ConsolidationValidation:
        row_id = f"{validation.consolidation_id}:verdict"
        existing = self._session.get(rows.BillingConsolidationValidationRow, row_id)
        values = {
            "passed": validation.passed,
            "check_count": len(validation.checks),
            "failure_count": len(validation.failures),
            "validated_at": validation.validated_at,
            "validated_by": validation.validated_by,
            "payload": encode(validation),
        }
        if existing is not None:
            table = rows.BillingConsolidationValidationRow.__table__
            self._session.execute(
                table.update().where(table.c.id == row_id).values(**values)
            )
            self._session.expire_all()
            return validation
        self._add(
            rows.BillingConsolidationValidationRow(
                id=row_id,
                org_id=validation.org_id,
                consolidation_id=validation.consolidation_id,
                **values,
            )
        )
        return validation

    def current(self, org_id: str, consolidation_id: str) -> ConsolidationValidation | None:
        row = self._one(
            select(rows.BillingConsolidationValidationRow).where(
                rows.BillingConsolidationValidationRow.org_id == org_id,
                rows.BillingConsolidationValidationRow.consolidation_id == consolidation_id,
            )
        )
        return decode(ConsolidationValidation, row.payload) if row else None


class SqlConsolidationSnapshotRepository(_Repo, ConsolidationSnapshotRepository):
    """Finalized artifacts. **No update, no delete** — there is no method to call."""

    def create(self, snapshot: ConsolidationSnapshot) -> ConsolidationSnapshot:
        existing = self._session.get(rows.BillingConsolidationResultRow, snapshot.id)
        if existing is not None:
            return decode(ConsolidationSnapshot, existing.payload)
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingConsolidationResultRow(
                        id=snapshot.id,
                        org_id=snapshot.org_id,
                        consolidation_id=snapshot.consolidation_id,
                        consolidation_digest=snapshot.consolidation_digest,
                        scope_snapshot_id=snapshot.scope_snapshot_id,
                        scope_snapshot_digest=snapshot.scope_snapshot_digest,
                        period_key=snapshot.period_key,
                        source_digest=snapshot.source_digest,
                        completeness=snapshot.completeness.value,
                        representation=snapshot.representation,
                        finalized_at=snapshot.finalized_at,
                        finalized_by=snapshot.finalized_by,
                        payload=encode(snapshot),
                    )
                )
                self._session.flush()
        except IntegrityError:
            # Unique on `(org, consolidation)`: a run finalizes once, whichever worker got there.
            winner = self._one(
                select(rows.BillingConsolidationResultRow).where(
                    rows.BillingConsolidationResultRow.org_id == snapshot.org_id,
                    rows.BillingConsolidationResultRow.consolidation_id
                    == snapshot.consolidation_id,
                )
            )
            if winner is None:  # pragma: no cover
                raise
            return decode(ConsolidationSnapshot, winner.payload)
        return snapshot

    def get(self, org_id: str, snapshot_id: str) -> ConsolidationSnapshot | None:
        row = self._one(
            select(rows.BillingConsolidationResultRow).where(
                rows.BillingConsolidationResultRow.org_id == org_id,
                rows.BillingConsolidationResultRow.id == snapshot_id,
            )
        )
        return decode(ConsolidationSnapshot, row.payload) if row else None

    def for_consolidation(
        self, org_id: str, consolidation_id: str
    ) -> ConsolidationSnapshot | None:
        row = self._one(
            select(rows.BillingConsolidationResultRow).where(
                rows.BillingConsolidationResultRow.org_id == org_id,
                rows.BillingConsolidationResultRow.consolidation_id == consolidation_id,
            )
        )
        return decode(ConsolidationSnapshot, row.payload) if row else None


class SqlConsolidationEventRepository(_Repo, ConsolidationEventRepository):
    """The trail. Append-only; there is no update and no delete."""

    def append(self, event: ConsolidationEvent) -> ConsolidationEvent:
        existing = self._session.get(rows.BillingConsolidationEventRow, event.id)
        if existing is not None:
            return decode(ConsolidationEvent, existing.payload)
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingConsolidationEventRow(
                        id=event.id,
                        org_id=event.org_id,
                        consolidation_id=event.consolidation_id,
                        kind=event.kind.value,
                        sequence=event.sequence,
                        subject=event.subject,
                        detail=event.detail,
                        actor=event.actor,
                        occurred_at=event.occurred_at,
                        payload=encode(event),
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingConsolidationEventRow, event.id)
            if winner is None:  # pragma: no cover
                # Two workers raced for one sequence number. The trail is append-only and the
                # sequence is unique, so the loser retries at the next position rather than
                # overwriting the winner's entry.
                raise
            return decode(ConsolidationEvent, winner.payload)
        return event

    def timeline(
        self, org_id: str, consolidation_id: str, limit: int = 500
    ) -> tuple[ConsolidationEvent, ...]:
        found = self._all(
            select(rows.BillingConsolidationEventRow)
            .where(
                rows.BillingConsolidationEventRow.org_id == org_id,
                rows.BillingConsolidationEventRow.consolidation_id == consolidation_id,
            )
            .order_by(rows.BillingConsolidationEventRow.sequence)
            .limit(limit)
        )
        return tuple(decode(ConsolidationEvent, r.payload) for r in found)

    def next_sequence(self, org_id: str, consolidation_id: str) -> int:
        table = rows.BillingConsolidationEventRow
        highest = self._session.execute(
            select(func.max(table.sequence)).where(
                table.org_id == org_id, table.consolidation_id == consolidation_id
            )
        ).scalar_one_or_none()
        return int(highest or 0) + 1


__all__ = [
    "SqlConsolidationEventRepository",
    "SqlConsolidationExceptionRepository",
    "SqlConsolidationPeriodRepository",
    "SqlConsolidationRepository",
    "SqlConsolidationSnapshotRepository",
    "SqlConsolidationValidationRepository",
    "SqlScopeDefinitionRepository",
    "SqlScopeSnapshotRepository",
    "SqlSourceBindingRepository",
]
