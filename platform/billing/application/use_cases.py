"""Use cases (M9.3) — orchestration, and nothing else.

Every command use case follows the same four beats, in the same order:

1. **authorize** — ask the injected seam (Billing evaluates nothing, ADR-0023 §11.3);
2. **open a transaction** — `with self._uow.new() as uow:`;
3. **coordinate the Foundation** — call a factory method or a stateless service, then
   persist through a repository interface;
4. **stage** an application event, published atomically on commit.

**Zero business rules live here.** Validation, construction, lifecycle legality, money
arithmetic, proration, entitlement composition, quota derivation, invoice totals, and
numbering are all Foundation behaviour that these methods *call*. Where the Foundation has
no factory method yet (its domain milestone has not landed), the use case composes the
Foundation's own immutable aggregate from the command's fields and then runs the
**Foundation validator** before persisting — construction is not a rule; the rule stays in
`billing.foundation`.

Query use cases are read-only: no transaction, no audit, no mutation. Any number they
report is computed by a Foundation service or classmethod, never re-derived here.
"""

from __future__ import annotations

from dataclasses import replace

from billing.foundation import (
    AccountHierarchy,
    AccountStatus,
    BillingAccount,
    BillingValidationError,
    CreditGrant,
    CreditMovement,
    DefineMeterRequest,
    DefinePlanRequest,
    EntitlementGrant,
    IssueInvoiceRequest,
    LedgerBalance,
    LedgerEntry,
    License,
    LicenseState,
    MeterAggregation,
    Money,
    OpenAccountRequest,
    Payment,
    PaymentState,
    PriceBook,
    RecordUsageRequest,
    Refund,
    RefundApproval,
    RefundPolicy,
    RefundState,
    StartSubscriptionRequest,
    UsageRecord,
)
from billing.foundation import CreditBalance as _CreditBalance
from billing.foundation.seams import (
    PREFIX_CREDIT,
    PREFIX_CREDIT_MOVEMENT,
    PREFIX_ENTITLEMENT,
    PREFIX_LEDGER_ENTRY,
    PREFIX_LICENSE,
    PREFIX_PAYMENT,
    PREFIX_PRICE_BOOK,
    PREFIX_REFUND,
    Clock,
    IdGenerator,
)

from . import commands as c
from . import mapping as m
from . import queries as q
from .authorization import AuthorizationOrchestrator
from .authorization import BillingPermission as P
from .context import BillingContext
from .errors import BillingApplicationError
from .events import PREFIX_APP_EVENT, ApplicationEvent, ApplicationEventType
from .unit_of_work import UnitOfWorkFactory


class CommandUseCases:
    """Write orchestration. Stateless apart from its injected collaborators."""

    def __init__(
        self,
        *,
        context: BillingContext,
        authz: AuthorizationOrchestrator,
        uow_factory: UnitOfWorkFactory,
        ids: IdGenerator,
        clock: Clock,
    ) -> None:
        self._ctx = context
        self._authz = authz
        self._uow = uow_factory
        self._ids = ids
        self._clock = clock

    # ── internals ───────────────────────────────────────────────────────────────
    @property
    def _repos(self):
        return self._ctx.repositories

    @property
    def _svc(self):
        return self._ctx.services

    def _event(
        self,
        type_: ApplicationEventType,
        command,
        *,
        subject_id: str = "",
        **data: object,
    ) -> ApplicationEvent:
        return ApplicationEvent(
            id=self._ids.new(PREFIX_APP_EVENT),
            type=type_,
            ts=self._clock.now(),
            use_case=type(command).__name__,
            outcome="completed",
            org_id=getattr(command, "org_id", ""),
            actor=getattr(command, "actor_id", ""),
            subject_id=subject_id,
            data=dict(data),
        )

    def _account(self, org_id: str, account_id: str) -> BillingAccount:
        account = self._repos.accounts.get(org_id, account_id)
        if account is None:
            raise BillingApplicationError(f"billing account {account_id!r} not found in {org_id!r}")
        return account

    # ── billing accounts ────────────────────────────────────────────────────────
    def open_account(self, command: c.OpenBillingAccount) -> BillingAccount:
        self._authz.require(command.actor_id, command.org_id, P.BILLING_MANAGE)
        with self._uow.new() as uow:
            result = self._ctx.factory.open_account(
                OpenAccountRequest(
                    org_id=command.org_id,
                    display_name=command.display_name,
                    currency=command.currency,
                    kind=command.kind,
                    parent_account_id=command.parent_account_id,
                    contact=command.contact,
                    address=command.address,
                ),
                actor_id=command.actor_id,
            )
            account = self._repos.accounts.add(result.account)
            uow.stage(
                self._event(
                    ApplicationEventType.ACCOUNT_PROVISIONED,
                    command,
                    subject_id=account.id,
                    currency=account.currency.value,
                )
            )
            if account.is_child:
                uow.stage(
                    self._event(
                        ApplicationEventType.ACCOUNT_HIERARCHY_LINKED,
                        command,
                        subject_id=account.id,
                        parent_account_id=account.parent_account_id,
                    )
                )
            return account

    def update_profile(self, command: c.UpdateBillingProfile) -> BillingAccount:
        self._authz.require(
            command.actor_id, command.org_id, P.BILLING_MANAGE, resource_id=command.account_id
        )
        with self._uow.new() as uow:
            account = self._account(command.org_id, command.account_id)
            updated = self._repos.accounts.replace(
                replace(account, contact=command.contact, address=command.address)
            )
            uow.stage(
                self._event(
                    ApplicationEventType.ACCOUNT_PROVISIONED,
                    command,
                    subject_id=updated.id,
                    change="profile",
                )
            )
            return updated

    def close_account(self, command: c.CloseBillingAccount) -> BillingAccount:
        """Closing **retains** the account and every revenue record (ADR-0023 RS4)."""
        self._authz.require(
            command.actor_id, command.org_id, P.BILLING_MANAGE, resource_id=command.account_id
        )
        with self._uow.new() as uow:
            account = self._account(command.org_id, command.account_id)
            closed = self._repos.accounts.replace(
                replace(account, status=AccountStatus.CLOSED, closed_at=self._clock.now())
            )
            uow.stage(
                self._event(
                    ApplicationEventType.ACCOUNT_PROVISIONED,
                    command,
                    subject_id=closed.id,
                    change="closed",
                    reason=command.reason,
                )
            )
            return closed

    # ── catalog ─────────────────────────────────────────────────────────────────
    def define_meter(self, command: c.DefineMeter):
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            request = DefineMeterRequest(
                code=command.code,
                display_name=command.display_name,
                unit=command.unit,
                aggregation=command.aggregation,
                source=command.source,
                org_id=command.org_id,
            )
            if command.rounding is not None:
                request = replace(request, rounding=command.rounding)
            meter = self._ctx.factory.define_meter(request, actor_id=command.actor_id)
            stored = self._repos.meters.add(meter)
            uow.stage(
                self._event(
                    ApplicationEventType.CATALOG_PUBLISHED,
                    command,
                    subject_id=stored.id,
                    meter_code=stored.code,
                )
            )
            return stored

    def define_plan(self, command: c.DefinePlan):
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            known_meters = frozenset(
                mt.code for mt in self._repos.meters.all(org_id=command.org_id)
            )
            plan = self._ctx.factory.define_plan(
                DefinePlanRequest(
                    code=command.code,
                    display_name=command.display_name,
                    tier=command.tier,
                    features=command.features,
                    metered_codes=command.metered_codes,
                    org_id=command.org_id,
                ),
                known_meters=known_meters,
                actor_id=command.actor_id,
            )
            stored = self._repos.plans.add(plan)
            uow.stage(
                self._event(
                    ApplicationEventType.CATALOG_PUBLISHED,
                    command,
                    subject_id=stored.id,
                    plan_code=stored.code,
                )
            )
            return stored

    def publish_plan(self, command: c.PublishPlan):
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            published = self._repos.plans.add(
                self._ctx.factory.publish_plan(command.plan, actor_id=command.actor_id)
            )
            uow.stage(
                self._event(
                    ApplicationEventType.CATALOG_PUBLISHED,
                    command,
                    subject_id=published.id,
                    plan_code=published.code,
                    version=published.version,
                )
            )
            return published

    # ── pricing ─────────────────────────────────────────────────────────────────
    def publish_price_book(self, command: c.PublishPriceBook) -> PriceBook:
        """Composes the Foundation aggregate, then defers to the **Foundation validator**;
        no pricing rule is evaluated here."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CATALOG_MANAGE)
        with self._uow.new() as uow:
            book = PriceBook(
                id=self._ids.new(PREFIX_PRICE_BOOK),
                org_id=command.org_id,
                code=command.code,
                display_name=command.display_name,
                currency=command.currency,
                published=True,
                prices=command.prices,
            )
            diagnostics = self._svc.validator.validate_price_book(book)
            if diagnostics.has_errors:
                raise BillingValidationError(diagnostics)
            stored = self._repos.price_books.add(book)
            uow.stage(
                self._event(
                    ApplicationEventType.PRICE_BOOK_PUBLISHED,
                    command,
                    subject_id=stored.id,
                    code=stored.code,
                )
            )
            return stored

    # ── subscriptions ───────────────────────────────────────────────────────────
    def start_subscription(self, command: c.StartSubscription):
        self._authz.require(
            command.actor_id, command.org_id, P.BILLING_MANAGE, resource_id=command.account_id
        )
        with self._uow.new() as uow:
            result = self._ctx.factory.start_subscription(
                StartSubscriptionRequest(
                    org_id=command.org_id,
                    account_id=command.account_id,
                    plan_code=command.plan_code,
                    plan_version=command.plan_version,
                    price_book_id=command.price_book_id,
                    price_book_version=command.price_book_version,
                    currency=command.currency,
                    interval=command.interval,
                    items=command.items,
                    trial_seconds=command.trial_seconds,
                    contract_id=command.contract_id,
                ),
                actor_id=command.actor_id,
            )
            subscription = self._repos.subscriptions.add(result.subscription)
            uow.stage(
                self._event(
                    ApplicationEventType.SUBSCRIPTION_PROVISIONED,
                    command,
                    subject_id=subscription.id,
                    state=subscription.state.value,
                )
            )
            return subscription

    def transition_subscription(self, command: c.TransitionSubscription):
        """Legality is decided by the Foundation FSM; this method only routes the trigger."""
        self._authz.require(
            command.actor_id, command.org_id, P.BILLING_MANAGE, resource_id=command.subscription_id
        )
        with self._uow.new() as uow:
            current = self._repos.subscriptions.get(command.org_id, command.subscription_id)
            if current is None:
                raise BillingApplicationError(f"subscription {command.subscription_id!r} not found")
            moved = self._svc.lifecycle.apply(current, command.trigger, at=self._clock.now())
            stored = self._repos.subscriptions.replace(moved)
            uow.stage(
                self._event(
                    ApplicationEventType.SUBSCRIPTION_TRANSITIONED,
                    command,
                    subject_id=stored.id,
                    trigger=command.trigger.value,
                    state=stored.state.value,
                )
            )
            return stored

    def change_plan(self, command: c.ChangeSubscriptionPlan):
        """Reports the Foundation's proration decision. The *decision* and the *amount* are
        both computed by `ProrationService`."""
        self._authz.require(
            command.actor_id, command.org_id, P.BILLING_MANAGE, resource_id=command.subscription_id
        )
        with self._uow.new() as uow:
            subscription = self._repos.subscriptions.get(command.org_id, command.subscription_id)
            if subscription is None or subscription.current_period is None:
                raise BillingApplicationError(
                    f"subscription {command.subscription_id!r} not found or has no period"
                )
            at = self._clock.now() if command.at is None else command.at
            outcome = self._svc.proration.decide(
                period=subscription.current_period,
                at=at,
                is_expansion=command.is_expansion,
                immediate_downgrade=command.immediate_downgrade,
            )
            adjustment = (
                self._svc.proration.amount(command.price_delta, outcome)
                if command.price_delta is not None
                else None
            )
            uow.stage(
                self._event(
                    ApplicationEventType.SUBSCRIPTION_TRANSITIONED,
                    command,
                    subject_id=subscription.id,
                    timing=outcome.timing.value,
                    behaviour=outcome.behaviour.value,
                )
            )
            return outcome, adjustment

    # ── entitlements ────────────────────────────────────────────────────────────
    def grant_entitlement(self, command: c.GrantEntitlement) -> EntitlementGrant:
        self._authz.require(
            command.actor_id, command.org_id, P.BILLING_MANAGE, resource_id=command.account_id
        )
        with self._uow.new() as uow:
            grant = self._repos.grants.add(
                EntitlementGrant(
                    id=self._ids.new(PREFIX_ENTITLEMENT),
                    org_id=command.org_id,
                    account_id=command.account_id,
                    entitlement=command.entitlement,
                    source=command.entitlement.source,
                    source_ref=command.source_ref,
                    granted_at=self._clock.now(),
                )
            )
            uow.stage(
                self._event(
                    ApplicationEventType.ENTITLEMENTS_REPUBLISHED,
                    command,
                    subject_id=grant.id,
                    code=command.entitlement.code,
                )
            )
            return grant

    def revoke_entitlement(self, command: c.RevokeEntitlement) -> EntitlementGrant:
        self._authz.require(
            command.actor_id, command.org_id, P.BILLING_MANAGE, resource_id=command.grant_id
        )
        with self._uow.new() as uow:
            revoked = self._repos.grants.revoke(command.org_id, command.grant_id, self._clock.now())
            uow.stage(
                self._event(
                    ApplicationEventType.ENTITLEMENTS_REPUBLISHED,
                    command,
                    subject_id=command.grant_id,
                    change="revoked",
                )
            )
            return revoked

    def publish_snapshot(self, command: c.PublishEntitlementSnapshot):
        """Composition precedence and contraction are `EntitlementResolver` behaviour; the
        snapshot is built by the Foundation factory, which validates gate classification."""
        self._authz.require(
            command.actor_id, command.org_id, P.BILLING_MANAGE, resource_id=command.account_id
        )
        with self._uow.new() as uow:
            from billing.foundation import EntitlementResolver

            resolver = EntitlementResolver(command.gates)
            grants = self._repos.grants.active_for(command.org_id, command.account_id)
            entitlements = resolver.compose(grants)
            if command.subscription_id:
                subscription = self._repos.subscriptions.get(
                    command.org_id, command.subscription_id
                )
                if subscription is not None:
                    entitlements = resolver.contract_for_state(entitlements, subscription.state)
            previous = self._repos.snapshots.latest(command.org_id, command.account_id)
            version = (previous.version + 1) if previous else 1
            result = self._ctx.factory.publish_snapshot(
                org_id=command.org_id,
                account_id=command.account_id,
                version=version,
                entitlements=entitlements,
                gates=command.gates,
                subscription_id=command.subscription_id,
                max_staleness_seconds=command.max_staleness_seconds,
                actor_id=command.actor_id,
            )
            snapshot = self._repos.snapshots.publish(result.snapshot)
            uow.stage(
                self._event(
                    ApplicationEventType.ENTITLEMENTS_REPUBLISHED,
                    command,
                    subject_id=snapshot.id,
                    version=snapshot.version,
                )
            )
            return snapshot

    # ── metering ────────────────────────────────────────────────────────────────
    def record_usage(self, command: c.RecordUsage) -> UsageRecord | None:
        """Idempotent: a source event already ingested is a no-op returning `None`.
        Billing measures nothing — the caller supplies a decoded observation."""
        self._authz.require(
            command.actor_id, command.org_id, P.BILLING_USAGE_INGEST, resource_id=command.account_id
        )
        with self._uow.new() as uow:
            if self._repos.usage.exists(command.event_id, command.meter_code):
                return None
            record = self._ctx.factory.record_usage(
                RecordUsageRequest(
                    org_id=command.org_id,
                    account_id=command.account_id,
                    meter_code=command.meter_code,
                    units=command.units,
                    unit=command.unit,
                    scope=command.scope,
                    period=command.period,
                    event_id=command.event_id,
                    event_type=command.event_type,
                    observed_at=command.observed_at,
                    subscription_id=command.subscription_id,
                )
            )
            stored = self._repos.usage.append(record)
            uow.stage(
                self._event(
                    ApplicationEventType.USAGE_INGESTED,
                    command,
                    subject_id=stored.id,
                    meter_code=stored.meter_code,
                    units=stored.quantity.units,
                )
            )
            return stored

    def quarantine_usage(self, command: c.QuarantineUsage) -> UsageRecord:
        """Unattributable usage is recorded and surfaced — never silently billed, never
        silently dropped (ADR-0023 M6)."""
        self._authz.require(command.actor_id, command.org_id, P.BILLING_USAGE_INGEST)
        with self._uow.new() as uow:
            record = self._ctx.factory.quarantine_usage(
                RecordUsageRequest(
                    org_id=command.org_id,
                    account_id=command.account_id,
                    meter_code=command.meter_code,
                    units=command.units,
                    unit=command.unit,
                    scope=command.scope,
                    period=command.period,
                    event_id=command.event_id,
                    event_type=command.event_type,
                    observed_at=command.observed_at,
                ),
                reason=command.reason,
            )
            stored = self._repos.usage.append(record)
            uow.stage(
                self._event(
                    ApplicationEventType.USAGE_REJECTED,
                    command,
                    subject_id=stored.id,
                    reason=command.reason,
                )
            )
            return stored

    # ── credits + ledger ────────────────────────────────────────────────────────
    def grant_credit(self, command: c.GrantCredit) -> CreditGrant:
        self._authz.require(
            command.actor_id, command.org_id, P.BILLING_MANAGE, resource_id=command.account_id
        )
        with self._uow.new() as uow:
            at = self._clock.now()
            grant = self._repos.credits.grant(
                CreditGrant(
                    id=self._ids.new(PREFIX_CREDIT),
                    org_id=command.org_id,
                    account_id=command.account_id,
                    kind=command.kind,
                    amount=command.amount,
                    granted_at=at,
                    expires_at=command.expires_at,
                    source_ref=command.source_ref,
                )
            )
            self._repos.credits.append_movement(
                CreditMovement(
                    id=self._ids.new(PREFIX_CREDIT_MOVEMENT),
                    org_id=command.org_id,
                    account_id=command.account_id,
                    grant_id=grant.id,
                    amount=command.amount,
                    occurred_at=at,
                    reason="grant",
                )
            )
            uow.stage(
                self._event(
                    ApplicationEventType.CREDIT_ISSUED,
                    command,
                    subject_id=grant.id,
                    minor=command.amount.minor,
                )
            )
            return grant

    def post_ledger_entry(self, command: c.PostLedgerEntry) -> LedgerEntry:
        self._authz.require(
            command.actor_id, command.org_id, P.BILLING_MANAGE, resource_id=command.account_id
        )
        with self._uow.new() as uow:
            entry = self._repos.ledger.append(
                LedgerEntry(
                    id=self._ids.new(PREFIX_LEDGER_ENTRY),
                    org_id=command.org_id,
                    account_id=command.account_id,
                    entry_type=command.entry_type,
                    direction=command.direction,
                    amount=command.amount,
                    recorded_at=self._clock.now(),
                    invoice_id=command.invoice_id,
                    payment_id=command.payment_id,
                    subscription_id=command.subscription_id,
                    description=command.description,
                )
            )
            uow.stage(
                self._event(
                    ApplicationEventType.LEDGER_POSTED,
                    command,
                    subject_id=entry.id,
                    entry_type=entry.entry_type.value,
                    minor=entry.amount.minor,
                )
            )
            return entry

    # ── invoicing ───────────────────────────────────────────────────────────────
    def draft_invoice(self, command: c.DraftInvoice):
        self._authz.require(
            command.actor_id,
            command.org_id,
            P.BILLING_INVOICE_ISSUE,
            resource_id=command.account_id,
        )
        with self._uow.new() as uow:
            result = self._ctx.factory.draft_invoice(
                IssueInvoiceRequest(
                    org_id=command.org_id,
                    account_id=command.account_id,
                    currency=command.currency,
                    period=command.period,
                    lines=command.lines,
                    subscription_id=command.subscription_id,
                    payment_terms_days=command.payment_terms_days,
                    purchase_order_ref=command.purchase_order_ref,
                )
            )
            invoice = self._repos.invoices.add(result.invoice)
            uow.stage(
                self._event(
                    ApplicationEventType.INVOICE_PREPARED,
                    command,
                    subject_id=invoice.id,
                    total_minor=result.totals.total.minor,
                )
            )
            return invoice

    def issue_invoice(self, command: c.IssueInvoice):
        """Immutability and gapless numbering are Foundation guarantees; this method only
        supplies the allocator seam and persists the result."""
        self._authz.require(
            command.actor_id,
            command.org_id,
            P.BILLING_INVOICE_ISSUE,
            resource_id=command.invoice_id,
        )
        with self._uow.new() as uow:
            draft = self._repos.invoices.get(command.org_id, command.invoice_id)
            if draft is None:
                raise BillingApplicationError(f"invoice {command.invoice_id!r} not found")
            issued = self._ctx.factory.issue_invoice(
                draft,
                self._repos.invoice_numbers,
                entity_code=command.entity_code,
                jurisdiction=command.jurisdiction,
                actor_id=command.actor_id,
            )
            stored = self._repos.invoices.replace_draft(issued)
            uow.stage(
                self._event(
                    ApplicationEventType.INVOICE_FINALIZED,
                    command,
                    subject_id=stored.id,
                    number=stored.number.formatted if stored.number else "",
                )
            )
            return stored

    # ── payments (domain only — no execution at M9.3) ───────────────────────────
    def request_payment(self, command: c.RequestPayment) -> Payment:
        """Records the intent to collect. **Nothing is executed**: adapters and provider
        interaction land at M9.14 behind contracts (M9.0C)."""
        self._authz.require(
            command.actor_id, command.org_id, P.BILLING_MANAGE, resource_id=command.invoice_id
        )
        with self._uow.new() as uow:
            payment = self._repos.payments.add(
                Payment(
                    id=self._ids.new(PREFIX_PAYMENT),
                    org_id=command.org_id,
                    account_id=command.account_id,
                    invoice_id=command.invoice_id,
                    amount=command.amount,
                    state=PaymentState.REQUIRED,
                    method=command.method,
                    instrument_id=command.instrument_id,
                    requested_at=self._clock.now(),
                )
            )
            uow.stage(
                self._event(
                    ApplicationEventType.PAYMENT_ORCHESTRATED,
                    command,
                    subject_id=payment.id,
                    state=payment.state.value,
                )
            )
            return payment

    # ── refunds ─────────────────────────────────────────────────────────────────
    def request_refund(self, command: c.RequestRefund) -> Refund:
        """Bounds and idempotency are checked by the **Foundation validator**."""
        self._authz.require(
            command.actor_id,
            command.org_id,
            P.BILLING_REFUND_REQUEST,
            resource_id=command.payment_id,
        )
        with self._uow.new() as uow:
            existing = self._repos.refunds.by_request_key(command.org_id, command.request_key)
            if existing is not None:
                return existing  # one refund per request identity
            refund = Refund(
                id=self._ids.new(PREFIX_REFUND),
                org_id=command.org_id,
                account_id=command.account_id,
                payment_id=command.payment_id,
                invoice_id=command.invoice_id,
                amount=command.amount,
                reason=command.reason,
                state=RefundState.PENDING_APPROVAL,
                requester_id=command.actor_id,
                request_key=command.request_key,
                requested_at=self._clock.now(),
            )
            captured = command.captured or command.amount
            prior = self._repos.refunds.for_payment(command.org_id, command.payment_id)
            already = Money.zero(command.amount.currency)
            for r in prior:
                already = already + r.amount
            diagnostics = self._svc.validator.validate_refund(
                refund, captured=captured, already_refunded=already, policy=RefundPolicy()
            )
            if diagnostics.has_errors:
                raise BillingValidationError(diagnostics)
            stored = self._repos.refunds.add(refund)
            uow.stage(
                self._event(
                    ApplicationEventType.REFUND_ORCHESTRATED,
                    command,
                    subject_id=stored.id,
                    state=stored.state.value,
                )
            )
            return stored

    def approve_refund(self, command: c.ApproveRefund) -> Refund:
        """Separation of duties is a Foundation rule — the validator rejects self-approval."""
        self._authz.require(
            command.actor_id,
            command.org_id,
            P.BILLING_REFUND_APPROVE,
            resource_id=command.refund_id,
        )
        with self._uow.new() as uow:
            refund = self._repos.refunds.get(command.org_id, command.refund_id)
            if refund is None:
                raise BillingApplicationError(f"refund {command.refund_id!r} not found")
            approved = replace(
                refund,
                state=RefundState.APPROVED,
                approval=RefundApproval(command.actor_id, self._clock.now(), command.note),
            )
            diagnostics = self._svc.validator.validate_refund(
                approved,
                captured=refund.amount,
                already_refunded=Money.zero(refund.amount.currency),
                policy=RefundPolicy(),
            )
            if diagnostics.has_errors:
                raise BillingValidationError(diagnostics)
            stored = self._repos.refunds.replace(approved)
            uow.stage(
                self._event(
                    ApplicationEventType.REFUND_ORCHESTRATED,
                    command,
                    subject_id=stored.id,
                    state=stored.state.value,
                )
            )
            return stored

    # ── tax ─────────────────────────────────────────────────────────────────────
    def upsert_tax_profile(self, command: c.UpsertTaxProfile):
        """Validation (GSTIN/VAT shape, place of supply) is Foundation behaviour. **No tax
        is determined here** — determination is a rented edge arriving at M9.15."""
        self._authz.require(
            command.actor_id,
            command.org_id,
            P.BILLING_MANAGE,
            resource_id=command.profile.account_id,
        )
        with self._uow.new() as uow:
            diagnostics = self._svc.validator.validate_tax_profile(command.profile)
            if diagnostics.has_errors:
                raise BillingValidationError(diagnostics)
            stored = self._repos.tax_profiles.upsert(command.profile)
            uow.stage(
                self._event(
                    ApplicationEventType.TAX_PROFILE_SYNCHRONIZED,
                    command,
                    subject_id=stored.id,
                    regime=stored.regime.value,
                )
            )
            return stored

    # ── contracts + licensing ───────────────────────────────────────────────────
    def record_contract(self, command: c.RecordContract):
        self._authz.require(command.actor_id, command.org_id, P.BILLING_CONTRACT_MANAGE)
        with self._uow.new() as uow:
            stored = self._repos.contracts.add(command.contract)  # type: ignore[arg-type]
            uow.stage(
                self._event(
                    ApplicationEventType.CONTRACT_ORCHESTRATED,
                    command,
                    subject_id=stored.id,
                    state=stored.state.value,
                )
            )
            return stored

    def issue_license(self, command: c.IssueLicense) -> License:
        self._authz.require(
            command.actor_id,
            command.org_id,
            P.BILLING_CONTRACT_MANAGE,
            resource_id=command.account_id,
        )
        with self._uow.new() as uow:
            license_ = self._repos.licenses.add(
                License(
                    id=self._ids.new(PREFIX_LICENSE),
                    org_id=command.org_id,
                    account_id=command.account_id,
                    kind=command.kind,
                    state=LicenseState.ISSUED,
                    contract_id=command.contract_id,
                    issued_at=self._clock.now(),
                    valid_from=command.valid_from,
                    valid_until=command.valid_until,
                    entitlement_codes=command.entitlement_codes,
                )
            )
            uow.stage(
                self._event(
                    ApplicationEventType.LICENSE_ORCHESTRATED,
                    command,
                    subject_id=license_.id,
                    state=license_.state.value,
                )
            )
            return license_

    def revoke_license(self, command: c.RevokeLicense) -> License:
        self._authz.require(
            command.actor_id,
            command.org_id,
            P.BILLING_CONTRACT_MANAGE,
            resource_id=command.license_id,
        )
        with self._uow.new() as uow:
            license_ = self._repos.licenses.get(command.org_id, command.license_id)
            if license_ is None:
                raise BillingApplicationError(f"license {command.license_id!r} not found")
            revoked = self._repos.licenses.replace(
                replace(license_, state=LicenseState.REVOKED, revoked_at=self._clock.now())
            )
            uow.stage(
                self._event(
                    ApplicationEventType.LICENSE_ORCHESTRATED,
                    command,
                    subject_id=revoked.id,
                    state=revoked.state.value,
                    reason=command.reason,
                )
            )
            return revoked


class QueryUseCases:
    """Read orchestration: fetch through repository interfaces, map to view DTOs.

    No transaction, no audit, no mutation. Every computed number comes from a Foundation
    service or classmethod."""

    def __init__(self, *, context: BillingContext, authz: AuthorizationOrchestrator) -> None:
        self._ctx = context
        self._authz = authz

    @property
    def _repos(self):
        return self._ctx.repositories

    def _read(self, query) -> None:
        self._authz.require(query.actor_id, query.org_id, P.BILLING_READ)

    # ── accounts ────────────────────────────────────────────────────────────────
    def get_account(self, query: q.GetAccount) -> m.AccountView | None:
        self._read(query)
        account = self._repos.accounts.get(query.org_id, query.account_id)
        return m.account_view(account) if account else None

    def get_hierarchy(self, query: q.GetAccountHierarchy) -> m.AccountHierarchyView | None:
        self._read(query)
        parent = self._repos.accounts.get(query.org_id, query.account_id)
        if parent is None:
            return None
        children = self._repos.accounts.children_of(query.org_id, parent.id)
        return m.hierarchy_view(AccountHierarchy(parent, children))

    # ── catalog + pricing ───────────────────────────────────────────────────────
    def list_plans(self, query: q.ListPublishedPlans) -> tuple[m.PlanView, ...]:
        self._read(query)
        return tuple(m.plan_view(p) for p in self._repos.plans.published(org_id=query.org_id))

    def get_price_book(self, query: q.GetPriceBook) -> m.PriceBookView | None:
        self._read(query)
        book = self._repos.price_books.get(query.price_book_id, query.version)
        return m.price_book_view(book) if book else None

    # ── subscriptions ───────────────────────────────────────────────────────────
    def get_subscription(self, query: q.GetSubscription) -> m.SubscriptionView | None:
        self._read(query)
        subscription = self._repos.subscriptions.get(query.org_id, query.subscription_id)
        return m.subscription_view(subscription) if subscription else None

    def list_subscriptions(self, query: q.ListSubscriptions) -> tuple[m.SubscriptionView, ...]:
        self._read(query)
        found = self._repos.subscriptions.by_account(query.org_id, query.account_id)
        return tuple(m.subscription_view(s) for s in found)

    # ── entitlements ────────────────────────────────────────────────────────────
    def get_snapshot(self, query: q.GetEntitlementSnapshot) -> m.EntitlementSnapshotView | None:
        self._read(query)
        snapshot = self._repos.snapshots.latest(query.org_id, query.account_id)
        return m.snapshot_view(snapshot) if snapshot else None

    def get_quota_verdict(self, query: q.GetQuotaVerdict) -> m.QuotaVerdictView | None:
        """Reports the Foundation's verdict. It is information for the enforcing plane,
        not an authorization answer (ADR-0023 §11.1)."""
        self._read(query)
        snapshot = self._repos.snapshots.latest(query.org_id, query.account_id)
        if snapshot is None:
            return None
        verdict = self._ctx.services.quotas.verdict(
            snapshot, query.entitlement_code, consumed=query.consumed, at=query.at
        )
        return m.quota_view(verdict)

    # ── usage ───────────────────────────────────────────────────────────────────
    def get_usage_summary(self, query: q.GetUsageSummary) -> m.UsageSummaryView:
        self._read(query)
        records = self._repos.usage.for_period(
            query.org_id, query.account_id, query.meter_code, query.period
        )
        meter = self._repos.meters.get(query.meter_code, org_id=query.org_id)
        aggregation = meter.aggregation if meter else MeterAggregation.SUM
        unit = meter.unit if meter else (records[0].quantity.unit if records else "unit")
        total = self._ctx.services.metering.total(records, aggregation, unit=unit)
        return m.usage_summary_view(
            account_id=query.account_id,
            meter_code=query.meter_code,
            total=total,
            records=records,
            period_start=query.period.start,
            period_end=query.period.end,
        )

    # ── ledger + credits ────────────────────────────────────────────────────────
    def get_ledger_balance(self, query: q.GetLedgerBalance) -> m.BalanceView | None:
        self._read(query)
        account = self._repos.accounts.get(query.org_id, query.account_id)
        if account is None:
            return None
        entries = self._repos.ledger.for_account(query.org_id, query.account_id)
        return m.ledger_balance_view(LedgerBalance.of(query.account_id, account.currency, entries))

    def get_credit_balance(self, query: q.GetCreditBalance) -> m.BalanceView | None:
        self._read(query)
        account = self._repos.accounts.get(query.org_id, query.account_id)
        if account is None:
            return None
        movements = self._repos.credits.movements_for(query.org_id, query.account_id)
        return m.credit_balance_view(
            _CreditBalance.of(query.account_id, account.currency, movements)
        )

    # ── invoices ────────────────────────────────────────────────────────────────
    def get_invoice(self, query: q.GetInvoice) -> m.InvoiceView | None:
        self._read(query)
        invoice = self._repos.invoices.get(query.org_id, query.invoice_id)
        return m.invoice_view(invoice) if invoice else None

    def list_invoices(self, query: q.ListInvoices) -> tuple[m.InvoiceView, ...]:
        self._read(query)
        return tuple(
            m.invoice_view(i)
            for i in self._repos.invoices.for_account(query.org_id, query.account_id)
        )

    # ── payments + refunds ──────────────────────────────────────────────────────
    def list_payments(self, query: q.ListPayments) -> tuple[m.PaymentView, ...]:
        self._read(query)
        return tuple(
            m.payment_view(p)
            for p in self._repos.payments.for_invoice(query.org_id, query.invoice_id)
        )

    def list_refunds(self, query: q.ListRefunds) -> tuple[m.RefundView, ...]:
        self._read(query)
        return tuple(
            m.refund_view(r)
            for r in self._repos.refunds.for_payment(query.org_id, query.payment_id)
        )

    # ── tax ─────────────────────────────────────────────────────────────────────
    def get_tax_profile(self, query: q.GetTaxProfile) -> m.TaxProfileView | None:
        self._read(query)
        profile = self._repos.tax_profiles.for_account(query.org_id, query.account_id)
        return m.tax_profile_view(profile) if profile else None

    def get_tax_determination(self, query: q.GetTaxDetermination) -> m.TaxDeterminationView | None:
        self._read(query)
        determination = self._repos.tax_determinations.for_invoice(query.org_id, query.invoice_id)
        return m.tax_determination_view(determination) if determination else None

    # ── contracts + licenses ────────────────────────────────────────────────────
    def list_contracts(self, query: q.ListContracts) -> tuple[m.ContractView, ...]:
        self._read(query)
        return tuple(
            m.contract_view(ct)
            for ct in self._repos.contracts.for_account(query.org_id, query.account_id)
        )

    def list_licenses(self, query: q.ListLicenses) -> tuple[m.LicenseView, ...]:
        self._read(query)
        return tuple(
            m.license_view(lic)
            for lic in self._repos.licenses.for_account(query.org_id, query.account_id)
        )
