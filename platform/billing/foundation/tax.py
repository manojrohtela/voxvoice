"""Tax — **domain only** (M9.15 domain, no provider at M9.2).

ADR-0023 TX1–TX6 / M9.0C §15. One sentence carries the design:

> **Tax determination is a rented edge; tax evidence is an owned fact.**

So this module declares no rate, no jurisdiction rule, and no calculation. It declares the
customer's **tax profile** (a revenue fact Billing owns, referencing — never duplicating —
the organization), and the immutable **determination evidence** recorded against an
invoice. Rates change; issued invoices must not, which is why the determination is stored
rather than recomputed. Reporting *exports* this evidence; it never recalculates (TX4).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .money import Money


class TaxRegime(StrEnum):
    """Jurisdictional regimes supported by *evidence recording*. Adding a regime is
    adapter configuration plus a member here — never a structural change (TX5)."""

    NONE = "none"
    INDIA_GST = "india_gst"
    EU_VAT = "eu_vat"
    UK_VAT = "uk_vat"
    US_SALES_TAX = "us_sales_tax"
    OTHER = "other"


class TaxpayerKind(StrEnum):
    """B2B vs B2C changes the treatment materially (reverse charge, rate, evidence)."""

    BUSINESS = "business"
    CONSUMER = "consumer"


class TaxTreatment(StrEnum):
    """What the determination concluded. Recorded, never inferred at reporting time."""

    STANDARD = "standard"
    ZERO_RATED = "zero_rated"
    EXEMPT = "exempt"
    REVERSE_CHARGE = "reverse_charge"
    OUT_OF_SCOPE = "out_of_scope"


class GstSplit(StrEnum):
    """India GST place-of-supply outcome. Intra-state splits CGST+SGST; inter-state is
    IGST. Which one applied is evidence, not a runtime decision."""

    NOT_APPLICABLE = "not_applicable"
    INTRA_STATE = "intra_state"  # CGST + SGST
    INTER_STATE = "inter_state"  # IGST


@dataclass(frozen=True, slots=True)
class TaxRegistration:
    """A registration identifier (GSTIN, VAT ID, or equivalent) plus the *recorded result*
    of validating it. Validation is an adapter call; its outcome is stored with a
    timestamp, and a failure is surfaced — never assumed valid (TX under M9.0C §18)."""

    regime: TaxRegime
    number: str
    country: str = ""
    validated: bool = False
    validated_at: float | None = None
    validation_note: str = ""


@dataclass(frozen=True, slots=True)
class TaxProfile:
    """The customer's tax posture — a revenue fact Billing owns, referencing the
    organization (ADR-0023 TX3). `place_of_supply` is the jurisdiction code the
    determination is made against (e.g. an Indian state code for GST)."""

    id: str
    org_id: str
    account_id: str
    regime: TaxRegime = TaxRegime.NONE
    taxpayer_kind: TaxpayerKind = TaxpayerKind.CONSUMER
    country: str = ""
    place_of_supply: str = ""
    registrations: tuple[TaxRegistration, ...] = ()
    exemption_ref: str = ""
    prices_include_tax: bool = False  # declared per market, never improvised at issue time
    updated_at: float = 0.0

    def registration(self, regime: TaxRegime) -> TaxRegistration | None:
        return next((r for r in self.registrations if r.regime is regime), None)

    @property
    def is_business(self) -> bool:
        return self.taxpayer_kind is TaxpayerKind.BUSINESS


@dataclass(frozen=True, slots=True)
class TaxComponent:
    """One named component of a determination (CGST, SGST, IGST, VAT, state/county tax).
    `rate_basis_points` keeps the rate exact in integers — no float ever touches tax."""

    name: str
    rate_basis_points: int
    amount: Money

    def __post_init__(self) -> None:
        if self.rate_basis_points < 0:
            raise ValueError("tax rate basis points must be >= 0")


@dataclass(frozen=True, slots=True)
class TaxDetermination:
    """Immutable evidence of what was determined, by whom, and when.

    Recorded with the invoice so the invoice remains explainable after rates change
    (TX2). `adapter_code`/`adapter_version` name the rented edge that produced it — the
    determination is theirs; this record is ours."""

    id: str
    org_id: str
    account_id: str
    invoice_id: str
    regime: TaxRegime
    treatment: TaxTreatment
    taxable_amount: Money
    tax_total: Money
    components: tuple[TaxComponent, ...] = ()
    gst_split: GstSplit = GstSplit.NOT_APPLICABLE
    place_of_supply: str = ""
    customer_registration: str = ""
    adapter_code: str = ""
    adapter_version: str = ""
    determined_at: float = 0.0
    withholding_minor: int = 0  # customer remittance obligation, never a discount (TX6)

    @property
    def is_reverse_charge(self) -> bool:
        return self.treatment is TaxTreatment.REVERSE_CHARGE

    @property
    def is_zero(self) -> bool:
        return self.tax_total.is_zero
