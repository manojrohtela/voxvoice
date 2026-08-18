"""Durable `billing.*` fact stream (M9.4).

The Foundation emits billing facts through an injected `EventEmitter` and never persists
one itself (ADR-0023 §5.2). This is the production implementation of that seam: it appends
each fact to `cp_billing_event` inside the caller's transaction, so a fact becomes durable
**with** the records it describes and disappears with them on rollback — the event log and
the ledger can never disagree about whether something happened (§A5).

It is deliberately both `EventEmitter` (write seam, for the Foundation) and a reader over
the same rows, mirroring the Epic 8 `SqlAuditStore` shape. It is an append point only:
there is no update or delete path.
"""

from __future__ import annotations

from billing.foundation import BillingEvent, BillingEventType, EventEmitter

from .database import BillingDatabase
from .repositories import SqlBillingEventRepository


class SqlBillingEventStore(EventEmitter):
    def __init__(self, database: BillingDatabase) -> None:
        self._repo = SqlBillingEventRepository(database)

    def emit(self, event: BillingEvent) -> None:
        self._repo.append(event)

    def for_subject(self, org_id: str, subject_id: str) -> tuple[BillingEvent, ...]:
        return self._repo.for_subject(org_id, subject_id)

    def of_type(
        self, org_id: str, subject_id: str, event_type: BillingEventType
    ) -> tuple[BillingEvent, ...]:
        return tuple(e for e in self.for_subject(org_id, subject_id) if e.type is event_type)
