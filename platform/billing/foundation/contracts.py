"""Enterprise contracts and licenses (M9.21 domain).

Blueprint §16 / M9.0C §14. Enterprise is **contract-driven configuration of the same
core**, never a second code path: a contract materializes a subscription whose catalog and
price references point at contract-scoped versions, and everything downstream — rating,
invoicing, tax, ledger — is identical to self-serve.

A **license** is an entitlement grant with an issuer, a scope, and a validity window. It
has no payment edge at all, which is precisely why it could never be rented from a payment
platform (M9.0C §14).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .domain import BillingInterval
from .money import Money


class ContractState(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    RENEWED = "renewed"
    ENDED = "ended"


class CommitmentKind(StrEnum):
    """How a committed spend behaves when actual consumption diverges."""

    NONE = "none"
    MINIMUM_SPEND = "minimum_spend"  # shortfall is charged (true-up)
    PREPAID_DRAWDOWN = "prepaid_drawdown"  # consumption draws down a prepaid balance


class SettlementMode(StrEnum):
    """How a contract's invoices are settled. Offline is the enterprise default: a net-N
    invoice against a PO, settled by bank transfer, with no payment provider involved."""

    PROVIDER = "provider"
    OFFLINE_TRANSFER = "offline_transfer"


@dataclass(frozen=True, slots=True)
class ContractTerm:
    """The negotiated term. Notice windows and auto-renew are contract terms that drive
    the subscription FSM deterministically — never a manual reminder."""

    starts_at: float
    ends_at: float
    interval: BillingInterval = BillingInterval.YEARLY
    auto_renew: bool = True
    notice_period_seconds: int = 2592000  # 30 days
    uplift_basis_points: int = 0  # renewal price uplift, exact integer

    def __post_init__(self) -> None:
        if self.ends_at <= self.starts_at:
            raise ValueError("contract term must end after it starts")

    def renewal_notice_at(self) -> float:
        return self.ends_at - self.notice_period_seconds

    def covers(self, ts: float) -> bool:
        return self.starts_at <= ts < self.ends_at


@dataclass(frozen=True, slots=True)
class Commitment:
    """A committed spend tracked against actual consumption. Drawdown, overage, and
    true-up are **ledger facts**, not spreadsheets (Blueprint §16)."""

    kind: CommitmentKind
    amount: Money
    consumed: Money
    true_up_at_term_end: bool = True

    @property
    def remaining(self) -> Money:
        return self.amount - self.consumed

    @property
    def is_exhausted(self) -> bool:
        return self.remaining.minor <= 0

    @property
    def shortfall(self) -> Money:
        """What a MINIMUM_SPEND contract owes at true-up. Never negative."""
        r = self.remaining
        return r if r.minor > 0 else Money.zero(self.amount.currency)


@dataclass(frozen=True, slots=True)
class Contract:
    """The authoritative commercial agreement. Contract terms are VoxVoice facts — no
    provider ever sees them (M9.0C §14)."""

    id: str
    org_id: str
    account_id: str
    code: str
    state: ContractState = ContractState.DRAFT
    term: ContractTerm | None = None
    plan_code: str = ""
    plan_version: int = 0
    price_book_id: str = ""  # contract-scoped price book
    commitment: Commitment | None = None
    settlement: SettlementMode = SettlementMode.OFFLINE_TRANSFER
    payment_terms_days: int = 30
    purchase_order_ref: str = ""
    entitlement_overrides: tuple[str, ...] = ()  # entitlement codes granted by contract
    signed_at: float | None = None

    @property
    def is_active(self) -> bool:
        return self.state is ContractState.ACTIVE


class LicenseKind(StrEnum):
    """Deployment shapes a license may cover — including offline/air-gapped, where the
    platform still treats the license as an entitlement, not a new permission system."""

    HOSTED = "hosted"
    SELF_HOSTED = "self_hosted"
    AIR_GAPPED = "air_gapped"


class LicenseState(StrEnum):
    ISSUED = "issued"
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class License:
    """An entitlement grant with an issuer, scope, and validity window. Verification is a
    declared capability (`verification_ref` names how, without implementing it here); the
    platform still resolves the license through the Entitlement Engine."""

    id: str
    org_id: str
    account_id: str
    kind: LicenseKind
    state: LicenseState = LicenseState.ISSUED
    contract_id: str = ""
    issued_at: float = 0.0
    valid_from: float = 0.0
    valid_until: float | None = None
    entitlement_codes: tuple[str, ...] = ()
    seat_limit: int | None = None
    verification_ref: str = ""  # how an offline deployment verifies this grant
    revoked_at: float | None = None

    def is_valid_at(self, ts: float) -> bool:
        if self.state in (LicenseState.REVOKED, LicenseState.EXPIRED):
            return False
        return self.valid_from <= ts and (self.valid_until is None or ts < self.valid_until)
