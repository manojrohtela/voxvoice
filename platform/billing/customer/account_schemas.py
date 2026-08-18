"""Billing Account wire DTOs (M9.8) — Pydantic v2, additive to the M9.5/M9.6/M9.7 schemas.

Same discipline as before: a third representation, distinct from Foundation aggregates and
Application views, with `to_*()` inbound and `from_*()` outbound. Foundation enums are reused
so validation and OpenAPI enums come from the domain vocabulary.

A credit limit is the only money on this surface, and it crosses as integer minor units plus an
explicit currency like every other amount in Billing (ADR-0023 RS5).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from billing.foundation import (
    AccountAddress,
    AccountContact,
    AccountKind,
    AccountSearchQuery,
    AccountState,
    AddressKind,
    BillingAddress,
    BillingContact,
    BillingPreferences,
    CommercialStatus,
    ContactRole,
    CreditProfile,
    Currency,
    InvoiceDelivery,
    LegalEntity,
    LegalEntityKind,
    RiskRating,
    StatementFrequency,
)

from .schemas import MoneyBody, MoneyResponse


def _money(view) -> MoneyResponse | None:
    return None if view is None else MoneyResponse(minor=view.minor, currency=view.currency)


# ── value shapes ────────────────────────────────────────────────────────────────


class LegalEntityBody(BaseModel):
    legal_name: str = Field(default="", description="Who the contract is actually with")
    kind: LegalEntityKind = LegalEntityKind.COMPANY
    registration_number: str = ""
    tax_identifier: str = Field(default="", description="GSTIN / VAT number / EIN")
    incorporation_country: str = Field(default="", description="ISO-3166 alpha-2")

    def to_entity(self) -> LegalEntity:
        return LegalEntity(
            legal_name=self.legal_name,
            kind=self.kind,
            registration_number=self.registration_number,
            tax_identifier=self.tax_identifier,
            incorporation_country=self.incorporation_country,
        )


class LegalEntityResponse(BaseModel):
    legal_name: str
    kind: str
    registration_number: str
    tax_identifier: str
    incorporation_country: str

    @classmethod
    def from_view(cls, view) -> LegalEntityResponse:
        return cls(
            legal_name=view.legal_name,
            kind=view.kind,
            registration_number=view.registration_number,
            tax_identifier=view.tax_identifier,
            incorporation_country=view.incorporation_country,
        )


class ContactBody(BaseModel):
    role: ContactRole = ContactRole.BILLING
    name: str = ""
    email: str = ""
    phone: str = ""
    title: str = ""
    is_primary: bool = True

    def to_contact(self) -> AccountContact:
        return AccountContact(
            role=self.role,
            contact=BillingContact(email=self.email, name=self.name, phone=self.phone),
            is_primary=self.is_primary,
            title=self.title,
        )


class ContactResponse(BaseModel):
    role: str
    name: str
    email: str
    phone: str
    title: str
    is_primary: bool

    @classmethod
    def from_view(cls, view) -> ContactResponse:
        return cls(
            role=view.role,
            name=view.name,
            email=view.email,
            phone=view.phone,
            title=view.title,
            is_primary=view.is_primary,
        )


class AddressBody(BaseModel):
    kind: AddressKind = AddressKind.BILLING
    line1: str = ""
    line2: str = ""
    city: str = ""
    region: str = ""
    postal_code: str = ""
    country: str = Field(default="", description="ISO-3166 alpha-2; tax determination keys on it")
    label: str = ""
    is_primary: bool = True

    def to_address(self) -> AccountAddress:
        return AccountAddress(
            kind=self.kind,
            address=BillingAddress(
                line1=self.line1,
                line2=self.line2,
                city=self.city,
                region=self.region,
                postal_code=self.postal_code,
                country=self.country,
            ),
            is_primary=self.is_primary,
            label=self.label,
        )


class AddressResponse(BaseModel):
    kind: str
    line1: str
    line2: str
    city: str
    region: str
    postal_code: str
    country: str
    label: str
    is_primary: bool
    is_complete: bool

    @classmethod
    def from_view(cls, view) -> AddressResponse:
        return cls(
            kind=view.kind,
            line1=view.line1,
            line2=view.line2,
            city=view.city,
            region=view.region,
            postal_code=view.postal_code,
            country=view.country,
            label=view.label,
            is_primary=view.is_primary,
            is_complete=view.is_complete,
        )


class PreferencesBody(BaseModel):
    invoice_delivery: InvoiceDelivery = InvoiceDelivery.EMAIL
    invoice_language: str = "en"
    invoice_prefix: str = ""
    purchase_order_required: bool = False
    purchase_order_number: str = ""
    consolidated_invoicing: bool = False
    billing_day: int = Field(
        default=1, ge=1, le=28, description="28 is the last day every month has"
    )
    payment_terms_days: int = Field(default=30, ge=0)
    statement_frequency: StatementFrequency = StatementFrequency.NONE
    timezone: str = "UTC"

    def to_preferences(self) -> BillingPreferences:
        return BillingPreferences(
            invoice_delivery=self.invoice_delivery,
            invoice_language=self.invoice_language,
            invoice_prefix=self.invoice_prefix,
            purchase_order_required=self.purchase_order_required,
            purchase_order_number=self.purchase_order_number,
            consolidated_invoicing=self.consolidated_invoicing,
            billing_day=self.billing_day,
            payment_terms_days=self.payment_terms_days,
            statement_frequency=self.statement_frequency,
            timezone=self.timezone,
        )


class PreferencesResponse(BaseModel):
    invoice_delivery: str
    invoice_language: str
    invoice_prefix: str
    purchase_order_required: bool
    purchase_order_number: str
    consolidated_invoicing: bool
    billing_day: int
    payment_terms_days: int
    statement_frequency: str
    timezone: str

    @classmethod
    def from_view(cls, view) -> PreferencesResponse:
        return cls(**{f: getattr(view, f) for f in cls.model_fields})


class CreditBody(BaseModel):
    credit_limit: MoneyBody | None = Field(
        default=None, description="A policy figure, not a balance. Null = no limit set"
    )
    credit_hold: bool = False
    risk_rating: RiskRating = RiskRating.UNRATED
    payment_terms_days: int = Field(default=30, ge=0)
    dunning_exempt: bool = False
    notes: str = ""

    def to_credit(self) -> CreditProfile:
        return CreditProfile(
            credit_limit=self.credit_limit.to_money() if self.credit_limit else None,
            credit_hold=self.credit_hold,
            risk_rating=self.risk_rating,
            payment_terms_days=self.payment_terms_days,
            dunning_exempt=self.dunning_exempt,
            notes=self.notes,
        )


class CreditResponse(BaseModel):
    credit_limit: MoneyResponse | None
    credit_hold: bool
    risk_rating: str
    payment_terms_days: int
    dunning_exempt: bool
    assessed_at: float
    assessed_by: str
    has_limit: bool
    notes: str

    @classmethod
    def from_view(cls, view) -> CreditResponse:
        return cls(
            credit_limit=_money(view.credit_limit),
            credit_hold=view.credit_hold,
            risk_rating=view.risk_rating,
            payment_terms_days=view.payment_terms_days,
            dunning_exempt=view.dunning_exempt,
            assessed_at=view.assessed_at,
            assessed_by=view.assessed_by,
            has_limit=view.has_limit,
            notes=view.notes,
        )


class NoteResponse(BaseModel):
    id: str
    body: str
    author_id: str
    at: float
    pinned: bool

    @classmethod
    def from_view(cls, view) -> NoteResponse:
        return cls(
            id=view.id,
            body=view.body,
            author_id=view.author_id,
            at=view.at,
            pinned=view.pinned,
        )


# ── requests ────────────────────────────────────────────────────────────────────


class CreateAccountBody(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "display_name": "Acme Group",
                "currency": "INR",
                "legal_entity": {"legal_name": "Acme Technologies Private Limited"},
                "contacts": [{"role": "billing", "email": "ap@acme.example"}],
                "addresses": [
                    {"kind": "billing", "line1": "1 MG Road", "city": "Bengaluru", "country": "IN"}
                ],
            }
        }
    )
    display_name: str
    currency: Currency = Field(description="Fixed at creation; an account never converts")
    account_number: str = Field(default="", description="Empty = allocated from the sequence")
    kind: AccountKind = AccountKind.DIRECT
    legal_entity: LegalEntityBody = Field(default_factory=LegalEntityBody)
    parent_account_id: str = ""
    payer_account_id: str = ""
    contacts: list[ContactBody] = Field(default_factory=list)
    addresses: list[AddressBody] = Field(default_factory=list)
    preferences: PreferencesBody = Field(default_factory=PreferencesBody)
    credit: CreditBody = Field(default_factory=CreditBody)
    tax_profile_id: str = Field(default="", description="A reference; Billing computes no tax here")


class SuspendAccountBody(BaseModel):
    reason: str = ""


class CloseAccountReasonBody(BaseModel):
    reason: str = ""


class UpdateContactsBody(BaseModel):
    contacts: list[ContactBody] = Field(default_factory=list)


class UpdateAddressesBody(BaseModel):
    addresses: list[AddressBody] = Field(default_factory=list)


class UpdateCreditBody(BaseModel):
    credit: CreditBody = Field(default_factory=CreditBody)
    commercial_status: CommercialStatus | None = None


class AssignParentBody(BaseModel):
    parent_account_id: str = Field(
        default="",
        description="Empty detaches the account to the root. A **billing** relationship only — "
        "organization, workspace, and project hierarchy are untouched",
    )


class AssignPayerBody(BaseModel):
    payer_account_id: str = Field(default="", description="Empty means the account pays for itself")


class AddNoteBody(BaseModel):
    body: str
    pinned: bool = False


# ── responses ───────────────────────────────────────────────────────────────────


class BillingAccountResponse(BaseModel):
    id: str
    account_number: str
    display_name: str
    currency: str
    kind: str
    state: str
    commercial_status: str
    legal_entity: LegalEntityResponse
    parent_account_id: str
    payer_account_id: str
    contacts: list[ContactResponse]
    addresses: list[AddressResponse]
    preferences: PreferencesResponse
    credit: CreditResponse
    tax_profile_id: str
    notes: list[NoteResponse]
    is_active: bool
    is_child: bool
    is_billable: bool
    is_on_hold: bool
    is_retained_only: bool
    allowed_triggers: list[str] = Field(
        description="Lifecycle moves legal from the current state — a UI can render only "
        "buttons that cannot produce a 409"
    )
    created_at: float
    updated_at: float
    activated_at: float | None
    closed_at: float | None
    close_reason: str

    @classmethod
    def from_view(cls, view) -> BillingAccountResponse:
        return cls(
            id=view.id,
            account_number=view.account_number,
            display_name=view.display_name,
            currency=view.currency,
            kind=view.kind,
            state=view.state,
            commercial_status=view.commercial_status,
            legal_entity=LegalEntityResponse.from_view(view.legal_entity),
            parent_account_id=view.parent_account_id,
            payer_account_id=view.payer_account_id,
            contacts=[ContactResponse.from_view(c) for c in view.contacts],
            addresses=[AddressResponse.from_view(a) for a in view.addresses],
            preferences=PreferencesResponse.from_view(view.preferences),
            credit=CreditResponse.from_view(view.credit),
            tax_profile_id=view.tax_profile_id,
            notes=[NoteResponse.from_view(n) for n in view.notes],
            is_active=view.is_active,
            is_child=view.is_child,
            is_billable=view.is_billable,
            is_on_hold=view.is_on_hold,
            is_retained_only=view.is_retained_only,
            allowed_triggers=list(view.allowed_triggers),
            created_at=view.created_at,
            updated_at=view.updated_at,
            activated_at=view.activated_at,
            closed_at=view.closed_at,
            close_reason=view.close_reason,
        )


class AccountSearchResponse(BaseModel):
    accounts: list[BillingAccountResponse]
    total: int = Field(description="Unpaged total, so a caller can render '20 of 137'")

    @classmethod
    def from_view(cls, view) -> AccountSearchResponse:
        return cls(
            accounts=[BillingAccountResponse.from_view(a) for a in view.accounts],
            total=view.total,
        )


class HierarchyNodeResponse(BaseModel):
    account_id: str
    account_number: str
    display_name: str
    state: str
    kind: str
    depth: int
    payer_account_id: str
    children: list[HierarchyNodeResponse]

    @classmethod
    def from_view(cls, view) -> HierarchyNodeResponse:
        return cls(
            account_id=view.account_id,
            account_number=view.account_number,
            display_name=view.display_name,
            state=view.state,
            kind=view.kind,
            depth=view.depth,
            payer_account_id=view.payer_account_id,
            children=[cls.from_view(c) for c in view.children],
        )


class HierarchyResponse(BaseModel):
    root: HierarchyNodeResponse
    invoiced_account_id: str = Field(
        description="Attribution flows down; invoicing rolls up to this account"
    )
    account_ids: list[str]
    depth: int
    size: int

    @classmethod
    def from_view(cls, view) -> HierarchyResponse:
        return cls(
            root=HierarchyNodeResponse.from_view(view.root),
            invoiced_account_id=view.invoiced_account_id,
            account_ids=list(view.account_ids),
            depth=view.depth,
            size=view.size,
        )


class CommercialProfileResponse(BaseModel):
    account: BillingAccountResponse
    parent_account_id: str
    parent_display_name: str
    payer_account_id: str
    payer_display_name: str
    child_count: int
    descendant_count: int
    hierarchy_depth: int
    payer_chain: list[str]
    invoiced_account_id: str
    is_self_paying: bool
    is_root: bool

    @classmethod
    def from_view(cls, view) -> CommercialProfileResponse:
        return cls(
            account=BillingAccountResponse.from_view(view.account),
            parent_account_id=view.parent_account_id,
            parent_display_name=view.parent_display_name,
            payer_account_id=view.payer_account_id,
            payer_display_name=view.payer_display_name,
            child_count=view.child_count,
            descendant_count=view.descendant_count,
            hierarchy_depth=view.hierarchy_depth,
            payer_chain=list(view.payer_chain),
            invoiced_account_id=view.invoiced_account_id,
            is_self_paying=view.is_self_paying,
            is_root=view.is_root,
        )


class AccountActivityResponse(BaseModel):
    id: str
    account_id: str
    action: str
    actor_id: str
    at: float
    from_state: str
    to_state: str
    detail: str

    @classmethod
    def from_view(cls, view) -> AccountActivityResponse:
        return cls(
            id=view.id,
            account_id=view.account_id,
            action=view.action,
            actor_id=view.actor_id,
            at=view.at,
            from_state=view.from_state,
            to_state=view.to_state,
            detail=view.detail,
        )


def build_account_query(
    *,
    text: str = "",
    states: list[AccountState] | None = None,
    kinds: list[AccountKind] | None = None,
    statuses: list[CommercialStatus] | None = None,
    currencies: list[Currency] | None = None,
    countries: list[str] | None = None,
    risk_ratings: list[RiskRating] | None = None,
    parent_account_id: str = "",
    payer_account_id: str = "",
    roots_only: bool = False,
    on_hold_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> AccountSearchQuery:
    """Query params → the Foundation's deterministic filter. Translation only; the filtering
    itself is Foundation behaviour."""
    return AccountSearchQuery(
        text=text,
        states=tuple(states or ()),
        kinds=tuple(kinds or ()),
        statuses=tuple(statuses or ()),
        currencies=tuple(currencies or ()),
        countries=tuple(countries or ()),
        risk_ratings=tuple(risk_ratings or ()),
        parent_account_id=parent_account_id,
        payer_account_id=payer_account_id,
        roots_only=roots_only,
        on_hold_only=on_hold_only,
        limit=limit,
        offset=offset,
    )
