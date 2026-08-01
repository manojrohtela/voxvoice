"""Durable audit store (M8.9) — the production Audit & Activity backing.

`SqlAuditStore` is the SQL implementation of **both** Customer-Platform audit seams:

  * the Foundation's write seam `events.EventEmitter` — `emit()` appends one immutable
    `cp_audit_event` row (never updates, never deletes); and
  * the Foundation's read seam `audit.AuditReader` — `query()`/`stream()` read the durable
    stream with filtering, pagination and time-range support.

It writes through the shared `Database.session`, so an emitted audit row is part of the same
transaction as the business write that produced it (the `SqlUnitOfWork` commits both together,
or rolls both back) — audit is atomic with the fact it records. No interface changes: the
Foundation is wired with this in place of the in-memory `RecordingEventEmitter` at the
persistence composition root, and neither the Foundation nor the Application knows the
difference.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func, or_
from sqlmodel import select

from api.foundation import (
    AuditPage,
    AuditQuery,
    AuditReader,
    AuditRecord,
    EventEmitter,
    FoundationEvent,
    project_of,
    workspace_of,
)

from .database import Database
from .models import AuditEventRow


def _row_to_record(r: AuditEventRow) -> AuditRecord:
    return AuditRecord(
        seq=r.seq or 0, id=r.id, type=r.type, ts=r.ts, org_id=r.org_id, actor=r.actor,
        resource=r.resource, workspace_id=r.workspace_id, project_id=r.project_id,
        data=dict(r.data),
    )


class SqlAuditStore(EventEmitter, AuditReader):
    def __init__(self, database: Database) -> None:
        self._db = database

    @property
    def _s(self):
        return self._db.session

    # ── write side (events.EventEmitter) — append-only ────────────────────────────
    def emit(self, event: FoundationEvent) -> None:
        self._s.add(
            AuditEventRow(
                id=event.id, type=event.type.value, ts=event.ts, org_id=event.org_id,
                actor=event.actor, resource=event.resource,
                workspace_id=workspace_of(event), project_id=project_of(event),
                data=dict(event.data),
            )
        )
        self._s.flush()  # durable on the enclosing Unit of Work's commit

    # ── read side (audit.AuditReader) ─────────────────────────────────────────────
    def _filtered(self, q: AuditQuery):
        stmt = select(AuditEventRow).where(AuditEventRow.org_id == q.org_id)
        if q.workspace_id is not None:
            stmt = stmt.where(
                or_(
                    AuditEventRow.workspace_id == q.workspace_id,
                    AuditEventRow.resource == q.workspace_id,
                )
            )
        if q.project_id is not None:
            stmt = stmt.where(
                or_(
                    AuditEventRow.project_id == q.project_id,
                    AuditEventRow.resource == q.project_id,
                )
            )
        if q.actor is not None:
            stmt = stmt.where(AuditEventRow.actor == q.actor)
        if q.event_types:
            stmt = stmt.where(AuditEventRow.type.in_(q.event_types))
        if q.resource is not None:
            stmt = stmt.where(AuditEventRow.resource == q.resource)
        if q.since is not None:
            stmt = stmt.where(AuditEventRow.ts >= q.since)
        if q.until is not None:
            stmt = stmt.where(AuditEventRow.ts <= q.until)
        return stmt

    def query(self, q: AuditQuery) -> AuditPage:
        base = self._filtered(q)
        total = self._s.scalar(select(func.count()).select_from(base.subquery())) or 0
        windowed = base.order_by(AuditEventRow.seq.desc()).offset(q.offset)
        if q.limit > 0:
            windowed = windowed.limit(q.limit)
        rows = self._s.exec(windowed).all()
        return AuditPage(
            records=tuple(_row_to_record(r) for r in rows),
            total=int(total), limit=q.limit, offset=q.offset,
        )

    def stream(self, org_id: str) -> Iterable[AuditRecord]:
        stmt = (
            select(AuditEventRow)
            .where(AuditEventRow.org_id == org_id)
            .order_by(AuditEventRow.seq.asc())
        )
        return tuple(_row_to_record(r) for r in self._s.exec(stmt).all())
