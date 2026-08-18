"""Factories (M9.2) — validated composition of billing aggregates.

The one place aggregates are constructed, mirroring `product.ProductFactory` and
`api.foundation.FoundationFactory`. Each factory method:

1. **validates** the input structurally (diagnostics, never exceptions from the validator),
2. **fails fast** on error-severity findings (`BillingValidationError`, §A2.6),
3. **composes** an immutable aggregate using the injected id + clock seams, and
4. **emits** the corresponding `billing.*` fact through the injected emitter.

No I/O, no persistence, no networking. Emission is the only effect, and it is injected —
the Foundation never dispatches, queues, or stores an event itself (ADR-0023 §5.2).
"""

from __future__ import annotations

from .accounts import AccountStatus, BillingAccount
from .catalog import MeterDefinition, Plan
from .diagnostics import BillingDiagnostics
from .domain import Quantity
from .dto import (
    AccountResult,
    DefineMeterRequest,
    DefinePlanRequest,
    InvoiceResult,
    IssueInvoiceRequest,
    OpenAccountRequest,
    RecordUsageRequest,
    SnapshotResult,
    StartSubscriptionRequest,
    SubscriptionResult,
)
from .entitlement import Entitlement, EntitlementGate, EntitlementSnapshot
from .errors import BillingValidationError, ImmutabilityViolation
from .events import BillingEvent, BillingEventType, EventEmitter, NullEventEmitter
from .invoicing import Invoice, InvoiceState
from .metering import UsageRecord, UsageSource, UsageStatus
from .repositories import InvoiceNumberAllocator
from .seams import (
    PREFIX_ACCOUNT,
    PREFIX_EVENT,
    PREFIX_INVOICE,
    PREFIX_METER,
    PREFIX_PLAN,
    PREFIX_SNAPSHOT,
    PREFIX_SUBSCRIPTION,
    PREFIX_USAGE_RECORD,
    Clock,
    IdGenerator,
    SequentialIdGenerator,
    SystemClock,
)
from .services import InvoiceNumberFormatter, InvoiceTotalsService, PeriodService
from .subscription import Subscription, SubscriptionState
from .validation import BillingValidator


class BillingFactory:
    """Composes validated billing aggregates. Stateless apart from its injected seams."""

    def __init__(
        self,
        *,
        ids: IdGenerator | None = None,
        clock: Clock | None = None,
        emitter: EventEmitter | None = None,
        validator: BillingValidator | None = None,
    ) -> None:
        self._ids = ids or SequentialIdGenerator()
        self._clock = clock or SystemClock()
        self._emitter = emitter or NullEventEmitter()
        self._validator = validator or BillingValidator()
        self._periods = PeriodService()
        self._totals = InvoiceTotalsService()
        self._numbers = InvoiceNumberFormatter()

    # ── internals ───────────────────────────────────────────────────────────────
    def _guard(self, diagnostics: BillingDiagnostics) -> None:
        if diagnostics.has_errors:
            raise BillingValidationError(diagnostics)

    def _emit(
        self,
        event_type: BillingEventType,
        *,
        org_id: str,
        subject_id: str,
        at: float,
        actor_id: str = "",
        **data: object,
    ) -> BillingEvent:
        event = BillingEvent(
            id=self._ids.new(PREFIX_EVENT),
            type=event_type,
            ts=at,
            org_id=org_id,
            subject_id=subject_id,
            actor_id=actor_id,
            data=dict(data),
        )
        self._emitter.emit(event)
        return event

    # ── accounts (M9.5) ─────────────────────────────────────────────────────────
    def open_account(self, request: OpenAccountRequest, *, actor_id: str = "") -> AccountResult:
        diagnostics = BillingDiagnostics.empty()
        if not request.org_id:
            from .diagnostics import BillingDiagnosticCode, Diagnostic, Severity

            diagnostics = BillingDiagnostics.of(
                [
                    Diagnostic(
                        "account.org_id",
                        BillingDiagnosticCode.EMPTY_IDENTITY,
                        Severity.ERROR,
                        "an org_id is required",
                        "a billing account references an organization the Customer Platform owns",
                    )
                ]
            )
        self._guard(diagnostics)
        at = self._clock.now()
        account = BillingAccount(
            id=self._ids.new(PREFIX_ACCOUNT),
            org_id=request.org_id,
            display_name=request.display_name,
            currency=request.currency,
            kind=request.kind,
            status=AccountStatus.ACTIVE,
            parent_account_id=request.parent_account_id,
            contact=request.contact,
            address=request.address,
            created_at=at,
        )
        self._emit(
            BillingEventType.ACCOUNT_CREATED,
            org_id=account.org_id,
            subject_id=account.id,
            at=at,
            actor_id=actor_id,
            currency=account.currency.value,
            kind=account.kind.value,
        )
        return AccountResult(account, diagnostics)

    # ── catalog (M9.6) ──────────────────────────────────────────────────────────
    def define_meter(self, request: DefineMeterRequest, *, actor_id: str = "") -> MeterDefinition:
        meter = MeterDefinition(
            id=self._ids.new(PREFIX_METER),
            org_id=request.org_id,
            code=request.code,
            display_name=request.display_name,
            unit=request.unit,
            aggregation=request.aggregation,
            source=request.source,
            rounding=request.rounding,
            description=request.description,
        )
        self._guard(self._validator.validate_meter(meter))
        self._emit(
            BillingEventType.METER_DEFINED,
            org_id=request.org_id,
            subject_id=meter.id,
            at=self._clock.now(),
            actor_id=actor_id,
            code=meter.code,
            source=meter.source.value,
        )
        return meter

    def define_plan(
        self,
        request: DefinePlanRequest,
        *,
        known_features: frozenset[str] = frozenset(),
        known_meters: frozenset[str] = frozenset(),
        actor_id: str = "",
    ) -> Plan:
        plan = Plan(
            id=self._ids.new(PREFIX_PLAN),
            org_id=request.org_id,
            code=request.code,
            display_name=request.display_name,
            tier=request.tier,
            features=request.features,
            metered_codes=request.metered_codes,
            description=request.description,
        )
        self._guard(
            self._validator.validate_plan(
                plan, known_features=known_features, known_meters=known_meters
            )
        )
        return plan

    def publish_plan(self, plan: Plan, *, actor_id: str = "") -> Plan:
        """Publishing bumps nothing and edits nothing — it produces a new immutable plan
        value marked published, so an already-published version is never mutated."""
        if plan.published:
            raise ImmutabilityViolation(f"plan {plan.code!r} v{plan.version} is already published")
        published = Plan(
            id=plan.id,
            org_id=plan.org_id,
            code=plan.code,
            display_name=plan.display_name,
            tier=plan.tier,
            version=plan.version,
            published=True,
            deprecated=plan.deprecated,
            features=plan.features,
            metered_codes=plan.metered_codes,
            description=plan.description,
        )
        self._emit(
            BillingEventType.PLAN_PUBLISHED,
            org_id=plan.org_id,
            subject_id=plan.id,
            at=self._clock.now(),
            actor_id=actor_id,
            code=plan.code,
            version=plan.version,
        )
        return published

    # ── subscriptions (M9.8) ────────────────────────────────────────────────────
    def start_subscription(
        self, request: StartSubscriptionRequest, *, at: float | None = None, actor_id: str = ""
    ) -> SubscriptionResult:
        now = self._clock.now() if at is None else at
        period = self._periods.period_for(request.interval, start=now)
        trial_end = self._periods.trial_end(start=now, trial_seconds=request.trial_seconds)
        state = SubscriptionState.TRIALING if trial_end else SubscriptionState.PENDING
        subscription = Subscription(
            id=self._ids.new(PREFIX_SUBSCRIPTION),
            org_id=request.org_id,
            account_id=request.account_id,
            plan_code=request.plan_code,
            plan_version=request.plan_version,
            price_book_id=request.price_book_id,
            price_book_version=request.price_book_version,
            currency=request.currency,
            interval=request.interval,
            state=state,
            items=request.items,
            current_period=period,
            trial_end=trial_end,
            contract_id=request.contract_id,
        )
        diagnostics = self._validator.validate_subscription(subscription)
        self._guard(diagnostics)
        self._emit(
            BillingEventType.SUBSCRIPTION_CREATED,
            org_id=subscription.org_id,
            subject_id=subscription.id,
            at=now,
            actor_id=actor_id,
            plan_code=subscription.plan_code,
            plan_version=subscription.plan_version,
            state=state.value,
        )
        if trial_end:
            self._emit(
                BillingEventType.SUBSCRIPTION_TRIAL_STARTED,
                org_id=subscription.org_id,
                subject_id=subscription.id,
                at=now,
                actor_id=actor_id,
                trial_end=trial_end,
            )
        return SubscriptionResult(subscription, diagnostics)

    # ── entitlements (M9.9) ─────────────────────────────────────────────────────
    def publish_snapshot(
        self,
        *,
        org_id: str,
        account_id: str,
        version: int,
        entitlements: tuple[Entitlement, ...],
        gates: tuple[EntitlementGate, ...],
        subscription_id: str = "",
        max_staleness_seconds: float = 3600.0,
        at: float | None = None,
        actor_id: str = "",
    ) -> SnapshotResult:
        now = self._clock.now() if at is None else at
        snapshot = EntitlementSnapshot(
            id=self._ids.new(PREFIX_SNAPSHOT),
            org_id=org_id,
            account_id=account_id,
            version=version,
            issued_at=now,
            entitlements=entitlements,
            gates=gates,
            subscription_id=subscription_id,
            max_staleness_seconds=max_staleness_seconds,
        )
        diagnostics = self._validator.validate_snapshot(snapshot)
        self._guard(diagnostics)
        self._emit(
            BillingEventType.ENTITLEMENT_SNAPSHOT_PUBLISHED,
            org_id=org_id,
            subject_id=snapshot.id,
            at=now,
            actor_id=actor_id,
            version=version,
            entitlement_count=len(entitlements),
        )
        return SnapshotResult(snapshot, diagnostics)

    # ── metering (M9.10) ────────────────────────────────────────────────────────
    def record_usage(self, request: RecordUsageRequest) -> UsageRecord:
        """Derive a billable usage record from an already-decoded `metric.*` observation.

        Billing measures nothing: the caller supplies the source event's identity, and the
        record is keyed by it so re-derivation is a no-op (ADR-0023 M1/M4)."""
        record = UsageRecord(
            id=self._ids.new(PREFIX_USAGE_RECORD),
            org_id=request.org_id,
            account_id=request.account_id,
            meter_code=request.meter_code,
            quantity=Quantity(request.units, request.unit),
            scope=request.scope,
            period=request.period,
            source=UsageSource(request.event_id, request.event_type, request.observed_at),
            status=UsageStatus.RECORDED,
            subscription_id=request.subscription_id,
        )
        self._guard(self._validator.validate_scope(record.scope, org_id=record.org_id))
        self._emit(
            BillingEventType.USAGE_RECORDED,
            org_id=record.org_id,
            subject_id=record.id,
            at=request.observed_at,
            meter_code=record.meter_code,
            units=record.quantity.units,
            event_id=request.event_id,
        )
        return record

    def quarantine_usage(self, request: RecordUsageRequest, *, reason: str) -> UsageRecord:
        """Unattributable usage is recorded and alerted — **never silently billed and never
        silently dropped** (ADR-0023 M6)."""
        record = UsageRecord(
            id=self._ids.new(PREFIX_USAGE_RECORD),
            org_id=request.org_id,
            account_id=request.account_id,
            meter_code=request.meter_code,
            quantity=Quantity(request.units, request.unit),
            scope=request.scope,
            period=request.period,
            source=UsageSource(request.event_id, request.event_type, request.observed_at),
            status=UsageStatus.QUARANTINED,
            quarantine_reason=reason,
        )
        self._emit(
            BillingEventType.USAGE_QUARANTINED,
            org_id=record.org_id,
            subject_id=record.id,
            at=request.observed_at,
            meter_code=record.meter_code,
            reason=reason,
        )
        return record

    # ── invoicing (M9.13) ───────────────────────────────────────────────────────
    def draft_invoice(
        self, request: IssueInvoiceRequest, *, at: float | None = None
    ) -> InvoiceResult:
        now = self._clock.now() if at is None else at
        totals = self._totals.totals(request.lines, currency=request.currency)
        invoice = Invoice(
            id=self._ids.new(PREFIX_INVOICE),
            org_id=request.org_id,
            account_id=request.account_id,
            currency=request.currency,
            period=request.period,
            state=InvoiceState.DRAFT,
            lines=request.lines,
            totals=totals,
            subscription_id=request.subscription_id,
            payment_terms_days=request.payment_terms_days,
            purchase_order_ref=request.purchase_order_ref,
            evidence=self._evidence(request),
        )
        diagnostics = self._validator.validate_invoice(invoice)
        self._guard(diagnostics)
        self._emit(
            BillingEventType.INVOICE_DRAFTED,
            org_id=invoice.org_id,
            subject_id=invoice.id,
            at=now,
            total_minor=totals.total.minor,
            currency=invoice.currency.value,
        )
        return InvoiceResult(invoice, totals, diagnostics)

    def issue_invoice(
        self,
        invoice: Invoice,
        allocator: InvoiceNumberAllocator,
        *,
        entity_code: str = "voxvoice",
        jurisdiction: str = "",
        at: float | None = None,
        actor_id: str = "",
    ) -> Invoice:
        """Issue a draft. **An issued invoice is immutable** — re-issuing one raises
        rather than renumbering it (ADR-0023 IN2/IN4)."""
        if invoice.is_issued:
            raise ImmutabilityViolation(
                f"invoice {invoice.id} is already issued; corrections are credit notes"
            )
        now = self._clock.now() if at is None else at
        sequence = allocator.allocate(entity_code, jurisdiction)
        number = self._numbers.format(
            entity_code=entity_code, jurisdiction=jurisdiction, sequence=sequence
        )
        due_at = now + invoice.payment_terms_days * 86400 if invoice.payment_terms_days else now
        issued = Invoice(
            id=invoice.id,
            org_id=invoice.org_id,
            account_id=invoice.account_id,
            currency=invoice.currency,
            period=invoice.period,
            state=InvoiceState.ISSUED,
            number=number,
            lines=invoice.lines,
            totals=invoice.totals,
            subscription_id=invoice.subscription_id,
            tax_determination_id=invoice.tax_determination_id,
            issued_at=now,
            due_at=due_at,
            purchase_order_ref=invoice.purchase_order_ref,
            payment_terms_days=invoice.payment_terms_days,
            evidence=invoice.evidence,
        )
        self._guard(self._validator.validate_invoice(issued))
        self._emit(
            BillingEventType.INVOICE_ISSUED,
            org_id=issued.org_id,
            subject_id=issued.id,
            at=now,
            actor_id=actor_id,
            number=number.formatted,
            total_minor=issued.totals.total.minor if issued.totals else 0,
        )
        return issued

    def _evidence(self, request: IssueInvoiceRequest) -> tuple[str, ...]:
        """Self-containment (ADR-0023 IN5): an invoice must be explainable without
        querying live state, so the inputs that produced it are recorded on it."""
        priced = sorted(
            {(line.price_code, line.price_version) for line in request.lines if line.price_code}
        )
        metered = sorted({line.meter_code for line in request.lines if line.meter_code})
        out = [f"period={int(request.period.start)}..{int(request.period.end)}"]
        if request.subscription_id:
            out.append(f"subscription={request.subscription_id}")
        out.extend(f"price={code}@v{version}" for code, version in priced)
        out.extend(f"meter={m}" for m in metered)
        return tuple(out)


def default_factory() -> BillingFactory:
    """A fully deterministic factory: sequential ids, a fixed clock, a recording emitter.

    The offline deterministic default (ADR-0031 CR5 / M9.0C AD7) — production swaps the
    seams at the control-plane composition root without touching a service."""
    from .events import RecordingEventEmitter
    from .seams import FixedClock

    return BillingFactory(
        ids=SequentialIdGenerator(),
        clock=FixedClock(),
        emitter=RecordingEventEmitter(),
        validator=BillingValidator(),
    )
