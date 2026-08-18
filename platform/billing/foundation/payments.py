"""Payments and dunning — **domain only** (M9.14 domain, no execution at M9.2).

ADR-0023 PY1–PY9 and the M9.0C Provider Strategy. The boundary this module encodes:

> **The payment provider is the authority for money movement; Billing is the authority
> for revenue state.** Neither overrides the other; they are reconciled deterministically.

At this milestone there is **no provider, no SDK, no network, no execution** — only the
domain: the payment lifecycle, the operation identity that makes every provider call
idempotent, the record of what a provider reported, the reconciliation verdict, and the
dunning ladder. Adapters land at M9.14 behind contracts; nothing here will change when
they do, which is the point of declaring the domain first.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .money import Money


class PaymentState(StrEnum):
    REQUIRED = "required"
    AUTHORIZING = "authorizing"
    AUTHORIZED = "authorized"
    AUTHORIZATION_EXPIRED = "authorization_expired"
    CAPTURING = "capturing"
    CAPTURED = "captured"
    SETTLED = "settled"
    DISPUTED = "disputed"
    CHARGED_BACK = "charged_back"
    FAILED = "failed"
    RETRY_SCHEDULED = "retry_scheduled"
    ABANDONED = "abandoned"


class PaymentTrigger(StrEnum):
    AUTHORIZE = "authorize"
    AUTHORIZED = "authorized"
    CAPTURE = "capture"
    CAPTURED = "captured"
    SETTLE = "settle"
    DISPUTE = "dispute"
    RESOLVE_DISPUTE = "resolve_dispute"
    CHARGEBACK = "chargeback"
    FAIL = "fail"
    SCHEDULE_RETRY = "schedule_retry"
    ABANDON = "abandon"
    EXPIRE = "expire"


class PaymentMethodKind(StrEnum):
    """How money moves. `OFFLINE_TRANSFER` is first-class, not an exception: most
    enterprise revenue never touches a payment provider at all (M9.0C §14)."""

    CARD = "card"
    BANK_DEBIT = "bank_debit"
    UPI = "upi"
    NETBANKING = "netbanking"
    WALLET = "wallet"
    OFFLINE_TRANSFER = "offline_transfer"
    CREDIT_BALANCE = "credit_balance"


@dataclass(frozen=True, slots=True)
class PaymentInstrument:
    """A **provider token reference** — never instrument data. ADR-0023 PY6: payment data
    never enters VoxVoice, which is a compliance-surface decision, not an operational one.
    `token_ref` is opaque to Billing and meaningful only to the issuing adapter."""

    id: str
    org_id: str
    account_id: str
    kind: PaymentMethodKind
    provider_code: str  # which adapter issued the token; Billing stores the name only
    token_ref: str = ""
    display_hint: str = ""  # e.g. "•••• 4242" — display only, never used for matching
    is_default: bool = False


@dataclass(frozen=True, slots=True)
class OperationIdentity:
    """The Billing-generated idempotency key for a provider operation (ADR-0023 PY3).

    Keyed by `(payment_id, attempt)` so a retried request is provably the *same* request:
    a retry never double-charges, because the provider sees an identical key."""

    payment_id: str
    attempt: int
    key: str

    def __post_init__(self) -> None:
        if self.attempt < 1:
            raise ValueError("payment attempt must be >= 1")


@dataclass(frozen=True, slots=True)
class ProviderReport:
    """What a provider said, recorded verbatim as an observation — **not** as truth.

    ADR-0023 PY4: callbacks are notifications, not state. They are verified, deduplicated,
    and ordered before the FSM advances; duplicate, out-of-order, and missing reports must
    all converge to the same terminal state."""

    provider_code: str
    reference: str  # the provider's own id for the movement
    reported_state: str  # the provider's vocabulary, untranslated
    amount: Money | None = None
    received_at: float = 0.0
    verified: bool = False
    sequence_hint: int = 0


@dataclass(frozen=True, slots=True)
class Payment:
    """An attempt to move money for an invoice. Billing owns this state; the provider owns
    whether the money actually moved."""

    id: str
    org_id: str
    account_id: str
    invoice_id: str
    amount: Money
    state: PaymentState = PaymentState.REQUIRED
    method: PaymentMethodKind = PaymentMethodKind.CARD
    instrument_id: str = ""
    provider_code: str = ""
    attempt: int = 1
    requested_at: float = 0.0
    captured_at: float | None = None
    settled_at: float | None = None
    failure_code: str = ""
    reports: tuple[ProviderReport, ...] = ()

    @property
    def is_successful(self) -> bool:
        return self.state in (PaymentState.CAPTURED, PaymentState.SETTLED)

    @property
    def is_offline(self) -> bool:
        return self.method is PaymentMethodKind.OFFLINE_TRANSFER

    def operation_identity(self) -> OperationIdentity:
        return OperationIdentity(self.id, self.attempt, f"{self.id}:{self.attempt}")


class ReconciliationVerdict(StrEnum):
    """The outcome of comparing provider records against the ledger. DIVERGED raises an
    incident and is **never auto-resolved** (ADR-0023 PY5)."""

    MATCHED = "matched"
    MISSING_IN_PROVIDER = "missing_in_provider"
    MISSING_IN_LEDGER = "missing_in_ledger"
    AMOUNT_DIVERGED = "amount_diverged"


@dataclass(frozen=True, slots=True)
class ReconciliationFinding:
    """One immutable reconciliation observation. Findings are recorded, surfaced, and
    resolved by a human — they are never silently corrected."""

    payment_id: str
    provider_code: str
    verdict: ReconciliationVerdict
    ledger_amount: Money | None = None
    provider_amount: Money | None = None
    observed_at: float = 0.0

    @property
    def is_divergent(self) -> bool:
        return self.verdict is not ReconciliationVerdict.MATCHED


class DunningState(StrEnum):
    """The recovery ladder (§A10 graceful degradation applied to money). Every rung is a
    recorded, reversible state; **payment failure never destroys anything** (PY8)."""

    NOT_STARTED = "not_started"
    RETRYING = "retrying"
    GRACE = "grace"
    CONTRACTED = "contracted"  # entitlements reduced, data intact
    RECOVERED = "recovered"
    ABANDONED = "abandoned"


class DunningTrigger(StrEnum):
    START = "start"
    RETRY = "retry"
    ENTER_GRACE = "enter_grace"
    CONTRACT_ENTITLEMENT = "contract_entitlement"
    RECOVER = "recover"
    ABANDON = "abandon"


@dataclass(frozen=True, slots=True)
class DunningPolicy:
    """A declared, deterministic recovery policy — the ladder is policy, not provider
    behaviour. Provider retry mechanics are an input; this is the decision."""

    max_attempts: int = 4
    retry_backoff_seconds: tuple[int, ...] = (86400, 259200, 604800)
    grace_period_seconds: int = 604800
    contract_entitlements_after_grace: bool = True

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("dunning max_attempts must be >= 1")
        if any(s < 0 for s in self.retry_backoff_seconds):
            raise ValueError("dunning backoff must be non-negative")

    def backoff_for(self, attempt: int) -> int:
        """Deterministic backoff: the last declared interval repeats once exhausted."""
        if attempt < 1:
            raise ValueError("attempt must be >= 1")
        if not self.retry_backoff_seconds:
            return 0
        idx = min(attempt, len(self.retry_backoff_seconds)) - 1
        return self.retry_backoff_seconds[idx]


@dataclass(frozen=True, slots=True)
class DunningProcess:
    """The recovery attempt for one failed payment."""

    id: str
    org_id: str
    account_id: str
    payment_id: str
    state: DunningState = DunningState.NOT_STARTED
    attempts: int = 0
    started_at: float = 0.0
    next_attempt_at: float | None = None
    resolved_at: float | None = None
