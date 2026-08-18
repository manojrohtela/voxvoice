"""Application-level events (M9.3) — orchestration altitude, disjoint vocabulary.

Two altitudes, deliberately kept apart:

* **Foundation events** (`billing.*`, M9.2) record that a *business fact* happened — an
  invoice was issued, a subscription was created.
* **Application events** (`use_case.*`, `billing_app.*`) record that a *use case ran to an
  outcome* — the orchestration completed or was rejected, a composite operation finished.

The Application **never re-emits a Foundation event**. The two enums share no value, and a
test asserts the disjointness so the vocabularies cannot drift together.

Staged events become observable only on commit (see `unit_of_work`), which is the §A5
durable-outbox shape expressed at the transaction boundary.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

PREFIX_APP_EVENT = "bap"


class ApplicationEventType(StrEnum):
    """The billing application-event vocabulary. Dotted, lowercase, stable, additive —
    and deliberately disjoint from `BillingEventType`."""

    # cross-cutting: every dispatched command produces exactly one of these
    USE_CASE_COMPLETED = "use_case.completed"
    USE_CASE_REJECTED = "use_case.rejected"

    # composite orchestration outcomes (staged transactionally by use cases)
    ACCOUNT_PROVISIONED = "billing_app.account_provisioned"
    ACCOUNT_HIERARCHY_LINKED = "billing_app.account_hierarchy_linked"
    CATALOG_PUBLISHED = "billing_app.catalog_published"
    PRICE_BOOK_PUBLISHED = "billing_app.price_book_published"
    SUBSCRIPTION_PROVISIONED = "billing_app.subscription_provisioned"
    SUBSCRIPTION_TRANSITIONED = "billing_app.subscription_transitioned"
    ENTITLEMENTS_REPUBLISHED = "billing_app.entitlements_republished"
    USAGE_INGESTED = "billing_app.usage_ingested"
    USAGE_REJECTED = "billing_app.usage_rejected"
    CREDIT_ISSUED = "billing_app.credit_issued"
    LEDGER_POSTED = "billing_app.ledger_posted"
    INVOICE_PREPARED = "billing_app.invoice_prepared"
    INVOICE_FINALIZED = "billing_app.invoice_finalized"
    PAYMENT_ORCHESTRATED = "billing_app.payment_orchestrated"
    REFUND_ORCHESTRATED = "billing_app.refund_orchestrated"
    TAX_PROFILE_SYNCHRONIZED = "billing_app.tax_profile_synchronized"
    TAX_EVIDENCE_RECORDED = "billing_app.tax_evidence_recorded"
    CONTRACT_ORCHESTRATED = "billing_app.contract_orchestrated"
    CHARGES_RATED = "billing_app.charges_rated"  # M9.12 (additive)
    LICENSE_ORCHESTRATED = "billing_app.license_orchestrated"
    COLLECTION_ORCHESTRATED = "billing_app.collection_orchestrated"  # M9.17 (additive)
    REVENUE_RECOGNIZED = "billing_app.revenue_recognized"  # M9.18 (additive)
    INTEGRITY_RECONCILED = "billing_app.integrity_reconciled"  # M9.19 (additive)
    INCIDENT_CORRELATED = "billing_app.incident_correlated"  # M9.20 (additive)
    INVESTIGATION_RECORDED = "billing_app.investigation_recorded"  # M9.21 (additive)
    REPORT_PREPARED = "billing_app.report_prepared"  # M9.22 (additive)
    CONSOLIDATION_ADVANCED = "billing_app.consolidation_advanced"  # M9.23 (additive)


@dataclass(frozen=True, slots=True)
class ApplicationEvent:
    """One immutable orchestration fact. Org-scoped and actor-attributed, so the
    application audit trail answers *who ran what, with what outcome*."""

    id: str
    type: ApplicationEventType
    ts: float
    use_case: str
    outcome: str
    org_id: str = ""
    actor: str = ""
    subject_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class ApplicationEventEmitter(ABC):
    """The emission seam. The Application depends on this ABC only."""

    @abstractmethod
    def emit(self, event: ApplicationEvent) -> None: ...


class NullApplicationEventEmitter(ApplicationEventEmitter):
    def emit(self, event: ApplicationEvent) -> None:  # noqa: D102
        return None


class RecordingApplicationEventEmitter(ApplicationEventEmitter):
    """Deterministic, append-only recorder — the default and the application audit trail."""

    def __init__(self) -> None:
        self._events: list[ApplicationEvent] = []

    def emit(self, event: ApplicationEvent) -> None:
        self._events.append(event)

    @property
    def events(self) -> tuple[ApplicationEvent, ...]:
        return tuple(self._events)

    def of_type(self, event_type: ApplicationEventType) -> tuple[ApplicationEvent, ...]:
        return tuple(e for e in self._events if e.type is event_type)
