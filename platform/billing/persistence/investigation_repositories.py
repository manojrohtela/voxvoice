"""Financial Investigation repositories (M9.21) — additive to the M9.4 persistence layer.

Seven SQL implementations of the M9.21 Foundation contracts, reusing the M9.4 machinery unchanged:
the same `_Repo` session handle, the same codec, the same projection-plus-payload mapping, the same
"never commit, never roll back" rule, and the same org-scoped-by-construction reads (§A11.2).

## The four race-critical writes

`SqlInvestigationRepository.create` inserts on an id derived from `(org, incident)`, defended the
three ways every idempotent write since M9.11 has been: a derived id, a read-before-write, and a
`SAVEPOINT` whose `IntegrityError` becomes a read of the winner rather than a destroyed
transaction. Two investigators opening a case on one incident therefore get one investigation.

`SqlInvestigationRepository.advance` is the compare-and-set on `version` — **the stale-worker
defence**. Investigator A reads a case at version 6, B concludes and closes it to version 8, A
resumes and writes: zero rows match, and it raises `InvestigationStale` rather than silently
overwriting a conclusion somebody else recorded.

`SqlEvidenceRepository.append` is **the duplicate-capture defence**, and it is content-addressed:
the same immutable source captured twice by two workers produces one row, because both compute the
same id from the same canonical body. A changed representation computes a different id and becomes
a new link in the chain.

`SqlAuditPackageRepository.create` is **the export determinism defence**: the id is the manifest
digest, so exporting unchanged state twice converges on one package and two concurrent exports
produce one row.

## Pagination is a database concern

`page()` issues a `COUNT` and a `LIMIT/OFFSET` against an index, and `counts()` issues `GROUP BY`
aggregates — rather than loading every investigation an org ever had and filtering in Python. At
organizations × incidents × investigations × evidence captures that is not a performance detail,
it is an outage.

## What no repository here does

None investigates, concludes, decides completeness or assembles a manifest — those are Foundation
services. And **none writes to an incident, reconciliation, ledger, invoice, tax, payment,
recognition, collection or subscription table.** An architecture test scans the write targets of
the whole Application to prove it. M9.21 reads those domains through their own seams and mutates
none of them, including M9.20's.
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from billing.foundation import (
    AuditPackage,
    AuditPackageRepository,
    EvidenceCapture,
    EvidenceRepository,
    Investigation,
    InvestigationConclusion,
    InvestigationConclusionRepository,
    InvestigationEvent,
    InvestigationEventRepository,
    InvestigationObservation,
    InvestigationObservationRepository,
    InvestigationQuestion,
    InvestigationQuestionRepository,
    InvestigationRepository,
    InvestigationSearchQuery,
    InvestigationStale,
)

from . import investigation_models as rows
from .codec import encode
from .mapping import from_row
from .repositories import _Repo


def _investigation_projection(i: Investigation) -> dict:
    return {
        "id": i.id,
        "org_id": i.org_id,
        "incident_id": i.incident_id,
        "status": i.status.value,
        "priority": i.priority.value,
        "incident_severity": i.incident_severity.value,
        "summary": i.summary,
        "opened_at": i.opened_at,
        "opened_by": i.opened_by,
        "last_activity_at": i.last_activity_at,
        "evidence_digest": i.evidence_digest,
        "evidence_count": i.evidence_count,
        "open_question_count": i.open_question_count,
        "unavailable_evidence_count": i.unavailable_evidence_count,
        "conclusion_id": i.conclusion_id,
        "closed_at": i.closed_at,
        "closed_by": i.closed_by,
        "closed_reason": i.closed_reason,
        "version": i.version,
    }


def _evidence_projection(c: EvidenceCapture) -> dict:
    return {
        "id": c.id,
        "org_id": c.org_id,
        "investigation_id": c.investigation_id,
        "domain": c.reference.domain.value,
        "source_type": c.reference.source_type.value,
        "source_id": c.reference.source_id,
        "revision": c.reference.revision,
        "locator": c.locator,
        # The chain key: the locator **without** the revision, so a document moving from revision 1
        # to revision 2 stays in one chain and the change is visible rather than hidden in a
        # second chain of its own.
        "source_key": c.locator.split("@")[0],
        "mode": c.mode.value,
        "availability": c.availability.value,
        "content_digest": c.content_digest,
        "captured_at": c.captured_at,
        "captured_by": c.captured_by,
        "previous_evidence_id": c.previous_evidence_id,
        "unavailable_reason": c.unavailable_reason,
        "redaction_reason": c.redaction_reason,
        "sequence": c.sequence,
    }


def _question_projection(q: InvestigationQuestion) -> dict:
    return {
        "id": q.id,
        "org_id": q.org_id,
        "investigation_id": q.investigation_id,
        "kind": q.kind.value,
        "text": q.text,
        "status": q.status.value,
        "required": q.required,
        "answer": q.answer,
        "asked_at": q.asked_at,
        "asked_by": q.asked_by,
        "answered_at": q.answered_at,
        "answered_by": q.answered_by,
        "version": q.version,
    }


def _observation_projection(o: InvestigationObservation) -> dict:
    return {
        "id": o.id,
        "org_id": o.org_id,
        "investigation_id": o.investigation_id,
        "kind": o.kind.value,
        "statement": o.statement,
        "statement_digest": o.statement_digest,
        "hypothesis_status": o.hypothesis_status.value,
        "recorded_at": o.recorded_at,
        "recorded_by": o.recorded_by,
        "settled_at": o.settled_at,
        "settled_by": o.settled_by,
        "settled_reason": o.settled_reason,
        "sequence": o.sequence,
        "version": o.version,
    }


def _conclusion_projection(c: InvestigationConclusion) -> dict:
    return {
        "id": c.id,
        "org_id": c.org_id,
        "investigation_id": c.investigation_id,
        "incident_id": c.incident_id,
        "classification": c.classification.value,
        "statement": c.statement,
        "statement_digest": c.statement_digest,
        "incident_root_cause": c.incident_root_cause.value,
        "evidenced_root_cause": (
            None if c.evidenced_root_cause is None else c.evidenced_root_cause.value
        ),
        "supersedes": c.supersedes,
        "recorded_at": c.recorded_at,
        "recorded_by": c.recorded_by,
        "version": c.version,
    }


def _event_projection(e: InvestigationEvent) -> dict:
    return {
        "id": e.id,
        "org_id": e.org_id,
        "investigation_id": e.investigation_id,
        "sequence": e.sequence,
        "kind": e.kind.value,
        "at": e.at,
        "actor_id": e.actor_id,
        "subject": e.subject,
        "summary": e.summary,
    }


def _package_projection(p: AuditPackage) -> dict:
    return {
        "id": p.id,
        "org_id": p.org_id,
        "investigation_id": p.investigation_id,
        "incident_id": p.incident_id,
        "manifest_digest": p.manifest_digest,
        "completeness": p.completeness.value,
        "evidence_count": p.evidence_count,
        "observation_count": p.observation_count,
        "question_count": p.question_count,
        "conclusion_count": p.conclusion_count,
        "timeline_length": p.timeline_length,
        "gap_count": len(p.gaps),
        "created_at": p.created_at,
        "created_by": p.created_by,
    }


class SqlInvestigationRepository(_Repo, InvestigationRepository):
    """Investigations, arbitrated by `(org, incident)`. There is no delete, and that is the
    contract: an investigation is the record that somebody looked."""

    def create(self, investigation: Investigation) -> tuple[Investigation, bool]:
        existing = self._session.get(rows.BillingInvestigationRow, investigation.id)
        if existing is not None:
            return from_row(Investigation, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingInvestigationRow(
                        payload=encode(investigation),
                        **_investigation_projection(investigation),
                    )
                )
                self._session.flush()
        except IntegrityError:
            # Another investigator opened a case on the same incident between this read and this
            # insert. The derived id guarantees they computed the same investigation, so this
            # caller joins the winner's rather than opening a rival one.
            winner = self._session.get(rows.BillingInvestigationRow, investigation.id)
            if winner is None:  # pragma: no cover — the primary key guarantees a row
                raise
            return from_row(Investigation, winner), False
        return investigation, True

    def advance(self, investigation: Investigation, *, expected_version: int) -> Investigation:
        """Compare-and-set on `version`. **The stale-worker defence.**

        A filtered UPDATE rather than a read followed by a write, because the read would be exactly
        the stale read the check exists to catch. Zero rows is not retryable here: the caller must
        re-read, and when it does it may find a case the FSM refuses to move at all — which is the
        correct outcome, because somebody else already closed it."""
        table = rows.BillingInvestigationRow.__table__
        result = self._session.execute(
            table.update()
            .where(
                table.c.id == investigation.id,
                table.c.org_id == investigation.org_id,
                table.c.version == expected_version,
            )
            .values(
                payload=encode(investigation), **_investigation_projection(investigation)
            )
        )
        if result.rowcount != 1:
            stored = self._session.get(rows.BillingInvestigationRow, investigation.id)
            current = stored.status if stored is not None else "missing"
            raise InvestigationStale(
                f"investigation {investigation.id!r} moved while this worker was deciding "
                f"(expected version {expected_version}, found status {current!r}); "
                "another investigator settled it — re-read rather than overwrite"
            )
        self._session.expire_all()
        return investigation

    def get(self, org_id: str, investigation_id: str) -> Investigation | None:
        row = self._one(
            select(rows.BillingInvestigationRow).where(
                rows.BillingInvestigationRow.org_id == org_id,
                rows.BillingInvestigationRow.id == investigation_id,
            )
        )
        return from_row(Investigation, row) if row else None

    def by_incident(self, org_id: str, incident_id: str) -> Investigation | None:
        row = self._one(
            select(rows.BillingInvestigationRow).where(
                rows.BillingInvestigationRow.org_id == org_id,
                rows.BillingInvestigationRow.incident_id == incident_id,
            )
        )
        return from_row(Investigation, row) if row else None

    def page(self, query: InvestigationSearchQuery) -> tuple[tuple[Investigation, ...], int]:
        """**One page and the total, both from the database.**

        Filtering in Python would mean loading every investigation an org ever had to return
        fifty."""
        table = rows.BillingInvestigationRow
        filters = [table.org_id == query.org_id]
        if query.incident_id:
            filters.append(table.incident_id == query.incident_id)
        if query.status is not None:
            filters.append(table.status == query.status.value)
        if query.priority is not None:
            filters.append(table.priority == query.priority.value)
        if query.opened_by:
            filters.append(table.opened_by == query.opened_by)
        if query.opened_from is not None:
            filters.append(table.opened_at >= query.opened_from)
        if query.opened_to is not None:
            filters.append(table.opened_at <= query.opened_to)
        if query.blocked_only:
            filters.append(table.status == "awaiting_evidence")
        total = self._session.execute(
            select(func.count()).select_from(table).where(*filters)
        ).scalar_one()
        found = self._all(
            select(table)
            .where(*filters)
            .order_by(table.opened_at.desc(), table.id)
            .limit(max(1, query.limit))
            .offset(max(0, query.offset))
        )
        return tuple(from_row(Investigation, r) for r in found), int(total)

    def counts(self, org_id: str) -> dict[str, dict[str, int]]:
        """Grouped counts, computed by the database. **Counts only — never a figure.**"""
        table = rows.BillingInvestigationRow
        by_status = {
            str(status): int(n)
            for status, n in self._session.execute(
                select(table.status, func.count())
                .where(table.org_id == org_id)
                .group_by(table.status)
            ).all()
        }
        by_priority = {
            str(priority): int(n)
            for priority, n in self._session.execute(
                select(table.priority, func.count())
                .where(table.org_id == org_id)
                .group_by(table.priority)
            ).all()
        }
        evidence = rows.BillingInvestigationEvidenceRow
        unavailable = self._session.execute(
            select(func.count())
            .select_from(evidence)
            .where(evidence.org_id == org_id, evidence.availability == "unavailable")
        ).scalar_one()
        packages = rows.BillingAuditPackageRow
        exported = self._session.execute(
            select(func.count()).select_from(packages).where(packages.org_id == org_id)
        ).scalar_one()
        incomplete = self._session.execute(
            select(func.count())
            .select_from(packages)
            .where(packages.org_id == org_id, packages.completeness == "incomplete")
        ).scalar_one()
        return {
            "by_status": by_status,
            "by_priority": by_priority,
            "totals": {
                "unavailable_evidence": int(unavailable),
                "packages": int(exported),
                "incomplete_packages": int(incomplete),
            },
        }


class SqlEvidenceRepository(_Repo, EvidenceRepository):
    """**Append-only. There is no update method and no delete method on this class.**

    That absence is the milestone's central guarantee expressed as an API: a capture's mode, body
    and digest are fixed for the life of the row, so a live reference can never become a snapshot,
    a snapshot can never be silently edited, and lost evidence stays visible as lost."""

    def append(self, capture: EvidenceCapture) -> tuple[EvidenceCapture, bool]:
        existing = self._session.get(rows.BillingInvestigationEvidenceRow, capture.id)
        if existing is not None:
            return from_row(EvidenceCapture, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingInvestigationEvidenceRow(
                        payload=encode(capture), **_evidence_projection(capture)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingInvestigationEvidenceRow, capture.id)
            if winner is None:  # pragma: no cover
                raise
            return from_row(EvidenceCapture, winner), False
        return capture, True

    def get(self, org_id: str, evidence_id: str) -> EvidenceCapture | None:
        row = self._one(
            select(rows.BillingInvestigationEvidenceRow).where(
                rows.BillingInvestigationEvidenceRow.org_id == org_id,
                rows.BillingInvestigationEvidenceRow.id == evidence_id,
            )
        )
        return from_row(EvidenceCapture, row) if row else None

    def for_investigation(
        self, org_id: str, investigation_id: str, *, offset: int = 0, limit: int = 200
    ) -> tuple[tuple[EvidenceCapture, ...], int]:
        table = rows.BillingInvestigationEvidenceRow
        filters = [table.org_id == org_id, table.investigation_id == investigation_id]
        total = self._session.execute(
            select(func.count()).select_from(table).where(*filters)
        ).scalar_one()
        found = self._all(
            select(table)
            .where(*filters)
            .order_by(table.sequence, table.id)
            .limit(max(1, limit))
            .offset(max(0, offset))
        )
        return tuple(from_row(EvidenceCapture, r) for r in found), int(total)

    def all_for_investigation(
        self, org_id: str, investigation_id: str
    ) -> tuple[EvidenceCapture, ...]:
        """Every capture, for manifest construction. **Bounded by one investigation**, never by
        org — a manifest must contain the whole chain or it is not a reconstruction."""
        table = rows.BillingInvestigationEvidenceRow
        found = self._all(
            select(table)
            .where(table.org_id == org_id, table.investigation_id == investigation_id)
            .order_by(table.sequence, table.id)
        )
        return tuple(from_row(EvidenceCapture, r) for r in found)

    def latest_for_source(
        self, org_id: str, investigation_id: str, locator_prefix: str
    ) -> EvidenceCapture | None:
        table = rows.BillingInvestigationEvidenceRow
        row = self._one(
            select(table)
            .where(
                table.org_id == org_id,
                table.investigation_id == investigation_id,
                table.source_key == locator_prefix,
            )
            .order_by(table.sequence.desc(), table.id.desc())
        )
        return from_row(EvidenceCapture, row) if row else None

    def next_sequence(self, org_id: str, investigation_id: str) -> int:
        table = rows.BillingInvestigationEvidenceRow
        highest = self._session.execute(
            select(func.max(table.sequence)).where(
                table.org_id == org_id, table.investigation_id == investigation_id
            )
        ).scalar_one()
        return int(highest or 0) + 1


class SqlInvestigationQuestionRepository(_Repo, InvestigationQuestionRepository):
    """Questions. One narrow update — the outcome — and no delete: "this was asked and never
    settled" is what an examiner asks about."""

    def append(self, question: InvestigationQuestion) -> tuple[InvestigationQuestion, bool]:
        existing = self._session.get(rows.BillingInvestigationQuestionRow, question.id)
        if existing is not None:
            return from_row(InvestigationQuestion, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingInvestigationQuestionRow(
                        payload=encode(question), **_question_projection(question)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingInvestigationQuestionRow, question.id)
            if winner is None:  # pragma: no cover
                raise
            return from_row(InvestigationQuestion, winner), False
        return question, True

    def resolve(
        self, question: InvestigationQuestion, *, expected_version: int
    ) -> InvestigationQuestion:
        table = rows.BillingInvestigationQuestionRow.__table__
        result = self._session.execute(
            table.update()
            .where(
                table.c.id == question.id,
                table.c.org_id == question.org_id,
                table.c.version == expected_version,
            )
            .values(payload=encode(question), **_question_projection(question))
        )
        if result.rowcount != 1:
            raise InvestigationStale(
                f"question {question.id!r} was answered by somebody else while this worker was "
                f"deciding (expected version {expected_version}); re-read rather than overwrite — "
                "an audit record must not show one outcome and hide another"
            )
        self._session.expire_all()
        return question

    def get(self, org_id: str, question_id: str) -> InvestigationQuestion | None:
        row = self._one(
            select(rows.BillingInvestigationQuestionRow).where(
                rows.BillingInvestigationQuestionRow.org_id == org_id,
                rows.BillingInvestigationQuestionRow.id == question_id,
            )
        )
        return from_row(InvestigationQuestion, row) if row else None

    def for_investigation(
        self, org_id: str, investigation_id: str
    ) -> tuple[InvestigationQuestion, ...]:
        table = rows.BillingInvestigationQuestionRow
        found = self._all(
            select(table)
            .where(table.org_id == org_id, table.investigation_id == investigation_id)
            .order_by(table.asked_at, table.id)
        )
        return tuple(from_row(InvestigationQuestion, r) for r in found)


class SqlInvestigationObservationRepository(_Repo, InvestigationObservationRepository):
    """Observations. One narrow update — the hypothesis verdict — and no delete: "this was believed
    and then rejected" is exactly what the trail is for. The statement itself is never rewritten."""

    def append(
        self, observation: InvestigationObservation
    ) -> tuple[InvestigationObservation, bool]:
        existing = self._session.get(rows.BillingInvestigationObservationRow, observation.id)
        if existing is not None:
            return from_row(InvestigationObservation, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingInvestigationObservationRow(
                        payload=encode(observation), **_observation_projection(observation)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingInvestigationObservationRow, observation.id)
            if winner is None:  # pragma: no cover
                raise
            return from_row(InvestigationObservation, winner), False
        return observation, True

    def settle(
        self, observation: InvestigationObservation, *, expected_version: int
    ) -> InvestigationObservation:
        table = rows.BillingInvestigationObservationRow.__table__
        result = self._session.execute(
            table.update()
            .where(
                table.c.id == observation.id,
                table.c.org_id == observation.org_id,
                table.c.version == expected_version,
            )
            .values(payload=encode(observation), **_observation_projection(observation))
        )
        if result.rowcount != 1:
            raise InvestigationStale(
                f"hypothesis {observation.id!r} was settled by somebody else while this worker "
                f"was deciding (expected version {expected_version}); re-read rather than "
                "overwrite — re-settling would rewrite what the investigation concluded and when"
            )
        self._session.expire_all()
        return observation

    def get(self, org_id: str, observation_id: str) -> InvestigationObservation | None:
        row = self._one(
            select(rows.BillingInvestigationObservationRow).where(
                rows.BillingInvestigationObservationRow.org_id == org_id,
                rows.BillingInvestigationObservationRow.id == observation_id,
            )
        )
        return from_row(InvestigationObservation, row) if row else None

    def for_investigation(
        self, org_id: str, investigation_id: str
    ) -> tuple[InvestigationObservation, ...]:
        table = rows.BillingInvestigationObservationRow
        found = self._all(
            select(table)
            .where(table.org_id == org_id, table.investigation_id == investigation_id)
            .order_by(table.sequence, table.id)
        )
        return tuple(from_row(InvestigationObservation, r) for r in found)

    def next_sequence(self, org_id: str, investigation_id: str) -> int:
        table = rows.BillingInvestigationObservationRow
        highest = self._session.execute(
            select(func.max(table.sequence)).where(
                table.org_id == org_id, table.investigation_id == investigation_id
            )
        ).scalar_one()
        return int(highest or 0) + 1


class SqlInvestigationConclusionRepository(_Repo, InvestigationConclusionRepository):
    """**Append-only. No update, no delete.** A revision is a new row naming what it supersedes,
    so an examiner can see that the first answer was wrong and when it was corrected."""

    def append(
        self, conclusion: InvestigationConclusion
    ) -> tuple[InvestigationConclusion, bool]:
        existing = self._session.get(rows.BillingInvestigationConclusionRow, conclusion.id)
        if existing is not None:
            return from_row(InvestigationConclusion, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingInvestigationConclusionRow(
                        payload=encode(conclusion), **_conclusion_projection(conclusion)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingInvestigationConclusionRow, conclusion.id)
            if winner is None:  # pragma: no cover
                raise
            return from_row(InvestigationConclusion, winner), False
        return conclusion, True

    def get(self, org_id: str, conclusion_id: str) -> InvestigationConclusion | None:
        row = self._one(
            select(rows.BillingInvestigationConclusionRow).where(
                rows.BillingInvestigationConclusionRow.org_id == org_id,
                rows.BillingInvestigationConclusionRow.id == conclusion_id,
            )
        )
        return from_row(InvestigationConclusion, row) if row else None

    def for_investigation(
        self, org_id: str, investigation_id: str
    ) -> tuple[InvestigationConclusion, ...]:
        table = rows.BillingInvestigationConclusionRow
        found = self._all(
            select(table)
            .where(table.org_id == org_id, table.investigation_id == investigation_id)
            .order_by(table.version, table.id)
        )
        return tuple(from_row(InvestigationConclusion, r) for r in found)


class SqlInvestigationEventRepository(_Repo, InvestigationEventRepository):
    """**Append-only. No update, no delete.** The timeline is the record, not a log of it."""

    def append(self, event: InvestigationEvent) -> tuple[InvestigationEvent, bool]:
        existing = self._session.get(rows.BillingInvestigationEventRow, event.id)
        if existing is not None:
            return from_row(InvestigationEvent, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingInvestigationEventRow(
                        payload=encode(event), **_event_projection(event)
                    )
                )
                self._session.flush()
        except IntegrityError:
            # Two workers computed the same sequence. The unique constraint on
            # `(org, investigation, sequence)` arbitrates; the loser reads the winner's line rather
            # than appending a second entry claiming the same position in the trail.
            winner = self._session.get(rows.BillingInvestigationEventRow, event.id)
            if winner is not None:
                return from_row(InvestigationEvent, winner), False
            existing_seq = self._one(
                select(rows.BillingInvestigationEventRow).where(
                    rows.BillingInvestigationEventRow.org_id == event.org_id,
                    rows.BillingInvestigationEventRow.investigation_id
                    == event.investigation_id,
                    rows.BillingInvestigationEventRow.sequence == event.sequence,
                )
            )
            if existing_seq is None:  # pragma: no cover
                raise
            return from_row(InvestigationEvent, existing_seq), False
        return event, True

    def for_investigation(
        self, org_id: str, investigation_id: str, *, offset: int = 0, limit: int = 200
    ) -> tuple[tuple[InvestigationEvent, ...], int]:
        table = rows.BillingInvestigationEventRow
        filters = [table.org_id == org_id, table.investigation_id == investigation_id]
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
        return tuple(from_row(InvestigationEvent, r) for r in found), int(total)

    def all_for_investigation(
        self, org_id: str, investigation_id: str
    ) -> tuple[InvestigationEvent, ...]:
        table = rows.BillingInvestigationEventRow
        found = self._all(
            select(table)
            .where(table.org_id == org_id, table.investigation_id == investigation_id)
            .order_by(table.sequence)
        )
        return tuple(from_row(InvestigationEvent, r) for r in found)

    def next_sequence(self, org_id: str, investigation_id: str) -> int:
        table = rows.BillingInvestigationEventRow
        highest = self._session.execute(
            select(func.max(table.sequence)).where(
                table.org_id == org_id, table.investigation_id == investigation_id
            )
        ).scalar_one()
        return int(highest or 0) + 1


class SqlAuditPackageRepository(_Repo, AuditPackageRepository):
    """**Immutable. No update, no delete.**

    The id is the manifest digest, so two concurrent exports of the same state write one row, and
    an export after any change writes a new row beside the old one rather than over it."""

    def create(self, package: AuditPackage) -> tuple[AuditPackage, bool]:
        existing = self._session.get(rows.BillingAuditPackageRow, package.id)
        if existing is not None:
            return from_row(AuditPackage, existing), False
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingAuditPackageRow(
                        payload=encode(package), **_package_projection(package)
                    )
                )
                self._session.flush()
        except IntegrityError:
            winner = self._session.get(rows.BillingAuditPackageRow, package.id)
            if winner is None:  # pragma: no cover
                raise
            return from_row(AuditPackage, winner), False
        return package, True

    def get(self, org_id: str, package_id: str) -> AuditPackage | None:
        row = self._one(
            select(rows.BillingAuditPackageRow).where(
                rows.BillingAuditPackageRow.org_id == org_id,
                rows.BillingAuditPackageRow.id == package_id,
            )
        )
        return from_row(AuditPackage, row) if row else None

    def for_investigation(self, org_id: str, investigation_id: str) -> tuple[AuditPackage, ...]:
        table = rows.BillingAuditPackageRow
        found = self._all(
            select(table)
            .where(table.org_id == org_id, table.investigation_id == investigation_id)
            .order_by(table.created_at, table.id)
        )
        return tuple(from_row(AuditPackage, r) for r in found)

    def latest(self, org_id: str, investigation_id: str) -> AuditPackage | None:
        table = rows.BillingAuditPackageRow
        row = self._one(
            select(table)
            .where(table.org_id == org_id, table.investigation_id == investigation_id)
            .order_by(table.created_at.desc(), table.id.desc())
        )
        return from_row(AuditPackage, row) if row else None


__all__ = [
    "SqlAuditPackageRepository",
    "SqlEvidenceRepository",
    "SqlInvestigationConclusionRepository",
    "SqlInvestigationEventRepository",
    "SqlInvestigationObservationRepository",
    "SqlInvestigationQuestionRepository",
    "SqlInvestigationRepository",
]
