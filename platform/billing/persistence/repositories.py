"""SQLModel-backed repository implementations (M9.4).

The production implementations of the Foundation's 29 repository **interfaces** — satisfying
`billing.foundation.repositories.*` exactly, with no interface change. Each repository:

* reads and writes the shared `BillingDatabase.session` and **never commits or rolls back**
  — `SqlBillingUnitOfWork` owns the transaction boundary, opened by the Application layer.
  Writes here become durable together on commit and vanish together on rollback;
* **maps only** (row ↔ frozen aggregate at the boundary); no business rule, no
  authorization, no derivation the Foundation owns;
* is **org-scoped by construction** — every org-owned read filters on `org_id`, because the
  contract's query shape makes cross-tenant reads unexpressible (§A11.2).

Three storage-level invariants are enforced *here* rather than trusted from above, because
they are the ones a database is uniquely able to guarantee:

* **Append-only** (ledger, credits, usage, credit notes, tax determinations, findings,
  events): these classes expose `append`/`add` and reads only — there is no update or delete
  path to call (ADR-0023 RS1).
* **Issued documents are immutable**: `replace_draft` refuses a non-draft invoice with
  `ImmutabilityViolation`, and a published entitlement snapshot or catalog version is
  written once (IN2 / E4).
* **Exactly-once usage**: `append` is idempotent on `(source event id, meter)` and the table
  carries the matching unique constraint, so at-least-once delivery cannot become
  double-billing (M4).
"""

from __future__ import annotations

from dataclasses import replace

from sqlmodel import select

from billing.foundation import (
    BillingAccount,
    BillingAccountRepository,
    BillingEvent,
    BillingEventRepository,
    BillingPeriod,
    BillingRun,
    BillingRunRepository,
    CatalogSnapshotRepository,
    CommercialCatalog,
    Contract,
    ContractRepository,
    CreditGrant,
    CreditMovement,
    CreditNote,
    CreditNoteRepository,
    CreditRepository,
    Dispute,
    DisputeRepository,
    DunningProcess,
    DunningRepository,
    EntitlementGrant,
    EntitlementGrantRepository,
    EntitlementSnapshot,
    EntitlementSnapshotRepository,
    Feature,
    FeatureRepository,
    ImmutabilityViolation,
    Invoice,
    InvoiceNumberAllocator,
    InvoiceRepository,
    InvoiceState,
    LedgerEntry,
    LedgerRepository,
    License,
    LicenseRepository,
    MeterDefinition,
    MeterRepository,
    Payment,
    PaymentInstrument,
    PaymentInstrumentRepository,
    PaymentRepository,
    Plan,
    PlanRepository,
    PriceBook,
    PriceBookRepository,
    ReconciliationFinding,
    ReconciliationRepository,
    Refund,
    RefundRepository,
    Subscription,
    SubscriptionRepository,
    TaxDetermination,
    TaxDeterminationRepository,
    TaxProfile,
    TaxProfileRepository,
    UsageAggregate,
    UsageAggregateRepository,
    UsageQuarantine,
    UsageQuarantineRepository,
    UsageRecord,
    UsageRecordRepository,
)

from . import mapping as m
from . import models as rows
from .database import BillingDatabase


class _Repo:
    """Shared plumbing: a session handle and flush. Deliberately tiny — a repository that
    grows behaviour has stopped being a repository."""

    def __init__(self, database: BillingDatabase) -> None:
        self._db = database

    @property
    def _session(self):
        return self._db.session

    def _add(self, row):
        self._session.add(row)
        self._session.flush()  # assign defaults + surface constraint violations now
        return row

    def _all(self, statement):
        return list(self._session.exec(statement))

    def _one(self, statement):
        return self._session.exec(statement).first()


# ── accounts ────────────────────────────────────────────────────────────────────


class SqlBillingAccountRepository(_Repo, BillingAccountRepository):
    def add(self, account: BillingAccount) -> BillingAccount:
        self._add(m.account_row(account))
        return account

    def get(self, org_id: str, account_id: str) -> BillingAccount | None:
        row = self._one(
            select(rows.BillingAccountRow).where(
                rows.BillingAccountRow.id == account_id,
                rows.BillingAccountRow.org_id == org_id,
            )
        )
        return m.from_row(BillingAccount, row) if row else None

    def by_org(self, org_id: str) -> BillingAccount | None:
        row = self._one(
            select(rows.BillingAccountRow).where(rows.BillingAccountRow.org_id == org_id)
        )
        return m.from_row(BillingAccount, row) if row else None

    def children_of(self, org_id: str, parent_account_id: str) -> tuple[BillingAccount, ...]:
        found = self._all(
            select(rows.BillingAccountRow)
            .where(rows.BillingAccountRow.parent_account_id == parent_account_id)
            .order_by(rows.BillingAccountRow.created_at, rows.BillingAccountRow.id)
        )
        # The child account may sit in its own organization (a billing hierarchy is not an
        # identity hierarchy, Blueprint §17); the parent id is the authorization edge.
        return tuple(m.from_row(BillingAccount, r) for r in found)

    def replace(self, account: BillingAccount) -> BillingAccount:
        row = self._one(
            select(rows.BillingAccountRow).where(
                rows.BillingAccountRow.id == account.id,
                rows.BillingAccountRow.org_id == account.org_id,
            )
        )
        if row is None:
            return self.add(account)
        m.update_row(row, account, m.account_projection(account))
        self._session.flush()
        return account


# ── catalog + pricing ───────────────────────────────────────────────────────────


class SqlPlanRepository(_Repo, PlanRepository):
    def add(self, plan: Plan) -> Plan:
        """Store a plan version, keyed by `(org, code, version)`.

        **Defect found at M9.5 and fixed here.** `PlanRepository` declares `add` and no
        `replace`, and the Application legitimately calls `add` twice for one plan version:
        once to define it and once to store the published copy (`publish_plan` flips
        `published` and re-adds). Against the in-memory fakes M9.3 tested with, the second
        call overwrote; against a real table with the uniqueness constraint the catalog
        needs, it raised `IntegrityError` and the first HTTP request to publish a plan
        returned 500.

        A plan version is mutable until it is published, and the contract offers no other
        write, so `add` means *store this version* — an upsert on the natural key. Fixing it
        here keeps the Foundation contract and the frozen Application untouched; the
        alternative (a `replace` on the contract) would have changed both."""
        row = self._one(
            select(rows.BillingPlanRow).where(
                rows.BillingPlanRow.org_id == plan.org_id,
                rows.BillingPlanRow.code == plan.code,
                rows.BillingPlanRow.version == plan.version,
            )
        )
        if row is None:
            self._add(m.plan_row(plan))
            return plan
        m.update_row(row, plan, m.plan_projection(plan))
        self._session.flush()
        return plan

    def get(self, code: str, version: int, *, org_id: str = "") -> Plan | None:
        row = self._one(
            select(rows.BillingPlanRow).where(
                rows.BillingPlanRow.code == code,
                rows.BillingPlanRow.version == version,
                rows.BillingPlanRow.org_id == org_id,
            )
        )
        return m.from_row(Plan, row) if row else None

    def published(self, *, org_id: str = "") -> tuple[Plan, ...]:
        found = self._all(
            select(rows.BillingPlanRow)
            .where(rows.BillingPlanRow.org_id == org_id, rows.BillingPlanRow.published)
            .order_by(rows.BillingPlanRow.code, rows.BillingPlanRow.version)
        )
        return tuple(m.from_row(Plan, r) for r in found)


class SqlFeatureRepository(_Repo, FeatureRepository):
    def add(self, feature: Feature) -> Feature:
        self._add(m.feature_row(feature))
        return feature

    def get(self, code: str) -> Feature | None:
        row = self._one(select(rows.BillingFeatureRow).where(rows.BillingFeatureRow.code == code))
        return m.from_row(Feature, row) if row else None

    def all(self) -> tuple[Feature, ...]:
        found = self._all(select(rows.BillingFeatureRow).order_by(rows.BillingFeatureRow.code))
        return tuple(m.from_row(Feature, r) for r in found)


class SqlMeterRepository(_Repo, MeterRepository):
    def add(self, meter: MeterDefinition) -> MeterDefinition:
        self._add(m.meter_row(meter))
        return meter

    def get(self, code: str, *, org_id: str = "") -> MeterDefinition | None:
        row = self._one(
            select(rows.BillingMeterRow).where(
                rows.BillingMeterRow.code == code, rows.BillingMeterRow.org_id == org_id
            )
        )
        return m.from_row(MeterDefinition, row) if row else None

    def all(self, *, org_id: str = "") -> tuple[MeterDefinition, ...]:
        found = self._all(
            select(rows.BillingMeterRow)
            .where(rows.BillingMeterRow.org_id == org_id)
            .order_by(rows.BillingMeterRow.code)
        )
        return tuple(m.from_row(MeterDefinition, r) for r in found)


class SqlCatalogSnapshotRepository(_Repo, CatalogSnapshotRepository):
    """Catalog versions are published, never updated — `publish` only appends."""

    def __init__(self, database: BillingDatabase, *, org_id: str = "") -> None:
        super().__init__(database)
        self._org_id = org_id

    def publish(self, catalog: CommercialCatalog) -> CommercialCatalog:
        self._add(m.catalog_row(catalog, org_id=self._org_id))
        return catalog

    def latest(self, *, org_id: str = "") -> CommercialCatalog | None:
        row = self._one(
            select(rows.BillingCatalogSnapshotRow)
            .where(rows.BillingCatalogSnapshotRow.org_id == org_id)
            .order_by(rows.BillingCatalogSnapshotRow.version.desc())  # type: ignore[attr-defined]
        )
        return m.from_row(CommercialCatalog, row) if row else None

    def version(self, version: int, *, org_id: str = "") -> CommercialCatalog | None:
        row = self._one(
            select(rows.BillingCatalogSnapshotRow).where(
                rows.BillingCatalogSnapshotRow.org_id == org_id,
                rows.BillingCatalogSnapshotRow.version == version,
            )
        )
        return m.from_row(CommercialCatalog, row) if row else None


class SqlPriceBookRepository(_Repo, PriceBookRepository):
    def add(self, book: PriceBook) -> PriceBook:
        self._add(m.price_book_row(book))
        return book

    def get(self, book_id: str, version: int) -> PriceBook | None:
        row = self._one(
            select(rows.BillingPriceBookRow).where(
                rows.BillingPriceBookRow.id == book_id,
                rows.BillingPriceBookRow.version == version,
            )
        )
        return m.from_row(PriceBook, row) if row else None

    def by_code(self, code: str, *, org_id: str = "") -> tuple[PriceBook, ...]:
        found = self._all(
            select(rows.BillingPriceBookRow)
            .where(
                rows.BillingPriceBookRow.code == code,
                rows.BillingPriceBookRow.org_id == org_id,
            )
            .order_by(rows.BillingPriceBookRow.version)
        )
        return tuple(m.from_row(PriceBook, r) for r in found)


# ── subscriptions + entitlements ────────────────────────────────────────────────


class SqlSubscriptionRepository(_Repo, SubscriptionRepository):
    def add(self, subscription: Subscription) -> Subscription:
        self._add(m.subscription_row(subscription))
        return subscription

    def get(self, org_id: str, subscription_id: str) -> Subscription | None:
        row = self._one(
            select(rows.BillingSubscriptionRow).where(
                rows.BillingSubscriptionRow.id == subscription_id,
                rows.BillingSubscriptionRow.org_id == org_id,
            )
        )
        return m.from_row(Subscription, row) if row else None

    def by_account(self, org_id: str, account_id: str) -> tuple[Subscription, ...]:
        found = self._all(
            select(rows.BillingSubscriptionRow)
            .where(
                rows.BillingSubscriptionRow.org_id == org_id,
                rows.BillingSubscriptionRow.account_id == account_id,
            )
            .order_by(rows.BillingSubscriptionRow.id)
        )
        return tuple(m.from_row(Subscription, r) for r in found)

    def replace(self, subscription: Subscription) -> Subscription:
        row = self._one(
            select(rows.BillingSubscriptionRow).where(
                rows.BillingSubscriptionRow.id == subscription.id,
                rows.BillingSubscriptionRow.org_id == subscription.org_id,
            )
        )
        if row is None:
            return self.add(subscription)
        m.update_row(row, subscription, m.subscription_projection(subscription))
        self._session.flush()
        return subscription


class SqlEntitlementGrantRepository(_Repo, EntitlementGrantRepository):
    def add(self, grant: EntitlementGrant) -> EntitlementGrant:
        self._add(m.grant_row(grant))
        return grant

    def active_for(self, org_id: str, account_id: str) -> tuple[EntitlementGrant, ...]:
        found = self._all(
            select(rows.BillingEntitlementGrantRow)
            .where(
                rows.BillingEntitlementGrantRow.org_id == org_id,
                rows.BillingEntitlementGrantRow.account_id == account_id,
                rows.BillingEntitlementGrantRow.revoked_at.is_(None),  # type: ignore[union-attr]
            )
            .order_by(rows.BillingEntitlementGrantRow.granted_at)
        )
        return tuple(m.from_row(EntitlementGrant, r) for r in found)

    def revoke(self, org_id: str, grant_id: str, revoked_at: float) -> EntitlementGrant:
        row = self._one(
            select(rows.BillingEntitlementGrantRow).where(
                rows.BillingEntitlementGrantRow.id == grant_id,
                rows.BillingEntitlementGrantRow.org_id == org_id,
            )
        )
        if row is None:
            raise ImmutabilityViolation(f"entitlement grant {grant_id!r} not found to revoke")
        # Revocation is a recorded transition, never a delete — *why* an entitlement went
        # away is itself a fact worth keeping.
        revoked = replace(m.from_row(EntitlementGrant, row), revoked_at=revoked_at)
        m.update_row(row, revoked, m.grant_projection(revoked))
        self._session.flush()
        return revoked


class SqlEntitlementSnapshotRepository(_Repo, EntitlementSnapshotRepository):
    def publish(self, snapshot: EntitlementSnapshot) -> EntitlementSnapshot:
        self._add(m.snapshot_row(snapshot))
        return snapshot

    def latest(self, org_id: str, account_id: str) -> EntitlementSnapshot | None:
        row = self._one(
            select(rows.BillingEntitlementSnapshotRow)
            .where(
                rows.BillingEntitlementSnapshotRow.org_id == org_id,
                rows.BillingEntitlementSnapshotRow.account_id == account_id,
            )
            .order_by(rows.BillingEntitlementSnapshotRow.version.desc())  # type: ignore[attr-defined]
        )
        return m.from_row(EntitlementSnapshot, row) if row else None

    def version(self, org_id: str, account_id: str, version: int) -> EntitlementSnapshot | None:
        row = self._one(
            select(rows.BillingEntitlementSnapshotRow).where(
                rows.BillingEntitlementSnapshotRow.org_id == org_id,
                rows.BillingEntitlementSnapshotRow.account_id == account_id,
                rows.BillingEntitlementSnapshotRow.version == version,
            )
        )
        return m.from_row(EntitlementSnapshot, row) if row else None


# ── metering ────────────────────────────────────────────────────────────────────


class SqlUsageRecordRepository(_Repo, UsageRecordRepository):
    def append(self, record: UsageRecord) -> UsageRecord:
        existing = self._one(
            select(rows.BillingUsageRecordRow).where(
                rows.BillingUsageRecordRow.source_event_id == record.source.event_id,
                rows.BillingUsageRecordRow.meter_code == record.meter_code,
            )
        )
        if existing is not None:
            # Exactly-once billing over at-least-once delivery: the first write wins and the
            # replay returns it unchanged, so a retry can never bill twice (M4).
            return m.from_row(UsageRecord, existing)
        self._add(m.usage_record_row(record))
        return record

    def exists(self, event_id: str, meter_code: str) -> bool:
        return (
            self._one(
                select(rows.BillingUsageRecordRow).where(
                    rows.BillingUsageRecordRow.source_event_id == event_id,
                    rows.BillingUsageRecordRow.meter_code == meter_code,
                )
            )
            is not None
        )

    def for_period(
        self, org_id: str, account_id: str, meter_code: str, period: BillingPeriod
    ) -> tuple[UsageRecord, ...]:
        found = self._all(
            select(rows.BillingUsageRecordRow)
            .where(
                rows.BillingUsageRecordRow.org_id == org_id,
                rows.BillingUsageRecordRow.account_id == account_id,
                rows.BillingUsageRecordRow.meter_code == meter_code,
                rows.BillingUsageRecordRow.period_start == period.start,
                rows.BillingUsageRecordRow.period_end == period.end,
            )
            .order_by(rows.BillingUsageRecordRow.id)
        )
        return tuple(m.from_row(UsageRecord, r) for r in found)


class SqlUsageAggregateRepository(_Repo, UsageAggregateRepository):
    def upsert(self, aggregate: UsageAggregate) -> UsageAggregate:
        row = self._one(
            select(rows.BillingUsageAggregateRow).where(
                rows.BillingUsageAggregateRow.org_id == aggregate.org_id,
                rows.BillingUsageAggregateRow.account_id == aggregate.account_id,
                rows.BillingUsageAggregateRow.meter_code == aggregate.meter_code,
                rows.BillingUsageAggregateRow.period_start == aggregate.period.start,
                rows.BillingUsageAggregateRow.period_end == aggregate.period.end,
            )
        )
        if row is None:
            self._add(m.usage_aggregate_row(aggregate))
            return aggregate
        if row.sealed and not aggregate.sealed:
            # Sealing is a one-way door (M7): a sealed period may not be reopened by a
            # later recomputation.
            raise ImmutabilityViolation(
                f"usage aggregate {row.id!r} is sealed and cannot be unsealed"
            )
        m.update_row(row, aggregate, m.usage_aggregate_projection(aggregate))
        self._session.flush()
        return aggregate

    def get(
        self, org_id: str, account_id: str, meter_code: str, period: BillingPeriod
    ) -> UsageAggregate | None:
        row = self._one(
            select(rows.BillingUsageAggregateRow).where(
                rows.BillingUsageAggregateRow.org_id == org_id,
                rows.BillingUsageAggregateRow.account_id == account_id,
                rows.BillingUsageAggregateRow.meter_code == meter_code,
                rows.BillingUsageAggregateRow.period_start == period.start,
                rows.BillingUsageAggregateRow.period_end == period.end,
            )
        )
        return m.from_row(UsageAggregate, row) if row else None

    def for_period(
        self, org_id: str, account_id: str, period: BillingPeriod
    ) -> tuple[UsageAggregate, ...]:
        found = self._all(
            select(rows.BillingUsageAggregateRow)
            .where(
                rows.BillingUsageAggregateRow.org_id == org_id,
                rows.BillingUsageAggregateRow.account_id == account_id,
                rows.BillingUsageAggregateRow.period_start == period.start,
                rows.BillingUsageAggregateRow.period_end == period.end,
            )
            .order_by(rows.BillingUsageAggregateRow.meter_code)
        )
        return tuple(m.from_row(UsageAggregate, r) for r in found)


class SqlUsageQuarantineRepository(_Repo, UsageQuarantineRepository):
    def append(self, quarantined: UsageQuarantine) -> UsageQuarantine:
        self._add(m.quarantine_row(quarantined))
        return quarantined

    def pending(self, limit: int = 100) -> tuple[UsageQuarantine, ...]:
        found = self._all(
            select(rows.BillingUsageQuarantineRow)
            .order_by(rows.BillingUsageQuarantineRow.received_at)
            .limit(limit)
        )
        return tuple(m.from_row(UsageQuarantine, r) for r in found)


# ── ledger + credits (append-only) ──────────────────────────────────────────────


class SqlLedgerRepository(_Repo, LedgerRepository):
    """Append-only. No update, no delete — not by convention, but because no such method
    exists to call (ADR-0023 RS1)."""

    def append(self, entry: LedgerEntry) -> LedgerEntry:
        self._add(m.ledger_row(entry))
        return entry

    def for_account(self, org_id: str, account_id: str) -> tuple[LedgerEntry, ...]:
        found = self._all(
            select(rows.BillingLedgerEntryRow)
            .where(
                rows.BillingLedgerEntryRow.org_id == org_id,
                rows.BillingLedgerEntryRow.account_id == account_id,
            )
            .order_by(rows.BillingLedgerEntryRow.recorded_at, rows.BillingLedgerEntryRow.id)
        )
        return tuple(m.from_row(LedgerEntry, r) for r in found)

    def for_invoice(self, org_id: str, invoice_id: str) -> tuple[LedgerEntry, ...]:
        found = self._all(
            select(rows.BillingLedgerEntryRow)
            .where(
                rows.BillingLedgerEntryRow.org_id == org_id,
                rows.BillingLedgerEntryRow.invoice_id == invoice_id,
            )
            .order_by(rows.BillingLedgerEntryRow.recorded_at, rows.BillingLedgerEntryRow.id)
        )
        return tuple(m.from_row(LedgerEntry, r) for r in found)


class SqlCreditRepository(_Repo, CreditRepository):
    def grant(self, grant: CreditGrant) -> CreditGrant:
        self._add(m.credit_grant_row(grant))
        return grant

    def append_movement(self, movement: CreditMovement) -> CreditMovement:
        self._add(m.credit_movement_row(movement))
        return movement

    def grants_for(self, org_id: str, account_id: str) -> tuple[CreditGrant, ...]:
        found = self._all(
            select(rows.BillingCreditGrantRow)
            .where(
                rows.BillingCreditGrantRow.org_id == org_id,
                rows.BillingCreditGrantRow.account_id == account_id,
            )
            .order_by(rows.BillingCreditGrantRow.granted_at, rows.BillingCreditGrantRow.id)
        )
        return tuple(m.from_row(CreditGrant, r) for r in found)

    def movements_for(self, org_id: str, account_id: str) -> tuple[CreditMovement, ...]:
        found = self._all(
            select(rows.BillingCreditMovementRow)
            .where(
                rows.BillingCreditMovementRow.org_id == org_id,
                rows.BillingCreditMovementRow.account_id == account_id,
            )
            .order_by(rows.BillingCreditMovementRow.occurred_at, rows.BillingCreditMovementRow.id)
        )
        return tuple(m.from_row(CreditMovement, r) for r in found)


# ── invoicing ───────────────────────────────────────────────────────────────────


class SqlInvoiceRepository(_Repo, InvoiceRepository):
    def add(self, invoice: Invoice) -> Invoice:
        self._add(m.invoice_row(invoice))
        return invoice

    def get(self, org_id: str, invoice_id: str) -> Invoice | None:
        row = self._one(
            select(rows.BillingInvoiceRow).where(
                rows.BillingInvoiceRow.id == invoice_id,
                rows.BillingInvoiceRow.org_id == org_id,
            )
        )
        return m.from_row(Invoice, row) if row else None

    def for_account(self, org_id: str, account_id: str) -> tuple[Invoice, ...]:
        found = self._all(
            select(rows.BillingInvoiceRow)
            .where(
                rows.BillingInvoiceRow.org_id == org_id,
                rows.BillingInvoiceRow.account_id == account_id,
            )
            .order_by(rows.BillingInvoiceRow.period_start, rows.BillingInvoiceRow.id)
        )
        return tuple(m.from_row(Invoice, r) for r in found)

    def replace_draft(self, invoice: Invoice) -> Invoice:
        row = self._one(
            select(rows.BillingInvoiceRow).where(
                rows.BillingInvoiceRow.id == invoice.id,
                rows.BillingInvoiceRow.org_id == invoice.org_id,
            )
        )
        if row is None:
            return self.add(invoice)
        stored_state = row.state
        if stored_state != InvoiceState.DRAFT.value:
            # An issued invoice is a legal document, not a mutable record (ADR-0023 IN2).
            # Correcting one is a credit note; the storage layer refuses the shortcut.
            raise ImmutabilityViolation(
                f"invoice {invoice.id!r} is {stored_state!r} and can no longer be replaced"
            )
        m.update_row(row, invoice, m.invoice_projection(invoice))
        self._session.flush()
        return invoice


class SqlInvoiceNumberAllocator(_Repo, InvoiceNumberAllocator):
    """Gapless sequential allocation per (legal entity, jurisdiction).

    The counter lives in its own row so allocation is a storage fact, not an in-process one:
    a restart, a retry, or a second worker continues the same sequence rather than starting
    over. Under the single-writer Control Plane (§A9 Stage 1–2) the shared session serialises
    allocation; the multi-worker upgrade is a `SELECT … FOR UPDATE` on this one row (Postgres)
    — a one-line change confined here, which is why the counter is a row and not a column on
    something else.

    Note the deliberate tension, resolved toward the legal requirement: the counter is
    incremented and **flushed** within the caller's transaction, so a rolled-back billing run
    releases its number back. Gaplessness is guaranteed for *committed* invoices, which is
    what the tax authority actually requires (IN4).
    """

    def allocate(self, entity_code: str, jurisdiction: str) -> int:
        key = f"{entity_code}:{jurisdiction}"
        row = self._one(
            select(rows.BillingInvoiceSequenceRow).where(rows.BillingInvoiceSequenceRow.id == key)
        )
        if row is None:
            row = self._add(
                rows.BillingInvoiceSequenceRow(
                    id=key,
                    entity_code=entity_code,
                    jurisdiction=jurisdiction,
                    next_sequence=1,
                )
            )
        allocated = row.next_sequence
        row.next_sequence = allocated + 1
        self._session.flush()
        return allocated


class SqlCreditNoteRepository(_Repo, CreditNoteRepository):
    def add(self, note: CreditNote) -> CreditNote:
        self._add(m.credit_note_row(note))
        return note

    def for_invoice(self, org_id: str, invoice_id: str) -> tuple[CreditNote, ...]:
        found = self._all(
            select(rows.BillingCreditNoteRow)
            .where(
                rows.BillingCreditNoteRow.org_id == org_id,
                rows.BillingCreditNoteRow.invoice_id == invoice_id,
            )
            .order_by(rows.BillingCreditNoteRow.issued_at, rows.BillingCreditNoteRow.id)
        )
        return tuple(m.from_row(CreditNote, r) for r in found)


class SqlBillingRunRepository(_Repo, BillingRunRepository):
    def upsert(self, run: BillingRun) -> BillingRun:
        row = self._one(
            select(rows.BillingRunRow).where(
                rows.BillingRunRow.org_id == run.org_id,
                rows.BillingRunRow.account_id == run.account_id,
                rows.BillingRunRow.period_start == run.period.start,
                rows.BillingRunRow.period_end == run.period.end,
            )
        )
        if row is None:
            self._add(m.run_row(run))
            return run
        # Keyed by (account, period) so re-running a period resumes rather than duplicates.
        m.update_row(row, run, m.run_projection(run))
        self._session.flush()
        return run

    def get(self, org_id: str, account_id: str, period: BillingPeriod) -> BillingRun | None:
        row = self._one(
            select(rows.BillingRunRow).where(
                rows.BillingRunRow.org_id == org_id,
                rows.BillingRunRow.account_id == account_id,
                rows.BillingRunRow.period_start == period.start,
                rows.BillingRunRow.period_end == period.end,
            )
        )
        return m.from_row(BillingRun, row) if row else None


# ── payments ────────────────────────────────────────────────────────────────────


class SqlPaymentRepository(_Repo, PaymentRepository):
    def add(self, payment: Payment) -> Payment:
        self._add(m.payment_row(payment))
        return payment

    def get(self, org_id: str, payment_id: str) -> Payment | None:
        row = self._one(
            select(rows.BillingPaymentRow).where(
                rows.BillingPaymentRow.id == payment_id,
                rows.BillingPaymentRow.org_id == org_id,
            )
        )
        return m.from_row(Payment, row) if row else None

    def for_invoice(self, org_id: str, invoice_id: str) -> tuple[Payment, ...]:
        found = self._all(
            select(rows.BillingPaymentRow)
            .where(
                rows.BillingPaymentRow.org_id == org_id,
                rows.BillingPaymentRow.invoice_id == invoice_id,
            )
            .order_by(rows.BillingPaymentRow.requested_at, rows.BillingPaymentRow.id)
        )
        return tuple(m.from_row(Payment, r) for r in found)

    def replace(self, payment: Payment) -> Payment:
        row = self._one(
            select(rows.BillingPaymentRow).where(
                rows.BillingPaymentRow.id == payment.id,
                rows.BillingPaymentRow.org_id == payment.org_id,
            )
        )
        if row is None:
            return self.add(payment)
        m.update_row(row, payment, m.payment_projection(payment))
        self._session.flush()
        return payment


class SqlPaymentInstrumentRepository(_Repo, PaymentInstrumentRepository):
    def add(self, instrument: PaymentInstrument) -> PaymentInstrument:
        self._add(m.instrument_row(instrument))
        return instrument

    def for_account(self, org_id: str, account_id: str) -> tuple[PaymentInstrument, ...]:
        found = self._all(
            select(rows.BillingPaymentInstrumentRow)
            .where(
                rows.BillingPaymentInstrumentRow.org_id == org_id,
                rows.BillingPaymentInstrumentRow.account_id == account_id,
            )
            .order_by(rows.BillingPaymentInstrumentRow.id)
        )
        return tuple(m.from_row(PaymentInstrument, r) for r in found)

    def remove(self, org_id: str, instrument_id: str) -> None:
        row = self._one(
            select(rows.BillingPaymentInstrumentRow).where(
                rows.BillingPaymentInstrumentRow.id == instrument_id,
                rows.BillingPaymentInstrumentRow.org_id == org_id,
            )
        )
        if row is None:
            return
        # The token reference goes; every payment, ledger entry, and invoice it produced
        # stays — detaching an instrument touches no revenue record.
        self._session.delete(row)
        self._session.flush()


class SqlDunningRepository(_Repo, DunningRepository):
    def upsert(self, process: DunningProcess) -> DunningProcess:
        row = self._one(
            select(rows.BillingDunningRow).where(
                rows.BillingDunningRow.org_id == process.org_id,
                rows.BillingDunningRow.payment_id == process.payment_id,
            )
        )
        if row is None:
            self._add(m.dunning_row(process))
            return process
        m.update_row(row, process, m.dunning_projection(process))
        self._session.flush()
        return process

    def for_payment(self, org_id: str, payment_id: str) -> DunningProcess | None:
        row = self._one(
            select(rows.BillingDunningRow).where(
                rows.BillingDunningRow.org_id == org_id,
                rows.BillingDunningRow.payment_id == payment_id,
            )
        )
        return m.from_row(DunningProcess, row) if row else None


class SqlReconciliationRepository(_Repo, ReconciliationRepository):
    def append(self, finding: ReconciliationFinding) -> ReconciliationFinding:
        self._add(m.reconciliation_row(finding))
        return finding

    def divergent(self, limit: int = 100) -> tuple[ReconciliationFinding, ...]:
        found = self._all(
            select(rows.BillingReconciliationRow)
            .where(rows.BillingReconciliationRow.verdict != "matched")
            .order_by(rows.BillingReconciliationRow.observed_at, rows.BillingReconciliationRow.id)
            .limit(limit)
        )
        return tuple(m.from_row(ReconciliationFinding, r) for r in found)


# ── refunds ─────────────────────────────────────────────────────────────────────


class SqlRefundRepository(_Repo, RefundRepository):
    def add(self, refund: Refund) -> Refund:
        self._add(m.refund_row(refund))
        return refund

    def get(self, org_id: str, refund_id: str) -> Refund | None:
        row = self._one(
            select(rows.BillingRefundRow).where(
                rows.BillingRefundRow.id == refund_id,
                rows.BillingRefundRow.org_id == org_id,
            )
        )
        return m.from_row(Refund, row) if row else None

    def by_request_key(self, org_id: str, request_key: str) -> Refund | None:
        if not request_key:
            return None
        row = self._one(
            select(rows.BillingRefundRow).where(
                rows.BillingRefundRow.org_id == org_id,
                rows.BillingRefundRow.request_key == request_key,
            )
        )
        return m.from_row(Refund, row) if row else None

    def for_payment(self, org_id: str, payment_id: str) -> tuple[Refund, ...]:
        found = self._all(
            select(rows.BillingRefundRow)
            .where(
                rows.BillingRefundRow.org_id == org_id,
                rows.BillingRefundRow.payment_id == payment_id,
            )
            .order_by(rows.BillingRefundRow.id)
        )
        return tuple(m.from_row(Refund, r) for r in found)

    def replace(self, refund: Refund) -> Refund:
        row = self._one(
            select(rows.BillingRefundRow).where(
                rows.BillingRefundRow.id == refund.id,
                rows.BillingRefundRow.org_id == refund.org_id,
            )
        )
        if row is None:
            return self.add(refund)
        m.update_row(row, refund, m.refund_projection(refund))
        self._session.flush()
        return refund


class SqlDisputeRepository(_Repo, DisputeRepository):
    def add(self, dispute: Dispute) -> Dispute:
        self._add(m.dispute_row(dispute))
        return dispute

    def for_payment(self, org_id: str, payment_id: str) -> tuple[Dispute, ...]:
        found = self._all(
            select(rows.BillingDisputeRow)
            .where(
                rows.BillingDisputeRow.org_id == org_id,
                rows.BillingDisputeRow.payment_id == payment_id,
            )
            .order_by(rows.BillingDisputeRow.id)
        )
        return tuple(m.from_row(Dispute, r) for r in found)

    def replace(self, dispute: Dispute) -> Dispute:
        row = self._one(
            select(rows.BillingDisputeRow).where(
                rows.BillingDisputeRow.id == dispute.id,
                rows.BillingDisputeRow.org_id == dispute.org_id,
            )
        )
        if row is None:
            return self.add(dispute)
        m.update_row(row, dispute, m.dispute_projection(dispute))
        self._session.flush()
        return dispute


# ── tax ─────────────────────────────────────────────────────────────────────────


class SqlTaxProfileRepository(_Repo, TaxProfileRepository):
    def upsert(self, profile: TaxProfile) -> TaxProfile:
        row = self._one(
            select(rows.BillingTaxProfileRow).where(
                rows.BillingTaxProfileRow.org_id == profile.org_id,
                rows.BillingTaxProfileRow.account_id == profile.account_id,
            )
        )
        if row is None:
            self._add(m.tax_profile_row(profile))
            return profile
        m.update_row(row, profile, m.tax_profile_projection(profile))
        self._session.flush()
        return profile

    def for_account(self, org_id: str, account_id: str) -> TaxProfile | None:
        row = self._one(
            select(rows.BillingTaxProfileRow).where(
                rows.BillingTaxProfileRow.org_id == org_id,
                rows.BillingTaxProfileRow.account_id == account_id,
            )
        )
        return m.from_row(TaxProfile, row) if row else None


class SqlTaxDeterminationRepository(_Repo, TaxDeterminationRepository):
    """Determinations are immutable evidence — appended once, never recomputed (TX2/TX4)."""

    def append(self, determination: TaxDetermination) -> TaxDetermination:
        self._add(m.tax_determination_row(determination))
        return determination

    def for_invoice(self, org_id: str, invoice_id: str) -> TaxDetermination | None:
        row = self._one(
            select(rows.BillingTaxDeterminationRow).where(
                rows.BillingTaxDeterminationRow.org_id == org_id,
                rows.BillingTaxDeterminationRow.invoice_id == invoice_id,
            )
        )
        return m.from_row(TaxDetermination, row) if row else None


# ── contracts, licenses, events ─────────────────────────────────────────────────


class SqlContractRepository(_Repo, ContractRepository):
    def add(self, contract: Contract) -> Contract:
        self._add(m.contract_row(contract))
        return contract

    def get(self, org_id: str, contract_id: str) -> Contract | None:
        row = self._one(
            select(rows.BillingContractRow).where(
                rows.BillingContractRow.id == contract_id,
                rows.BillingContractRow.org_id == org_id,
            )
        )
        return m.from_row(Contract, row) if row else None

    def for_account(self, org_id: str, account_id: str) -> tuple[Contract, ...]:
        found = self._all(
            select(rows.BillingContractRow)
            .where(
                rows.BillingContractRow.org_id == org_id,
                rows.BillingContractRow.account_id == account_id,
            )
            .order_by(rows.BillingContractRow.id)
        )
        return tuple(m.from_row(Contract, r) for r in found)

    def replace(self, contract: Contract) -> Contract:
        row = self._one(
            select(rows.BillingContractRow).where(
                rows.BillingContractRow.id == contract.id,
                rows.BillingContractRow.org_id == contract.org_id,
            )
        )
        if row is None:
            return self.add(contract)
        m.update_row(row, contract, m.contract_projection(contract))
        self._session.flush()
        return contract


class SqlLicenseRepository(_Repo, LicenseRepository):
    def add(self, license_: License) -> License:
        self._add(m.license_row(license_))
        return license_

    def get(self, org_id: str, license_id: str) -> License | None:
        row = self._one(
            select(rows.BillingLicenseRow).where(
                rows.BillingLicenseRow.id == license_id,
                rows.BillingLicenseRow.org_id == org_id,
            )
        )
        return m.from_row(License, row) if row else None

    def for_account(self, org_id: str, account_id: str) -> tuple[License, ...]:
        found = self._all(
            select(rows.BillingLicenseRow)
            .where(
                rows.BillingLicenseRow.org_id == org_id,
                rows.BillingLicenseRow.account_id == account_id,
            )
            .order_by(rows.BillingLicenseRow.id)
        )
        return tuple(m.from_row(License, r) for r in found)

    def replace(self, license_: License) -> License:
        row = self._one(
            select(rows.BillingLicenseRow).where(
                rows.BillingLicenseRow.id == license_.id,
                rows.BillingLicenseRow.org_id == license_.org_id,
            )
        )
        if row is None:
            return self.add(license_)
        m.update_row(row, license_, m.license_projection(license_))
        self._session.flush()
        return license_


class SqlBillingEventRepository(_Repo, BillingEventRepository):
    """Append-only `billing.*` fact stream. Billing keeps no private audit log — this is the
    durable hand-off point to the Epic 8 Audit Platform (ADR-0023 §5.2)."""

    def append(self, event: BillingEvent) -> BillingEvent:
        self._add(m.event_row(event))
        return event

    def for_subject(self, org_id: str, subject_id: str) -> tuple[BillingEvent, ...]:
        found = self._all(
            select(rows.BillingEventRow)
            .where(
                rows.BillingEventRow.org_id == org_id,
                rows.BillingEventRow.subject_id == subject_id,
            )
            .order_by(rows.BillingEventRow.ts, rows.BillingEventRow.id)
        )
        return tuple(m.from_row(BillingEvent, r) for r in found)


# Every Foundation repository contract, paired with its production implementation. The
# composition root wires from this list; the tests assert it covers the contracts exactly,
# so a Foundation contract added later cannot ship without an implementation.
SQL_REPOSITORIES = (
    (BillingAccountRepository, SqlBillingAccountRepository),
    (PlanRepository, SqlPlanRepository),
    (FeatureRepository, SqlFeatureRepository),
    (MeterRepository, SqlMeterRepository),
    (CatalogSnapshotRepository, SqlCatalogSnapshotRepository),
    (PriceBookRepository, SqlPriceBookRepository),
    (SubscriptionRepository, SqlSubscriptionRepository),
    (EntitlementGrantRepository, SqlEntitlementGrantRepository),
    (EntitlementSnapshotRepository, SqlEntitlementSnapshotRepository),
    (UsageRecordRepository, SqlUsageRecordRepository),
    (UsageAggregateRepository, SqlUsageAggregateRepository),
    (UsageQuarantineRepository, SqlUsageQuarantineRepository),
    (LedgerRepository, SqlLedgerRepository),
    (CreditRepository, SqlCreditRepository),
    (InvoiceRepository, SqlInvoiceRepository),
    (InvoiceNumberAllocator, SqlInvoiceNumberAllocator),
    (CreditNoteRepository, SqlCreditNoteRepository),
    (BillingRunRepository, SqlBillingRunRepository),
    (PaymentRepository, SqlPaymentRepository),
    (PaymentInstrumentRepository, SqlPaymentInstrumentRepository),
    (DunningRepository, SqlDunningRepository),
    (ReconciliationRepository, SqlReconciliationRepository),
    (RefundRepository, SqlRefundRepository),
    (DisputeRepository, SqlDisputeRepository),
    (TaxProfileRepository, SqlTaxProfileRepository),
    (TaxDeterminationRepository, SqlTaxDeterminationRepository),
    (ContractRepository, SqlContractRepository),
    (LicenseRepository, SqlLicenseRepository),
    (BillingEventRepository, SqlBillingEventRepository),
)
