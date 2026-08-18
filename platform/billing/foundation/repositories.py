"""Repository **interfaces** (M9.2) — the persistence seam, contracts only.

ADR-0031 §8/§13 and the M9.2 scope: *no persistence implementation.* These ABCs let the
durable System of Record land at M9.4 (inside the Epic 8 persistence layer) without
touching a service — dependency inversion, §D6/§A7 (one System of Record, behind an
interface). No SQL, no ORM, no in-memory store lives here or anywhere in the Foundation.

**Organization isolation is structural (§A11.2):** every org-owned lookup takes an
`org_id` and filters on it. A caller cannot read across tenants because the query shape
forbids it.

**Append-only is expressed in the contracts (ADR-0023 RS1/IN2):** the ledger, credit,
usage, invoice, credit-note, and event repositories declare `append`/`add` and read
methods and deliberately declare **no update and no delete**. An append-only posture that
ships with an `update()` is not append-only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .accounts import BillingAccount
from .catalog import CommercialCatalog, Feature, MeterDefinition, Plan
from .contracts import Contract, License
from .domain import BillingPeriod
from .entitlement import EntitlementGrant, EntitlementSnapshot
from .events import BillingEvent
from .invoicing import BillingRun, CreditNote, Invoice
from .ledger import CreditGrant, CreditMovement, LedgerEntry
from .metering import UsageAggregate, UsageQuarantine, UsageRecord
from .payments import DunningProcess, Payment, PaymentInstrument, ReconciliationFinding
from .pricing import PriceBook
from .refunds import Dispute, Refund
from .subscription import Subscription
from .tax import TaxDetermination, TaxProfile

# ── accounts ────────────────────────────────────────────────────────────────────


class BillingAccountRepository(ABC):
    @abstractmethod
    def add(self, account: BillingAccount) -> BillingAccount: ...
    @abstractmethod
    def get(self, org_id: str, account_id: str) -> BillingAccount | None: ...
    @abstractmethod
    def by_org(self, org_id: str) -> BillingAccount | None: ...
    @abstractmethod
    def children_of(self, org_id: str, parent_account_id: str) -> tuple[BillingAccount, ...]: ...
    @abstractmethod
    def replace(self, account: BillingAccount) -> BillingAccount:
        """Accounts are mutable aggregates (status, contact, address) — unlike revenue
        records, which are append-only. Replacement is whole-aggregate, never partial."""


# ── catalog + pricing ───────────────────────────────────────────────────────────


class PlanRepository(ABC):
    @abstractmethod
    def add(self, plan: Plan) -> Plan: ...
    @abstractmethod
    def get(self, code: str, version: int, *, org_id: str = "") -> Plan | None: ...
    @abstractmethod
    def published(self, *, org_id: str = "") -> tuple[Plan, ...]: ...


class FeatureRepository(ABC):
    @abstractmethod
    def add(self, feature: Feature) -> Feature: ...
    @abstractmethod
    def get(self, code: str) -> Feature | None: ...
    @abstractmethod
    def all(self) -> tuple[Feature, ...]: ...


class MeterRepository(ABC):
    @abstractmethod
    def add(self, meter: MeterDefinition) -> MeterDefinition: ...
    @abstractmethod
    def get(self, code: str, *, org_id: str = "") -> MeterDefinition | None: ...
    @abstractmethod
    def all(self, *, org_id: str = "") -> tuple[MeterDefinition, ...]: ...


class CatalogSnapshotRepository(ABC):
    """Immutable catalog snapshots — rating and entitlement resolve against a snapshot,
    never against mutable global state (the ADR-0019 discipline)."""

    @abstractmethod
    def publish(self, catalog: CommercialCatalog) -> CommercialCatalog: ...
    @abstractmethod
    def latest(self, *, org_id: str = "") -> CommercialCatalog | None: ...
    @abstractmethod
    def version(self, version: int, *, org_id: str = "") -> CommercialCatalog | None: ...


class PriceBookRepository(ABC):
    @abstractmethod
    def add(self, book: PriceBook) -> PriceBook: ...
    @abstractmethod
    def get(self, book_id: str, version: int) -> PriceBook | None: ...
    @abstractmethod
    def by_code(self, code: str, *, org_id: str = "") -> tuple[PriceBook, ...]: ...


# ── subscriptions + entitlements ────────────────────────────────────────────────


class SubscriptionRepository(ABC):
    @abstractmethod
    def add(self, subscription: Subscription) -> Subscription: ...
    @abstractmethod
    def get(self, org_id: str, subscription_id: str) -> Subscription | None: ...
    @abstractmethod
    def by_account(self, org_id: str, account_id: str) -> tuple[Subscription, ...]: ...
    @abstractmethod
    def replace(self, subscription: Subscription) -> Subscription: ...


class EntitlementGrantRepository(ABC):
    @abstractmethod
    def add(self, grant: EntitlementGrant) -> EntitlementGrant: ...
    @abstractmethod
    def active_for(self, org_id: str, account_id: str) -> tuple[EntitlementGrant, ...]: ...
    @abstractmethod
    def revoke(self, org_id: str, grant_id: str, revoked_at: float) -> EntitlementGrant:
        """Revocation is a recorded transition on the grant, not a delete: *why* an
        entitlement went away is itself a fact worth keeping."""


class EntitlementSnapshotRepository(ABC):
    """Snapshots are immutable and versioned — published, never updated."""

    @abstractmethod
    def publish(self, snapshot: EntitlementSnapshot) -> EntitlementSnapshot: ...
    @abstractmethod
    def latest(self, org_id: str, account_id: str) -> EntitlementSnapshot | None: ...
    @abstractmethod
    def version(self, org_id: str, account_id: str, version: int) -> EntitlementSnapshot | None: ...


# ── metering (append-only) ──────────────────────────────────────────────────────


class UsageRecordRepository(ABC):
    @abstractmethod
    def append(self, record: UsageRecord) -> UsageRecord:
        """Idempotent by `(source event id, meter)` — re-appending the same source event
        is a no-op, which is what turns at-least-once delivery into exactly-once billing
        (ADR-0023 M4). Implementations MUST honour that key."""

    @abstractmethod
    def exists(self, event_id: str, meter_code: str) -> bool: ...
    @abstractmethod
    def for_period(
        self, org_id: str, account_id: str, meter_code: str, period: BillingPeriod
    ) -> tuple[UsageRecord, ...]: ...


class UsageAggregateRepository(ABC):
    @abstractmethod
    def upsert(self, aggregate: UsageAggregate) -> UsageAggregate:
        """Aggregates are *derived* — recomputable from records at any time — so they may
        be replaced. Sealing one is a one-way door (M7)."""

    @abstractmethod
    def get(
        self, org_id: str, account_id: str, meter_code: str, period: BillingPeriod
    ) -> UsageAggregate | None: ...
    @abstractmethod
    def for_period(
        self, org_id: str, account_id: str, period: BillingPeriod
    ) -> tuple[UsageAggregate, ...]: ...


class UsageQuarantineRepository(ABC):
    @abstractmethod
    def append(self, quarantined: UsageQuarantine) -> UsageQuarantine: ...
    @abstractmethod
    def pending(self, limit: int = 100) -> tuple[UsageQuarantine, ...]: ...


# ── ledger + credits (append-only, no update, no delete) ────────────────────────


class LedgerRepository(ABC):
    @abstractmethod
    def append(self, entry: LedgerEntry) -> LedgerEntry: ...
    @abstractmethod
    def for_account(self, org_id: str, account_id: str) -> tuple[LedgerEntry, ...]: ...
    @abstractmethod
    def for_invoice(self, org_id: str, invoice_id: str) -> tuple[LedgerEntry, ...]: ...


class CreditRepository(ABC):
    @abstractmethod
    def grant(self, grant: CreditGrant) -> CreditGrant: ...
    @abstractmethod
    def append_movement(self, movement: CreditMovement) -> CreditMovement: ...
    @abstractmethod
    def grants_for(self, org_id: str, account_id: str) -> tuple[CreditGrant, ...]: ...
    @abstractmethod
    def movements_for(self, org_id: str, account_id: str) -> tuple[CreditMovement, ...]: ...


# ── invoicing (issued documents are immutable) ─────────────────────────────────


class InvoiceRepository(ABC):
    @abstractmethod
    def add(self, invoice: Invoice) -> Invoice: ...
    @abstractmethod
    def get(self, org_id: str, invoice_id: str) -> Invoice | None: ...
    @abstractmethod
    def for_account(self, org_id: str, account_id: str) -> tuple[Invoice, ...]: ...
    @abstractmethod
    def replace_draft(self, invoice: Invoice) -> Invoice:
        """Drafts may be replaced; an **issued** invoice may not (ADR-0023 IN2).
        Implementations MUST reject replacement of an issued invoice."""


class InvoiceNumberAllocator(ABC):
    """Gapless, sequential allocation per legal entity/jurisdiction, exactly once, never
    reused — including across retries, failures, and concurrent runs (ADR-0023 IN4)."""

    @abstractmethod
    def allocate(self, entity_code: str, jurisdiction: str) -> int: ...


class CreditNoteRepository(ABC):
    @abstractmethod
    def add(self, note: CreditNote) -> CreditNote: ...
    @abstractmethod
    def for_invoice(self, org_id: str, invoice_id: str) -> tuple[CreditNote, ...]: ...


class BillingRunRepository(ABC):
    @abstractmethod
    def upsert(self, run: BillingRun) -> BillingRun:
        """Keyed by (account, period) so re-running a period resumes rather than
        duplicates — the idempotent, resumable billing run (Blueprint §10)."""

    @abstractmethod
    def get(self, org_id: str, account_id: str, period: BillingPeriod) -> BillingRun | None: ...


# ── payments, refunds, tax ─────────────────────────────────────────────────────


class PaymentRepository(ABC):
    @abstractmethod
    def add(self, payment: Payment) -> Payment: ...
    @abstractmethod
    def get(self, org_id: str, payment_id: str) -> Payment | None: ...
    @abstractmethod
    def for_invoice(self, org_id: str, invoice_id: str) -> tuple[Payment, ...]: ...
    @abstractmethod
    def replace(self, payment: Payment) -> Payment: ...


class PaymentInstrumentRepository(ABC):
    @abstractmethod
    def add(self, instrument: PaymentInstrument) -> PaymentInstrument: ...
    @abstractmethod
    def for_account(self, org_id: str, account_id: str) -> tuple[PaymentInstrument, ...]: ...
    @abstractmethod
    def remove(self, org_id: str, instrument_id: str) -> None:
        """Detaching a stored instrument is a customer right and touches no revenue
        record — the token reference goes, the payment history stays."""


class DunningRepository(ABC):
    @abstractmethod
    def upsert(self, process: DunningProcess) -> DunningProcess: ...
    @abstractmethod
    def for_payment(self, org_id: str, payment_id: str) -> DunningProcess | None: ...


class ReconciliationRepository(ABC):
    @abstractmethod
    def append(self, finding: ReconciliationFinding) -> ReconciliationFinding: ...
    @abstractmethod
    def divergent(self, limit: int = 100) -> tuple[ReconciliationFinding, ...]: ...


class RefundRepository(ABC):
    @abstractmethod
    def add(self, refund: Refund) -> Refund: ...
    @abstractmethod
    def get(self, org_id: str, refund_id: str) -> Refund | None: ...
    @abstractmethod
    def by_request_key(self, org_id: str, request_key: str) -> Refund | None: ...
    @abstractmethod
    def for_payment(self, org_id: str, payment_id: str) -> tuple[Refund, ...]: ...
    @abstractmethod
    def replace(self, refund: Refund) -> Refund: ...


class DisputeRepository(ABC):
    @abstractmethod
    def add(self, dispute: Dispute) -> Dispute: ...
    @abstractmethod
    def for_payment(self, org_id: str, payment_id: str) -> tuple[Dispute, ...]: ...
    @abstractmethod
    def replace(self, dispute: Dispute) -> Dispute: ...


class TaxProfileRepository(ABC):
    @abstractmethod
    def upsert(self, profile: TaxProfile) -> TaxProfile: ...
    @abstractmethod
    def for_account(self, org_id: str, account_id: str) -> TaxProfile | None: ...


class TaxDeterminationRepository(ABC):
    """Determinations are immutable evidence — appended, never recomputed (TX2/TX4)."""

    @abstractmethod
    def append(self, determination: TaxDetermination) -> TaxDetermination: ...
    @abstractmethod
    def for_invoice(self, org_id: str, invoice_id: str) -> TaxDetermination | None: ...


# ── contracts, licenses, events ────────────────────────────────────────────────


class ContractRepository(ABC):
    @abstractmethod
    def add(self, contract: Contract) -> Contract: ...
    @abstractmethod
    def get(self, org_id: str, contract_id: str) -> Contract | None: ...
    @abstractmethod
    def for_account(self, org_id: str, account_id: str) -> tuple[Contract, ...]: ...
    @abstractmethod
    def replace(self, contract: Contract) -> Contract: ...


class LicenseRepository(ABC):
    @abstractmethod
    def add(self, license_: License) -> License: ...
    @abstractmethod
    def get(self, org_id: str, license_id: str) -> License | None: ...
    @abstractmethod
    def for_account(self, org_id: str, account_id: str) -> tuple[License, ...]: ...
    @abstractmethod
    def replace(self, license_: License) -> License: ...


class BillingEventRepository(ABC):
    """Append-only. Billing keeps no private audit log — this is the durable hand-off
    point to the Epic 8 Audit Platform (ADR-0023 §5.2)."""

    @abstractmethod
    def append(self, event: BillingEvent) -> BillingEvent: ...
    @abstractmethod
    def for_subject(self, org_id: str, subject_id: str) -> tuple[BillingEvent, ...]: ...
