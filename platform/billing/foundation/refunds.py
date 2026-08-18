"""Refunds, disputes, and chargebacks — **domain only** (M9.16 domain).

ADR-0023 PY9 / Blueprint §13. Three invariants encoded here:

* **A refund is never a ledger edit.** It is an independent, append-only event that
  references the original charge and produces a credit note.
* **Refunds are bounded**: never more than the captured amount, never against a voided
  invoice, never duplicated for the same request identity.
* **Disputes and chargebacks are distinct from refunds** — provider-initiated, with their
  own evidence and deadlines — and must never be silently reconciled as refunds.

Approval above a policy threshold requires **separation of duties**: the approver may not
be the requester. That is a domain rule, evaluated on identities the caller supplies; it is
*not* a permission check (Billing performs none — ADR-0023 §11.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .money import Money


class RefundState(StrEnum):
    REQUESTED = "requested"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    PROCESSING = "processing"
    REFUNDED = "refunded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RefundTrigger(StrEnum):
    SUBMIT_FOR_APPROVAL = "submit_for_approval"
    APPROVE = "approve"
    REJECT = "reject"
    PROCESS = "process"
    COMPLETE = "complete"
    FAIL = "fail"
    CANCEL = "cancel"


class RefundDestination(StrEnum):
    """Money back to the instrument, or credit onto the balance. Both are first-class;
    neither is a workaround for the other (Blueprint §13)."""

    ORIGINAL_INSTRUMENT = "original_instrument"
    CREDIT_BALANCE = "credit_balance"


class RefundReason(StrEnum):
    CUSTOMER_REQUEST = "customer_request"
    SERVICE_CREDIT = "service_credit"
    BILLING_ERROR = "billing_error"
    DOWNGRADE_CREDIT = "downgrade_credit"
    GOODWILL = "goodwill"
    DUPLICATE_CHARGE = "duplicate_charge"


@dataclass(frozen=True, slots=True)
class RefundApproval:
    """The approval trail. `approver_id != requester_id` is enforced by validation —
    separation of duties, the ADR-0022 approval posture applied to money."""

    approver_id: str
    approved_at: float
    note: str = ""


@dataclass(frozen=True, slots=True)
class Refund:
    """An append-only refund record referencing the original payment and invoice."""

    id: str
    org_id: str
    account_id: str
    payment_id: str
    invoice_id: str
    amount: Money
    reason: RefundReason
    destination: RefundDestination = RefundDestination.ORIGINAL_INSTRUMENT
    state: RefundState = RefundState.REQUESTED
    requester_id: str = ""
    request_key: str = ""  # idempotency key — one refund per request identity
    requested_at: float = 0.0
    approval: RefundApproval | None = None
    credit_note_id: str = ""
    tax_reversal_minor: int = 0  # proportional tax reversal, recorded as evidence
    completed_at: float | None = None

    @property
    def is_complete(self) -> bool:
        return self.state is RefundState.REFUNDED

    @property
    def requires_approval(self) -> bool:
        return self.state is RefundState.PENDING_APPROVAL


class DisputeStage(StrEnum):
    """Provider-initiated, deadline-bearing, and deliberately *not* a refund."""

    OPENED = "opened"
    EVIDENCE_REQUIRED = "evidence_required"
    EVIDENCE_SUBMITTED = "evidence_submitted"
    WON = "won"
    LOST = "lost"


@dataclass(frozen=True, slots=True)
class Dispute:
    """A provider-initiated challenge to a captured payment. Tracked separately from
    refunds so a chargeback is never silently booked as a customer refund."""

    id: str
    org_id: str
    account_id: str
    payment_id: str
    amount: Money
    stage: DisputeStage = DisputeStage.OPENED
    provider_code: str = ""
    opened_at: float = 0.0
    evidence_due_at: float | None = None
    resolved_at: float | None = None


@dataclass(frozen=True, slots=True)
class RefundPolicy:
    """Declared thresholds. Above `approval_threshold_minor`, approval with separation of
    duties is required — a policy, not an ad-hoc judgement at the call site."""

    approval_threshold_minor: int = 0  # 0 ⇒ every refund needs approval
    allow_partial: bool = True

    def requires_approval(self, amount: Money) -> bool:
        return amount.minor > self.approval_threshold_minor
