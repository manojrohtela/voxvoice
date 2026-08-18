"""Reconciliation repositories (M9.19) — additive to the M9.4 persistence layer.

Five SQL implementations of the M9.19 Foundation contracts, reusing the M9.4 machinery unchanged:
the same `_Repo` session handle, the same codec, the same projection-plus-payload mapping, the same
"never commit, never roll back" rule, and the same org-scoped-by-construction reads (§A11.2).

## The two race-critical writes

`SqlIntegrityRunRepository.create` is an insert on an id derived from the **evidence digest**,
defended the three ways every idempotent write since M9.11 has been: a derived id, a
read-before-write, and a `SAVEPOINT` whose `IntegrityError` becomes a read of the winner rather than
a destroyed transaction. Two workers reconciling the same facts therefore produce one run, and the
loser reads the winner's row instead of recording a second opinion about the same evidence.

`SqlIntegrityFindingRepository.advance` is **the stale-worker defence**, and it is the most
important method in the milestone: `UPDATE ... WHERE id = ? AND version = ?`. Worker A reads a
finding as `open`, worker B resolves it and commits, and worker A comes back to write — zero rows
match, and it raises `FindingStale` rather than silently replacing a resolution with a stale row.

The domain already makes the *transition* impossible: `FINDING_LIFECYCLE` has no edge out of
`resolved` or `closed`. This makes losing the *race* impossible too. Both are needed, because the
FSM protects against a worker that re-reads and the compare-and-set protects against one that does
not.

## What no repository here does

None decides a state, compares two facts, chooses a severity, computes a balance, or sums an amount.
And **none writes to a ledger, invoice, payment, tax, recognition, collection or subscription
table** — an architecture test scans the write targets of the whole Application to prove it. M9.19
reads those domains through their own seams and repairs none of them.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from billing.foundation import (
    FindingStale,
    IntegrityActivity,
    IntegrityActivityRepository,
    IntegrityFinding,
    IntegrityFindingRepository,
    IntegrityResolution,
    IntegrityResolutionRepository,
    IntegrityRun,
    IntegrityRunRepository,
    RepairRequest,
    RepairRequestRepository,
)

from . import reconciliation_models as rows
from .codec import encode
from .mapping import from_row, update_row
from .repositories import _Repo

#: Unresolved states, as stored strings. Declared once here so the queue query and the Foundation's
#: `UNRESOLVED_FINDING_STATES` cannot drift; a test asserts they name the same set.
_UNRESOLVED = ("open", "acknowledged", "repair_requested")

#: Worst first. The operator queue orders by this rather than by time, because a critical divergence
#: found this morning matters more than a warning found last week.
_SEVERITY_RANK = {"critical": 0, "error": 1, "warning": 2, "info": 3}


def _run_projection(r: IntegrityRun) -> dict:
    return {
        "id": r.id,
        "org_id": r.org_id,
        "relationship": r.scope.relationship.value,
        "subject_id": r.scope.subject_id,
        "period_key": r.scope.period_key,
        "scope_key": r.scope.key,
        "evidence_digest": r.evidence_digest,
        "state": r.state.value,
        "compared": r.compared,
        "matched": r.matched,
        "diverged": r.diverged,
        "max_severity": r.max_severity.value,
        "started_at": r.started_at,
        "completed_at": r.completed_at,
        "triggered_by": r.triggered_by,
    }


def _finding_projection(f: IntegrityFinding) -> dict:
    source = f.source
    counterpart = f.counterpart
    currency = ""
    for ref in (source, counterpart):
        if ref is not None and ref.amount is not None:
            currency = ref.amount.currency.value
            break
    return {
        "id": f.id,
        "org_id": f.org_id,
        "run_id": f.run_id,
        "relationship": f.relationship.value,
        "subject_key": f.subject_key,
        "divergence": f.divergence.value,
        "severity": f.severity.value,
        "owner": f.owner.value,
        "state": f.state.value,
        "verdict": f.verdict.value,
        "account_id": f.account_id,
        "source_identity": source.identity if source else "",
        "counterpart_identity": counterpart.identity if counterpart else "",
        # Copies of the two figures that disagreed, kept so the finding stays legible. Never summed.
        "source_amount_minor": (
            source.amount.minor if source is not None and source.amount is not None else None
        ),
        "counterpart_amount_minor": (
            counterpart.amount.minor
            if counterpart is not None and counterpart.amount is not None
            else None
        ),
        "currency": currency,
        "detected_at": f.detected_at,
        "acknowledged_at": f.acknowledged_at,
        "resolved_at": f.resolved_at,
        "resolved_by": f.resolved_by,
        "repair_request_id": f.repair_request_id,
        "resolution_id": f.resolution_id,
        "version": f.version,
    }


def _request_projection(r: RepairRequest) -> dict:
    return {
        "id": r.id,
        "org_id": r.org_id,
        "finding_id": r.finding_id,
        "owner": r.owner.value,
        "action": r.action,
        "request_key": r.request_key,
        "dispatched_command": r.dispatched_command,
        "accepted": r.accepted,
        "requested_at": r.requested_at,
        "requested_by": r.requested_by,
    }


def _resolution_projection(r: IntegrityResolution) -> dict:
    return {
        "id": r.id,
        "org_id": r.org_id,
        "finding_id": r.finding_id,
        "run_id": r.run_id,
        "owner": r.owner.value,
        "action": r.action,
        "request_key": r.request_key,
        "repair_request_id": r.repair_request_id,
        "source_command_id": r.source_command_id,
        "evidence_digest": r.evidence_digest,
        "resolved_at": r.resolved_at,
        "resolved_by": r.resolved_by,
    }


def _activity_projection(a: IntegrityActivity) -> dict:
    return {
        "id": a.id,
        "org_id": a.org_id,
        "run_id": a.run_id,
        "at": a.at,
        "kind": a.kind,
        "actor_id": a.actor_id,
        "finding_id": a.finding_id,
    }


class SqlIntegrityRunRepository(_Repo, IntegrityRunRepository):
    """Runs, arbitrated by the evidence digest. There is no delete method, and that is the
    contract: a run is the record that somebody looked."""

    def create(self, run: IntegrityRun) -> tuple[IntegrityRun, bool]:
        existing = self._session.get(rows.BillingIntegrityRunRow, run.id)
        if existing is not None:
            return from_row(IntegrityRun, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingIntegrityRunRow(payload=encode(run), **_run_projection(run))
                )
                self._session.flush()
        except IntegrityError:
            # Another worker read the same evidence between this read and this insert. The evidence
            # digest guarantees they looked at exactly the same facts, so this caller reads the
            # winner's run rather than recording a second opinion about the same books.
            winner = self._session.get(rows.BillingIntegrityRunRow, run.id)
            if winner is None:  # pragma: no cover — the primary key guarantees a row
                raise
            return from_row(IntegrityRun, winner), False
        return run, True

    def complete(self, run: IntegrityRun) -> IntegrityRun:
        row = self._session.get(rows.BillingIntegrityRunRow, run.id)
        if row is None:
            return self.create(run)[0]
        update_row(row, run, _run_projection(run))
        self._session.flush()
        return run

    def get(self, org_id: str, run_id: str) -> IntegrityRun | None:
        row = self._one(
            select(rows.BillingIntegrityRunRow).where(
                rows.BillingIntegrityRunRow.org_id == org_id,
                rows.BillingIntegrityRunRow.id == run_id,
            )
        )
        return from_row(IntegrityRun, row) if row else None

    def recent(
        self, org_id: str, relationship: str = "", limit: int = 100
    ) -> tuple[IntegrityRun, ...]:
        statement = select(rows.BillingIntegrityRunRow).where(
            rows.BillingIntegrityRunRow.org_id == org_id
        )
        if relationship:
            statement = statement.where(
                rows.BillingIntegrityRunRow.relationship == relationship
            )
        found = self._all(
            statement.order_by(rows.BillingIntegrityRunRow.started_at.desc()).limit(limit)
        )
        return tuple(from_row(IntegrityRun, r) for r in found)

    def for_subject(
        self, org_id: str, subject_id: str, limit: int = 100
    ) -> tuple[IntegrityRun, ...]:
        found = self._all(
            select(rows.BillingIntegrityRunRow)
            .where(
                rows.BillingIntegrityRunRow.org_id == org_id,
                rows.BillingIntegrityRunRow.subject_id == subject_id,
            )
            .order_by(rows.BillingIntegrityRunRow.started_at.desc())
            .limit(limit)
        )
        return tuple(from_row(IntegrityRun, r) for r in found)


class SqlIntegrityFindingRepository(_Repo, IntegrityFindingRepository):
    """Divergences. Append-only apart from the one conditional lifecycle move."""

    def append(self, finding: IntegrityFinding) -> tuple[IntegrityFinding, bool]:
        existing = self._session.get(rows.BillingIntegrityFindingRow, finding.id)
        if existing is not None:
            return from_row(IntegrityFinding, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingIntegrityFindingRow(
                        payload=encode(finding), **_finding_projection(finding)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingIntegrityFindingRow, finding.id)
            if winner is None:  # pragma: no cover — the primary key guarantees a row
                raise
            return from_row(IntegrityFinding, winner), False
        return finding, True

    def advance(self, finding: IntegrityFinding, *, expected_version: int) -> IntegrityFinding:
        """Compare-and-set on `version`. **The stale-worker defence.**

        A filtered UPDATE rather than a read followed by a write, because the read would be exactly
        the stale read the check exists to catch. Zero rows is not retryable: the caller must
        re-read, and when it does it will find a finding the FSM refuses to move at all, which is
        the correct outcome — somebody else already settled this divergence."""
        result = self._session.execute(
            rows.BillingIntegrityFindingRow.__table__.update()
            .where(
                rows.BillingIntegrityFindingRow.__table__.c.id == finding.id,
                rows.BillingIntegrityFindingRow.__table__.c.org_id == finding.org_id,
                rows.BillingIntegrityFindingRow.__table__.c.version == expected_version,
            )
            .values(payload=encode(finding), **_finding_projection(finding))
        )
        if result.rowcount != 1:
            stored = self._session.get(rows.BillingIntegrityFindingRow, finding.id)
            current = stored.state if stored is not None else "missing"
            raise FindingStale(
                f"integrity finding {finding.id!r} moved while this worker was deciding "
                f"(expected version {expected_version}, found state {current!r}); "
                "another worker settled it — re-read rather than overwrite"
            )
        self._session.expire_all()
        return finding

    def get(self, org_id: str, finding_id: str) -> IntegrityFinding | None:
        row = self._one(
            select(rows.BillingIntegrityFindingRow).where(
                rows.BillingIntegrityFindingRow.org_id == org_id,
                rows.BillingIntegrityFindingRow.id == finding_id,
            )
        )
        return from_row(IntegrityFinding, row) if row else None

    def for_run(self, org_id: str, run_id: str) -> tuple[IntegrityFinding, ...]:
        found = self._all(
            select(rows.BillingIntegrityFindingRow)
            .where(
                rows.BillingIntegrityFindingRow.org_id == org_id,
                rows.BillingIntegrityFindingRow.run_id == run_id,
            )
            .order_by(rows.BillingIntegrityFindingRow.detected_at)
        )
        return tuple(from_row(IntegrityFinding, r) for r in found)

    def open_findings(
        self, org_id: str, severities: tuple[str, ...] = (), limit: int = 200
    ) -> tuple[IntegrityFinding, ...]:
        """**The operator queue, and the read an external scheduler polls.**

        Indexed on `(org_id, state, severity)` and bounded. Sorted worst-first in Python over an
        already-bounded page rather than by a CASE expression, so the ordering is one declared table
        (`_SEVERITY_RANK`) instead of SQL that differs per dialect."""
        statement = select(rows.BillingIntegrityFindingRow).where(
            rows.BillingIntegrityFindingRow.org_id == org_id,
            rows.BillingIntegrityFindingRow.state.in_(_UNRESOLVED),
        )
        if severities:
            statement = statement.where(
                rows.BillingIntegrityFindingRow.severity.in_(severities)
            )
        found = self._all(
            statement.order_by(rows.BillingIntegrityFindingRow.detected_at.desc()).limit(limit)
        )
        ordered = sorted(found, key=lambda r: (_SEVERITY_RANK.get(r.severity, 9), -r.detected_at))
        return tuple(from_row(IntegrityFinding, r) for r in ordered)

    def by_owner(self, org_id: str, owner: str, limit: int = 200) -> tuple[IntegrityFinding, ...]:
        found = self._all(
            select(rows.BillingIntegrityFindingRow)
            .where(
                rows.BillingIntegrityFindingRow.org_id == org_id,
                rows.BillingIntegrityFindingRow.owner == owner,
                rows.BillingIntegrityFindingRow.state.in_(_UNRESOLVED),
            )
            .order_by(rows.BillingIntegrityFindingRow.detected_at.desc())
            .limit(limit)
        )
        return tuple(from_row(IntegrityFinding, r) for r in found)

    def for_account(
        self, org_id: str, account_id: str, limit: int = 200
    ) -> tuple[IntegrityFinding, ...]:
        found = self._all(
            select(rows.BillingIntegrityFindingRow)
            .where(
                rows.BillingIntegrityFindingRow.org_id == org_id,
                rows.BillingIntegrityFindingRow.account_id == account_id,
            )
            .order_by(rows.BillingIntegrityFindingRow.detected_at.desc())
            .limit(limit)
        )
        return tuple(from_row(IntegrityFinding, r) for r in found)

    def all(self, org_id: str, limit: int = 500) -> tuple[IntegrityFinding, ...]:
        found = self._all(
            select(rows.BillingIntegrityFindingRow)
            .where(rows.BillingIntegrityFindingRow.org_id == org_id)
            .order_by(rows.BillingIntegrityFindingRow.detected_at.desc())
            .limit(limit)
        )
        return tuple(from_row(IntegrityFinding, r) for r in found)


class SqlRepairRequestRepository(_Repo, RepairRequestRepository):
    """Requests. Append-only; there is no update and no delete."""

    def append(self, request: RepairRequest) -> tuple[RepairRequest, bool]:
        existing = self._session.get(rows.BillingIntegrityRepairRequestRow, request.id)
        if existing is not None:
            return from_row(RepairRequest, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingIntegrityRepairRequestRow(
                        payload=encode(request), **_request_projection(request)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingIntegrityRepairRequestRow, request.id)
            if winner is None:  # pragma: no cover — the primary key guarantees a row
                raise
            return from_row(RepairRequest, winner), False
        return request, True

    def get(self, org_id: str, request_id: str) -> RepairRequest | None:
        row = self._one(
            select(rows.BillingIntegrityRepairRequestRow).where(
                rows.BillingIntegrityRepairRequestRow.org_id == org_id,
                rows.BillingIntegrityRepairRequestRow.id == request_id,
            )
        )
        return from_row(RepairRequest, row) if row else None

    def for_finding(self, org_id: str, finding_id: str) -> tuple[RepairRequest, ...]:
        found = self._all(
            select(rows.BillingIntegrityRepairRequestRow)
            .where(
                rows.BillingIntegrityRepairRequestRow.org_id == org_id,
                rows.BillingIntegrityRepairRequestRow.finding_id == finding_id,
            )
            .order_by(rows.BillingIntegrityRepairRequestRow.requested_at)
        )
        return tuple(from_row(RepairRequest, r) for r in found)


class SqlIntegrityResolutionRepository(_Repo, IntegrityResolutionRepository):
    """Resolution evidence. **Append-only; there is no update path at all.**

    This is what keeps historical divergence auditable: a resolution is a new row pointing at a
    finding, never an edit that makes the finding look like it never happened."""

    def append(self, resolution: IntegrityResolution) -> tuple[IntegrityResolution, bool]:
        existing = self._session.get(rows.BillingIntegrityResolutionRow, resolution.id)
        if existing is not None:
            return from_row(IntegrityResolution, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingIntegrityResolutionRow(
                        payload=encode(resolution), **_resolution_projection(resolution)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingIntegrityResolutionRow, resolution.id)
            if winner is None:  # pragma: no cover — the primary key guarantees a row
                raise
            return from_row(IntegrityResolution, winner), False
        return resolution, True

    def get(self, org_id: str, resolution_id: str) -> IntegrityResolution | None:
        row = self._one(
            select(rows.BillingIntegrityResolutionRow).where(
                rows.BillingIntegrityResolutionRow.org_id == org_id,
                rows.BillingIntegrityResolutionRow.id == resolution_id,
            )
        )
        return from_row(IntegrityResolution, row) if row else None

    def for_finding(self, org_id: str, finding_id: str) -> tuple[IntegrityResolution, ...]:
        found = self._all(
            select(rows.BillingIntegrityResolutionRow)
            .where(
                rows.BillingIntegrityResolutionRow.org_id == org_id,
                rows.BillingIntegrityResolutionRow.finding_id == finding_id,
            )
            .order_by(rows.BillingIntegrityResolutionRow.resolved_at)
        )
        return tuple(from_row(IntegrityResolution, r) for r in found)


class SqlIntegrityActivityRepository(_Repo, IntegrityActivityRepository):
    """The audit trail. Append-only."""

    def append(self, activity: IntegrityActivity) -> IntegrityActivity:
        self._session.add(
            rows.BillingIntegrityActivityRow(
                payload=encode(activity), **_activity_projection(activity)
            )
        )
        self._session.flush()
        return activity

    def for_run(
        self, org_id: str, run_id: str, limit: int = 200
    ) -> tuple[IntegrityActivity, ...]:
        found = self._all(
            select(rows.BillingIntegrityActivityRow)
            .where(
                rows.BillingIntegrityActivityRow.org_id == org_id,
                rows.BillingIntegrityActivityRow.run_id == run_id,
            )
            .order_by(rows.BillingIntegrityActivityRow.at)
            .limit(limit)
        )
        return tuple(from_row(IntegrityActivity, r) for r in found)


__all__ = [
    "SqlIntegrityActivityRepository",
    "SqlIntegrityFindingRepository",
    "SqlIntegrityResolutionRepository",
    "SqlIntegrityRunRepository",
    "SqlRepairRequestRepository",
]
