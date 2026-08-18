"""Financial Integrity Correlation repositories (M9.20) — additive to the M9.4 persistence layer.

Six SQL implementations of the M9.20 Foundation contracts, reusing the M9.4 machinery unchanged: the
same `_Repo` session handle, the same codec, the same projection-plus-payload mapping, the same
"never commit, never roll back" rule, and the same org-scoped-by-construction reads (§A11.2).

## The three race-critical writes

`SqlIncidentRepository.create` is an insert on an id derived from the **correlation key**, defended
the three ways every idempotent write since M9.11 has been: a derived id, a read-before-write, and a
`SAVEPOINT` whose `IntegrityError` becomes a read of the winner rather than a destroyed transaction.
Two workers correlating the same finding therefore open one incident.

`SqlIncidentRepository.advance` is the compare-and-set on `version` — **the stale-worker defence**.
Worker A reads `OPEN` version 3, worker B acknowledges to version 4, worker A resumes and writes:
zero rows match, and it raises `IncidentStale` rather than silently overwriting a conclusion
somebody else reached.

`SqlFindingLinkRepository.attach` is **the duplicate-attachment defence**: the same finding
delivered twice attaches once, because the id is derived from `(incident, finding)` and the unique
constraint says the same thing a second way.

## Pagination is a database concern

`page()` issues a `COUNT` and a `LIMIT/OFFSET` against an index, rather than loading every incident
an org ever had and slicing in Python. At the scale this milestone assumes — organizations ×
invoices × payments × ledger entries × recognition periods × findings × incidents — an in-memory
filter is not a performance detail, it is an outage.

## What no repository here does

None correlates, classifies a root cause, decides a severity, or verifies anything. And **none
writes to a reconciliation, ledger, invoice, tax, payment, recognition, collection or subscription
table** — an architecture test scans the write targets of the whole Application to prove it. M9.20
reads those domains through their own seams and repairs none of them.
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from billing.foundation import (
    CorrelationCorrection,
    CorrelationCorrectionRepository,
    FindingLink,
    FindingLinkRepository,
    IncidentEvent,
    IncidentEventRepository,
    IncidentMerge,
    IncidentMergeRepository,
    IncidentRepair,
    IncidentRepairRepository,
    IncidentRepository,
    IncidentStale,
    IntegrityIncident,
)

from . import incident_models as rows
from .codec import encode
from .mapping import from_row, update_row
from .repositories import _Repo

#: Live states, as stored strings. Declared once here so the queue query and the Foundation's
#: `LIVE_INCIDENT_STATES` cannot drift; a test asserts they name the same set.
_LIVE = ("open", "acknowledged", "investigating", "repair_in_progress", "verifying")

#: Worst first. The operator queue orders by this rather than by time, because a critical incident
#: found this morning matters more than a warning found last week.
_SEVERITY_RANK = {"critical": 0, "error": 1, "warning": 2, "info": 3}


def _incident_projection(i: IntegrityIncident) -> dict:
    return {
        "id": i.id,
        "org_id": i.org_id,
        "correlation_key": i.correlation_key,
        "anchor_kind": i.anchor_kind.value,
        "anchor_value": i.anchor_value,
        "status": i.status.value,
        "severity": i.severity.value,
        "root_cause": i.root_cause.value,
        "impact": i.impact.value,
        "finding_count": i.finding_count,
        "unresolved_findings": i.unresolved_findings,
        "account_id": i.account_id,
        "evidence_digest": i.evidence_digest,
        "canonical_id": i.canonical_id,
        "first_detected_at": i.first_detected_at,
        "last_observed_at": i.last_observed_at,
        "closed_at": i.closed_at,
        "version": i.version,
    }


def _link_projection(link: FindingLink) -> dict:
    return {
        "id": link.id,
        "org_id": link.org_id,
        "incident_id": link.incident_id,
        "finding_id": link.finding_id,
        "run_id": link.run_id,
        "relationship": link.relationship.value,
        "divergence": link.divergence.value,
        "severity": link.severity.value,
        "anchor_kind": link.anchor_kind.value,
        "anchor_value": link.anchor_value,
        "edge": link.edge.value,
        "subject_key": link.subject_key,
        "attached_at": link.attached_at,
        "attached_by": link.attached_by,
        "detached_at": link.detached_at,
    }


def _event_projection(e: IncidentEvent) -> dict:
    return {
        "id": e.id,
        "org_id": e.org_id,
        "incident_id": e.incident_id,
        "sequence": e.sequence,
        "kind": e.kind.value,
        "at": e.at,
        "actor_id": e.actor_id,
        "finding_id": e.finding_id,
        "subject": e.subject,
    }


def _merge_projection(m: IncidentMerge) -> dict:
    return {
        "id": m.id,
        "org_id": m.org_id,
        "canonical_id": m.canonical_id,
        "merged_id": m.merged_id,
        "anchor_kind": m.anchor_kind.value,
        "decided_at": m.decided_at,
        "decided_by": m.decided_by,
    }


def _correction_projection(c: CorrelationCorrection) -> dict:
    return {
        "id": c.id,
        "org_id": c.org_id,
        "finding_id": c.finding_id,
        "previous_incident_id": c.previous_incident_id,
        "previous_key": c.previous_key,
        "corrected_incident_id": c.corrected_incident_id,
        "corrected_key": c.corrected_key,
        "corrected_at": c.corrected_at,
        "corrected_by": c.corrected_by,
    }


def _repair_projection(r: IncidentRepair) -> dict:
    return {
        "id": r.id,
        "org_id": r.org_id,
        "incident_id": r.incident_id,
        "owner": r.owner.value,
        "action": r.action,
        "request_key": r.request_key,
        "finding_id": r.finding_id,
        "reference": r.reference,
        "observed": r.observed,
        "observed_at": r.observed_at,
        "requested_at": r.requested_at,
        "requested_by": r.requested_by,
    }


class SqlIncidentRepository(_Repo, IncidentRepository):
    """Incidents, arbitrated by the correlation key. There is no delete, and that is the contract:
    an incident is the record that Billing noticed."""

    def create(self, incident: IntegrityIncident) -> tuple[IntegrityIncident, bool]:
        existing = self._session.get(rows.BillingIntegrityIncidentRow, incident.id)
        if existing is not None:
            return from_row(IntegrityIncident, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingIntegrityIncidentRow(
                        payload=encode(incident), **_incident_projection(incident)
                    )
                )
                self._session.flush()
        except IntegrityError:
            # Another worker correlated the same finding between this read and this insert. The
            # correlation key guarantees they computed the same incident, so this caller reads the
            # winner's rather than opening a second one about the same cause.
            winner = self._session.get(rows.BillingIntegrityIncidentRow, incident.id)
            if winner is None:  # pragma: no cover — the primary key guarantees a row
                raise
            return from_row(IntegrityIncident, winner), False
        return incident, True

    def advance(
        self, incident: IntegrityIncident, *, expected_version: int
    ) -> IntegrityIncident:
        """Compare-and-set on `version`. **The stale-worker defence.**

        A filtered UPDATE rather than a read followed by a write, because the read would be exactly
        the stale read the check exists to catch. Zero rows is not retryable: the caller must
        re-read, and when it does it may find an incident the FSM refuses to move at all — which is
        the correct outcome, because somebody else already settled it."""
        result = self._session.execute(
            rows.BillingIntegrityIncidentRow.__table__.update()
            .where(
                rows.BillingIntegrityIncidentRow.__table__.c.id == incident.id,
                rows.BillingIntegrityIncidentRow.__table__.c.org_id == incident.org_id,
                rows.BillingIntegrityIncidentRow.__table__.c.version == expected_version,
            )
            .values(payload=encode(incident), **_incident_projection(incident))
        )
        if result.rowcount != 1:
            stored = self._session.get(rows.BillingIntegrityIncidentRow, incident.id)
            current = stored.status if stored is not None else "missing"
            raise IncidentStale(
                f"integrity incident {incident.id!r} moved while this worker was deciding "
                f"(expected version {expected_version}, found status {current!r}); "
                "another worker settled it — re-read rather than overwrite"
            )
        self._session.expire_all()
        return incident

    def get(self, org_id: str, incident_id: str) -> IntegrityIncident | None:
        row = self._one(
            select(rows.BillingIntegrityIncidentRow).where(
                rows.BillingIntegrityIncidentRow.org_id == org_id,
                rows.BillingIntegrityIncidentRow.id == incident_id,
            )
        )
        return from_row(IntegrityIncident, row) if row else None

    def by_correlation_key(self, org_id: str, key: str) -> IntegrityIncident | None:
        row = self._one(
            select(rows.BillingIntegrityIncidentRow).where(
                rows.BillingIntegrityIncidentRow.org_id == org_id,
                rows.BillingIntegrityIncidentRow.correlation_key == key,
            )
        )
        return from_row(IntegrityIncident, row) if row else None

    def page(
        self,
        org_id: str,
        *,
        statuses: tuple[str, ...] = (),
        severities: tuple[str, ...] = (),
        account_id: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[tuple[IntegrityIncident, ...], int]:
        """**One page and the total, both from the database.**

        Filtering in Python would mean loading every incident an org ever had to return fifty, which
        at this milestone's assumed scale is not a performance detail but an outage."""
        table = rows.BillingIntegrityIncidentRow
        filters = [table.org_id == org_id]
        if statuses:
            filters.append(table.status.in_(statuses))
        if severities:
            filters.append(table.severity.in_(severities))
        if account_id:
            filters.append(table.account_id == account_id)
        total = self._session.execute(
            select(func.count()).select_from(table).where(*filters)
        ).scalar_one()
        found = self._all(
            select(table)
            .where(*filters)
            .order_by(table.first_detected_at.desc(), table.id)
            .limit(max(1, limit))
            .offset(max(0, offset))
        )
        return tuple(from_row(IntegrityIncident, r) for r in found), int(total)

    def live(self, org_id: str, limit: int = 200) -> tuple[IntegrityIncident, ...]:
        """Unsettled incidents, worst first. **The read an external scheduler polls** — M9.20 runs
        no scheduler, and whoever decides when to look lives outside this plane."""
        found = self._all(
            select(rows.BillingIntegrityIncidentRow)
            .where(
                rows.BillingIntegrityIncidentRow.org_id == org_id,
                rows.BillingIntegrityIncidentRow.status.in_(_LIVE),
            )
            .order_by(rows.BillingIntegrityIncidentRow.first_detected_at.desc())
            .limit(limit)
        )
        ordered = sorted(
            found, key=lambda r: (_SEVERITY_RANK.get(r.severity, 9), -r.first_detected_at)
        )
        return tuple(from_row(IntegrityIncident, r) for r in ordered)


class SqlFindingLinkRepository(_Repo, FindingLinkRepository):
    """Attachments. Append-only apart from the narrow detach columns; there is no delete."""

    def attach(self, link: FindingLink) -> tuple[FindingLink, bool]:
        existing = self._session.get(rows.BillingIntegrityFindingLinkRow, link.id)
        if existing is not None:
            if existing.detached_at is None:
                return from_row(FindingLink, existing), False
            # **A detached link can legitimately re-attach.** A finding detached from incident A by
            # a correlation correction can correlate back to A once the evidence moves again, and
            # returning the stored *detached* row would leave it attached to nothing. The detach is
            # not erased — it is already an immutable line in the audit trail — so reporting this as
            # new is what makes the re-attachment appear there too.
            update_row(existing, link, _link_projection(link))
            self._session.flush()
            return link, True
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingIntegrityFindingLinkRow(
                        payload=encode(link), **_link_projection(link)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingIntegrityFindingLinkRow, link.id)
            if winner is None:  # pragma: no cover — the primary key guarantees a row
                raise
            return from_row(FindingLink, winner), False
        return link, True

    def detach(self, link: FindingLink) -> FindingLink:
        """**The row is never deleted.** "This finding was once believed to belong here" is
        precisely what an auditor asks about after a correlation correction."""
        row = self._session.get(rows.BillingIntegrityFindingLinkRow, link.id)
        if row is None:  # pragma: no cover — the caller read it first
            return self.attach(link)[0]
        update_row(row, link, _link_projection(link))
        self._session.flush()
        return link

    def get(self, org_id: str, link_id: str) -> FindingLink | None:
        row = self._one(
            select(rows.BillingIntegrityFindingLinkRow).where(
                rows.BillingIntegrityFindingLinkRow.org_id == org_id,
                rows.BillingIntegrityFindingLinkRow.id == link_id,
            )
        )
        return from_row(FindingLink, row) if row else None

    def for_incident(self, org_id: str, incident_id: str) -> tuple[FindingLink, ...]:
        found = self._all(
            select(rows.BillingIntegrityFindingLinkRow)
            .where(
                rows.BillingIntegrityFindingLinkRow.org_id == org_id,
                rows.BillingIntegrityFindingLinkRow.incident_id == incident_id,
            )
            .order_by(rows.BillingIntegrityFindingLinkRow.attached_at)
        )
        return tuple(from_row(FindingLink, r) for r in found)

    def for_finding(self, org_id: str, finding_id: str) -> tuple[FindingLink, ...]:
        found = self._all(
            select(rows.BillingIntegrityFindingLinkRow)
            .where(
                rows.BillingIntegrityFindingLinkRow.org_id == org_id,
                rows.BillingIntegrityFindingLinkRow.finding_id == finding_id,
            )
            .order_by(rows.BillingIntegrityFindingLinkRow.attached_at)
        )
        return tuple(from_row(FindingLink, r) for r in found)


class SqlIncidentEventRepository(_Repo, IncidentEventRepository):
    """The audit trail. **Append-only; there is no update and no delete.**"""

    def append(self, event: IncidentEvent) -> tuple[IncidentEvent, bool]:
        existing = self._session.get(rows.BillingIntegrityEventRow, event.id)
        if existing is not None:
            return from_row(IncidentEvent, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingIntegrityEventRow(
                        payload=encode(event), **_event_projection(event)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingIntegrityEventRow, event.id)
            if winner is None:
                # Two workers raced for the same *sequence* with different content. The unique
                # constraint on `(org, incident, sequence)` caught it; the loser re-reads and
                # re-appends at the next position rather than corrupting the order.
                raise
            return from_row(IncidentEvent, winner), False
        return event, True

    def for_incident(
        self, org_id: str, incident_id: str, limit: int = 500
    ) -> tuple[IncidentEvent, ...]:
        found = self._all(
            select(rows.BillingIntegrityEventRow)
            .where(
                rows.BillingIntegrityEventRow.org_id == org_id,
                rows.BillingIntegrityEventRow.incident_id == incident_id,
            )
            .order_by(rows.BillingIntegrityEventRow.sequence)
            .limit(limit)
        )
        return tuple(from_row(IncidentEvent, r) for r in found)

    def next_sequence(self, org_id: str, incident_id: str) -> int:
        """The next position from a stored count, inside the caller's transaction."""
        table = rows.BillingIntegrityEventRow
        count = self._session.execute(
            select(func.count())
            .select_from(table)
            .where(table.org_id == org_id, table.incident_id == incident_id)
        ).scalar_one()
        return int(count) + 1


class SqlIncidentMergeRepository(_Repo, IncidentMergeRepository):
    """Merge decisions. Append-only; there is no update and no delete."""

    def append(self, merge: IncidentMerge) -> tuple[IncidentMerge, bool]:
        existing = self._session.get(rows.BillingIntegrityMergeRow, merge.id)
        if existing is not None:
            return from_row(IncidentMerge, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingIntegrityMergeRow(
                        payload=encode(merge), **_merge_projection(merge)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingIntegrityMergeRow, merge.id)
            if winner is None:  # pragma: no cover — the unique constraint guarantees a row
                raise
            return from_row(IncidentMerge, winner), False
        return merge, True

    def for_canonical(self, org_id: str, canonical_id: str) -> tuple[IncidentMerge, ...]:
        found = self._all(
            select(rows.BillingIntegrityMergeRow)
            .where(
                rows.BillingIntegrityMergeRow.org_id == org_id,
                rows.BillingIntegrityMergeRow.canonical_id == canonical_id,
            )
            .order_by(rows.BillingIntegrityMergeRow.decided_at)
        )
        return tuple(from_row(IncidentMerge, r) for r in found)

    def for_merged(self, org_id: str, merged_id: str) -> IncidentMerge | None:
        row = self._one(
            select(rows.BillingIntegrityMergeRow).where(
                rows.BillingIntegrityMergeRow.org_id == org_id,
                rows.BillingIntegrityMergeRow.merged_id == merged_id,
            )
        )
        return from_row(IncidentMerge, row) if row else None


class SqlCorrelationCorrectionRepository(_Repo, CorrelationCorrectionRepository):
    """Corrections. Append-only; there is no update and no delete.

    This is what keeps a rethought correlation auditable: a correction is a new row naming both the
    previous and the corrected key, never an edit that makes the old belief disappear."""

    def append(
        self, correction: CorrelationCorrection
    ) -> tuple[CorrelationCorrection, bool]:
        existing = self._session.get(rows.BillingIntegrityCorrectionRow, correction.id)
        if existing is not None:
            return from_row(CorrelationCorrection, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingIntegrityCorrectionRow(
                        payload=encode(correction), **_correction_projection(correction)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingIntegrityCorrectionRow, correction.id)
            if winner is None:  # pragma: no cover — the primary key guarantees a row
                raise
            return from_row(CorrelationCorrection, winner), False
        return correction, True

    def for_finding(self, org_id: str, finding_id: str) -> tuple[CorrelationCorrection, ...]:
        found = self._all(
            select(rows.BillingIntegrityCorrectionRow)
            .where(
                rows.BillingIntegrityCorrectionRow.org_id == org_id,
                rows.BillingIntegrityCorrectionRow.finding_id == finding_id,
            )
            .order_by(rows.BillingIntegrityCorrectionRow.corrected_at)
        )
        return tuple(from_row(CorrelationCorrection, r) for r in found)

    def for_incident(self, org_id: str, incident_id: str) -> tuple[CorrelationCorrection, ...]:
        table = rows.BillingIntegrityCorrectionRow
        found = self._all(
            select(table)
            .where(
                table.org_id == org_id,
                (table.previous_incident_id == incident_id)
                | (table.corrected_incident_id == incident_id),
            )
            .order_by(table.corrected_at)
        )
        return tuple(from_row(CorrelationCorrection, r) for r in found)


class SqlIncidentRepairRepository(_Repo, IncidentRepairRepository):
    """Repair references. Append-only apart from the observation flag."""

    def append(self, repair: IncidentRepair) -> tuple[IncidentRepair, bool]:
        existing = self._session.get(rows.BillingIntegrityRepairRow, repair.id)
        if existing is not None:
            return from_row(IncidentRepair, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingIntegrityRepairRow(
                        payload=encode(repair), **_repair_projection(repair)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingIntegrityRepairRow, repair.id)
            if winner is None:  # pragma: no cover — the primary key guarantees a row
                raise
            return from_row(IncidentRepair, winner), False
        return repair, True

    def observe(self, repair: IncidentRepair) -> IncidentRepair:
        row = self._session.get(rows.BillingIntegrityRepairRow, repair.id)
        if row is None:  # pragma: no cover — the caller read it first
            return self.append(repair)[0]
        update_row(row, repair, _repair_projection(repair))
        self._session.flush()
        return repair

    def get(self, org_id: str, repair_id: str) -> IncidentRepair | None:
        row = self._one(
            select(rows.BillingIntegrityRepairRow).where(
                rows.BillingIntegrityRepairRow.org_id == org_id,
                rows.BillingIntegrityRepairRow.id == repair_id,
            )
        )
        return from_row(IncidentRepair, row) if row else None

    def for_incident(self, org_id: str, incident_id: str) -> tuple[IncidentRepair, ...]:
        found = self._all(
            select(rows.BillingIntegrityRepairRow)
            .where(
                rows.BillingIntegrityRepairRow.org_id == org_id,
                rows.BillingIntegrityRepairRow.incident_id == incident_id,
            )
            .order_by(rows.BillingIntegrityRepairRow.requested_at)
        )
        return tuple(from_row(IncidentRepair, r) for r in found)


__all__ = [
    "SqlCorrelationCorrectionRepository",
    "SqlFindingLinkRepository",
    "SqlIncidentEventRepository",
    "SqlIncidentMergeRepository",
    "SqlIncidentRepairRepository",
    "SqlIncidentRepository",
]
