"""Payment Engine wire DTOs (M9.16) — Pydantic v2, additive to the earlier schemas.

Same discipline as before: a third representation, distinct from Foundation aggregates and
Application views. Foundation enums are reused so validation and OpenAPI enums come from the domain
vocabulary, and **M9.5's `MoneyResponse` is reused rather than redeclared**.

Named distinctly from the frozen surfaces they sit beside: `PaymentIntentResponse` and
`PaymentAttemptResponse` rather than a second `PaymentResponse`, which M9.5 owns for the M9.2
record. `CommercialRefundResponse` rather than a second `RefundResponse`, for the same reason.

## What no schema here can carry

There is no field for a card number, a CVV, an expiry, a bank credential, an API key, or a raw
provider payload — not on the way in and not on the way out. `StartAttemptBody.instrument_token` is
a *provider token reference*, opaque to Billing and meaningful only to the adapter that issued it
(ADR-0023 PY6). A schema that could accept a PAN is a schema that will one day be sent one, and it
will be in a request log ten seconds later.

`IngestProviderEventBody` deliberately takes a **raw payload and a signature**, never a parsed
status. A body carrying `{"status": "succeeded"}` would let anyone who can reach the endpoint mark
invoices paid; verification belongs to the adapter that knows the provider's signing scheme.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from billing.foundation import (
    Currency,
    IntentState,
    PaymentMethodKind,
    PaymentPurpose,
    PaymentSubjectKind,
    RefundReason,
)

from .schemas import MoneyResponse


def _fields(view) -> dict:
    """An Application view as a plain mapping.

    `vars()` is not available: every view is a slotted frozen dataclass, deliberately, and a slotted
    object has no `__dict__`. Reading the declared fields is also the stricter choice — it copies
    exactly what the view declares and silently picks up nothing else."""
    return {name: getattr(view, name) for name in type(view).__dataclass_fields__}


def _money(view) -> MoneyResponse:
    return MoneyResponse(minor=view.minor, currency=view.currency)

# ── inbound ─────────────────────────────────────────────────────────────────────


class DeclarePaymentRouteBody(BaseModel):
    """Declare which provider serves which payments. **The only place a provider is named**, and
    it is named by an operator — nothing in this codebase names one."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "provider_code": "offline",
                "priority": 100,
                "currency": "INR",
                "country": "IN",
                "method": "card",
                "version": "2026-01",
            }
        }
    )
    provider_code: str = Field(min_length=1)
    priority: int = 100
    currency: Currency | None = None
    country: str = ""
    method: PaymentMethodKind | None = None
    account_id: str = ""
    version: str = ""
    notes: str = ""


class CreatePaymentIntentBody(BaseModel):
    """Open a collection objective against an already-issued document.

    `request_key` is the caller's idempotency token and is part of the intent's derived identity:
    the same request twice is one objective, and a deliberate second collection on a partly-paid
    document carries a different key and is a different objective."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "account_id": "bac_1",
                "subject_id": "cin_1",
                "subject_kind": "invoice",
                "purpose": "invoice",
                "amount_minor": 106200,
                "currency": "INR",
                "method": "card",
                "request_key": "checkout-9f2c",
                "country": "IN",
            }
        }
    )
    account_id: str = Field(min_length=1)
    subject_id: str = ""
    subject_kind: PaymentSubjectKind = PaymentSubjectKind.INVOICE
    purpose: PaymentPurpose = PaymentPurpose.INVOICE
    amount_minor: int = Field(gt=0)
    currency: Currency = Currency.INR
    method: PaymentMethodKind = PaymentMethodKind.CARD
    instrument_id: str = ""
    request_key: str = Field(default="", max_length=120)
    country: str = ""
    description: str = ""


class StartAttemptBody(BaseModel):
    """Move money. `instrument_token` is a **provider token reference** — never instrument data."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "instrument_token": "tok_provider_opaque",
                "customer_reference": "cus_provider_opaque",
                "return_url": "https://example.test/checkout/return",
            }
        }
    )
    instrument_token: str = Field(default="", max_length=200)
    customer_reference: str = Field(default="", max_length=200)
    return_url: str = ""
    correlation_id: str = ""


class IngestProviderEventBody(BaseModel):
    """A provider callback, raw. **No status field, deliberately** — see the module docstring."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "provider_code": "offline",
                "payload": "event=evt_1;status=succeeded;reference=ref_1;key=pop_abc",
                "signature": "<provider signature header>",
            }
        }
    )
    provider_code: str = Field(min_length=1)
    payload: str = Field(min_length=1, max_length=64_000)
    signature: str = ""


class RefundPaymentBody(BaseModel):
    """Return money. Bounded by what was captured and not already returned — computed from facts."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "amount_minor": 40000,
                "currency": "INR",
                "reason": "customer_request",
                "request_key": "refund-3a71",
            }
        }
    )
    amount_minor: int = Field(gt=0)
    currency: Currency = Currency.INR
    reason: RefundReason = RefundReason.CUSTOMER_REQUEST
    request_key: str = Field(default="", max_length=120)
    note: str = ""


class ResolveDivergenceBody(BaseModel):
    """Close a reconciliation finding. Always attributed — ADR-0023 PY5."""

    note: str = Field(min_length=1)
    acknowledge_only: bool = False


class PaymentReasonBody(BaseModel):
    reason: str = ""


class ReconcileOperationsBody(BaseModel):
    older_than_seconds: float = Field(default=300.0, ge=0)
    limit: int = Field(default=100, ge=1, le=500)


# ── outbound ────────────────────────────────────────────────────────────────────


class PaymentRouteResponse(BaseModel):
    """The provider chosen, **and why** — evidence, not just a code."""

    provider_code: str = ""
    reason: str = ""
    policy_id: str = ""
    policy_version: str = ""
    decided_at: float = 0.0

    @classmethod
    def of(cls, view) -> PaymentRouteResponse:
        return cls(**_fields(view))


class PaymentRoutePolicyResponse(BaseModel):
    id: str
    provider_code: str
    priority: int
    currency: str = ""
    country: str = ""
    method: str = ""
    account_id: str = ""
    version: str = ""
    is_active: bool = True
    notes: str = ""

    @classmethod
    def of(cls, view) -> PaymentRoutePolicyResponse:
        return cls(**_fields(view))


class PaymentPositionResponse(BaseModel):
    """**Derived every time it is asked.** No column anywhere holds these figures."""

    intent_id: str
    currency: str
    requested: MoneyResponse
    captured: MoneyResponse
    refunded: MoneyResponse
    net: MoneyResponse
    outstanding: MoneyResponse
    attempts: int = 0
    successful_attempts: int = 0
    open_attempts: int = 0
    unresolved_attempts: int = 0
    refunds: int = 0
    is_fully_collected: bool = False
    is_overpaid: bool = False
    is_blocked: bool = False

    @classmethod
    def of(cls, view) -> PaymentPositionResponse:
        return cls(
            intent_id=view.intent_id,
            currency=view.currency,
            requested=_money(view.requested),
            captured=_money(view.captured),
            refunded=_money(view.refunded),
            net=_money(view.net),
            outstanding=_money(view.outstanding),
            attempts=view.attempts,
            successful_attempts=view.successful_attempts,
            open_attempts=view.open_attempts,
            unresolved_attempts=view.unresolved_attempts,
            refunds=view.refunds,
            is_fully_collected=view.is_fully_collected,
            is_overpaid=view.is_overpaid,
            is_blocked=view.is_blocked,
        )


class ProviderObservationResponse(BaseModel):
    """What a provider said, recorded verbatim as an observation — **not** as truth."""

    provider_code: str = ""
    reference: str = ""
    reported_state: str = ""
    received_at: float = 0.0
    verified: bool = False
    amount: MoneyResponse | None = None

    @classmethod
    def of(cls, view) -> ProviderObservationResponse:
        return cls(
            provider_code=view.provider_code,
            reference=view.reference,
            reported_state=view.reported_state,
            received_at=view.received_at,
            verified=view.verified,
            amount=_money(view.amount) if view.amount else None,
        )


class PaymentAttemptResponse(BaseModel):
    """One external movement of money. Append-only: a retry is a *different* attempt."""

    id: str
    intent_id: str
    account_id: str
    sequence: int
    state: str
    amount: MoneyResponse
    operation_key: str
    provider_code: str = ""
    provider_reference: str = ""
    provider_status: str = ""
    method: str = ""
    failure_code: str = ""
    failure_category: str = ""
    failure_message: str = ""
    action_url: str = ""
    journal_entry_id: str = ""
    started_at: float = 0.0
    settled_at: float | None = None
    resolved_by: str = ""
    resolution_reason: str = ""
    is_unresolved: bool = False
    observations: list[ProviderObservationResponse] = Field(default_factory=list)
    allowed_triggers: list[str] = Field(default_factory=list)

    @classmethod
    def of(cls, view) -> PaymentAttemptResponse:
        return cls(
            id=view.id,
            intent_id=view.intent_id,
            account_id=view.account_id,
            sequence=view.sequence,
            state=view.state,
            amount=_money(view.amount),
            operation_key=view.operation_key,
            provider_code=view.provider_code,
            provider_reference=view.provider_reference,
            provider_status=view.provider_status,
            method=view.method,
            failure_code=view.failure_code,
            failure_category=view.failure_category,
            failure_message=view.failure_message,
            action_url=view.action_url,
            journal_entry_id=view.journal_entry_id,
            started_at=view.started_at,
            settled_at=view.settled_at,
            resolved_by=view.resolved_by,
            resolution_reason=view.resolution_reason,
            is_unresolved=view.is_unresolved,
            observations=[ProviderObservationResponse.of(o) for o in view.observations],
            allowed_triggers=list(view.allowed_triggers),
        )


class PaymentIntentResponse(BaseModel):
    """A collection objective. Carries no collected/outstanding figure — those are derived."""

    id: str
    account_id: str
    state: str
    amount: MoneyResponse
    purpose: str
    subject_kind: str
    subject_id: str = ""
    subject_digest: str = ""
    method: str = ""
    instrument_id: str = ""
    route: PaymentRouteResponse | None = None
    request_key: str = ""
    attempts_opened: int = 0
    country: str = ""
    description: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    closed_at: float | None = None
    allowed_triggers: list[str] = Field(default_factory=list)

    @classmethod
    def of(cls, view) -> PaymentIntentResponse:
        return cls(
            id=view.id,
            account_id=view.account_id,
            state=view.state,
            amount=_money(view.amount),
            purpose=view.purpose,
            subject_kind=view.subject_kind,
            subject_id=view.subject_id,
            subject_digest=view.subject_digest,
            method=view.method,
            instrument_id=view.instrument_id,
            route=PaymentRouteResponse.of(view.route) if view.route else None,
            request_key=view.request_key,
            attempts_opened=view.attempts_opened,
            country=view.country,
            description=view.description,
            created_at=view.created_at,
            updated_at=view.updated_at,
            closed_at=view.closed_at,
            allowed_triggers=list(view.allowed_triggers),
        )


class ProviderOperationResponse(BaseModel):
    """The durable record that a provider call happened. A `sending` row is a crash signature."""

    operation_key: str
    intent_id: str
    attempt_id: str
    action: str
    provider_code: str
    state: str
    amount: MoneyResponse
    sent_at: float = 0.0
    completed_at: float | None = None
    provider_reference: str = ""
    provider_status: str = ""
    outcome_status: str = ""
    attempts_sent: int = 0
    last_error: str = ""

    @classmethod
    def of(cls, view) -> ProviderOperationResponse:
        return cls(
            operation_key=view.operation_key,
            intent_id=view.intent_id,
            attempt_id=view.attempt_id,
            action=view.action,
            provider_code=view.provider_code,
            state=view.state,
            amount=_money(view.amount),
            sent_at=view.sent_at,
            completed_at=view.completed_at,
            provider_reference=view.provider_reference,
            provider_status=view.provider_status,
            outcome_status=view.outcome_status,
            attempts_sent=view.attempts_sent,
            last_error=view.last_error,
        )


class ProviderEventResponse(BaseModel):
    """An ingested callback and **what was done with it** — including when nothing was."""

    id: str
    provider_code: str
    event_ref: str
    event_type: str
    disposition: str
    verified: bool
    attempt_id: str = ""
    intent_id: str = ""
    outcome_status: str = ""
    provider_reference: str = ""
    raw_status: str = ""
    provider_ts: float = 0.0
    received_at: float = 0.0
    applied_at: float | None = None
    note: str = ""

    @classmethod
    def of(cls, view) -> ProviderEventResponse:
        return cls(**_fields(view))


class CommercialRefundResponse(BaseModel):
    """A refund is a new financial fact, never a state on the payment it reverses."""

    id: str
    intent_id: str
    attempt_id: str
    account_id: str
    state: str
    amount: MoneyResponse
    reason: str
    destination: str
    provider_code: str = ""
    provider_reference: str = ""
    request_key: str = ""
    requested_at: float = 0.0
    completed_at: float | None = None
    failure_code: str = ""
    journal_entry_id: str = ""
    note: str = ""

    @classmethod
    def of(cls, view) -> CommercialRefundResponse:
        return cls(
            id=view.id,
            intent_id=view.intent_id,
            attempt_id=view.attempt_id,
            account_id=view.account_id,
            state=view.state,
            amount=_money(view.amount),
            reason=view.reason,
            destination=view.destination,
            provider_code=view.provider_code,
            provider_reference=view.provider_reference,
            request_key=view.request_key,
            requested_at=view.requested_at,
            completed_at=view.completed_at,
            failure_code=view.failure_code,
            journal_entry_id=view.journal_entry_id,
            note=view.note,
        )


class PaymentDivergenceResponse(BaseModel):
    """Billing and a provider disagreeing, recorded with both sides. Never auto-resolved."""

    id: str
    outcome: str
    status: str
    provider_code: str = ""
    intent_id: str = ""
    attempt_id: str = ""
    operation_key: str = ""
    provider_reference: str = ""
    billing_state: str = ""
    provider_state: str = ""
    billing_amount: MoneyResponse | None = None
    provider_amount: MoneyResponse | None = None
    journal_entry_id: str = ""
    observed_at: float = 0.0
    resolved_at: float | None = None
    resolved_by: str = ""
    resolution_note: str = ""
    detail: str = ""
    is_divergent: bool = False

    @classmethod
    def of(cls, view) -> PaymentDivergenceResponse:
        return cls(
            id=view.id,
            outcome=view.outcome,
            status=view.status,
            provider_code=view.provider_code,
            intent_id=view.intent_id,
            attempt_id=view.attempt_id,
            operation_key=view.operation_key,
            provider_reference=view.provider_reference,
            billing_state=view.billing_state,
            provider_state=view.provider_state,
            billing_amount=(
                _money(view.billing_amount) if view.billing_amount else None
            ),
            provider_amount=(
                _money(view.provider_amount) if view.provider_amount else None
            ),
            journal_entry_id=view.journal_entry_id,
            observed_at=view.observed_at,
            resolved_at=view.resolved_at,
            resolved_by=view.resolved_by,
            resolution_note=view.resolution_note,
            detail=view.detail,
            is_divergent=view.is_divergent,
        )


class PaymentReceiptResponse(BaseModel):
    """What one money-moving request did.

    `outcome` distinguishes the states an HTTP layer must not flatten — `succeeded`,
    `requires_action`, `pending`, `authorized`, `failed`, `ambiguous`, `replayed`, `conflicting` —
    because a client that reads every 200 as "paid" will treat a timeout as a collection."""

    intent_id: str
    attempt_id: str
    outcome: str
    intent_state: str
    attempt_state: str
    provider_code: str = ""
    provider_reference: str = ""
    action_url: str = ""
    failure_code: str = ""
    message: str = ""
    journal_entry_id: str = ""
    ledger_outcome: str = ""
    position: PaymentPositionResponse | None = None
    requires_reconciliation: bool = False

    @classmethod
    def of(cls, view) -> PaymentReceiptResponse:
        return cls(
            intent_id=view.intent_id,
            attempt_id=view.attempt_id,
            outcome=view.outcome,
            intent_state=view.intent_state,
            attempt_state=view.attempt_state,
            provider_code=view.provider_code,
            provider_reference=view.provider_reference,
            action_url=view.action_url,
            failure_code=view.failure_code,
            message=view.message,
            journal_entry_id=view.journal_entry_id,
            ledger_outcome=view.ledger_outcome,
            position=PaymentPositionResponse.of(view.position) if view.position else None,
            requires_reconciliation=view.requires_reconciliation,
        )


class PaymentExplanationResponse(BaseModel):
    """**Why this payment is in this state — from stored evidence, never from a provider.**"""

    intent_id: str
    state: str
    position: PaymentPositionResponse
    provider_code: str = ""
    routing_reason: str = ""
    subject_id: str = ""
    subject_digest: str = ""
    attempts: list[PaymentAttemptResponse] = Field(default_factory=list)
    operations: list[ProviderOperationResponse] = Field(default_factory=list)
    events: list[ProviderEventResponse] = Field(default_factory=list)
    findings: list[PaymentDivergenceResponse] = Field(default_factory=list)
    journal_entry_ids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    is_self_contained: bool = True

    @classmethod
    def of(cls, view) -> PaymentExplanationResponse:
        return cls(
            intent_id=view.intent_id,
            state=view.state,
            position=PaymentPositionResponse.of(view.position),
            provider_code=view.provider_code,
            routing_reason=view.routing_reason,
            subject_id=view.subject_id,
            subject_digest=view.subject_digest,
            attempts=[PaymentAttemptResponse.of(a) for a in view.attempts],
            operations=[ProviderOperationResponse.of(o) for o in view.operations],
            events=[ProviderEventResponse.of(e) for e in view.events],
            findings=[PaymentDivergenceResponse.of(f) for f in view.findings],
            journal_entry_ids=list(view.journal_entry_ids),
            notes=list(view.notes),
            is_self_contained=view.is_self_contained,
        )


class PaymentInspectionResponse(BaseModel):
    intent_id: str
    state: str
    position: PaymentPositionResponse
    is_blocked: bool = False
    has_open_divergence: bool = False
    unposted_captures: list[str] = Field(default_factory=list)
    open_operations: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    is_clean: bool = True

    @classmethod
    def of(cls, view) -> PaymentInspectionResponse:
        return cls(
            intent_id=view.intent_id,
            state=view.state,
            position=PaymentPositionResponse.of(view.position),
            is_blocked=view.is_blocked,
            has_open_divergence=view.has_open_divergence,
            unposted_captures=list(view.unposted_captures),
            open_operations=list(view.open_operations),
            notes=list(view.notes),
            is_clean=view.is_clean,
        )


class PaymentDiagnosticResponse(BaseModel):
    path: str
    code: str
    severity: str
    message: str
    remediation: str = ""


class PaymentDiagnosticsResponse(BaseModel):
    intent_id: str
    is_valid: bool
    findings: list[PaymentDiagnosticResponse] = Field(default_factory=list)

    @classmethod
    def of(cls, view) -> PaymentDiagnosticsResponse:
        return cls(
            intent_id=view.intent_id,
            is_valid=view.is_valid,
            findings=[PaymentDiagnosticResponse(**_fields(f)) for f in view.findings],
        )


class PaymentActivityResponse(BaseModel):
    id: str
    action: str
    at: float
    actor_id: str = ""
    account_id: str = ""
    intent_id: str = ""
    attempt_id: str = ""
    refund_id: str = ""
    provider_code: str = ""
    operation_key: str = ""
    amount_minor: int = 0
    currency: str = ""
    reason: str = ""
    detail: str = ""

    @classmethod
    def of(cls, view) -> PaymentActivityResponse:
        return cls(**_fields(view))


class PaymentSearchResponse(BaseModel):
    intents: list[PaymentIntentResponse] = Field(default_factory=list)
    total: int = 0

    @classmethod
    def of(cls, view) -> PaymentSearchResponse:
        return cls(
            intents=[PaymentIntentResponse.of(i) for i in view.intents], total=view.total
        )


def build_payment_search(
    *,
    org_id: str,
    account_id: str = "",
    subject_id: str = "",
    provider_code: str = "",
    state: IntentState | None = None,
    purpose: PaymentPurpose | None = None,
    currency: Currency | None = None,
    limit: int = 100,
) -> dict:
    """Assemble the declared search dimensions. No free-text predicate reaches the Application."""
    return {
        "account_id": account_id,
        "subject_id": subject_id,
        "provider_code": provider_code,
        "state": state,
        "purpose": purpose,
        "currency": currency,
        "limit": limit,
    }


__all__ = [
    "CommercialRefundResponse",
    "CreatePaymentIntentBody",
    "DeclarePaymentRouteBody",
    "IngestProviderEventBody",
    "PaymentActivityResponse",
    "PaymentAttemptResponse",
    "PaymentDiagnosticResponse",
    "PaymentDiagnosticsResponse",
    "PaymentDivergenceResponse",
    "PaymentExplanationResponse",
    "PaymentInspectionResponse",
    "PaymentIntentResponse",
    "PaymentPositionResponse",
    "PaymentReasonBody",
    "PaymentReceiptResponse",
    "PaymentRoutePolicyResponse",
    "PaymentRouteResponse",
    "PaymentSearchResponse",
    "ProviderEventResponse",
    "ProviderObservationResponse",
    "ProviderOperationResponse",
    "ReconcileOperationsBody",
    "RefundPaymentBody",
    "ResolveDivergenceBody",
    "StartAttemptBody",
    "build_payment_search",
]
