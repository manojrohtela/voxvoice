"""Invoice Engine repositories (M9.14) — additive to the M9.4 persistence layer.

Six SQL implementations of the M9.14 Foundation contracts, reusing the M9.4 machinery unchanged: the
same `_Repo` session handle, the same codec, the same projection-plus-payload mapping, the same
"never commit, never roll back" rule, and the same org-scoped-by-construction reads (§A11.2).

Four expose **no update or delete method at all**: snapshots, revisions, allocated numbers, and
renderings. Not by convention — there is no method to call.

## The gapless allocation

`SqlNumberSeriesAllocator.allocate` is where "gapless, exactly-once, multi-worker-safe, crash-safe"
stops being an adjective. It does three things and nothing else:

1. reads or creates the durable counter row for the series;
2. increments it and flushes, so a concurrent worker's read blocks or fails rather than seeing a
   stale value;
3. writes the allocation into `cp_billing_invoice_number`, whose `(org, series, sequence)` primary
   key makes a duplicate number **an impossible row** rather than a finding.

All three happen inside the *caller's* transaction, which is the crash-safety property: a process
that dies between allocating and issuing rolls both back, so the number is not consumed. The
alternative — allocating from a pool up front — hands out a number that a crash turns into a
permanent hole, and where a jurisdiction requires gapless numbering there is no legal way to fill
one afterwards.

## The append-only content store

`SqlInvoiceSnapshotRepository` has `append`, `get`, `by_digest`, `revisions`, and `latest`. There is
no `replace`. `by_digest` is the method that matters: an issued document resolves its content by the
digest it pinned at issue, so the *newest* revision cannot become the served document by being
newest. Immutability that depends on nobody calling an update method lasts until someone does;
immutability by content addressing does not depend on anyone.

No repository here builds a line, seals a snapshot, formats a number, totals a document, or renders
anything. Those are Foundation decisions, and a repository that made one would put document logic
where nobody would look for it.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from billing.foundation import (
    CommercialInvoice,
    CommercialInvoiceRepository,
    InvoiceActivity,
    InvoiceActivityRepository,
    InvoiceRevision,
    InvoiceRevisionRepository,
    InvoiceSnapshot,
    InvoiceSnapshotRepository,
    NumberSeriesAllocator,
    RenderedDocument,
    RenderedDocumentRepository,
    RenderFormat,
    derive_rendering_id,
    derive_revision_id,
    derive_snapshot_id,
)

from . import invoice_models as rows
from .codec import encode
from .mapping import from_row, update_row
from .repositories import _Repo


def _document_projection(document: CommercialInvoice) -> dict:
    return {
        "id": document.id,
        "org_id": document.org_id,
        "account_id": document.account_id,
        "kind": document.kind.value,
        "state": document.state.value,
        "currency": document.currency.value,
        "number_formatted": document.formatted_number,
        "number_sequence": document.number.sequence if document.number else 0,
        "entity_code": document.entity_code,
        "jurisdiction": document.jurisdiction,
        "revision": document.revision,
        "issued_revision": document.issued_revision,
        "issued_snapshot_digest": document.issued_snapshot_digest,
        "accounting_period_key": document.accounting_period_key,
        "period_start": document.period.start if document.period else None,
        "period_end": document.period.end if document.period else None,
        "total_minor": document.total_minor,
        "amount_due_minor": document.amount_due_minor,
        "delivery_state": document.delivery.state.value,
        "related_document_ids": ",".join(r.document_id for r in document.relations),
        "prepared_at": document.prepared_at,
        "issued_at": document.issued_at,
        "due_at": document.due_at,
        "settled_at": document.settled_at,
        "updated_at": document.updated_at,
    }


def _snapshot_projection(snapshot: InvoiceSnapshot) -> dict:
    totals = snapshot.totals
    return {
        "id": derive_snapshot_id(snapshot.document_id, snapshot.revision),
        "org_id": snapshot.org_id,
        "document_id": snapshot.document_id,
        "account_id": snapshot.account_id,
        "revision": snapshot.revision,
        "kind": snapshot.kind.value,
        "digest": snapshot.digest,
        "currency": snapshot.currency.value,
        "number_formatted": snapshot.number.formatted if snapshot.number else "",
        "total_minor": totals.total.minor if totals else 0,
        "amount_due_minor": totals.amount_due.minor if totals else 0,
        "tax_total_minor": totals.tax_total.minor if totals else 0,
        "line_count": snapshot.line_count,
        "accounting_period_key": snapshot.accounting_period_key,
        "tax_determination_id": snapshot.tax_determination_id,
        "template_code": snapshot.presentation.template_code,
        "template_version": snapshot.presentation.template_version,
        "issue_date": snapshot.issue_date,
        "due_date": snapshot.due_date,
        "prepared_at": snapshot.prepared_at,
        "prepared_by": snapshot.prepared_by,
    }


def _revision_projection(revision: InvoiceRevision) -> dict:
    return {
        "id": derive_revision_id(
            revision.document_id, revision.revision, revision.snapshot_digest
        ),
        "org_id": "",  # filled by the repository, which knows the scope the revision belongs to
        "document_id": revision.document_id,
        "revision": revision.revision,
        "snapshot_digest": revision.snapshot_digest,
        "at": revision.at,
        "actor_id": revision.actor_id,
        "reason": revision.reason,
        "issued": revision.issued,
    }


def _rendered_projection(rendered: RenderedDocument) -> dict:
    return {
        "id": derive_rendering_id(
            rendered.document_id,
            rendered.snapshot_digest,
            rendered.format.value,
            rendered.template_version,
        ),
        "org_id": "",
        "document_id": rendered.document_id,
        "snapshot_digest": rendered.snapshot_digest,
        "format": rendered.format.value,
        "template_code": rendered.template_code,
        "template_version": rendered.template_version,
        "byte_size": rendered.byte_size,
        "rendered_at": rendered.rendered_at,
        "content": rendered.content,
    }


def _activity_projection(activity: InvoiceActivity) -> dict:
    return {
        "id": activity.id,
        "org_id": activity.org_id,
        "action": activity.action.value,
        "actor_id": activity.actor_id,
        "at": activity.at,
        "account_id": activity.account_id,
        "document_id": activity.document_id,
        "document_number": activity.document_number,
        "revision": activity.revision,
        "amount_minor": activity.amount_minor,
        "currency": activity.currency.value if activity.currency else "",
        "reason": activity.reason,
    }


class SqlCommercialInvoiceRepository(_Repo, CommercialInvoiceRepository):
    """Document headers. `replace` moves lifecycle state, the number, the pinned digest, delivery,
    and relations — never content, because there is no content on a header to move."""

    def add(self, document: CommercialInvoice) -> CommercialInvoice:
        self._add(
            rows.BillingCommercialInvoiceRow(
                payload=encode(document), **_document_projection(document)
            )
        )
        return document

    def get(self, org_id: str, document_id: str) -> CommercialInvoice | None:
        row = self._row(org_id, document_id)
        return from_row(CommercialInvoice, row) if row else None

    def by_number(self, org_id: str, formatted: str) -> CommercialInvoice | None:
        row = self._one(
            select(rows.BillingCommercialInvoiceRow).where(
                rows.BillingCommercialInvoiceRow.org_id == org_id,
                rows.BillingCommercialInvoiceRow.number_formatted == formatted,
            )
        )
        return from_row(CommercialInvoice, row) if row else None

    def for_account(
        self, org_id: str, account_id: str, limit: int = 200
    ) -> tuple[CommercialInvoice, ...]:
        found = self._all(
            select(rows.BillingCommercialInvoiceRow)
            .where(
                rows.BillingCommercialInvoiceRow.org_id == org_id,
                rows.BillingCommercialInvoiceRow.account_id == account_id,
            )
            .order_by(
                rows.BillingCommercialInvoiceRow.prepared_at,
                rows.BillingCommercialInvoiceRow.id,
            )
            .limit(limit)
        )
        return tuple(from_row(CommercialInvoice, r) for r in found)

    def for_period(
        self, org_id: str, account_id: str, accounting_period_key: str
    ) -> tuple[CommercialInvoice, ...]:
        found = self._all(
            select(rows.BillingCommercialInvoiceRow)
            .where(
                rows.BillingCommercialInvoiceRow.org_id == org_id,
                rows.BillingCommercialInvoiceRow.account_id == account_id,
                rows.BillingCommercialInvoiceRow.accounting_period_key == accounting_period_key,
            )
            .order_by(
                rows.BillingCommercialInvoiceRow.prepared_at,
                rows.BillingCommercialInvoiceRow.id,
            )
        )
        return tuple(from_row(CommercialInvoice, r) for r in found)

    def related_to(self, org_id: str, document_id: str) -> tuple[CommercialInvoice, ...]:
        """Every document that references this one.

        Matched on the projected id list rather than by parsing payloads, so the query stays a
        column scan; the projection is derived from the aggregate, never written independently."""
        found = self._all(
            select(rows.BillingCommercialInvoiceRow)
            .where(
                rows.BillingCommercialInvoiceRow.org_id == org_id,
                rows.BillingCommercialInvoiceRow.related_document_ids.contains(  # type: ignore[attr-defined]
                    document_id
                ),
            )
            .order_by(rows.BillingCommercialInvoiceRow.prepared_at)
        )
        return tuple(
            from_row(CommercialInvoice, r) for r in found if r.id != document_id
        )

    def all(self, org_id: str, limit: int = 500) -> tuple[CommercialInvoice, ...]:
        found = self._all(
            select(rows.BillingCommercialInvoiceRow)
            .where(rows.BillingCommercialInvoiceRow.org_id == org_id)
            .order_by(
                rows.BillingCommercialInvoiceRow.prepared_at.desc(),  # type: ignore[attr-defined]
                rows.BillingCommercialInvoiceRow.id,
            )
            .limit(limit)
        )
        return tuple(from_row(CommercialInvoice, r) for r in found)

    def replace(self, document: CommercialInvoice) -> CommercialInvoice:
        existing = self._row(document.org_id, document.id)
        if existing is None:
            return self.add(document)
        update_row(existing, document, _document_projection(document))
        self._session.flush()
        return document

    def _row(self, org_id: str, document_id: str):
        return self._one(
            select(rows.BillingCommercialInvoiceRow).where(
                rows.BillingCommercialInvoiceRow.org_id == org_id,
                rows.BillingCommercialInvoiceRow.id == document_id,
            )
        )


class SqlInvoiceSnapshotRepository(_Repo, InvoiceSnapshotRepository):
    """**Append-only document content.** No update method, no delete method.

    `append` is idempotent on `(document, revision)` and defended the same three ways M9.11–M9.13
    defend theirs: a derived primary key, a read-before-write, and a `SAVEPOINT` around the insert
    whose `IntegrityError` becomes a read of the winner — so losing a race does not destroy the
    caller's transaction along with the failed insert."""

    def append(self, snapshot: InvoiceSnapshot) -> InvoiceSnapshot:
        projection = _snapshot_projection(snapshot)
        existing = self._by_id(snapshot.org_id, projection["id"])
        if existing is not None:
            return from_row(InvoiceSnapshot, existing)
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingInvoiceSnapshotRow(payload=encode(snapshot), **projection)
                )
                self._session.flush()
        except IntegrityError:
            winner = self._by_id(snapshot.org_id, projection["id"])
            if winner is None:  # pragma: no cover — the constraint guarantees a row
                raise
            return from_row(InvoiceSnapshot, winner)
        return snapshot

    def get(self, org_id: str, document_id: str, revision: int) -> InvoiceSnapshot | None:
        row = self._one(
            select(rows.BillingInvoiceSnapshotRow).where(
                rows.BillingInvoiceSnapshotRow.org_id == org_id,
                rows.BillingInvoiceSnapshotRow.document_id == document_id,
                rows.BillingInvoiceSnapshotRow.revision == revision,
            )
        )
        return from_row(InvoiceSnapshot, row) if row else None

    def by_digest(self, org_id: str, digest: str) -> InvoiceSnapshot | None:
        """The resolution path for an issued document — content addressed, not recency-ordered."""
        row = self._one(
            select(rows.BillingInvoiceSnapshotRow).where(
                rows.BillingInvoiceSnapshotRow.org_id == org_id,
                rows.BillingInvoiceSnapshotRow.digest == digest,
            )
        )
        return from_row(InvoiceSnapshot, row) if row else None

    def revisions(self, org_id: str, document_id: str) -> tuple[InvoiceSnapshot, ...]:
        found = self._all(
            select(rows.BillingInvoiceSnapshotRow)
            .where(
                rows.BillingInvoiceSnapshotRow.org_id == org_id,
                rows.BillingInvoiceSnapshotRow.document_id == document_id,
            )
            .order_by(rows.BillingInvoiceSnapshotRow.revision)
        )
        return tuple(from_row(InvoiceSnapshot, r) for r in found)

    def latest(self, org_id: str, document_id: str) -> InvoiceSnapshot | None:
        found = self.revisions(org_id, document_id)
        return found[-1] if found else None

    def _by_id(self, org_id: str, snapshot_id: str):
        return self._one(
            select(rows.BillingInvoiceSnapshotRow).where(
                rows.BillingInvoiceSnapshotRow.org_id == org_id,
                rows.BillingInvoiceSnapshotRow.id == snapshot_id,
            )
        )


class SqlInvoiceRevisionRepository(_Repo, InvoiceRevisionRepository):
    """Append-only revision history."""

    def __init__(self, database, org_scope: str = "") -> None:
        super().__init__(database)
        self._org_scope = org_scope

    def append(self, revision: InvoiceRevision) -> InvoiceRevision:
        projection = _revision_projection(revision)
        projection["org_id"] = self._org_of(revision.document_id)
        existing = self._one(
            select(rows.BillingInvoiceRevisionRow).where(
                rows.BillingInvoiceRevisionRow.id == projection["id"]
            )
        )
        if existing is not None:
            return from_row(InvoiceRevision, existing)
        self._add(rows.BillingInvoiceRevisionRow(payload=encode(revision), **projection))
        return revision

    def history(self, org_id: str, document_id: str) -> tuple[InvoiceRevision, ...]:
        found = self._all(
            select(rows.BillingInvoiceRevisionRow)
            .where(
                rows.BillingInvoiceRevisionRow.org_id == org_id,
                rows.BillingInvoiceRevisionRow.document_id == document_id,
            )
            .order_by(rows.BillingInvoiceRevisionRow.revision)
        )
        return tuple(from_row(InvoiceRevision, r) for r in found)

    def _org_of(self, document_id: str) -> str:
        """A revision is scoped by the document it belongs to. Read here rather than carried on the
        aggregate, because the org is a property of the document and duplicating it onto the
        revision would be a second copy that could disagree (§A2.5)."""
        row = self._one(
            select(rows.BillingCommercialInvoiceRow).where(
                rows.BillingCommercialInvoiceRow.id == document_id
            )
        )
        return row.org_id if row else self._org_scope


class SqlNumberSeriesAllocator(_Repo, NumberSeriesAllocator):
    """**Gapless, exactly-once, multi-worker-safe, crash-safe.**

    See the module docstring for why all three writes happen inside the caller's transaction."""

    def allocate(self, org_id: str, series_key: str) -> int:
        row = self._series(org_id, series_key)
        if row is None:
            row = rows.BillingInvoiceSeriesRow(
                org_id=org_id, series_key=series_key, last_sequence=0
            )
            self._add(row)
        row.last_sequence += 1
        self._session.flush()
        # The allocation ledger: `(org, series, sequence)` is the primary key, so a duplicate
        # number is a row the database refuses rather than a finding a validator might report.
        self._add(
            rows.BillingInvoiceNumberRow(
                org_id=org_id, series_key=series_key, sequence=row.last_sequence
            )
        )
        self._session.flush()
        return row.last_sequence

    def peek(self, org_id: str, series_key: str) -> int:
        row = self._series(org_id, series_key)
        return row.last_sequence if row else 0

    def series(self, org_id: str) -> tuple[str, ...]:
        found = self._all(
            select(rows.BillingInvoiceSeriesRow)
            .where(rows.BillingInvoiceSeriesRow.org_id == org_id)
            .order_by(rows.BillingInvoiceSeriesRow.series_key)
        )
        return tuple(r.series_key for r in found)

    def allocated(self, org_id: str, series_key: str) -> tuple[int, ...]:
        """Every sequence this series has handed out — what a gapless audit reads."""
        found = self._all(
            select(rows.BillingInvoiceNumberRow)
            .where(
                rows.BillingInvoiceNumberRow.org_id == org_id,
                rows.BillingInvoiceNumberRow.series_key == series_key,
            )
            .order_by(rows.BillingInvoiceNumberRow.sequence)
        )
        return tuple(r.sequence for r in found)

    def _series(self, org_id: str, series_key: str):
        return self._one(
            select(rows.BillingInvoiceSeriesRow).where(
                rows.BillingInvoiceSeriesRow.org_id == org_id,
                rows.BillingInvoiceSeriesRow.series_key == series_key,
            )
        )


class SqlRenderedDocumentRepository(_Repo, RenderedDocumentRepository):
    """Append-only renderings, idempotent on the derived key."""

    def __init__(self, database, org_scope: str = "") -> None:
        super().__init__(database)
        self._org_scope = org_scope

    def append(self, rendered: RenderedDocument) -> RenderedDocument:
        projection = _rendered_projection(rendered)
        projection["org_id"] = self._org_of(rendered.document_id)
        existing = self._by_id(projection["id"])
        if existing is not None:
            return from_row(RenderedDocument, existing)
        try:
            with self._session.begin_nested():
                self._session.add(
                    rows.BillingRenderedDocumentRow(payload=encode(rendered), **projection)
                )
                self._session.flush()
        except IntegrityError:
            winner = self._by_id(projection["id"])
            if winner is None:  # pragma: no cover — the constraint guarantees a row
                raise
            return from_row(RenderedDocument, winner)
        return rendered

    def get(
        self, org_id: str, document_id: str, digest: str, fmt: RenderFormat
    ) -> RenderedDocument | None:
        found = self._all(
            select(rows.BillingRenderedDocumentRow)
            .where(
                rows.BillingRenderedDocumentRow.org_id == org_id,
                rows.BillingRenderedDocumentRow.document_id == document_id,
                rows.BillingRenderedDocumentRow.snapshot_digest == digest,
                rows.BillingRenderedDocumentRow.format == fmt.value,
            )
            .order_by(rows.BillingRenderedDocumentRow.template_version.desc())  # type: ignore[attr-defined]
        )
        return from_row(RenderedDocument, found[0]) if found else None

    def for_document(self, org_id: str, document_id: str) -> tuple[RenderedDocument, ...]:
        found = self._all(
            select(rows.BillingRenderedDocumentRow)
            .where(
                rows.BillingRenderedDocumentRow.org_id == org_id,
                rows.BillingRenderedDocumentRow.document_id == document_id,
            )
            .order_by(rows.BillingRenderedDocumentRow.rendered_at)
        )
        return tuple(from_row(RenderedDocument, r) for r in found)

    def _by_id(self, rendered_id: str):
        return self._one(
            select(rows.BillingRenderedDocumentRow).where(
                rows.BillingRenderedDocumentRow.id == rendered_id
            )
        )

    def _org_of(self, document_id: str) -> str:
        row = self._one(
            select(rows.BillingCommercialInvoiceRow).where(
                rows.BillingCommercialInvoiceRow.id == document_id
            )
        )
        return row.org_id if row else self._org_scope


class SqlInvoiceActivityRepository(_Repo, InvoiceActivityRepository):
    """Append-only invoice history."""

    def append(self, activity: InvoiceActivity) -> InvoiceActivity:
        self._add(
            rows.BillingInvoiceActivityRow(
                payload=encode(activity), **_activity_projection(activity)
            )
        )
        return activity

    def history(
        self, org_id: str, account_id: str = "", limit: int = 100
    ) -> tuple[InvoiceActivity, ...]:
        statement = select(rows.BillingInvoiceActivityRow).where(
            rows.BillingInvoiceActivityRow.org_id == org_id
        )
        if account_id:
            statement = statement.where(
                rows.BillingInvoiceActivityRow.account_id == account_id
            )
        found = self._all(
            statement.order_by(
                rows.BillingInvoiceActivityRow.at.desc(),  # type: ignore[attr-defined]
                rows.BillingInvoiceActivityRow.id.desc(),  # type: ignore[attr-defined]
            ).limit(limit)
        )
        return tuple(from_row(InvoiceActivity, r) for r in found)

    def for_document(self, org_id: str, document_id: str) -> tuple[InvoiceActivity, ...]:
        found = self._all(
            select(rows.BillingInvoiceActivityRow)
            .where(
                rows.BillingInvoiceActivityRow.org_id == org_id,
                rows.BillingInvoiceActivityRow.document_id == document_id,
            )
            .order_by(rows.BillingInvoiceActivityRow.at, rows.BillingInvoiceActivityRow.id)
        )
        return tuple(from_row(InvoiceActivity, r) for r in found)
