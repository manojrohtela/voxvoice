"""Audit read-model + query seam (M8.9) — additive.

The **read side** of the Audit & Activity Platform. The write side is the existing
`EventEmitter` seam (unchanged): domain services emit one immutable `FoundationEvent` per
fact. This module adds the durable **read** contract those events are projected through:

  * `AuditRecord` — a persisted, enriched projection of a `FoundationEvent` (adds a stable
    monotonic `seq` for ordering/pagination and hoists `workspace_id`/`project_id` out of
    `data` for efficient filtering).
  * `AuditQuery` — an immutable filter/pagination/time-range descriptor.
  * `AuditPage` — a page of records plus the total count (so a UI can paginate / infinite
    scroll).
  * `AuditReader` — the read seam. The in-memory `RecordingEventEmitter` and the SQL
    `SqlAuditStore` both implement it; neither the Foundation nor the Application knows which
    backing store is wired (dependency inversion, exactly like the repository interfaces).

**Append-only, immutable (Constitution §A5/§A11.2):** there is no update or delete on the
audit stream — a record, once written, is never changed. Activity feeds are *projections*
built by querying this stream; they hold no authoritative state of their own.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .events import FoundationEvent

# Data keys the lifecycle events carry that we hoist to first-class, indexable fields.
_WORKSPACE_KEY = "workspace_id"
_PROJECT_KEY = "project_id"


def workspace_of(event: FoundationEvent) -> str | None:
    """The workspace an event pertains to (from its data payload), if any."""
    return event.data.get(_WORKSPACE_KEY)


def project_of(event: FoundationEvent) -> str | None:
    """The project an event pertains to (from its data payload), if any."""
    return event.data.get(_PROJECT_KEY)


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """One durable, immutable audit fact — a projection of a `FoundationEvent`."""

    seq: int
    id: str
    type: str
    ts: float
    org_id: str
    actor: str = ""
    resource: str = ""
    workspace_id: str | None = None
    project_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def of(cls, seq: int, event: FoundationEvent) -> AuditRecord:
        return cls(
            seq=seq, id=event.id, type=event.type.value, ts=event.ts, org_id=event.org_id,
            actor=event.actor, resource=event.resource,
            workspace_id=workspace_of(event), project_id=project_of(event),
            data=dict(event.data),
        )

    def as_dict(self) -> dict[str, Any]:
        """The feed-item shape the Studio consumes (backward-compatible superset of the
        pre-M8.9 `{type, ts, actor, resource, data}` feed item)."""
        return {
            "seq": self.seq, "type": self.type, "ts": self.ts, "actor": self.actor,
            "resource": self.resource, "workspace_id": self.workspace_id,
            "project_id": self.project_id, "data": self.data,
        }


@dataclass(frozen=True, slots=True)
class AuditQuery:
    """An immutable filter over the audit stream. Every field is optional except `org_id`
    (isolation is never optional — a query can never cross tenants)."""

    org_id: str
    workspace_id: str | None = None
    project_id: str | None = None
    actor: str | None = None
    event_types: tuple[str, ...] = ()
    resource: str | None = None
    since: float | None = None   # inclusive lower bound on ts
    until: float | None = None   # inclusive upper bound on ts
    limit: int = 50
    offset: int = 0

    def matches(self, rec: AuditRecord) -> bool:
        """Pure predicate used by the in-memory reader (the SQL reader mirrors it in SQL)."""
        if rec.org_id != self.org_id:
            return False
        if self.workspace_id is not None and self.workspace_id not in (
            rec.workspace_id, rec.resource
        ):
            return False
        if self.project_id is not None and self.project_id not in (
            rec.project_id, rec.resource
        ):
            return False
        if self.actor is not None and rec.actor != self.actor:
            return False
        if self.event_types and rec.type not in self.event_types:
            return False
        if self.resource is not None and rec.resource != self.resource:
            return False
        if self.since is not None and rec.ts < self.since:
            return False
        if self.until is not None and rec.ts > self.until:
            return False
        return True


@dataclass(frozen=True, slots=True)
class AuditPage:
    """A page of audit records plus the total number matching the filter (pagination)."""

    records: tuple[AuditRecord, ...]
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.records) < self.total

    @property
    def next_offset(self) -> int | None:
        return self.offset + len(self.records) if self.has_more else None


class AuditReader(ABC):
    """The durable read seam over the append-only audit stream."""

    @abstractmethod
    def query(self, q: AuditQuery) -> AuditPage:
        """Return the matching page (most-recent-first) plus the total match count."""

    @abstractmethod
    def stream(self, org_id: str) -> Iterable[AuditRecord]:
        """Iterate every record for an org in append order (oldest-first) — the basis for
        a projection rebuild. Kept separate from `query` so a rebuild never paginates."""


class NullAuditReader(AuditReader):
    """A reader over no events (used when no audit backing is wired)."""

    def query(self, q: AuditQuery) -> AuditPage:
        return AuditPage(records=(), total=0, limit=q.limit, offset=q.offset)

    def stream(self, org_id: str) -> Iterable[AuditRecord]:
        return ()


def paginate(
    records: list[AuditRecord], q: AuditQuery
) -> AuditPage:
    """Shared in-memory pagination helper: order most-recent-first (by seq desc), then
    apply offset/limit over the already-filtered list. Deterministic."""
    ordered = sorted(records, key=lambda r: r.seq, reverse=True)
    total = len(ordered)
    window = ordered[q.offset: q.offset + q.limit] if q.limit > 0 else ordered[q.offset:]
    return AuditPage(records=tuple(window), total=total, limit=q.limit, offset=q.offset)
