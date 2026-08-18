"""Billing lifecycle transition tables (M9.2) — pure, validated, deterministic.

§A2.4: every billing lifecycle is an explicit, table-driven state machine; nothing about
billing state is implicit. Built on `core.fsm` (M5.0) so validation, terminal-state
immutability, guards, and replay come from the shared framework rather than seven
hand-rolled tables.

This module declares **definitions only** — no machine is instantiated and no transition
is executed here. Services (M9.3+) instantiate machines from these definitions with an
injected clock. Illegal edges are rejected, never coerced.
"""

from __future__ import annotations

from core.fsm import StateMachineDefinition, define

from .invoicing import InvoiceState, InvoiceTrigger
from .payments import DunningState, DunningTrigger, PaymentState, PaymentTrigger
from .refunds import RefundState, RefundTrigger
from .subscription import SubscriptionState as S
from .subscription import SubscriptionTrigger as T

# ── subscriptions (Blueprint §6.2) ──────────────────────────────────────────────
SUBSCRIPTION_LIFECYCLE: StateMachineDefinition = define(
    [
        (S.DRAFT, T.SUBMIT, S.PENDING),
        (S.DRAFT, T.START_TRIAL, S.TRIALING),
        (S.PENDING, T.ACTIVATE, S.ACTIVE),
        (S.PENDING, T.CANCEL, S.CANCELLED),
        (S.TRIALING, T.CONVERT_TRIAL, S.ACTIVE),
        (S.TRIALING, T.CANCEL, S.CANCELLED),
        (S.TRIALING, T.EXPIRE, S.EXPIRED),
        (S.ACTIVE, T.SCHEDULE_CHANGE, S.SCHEDULED_CHANGE),
        (S.ACTIVE, T.PAYMENT_FAILED, S.PAST_DUE),
        (S.ACTIVE, T.PAUSE, S.PAUSED),
        (S.ACTIVE, T.CANCEL, S.CANCELLED),
        (S.SCHEDULED_CHANGE, T.APPLY_SCHEDULED_CHANGE, S.ACTIVE),
        (S.SCHEDULED_CHANGE, T.CANCEL, S.CANCELLED),
        (S.SCHEDULED_CHANGE, T.PAYMENT_FAILED, S.PAST_DUE),
        (S.PAST_DUE, T.PAYMENT_RECOVERED, S.ACTIVE),
        (S.PAST_DUE, T.EXHAUST_DUNNING, S.SUSPENDED),
        (S.PAST_DUE, T.CANCEL, S.CANCELLED),
        (S.SUSPENDED, T.REACTIVATE, S.ACTIVE),
        (S.SUSPENDED, T.CANCEL, S.CANCELLED),
        (S.PAUSED, T.RESUME, S.ACTIVE),
        (S.PAUSED, T.CANCEL, S.CANCELLED),
        (S.CANCELLED, T.EXPIRE, S.EXPIRED),
    ]
)

# ── invoices (Blueprint §12) ────────────────────────────────────────────────────
INVOICE_LIFECYCLE: StateMachineDefinition = define(
    [
        (InvoiceState.DRAFT, InvoiceTrigger.SUBMIT_FOR_APPROVAL, InvoiceState.PENDING_APPROVAL),
        (InvoiceState.DRAFT, InvoiceTrigger.ISSUE, InvoiceState.ISSUED),
        (InvoiceState.DRAFT, InvoiceTrigger.VOID, InvoiceState.VOIDED),
        (InvoiceState.PENDING_APPROVAL, InvoiceTrigger.APPROVE, InvoiceState.ISSUED),
        (InvoiceState.PENDING_APPROVAL, InvoiceTrigger.VOID, InvoiceState.VOIDED),
        (InvoiceState.ISSUED, InvoiceTrigger.MARK_PAID, InvoiceState.PAID),
        (InvoiceState.ISSUED, InvoiceTrigger.MARK_OVERDUE, InvoiceState.OVERDUE),
        (InvoiceState.ISSUED, InvoiceTrigger.CREDIT, InvoiceState.CREDITED),
        (InvoiceState.OVERDUE, InvoiceTrigger.MARK_PAID, InvoiceState.PAID),
        (InvoiceState.OVERDUE, InvoiceTrigger.SEND_TO_COLLECTION, InvoiceState.IN_COLLECTION),
        (InvoiceState.OVERDUE, InvoiceTrigger.CREDIT, InvoiceState.CREDITED),
        (InvoiceState.IN_COLLECTION, InvoiceTrigger.MARK_PAID, InvoiceState.PAID),
        (InvoiceState.IN_COLLECTION, InvoiceTrigger.CREDIT, InvoiceState.CREDITED),
    ]
)

# ── payments (Blueprint §11) ────────────────────────────────────────────────────
PAYMENT_LIFECYCLE: StateMachineDefinition = define(
    [
        (PaymentState.REQUIRED, PaymentTrigger.AUTHORIZE, PaymentState.AUTHORIZING),
        (PaymentState.AUTHORIZING, PaymentTrigger.AUTHORIZED, PaymentState.AUTHORIZED),
        (PaymentState.AUTHORIZING, PaymentTrigger.FAIL, PaymentState.FAILED),
        (PaymentState.AUTHORIZED, PaymentTrigger.CAPTURE, PaymentState.CAPTURING),
        (PaymentState.AUTHORIZED, PaymentTrigger.EXPIRE, PaymentState.AUTHORIZATION_EXPIRED),
        (PaymentState.CAPTURING, PaymentTrigger.CAPTURED, PaymentState.CAPTURED),
        (PaymentState.CAPTURING, PaymentTrigger.FAIL, PaymentState.FAILED),
        (PaymentState.CAPTURED, PaymentTrigger.SETTLE, PaymentState.SETTLED),
        (PaymentState.CAPTURED, PaymentTrigger.DISPUTE, PaymentState.DISPUTED),
        (PaymentState.DISPUTED, PaymentTrigger.RESOLVE_DISPUTE, PaymentState.SETTLED),
        (PaymentState.DISPUTED, PaymentTrigger.CHARGEBACK, PaymentState.CHARGED_BACK),
        (PaymentState.FAILED, PaymentTrigger.SCHEDULE_RETRY, PaymentState.RETRY_SCHEDULED),
        (PaymentState.FAILED, PaymentTrigger.ABANDON, PaymentState.ABANDONED),
        (PaymentState.RETRY_SCHEDULED, PaymentTrigger.AUTHORIZE, PaymentState.AUTHORIZING),
        (PaymentState.RETRY_SCHEDULED, PaymentTrigger.ABANDON, PaymentState.ABANDONED),
        (PaymentState.AUTHORIZATION_EXPIRED, PaymentTrigger.AUTHORIZE, PaymentState.AUTHORIZING),
    ]
)

# ── dunning (Blueprint §11 recovery ladder; §A10 graceful degradation) ──────────
DUNNING_LIFECYCLE: StateMachineDefinition = define(
    [
        (DunningState.NOT_STARTED, DunningTrigger.START, DunningState.RETRYING),
        (DunningState.RETRYING, DunningTrigger.RETRY, DunningState.RETRYING),
        (DunningState.RETRYING, DunningTrigger.RECOVER, DunningState.RECOVERED),
        (DunningState.RETRYING, DunningTrigger.ENTER_GRACE, DunningState.GRACE),
        (DunningState.GRACE, DunningTrigger.RECOVER, DunningState.RECOVERED),
        (DunningState.GRACE, DunningTrigger.CONTRACT_ENTITLEMENT, DunningState.CONTRACTED),
        (DunningState.CONTRACTED, DunningTrigger.RECOVER, DunningState.RECOVERED),
        (DunningState.CONTRACTED, DunningTrigger.ABANDON, DunningState.ABANDONED),
    ]
)

# ── refunds (Blueprint §13) ─────────────────────────────────────────────────────
REFUND_LIFECYCLE: StateMachineDefinition = define(
    [
        (RefundState.REQUESTED, RefundTrigger.SUBMIT_FOR_APPROVAL, RefundState.PENDING_APPROVAL),
        (RefundState.REQUESTED, RefundTrigger.CANCEL, RefundState.CANCELLED),
        (RefundState.PENDING_APPROVAL, RefundTrigger.APPROVE, RefundState.APPROVED),
        (RefundState.PENDING_APPROVAL, RefundTrigger.REJECT, RefundState.REJECTED),
        (RefundState.APPROVED, RefundTrigger.PROCESS, RefundState.PROCESSING),
        (RefundState.PROCESSING, RefundTrigger.COMPLETE, RefundState.REFUNDED),
        (RefundState.PROCESSING, RefundTrigger.FAIL, RefundState.FAILED),
        (RefundState.FAILED, RefundTrigger.PROCESS, RefundState.PROCESSING),
    ]
)

#: Every lifecycle this Foundation declares, by name — used by the certification tests to
#: assert that each is validated, acyclic-where-required, and has reachable terminals.
ALL_LIFECYCLES: dict[str, StateMachineDefinition] = {
    "subscription": SUBSCRIPTION_LIFECYCLE,
    "invoice": INVOICE_LIFECYCLE,
    "payment": PAYMENT_LIFECYCLE,
    "dunning": DUNNING_LIFECYCLE,
    "refund": REFUND_LIFECYCLE,
}
