"""Tax Determination Engine domain (M9.15) — tax treatment, and nothing else.

Nine milestones produced facts. M9.6 said **what is sold**, M9.7 **what it costs**, M9.8 **who we
bill**, M9.9 **what was agreed**, M9.10 **what may be used**, M9.11 **what was consumed**, M9.12
**what should be charged**, M9.13 **what is owed**, M9.14 **what the customer receives**. None
of them said what a tax authority is owed, or why.

**The Tax Engine owns** tax profiles, registrations, jurisdictions, nexus, residency,
classification, place-of-supply determination, treatment, exemptions, overrides, rate provenance,
the calculation and its rounding, the determination and its evidence, versioning, the audit trail,
search, and diagnostics.

**It owns nothing else.** It does not price, rate, meter, post a ledger entry, compute a balance,
create an invoice, execute a payment, evaluate an entitlement, or authorize a request. It never goes
backward: usage, quantity, price, discount, charge, and balance are all inputs it reads and none of
them is a thing it recomputes. Nine AST-driven tests assert each separately.

## The sentence the milestone rests on

> **Determination is a rented edge. Evidence is an owned fact.** (ADR-0023 TX1)

So this module declares **no rate**. Not one. There is no `STANDARD_RATE`, no GST table, no VAT map,
and no jurisdiction whose percentage is written in Python — a test greps for exactly that, because a
rate hard-coded to make a test pass is a rate that will still be there when the jurisdiction changes
it. Rates arrive through `TaxRateProvider`, an adapter port; the *evidence* of which rate applied,
from where, under which version, effective when, and against which registration is recorded here and
kept forever.

## Four properties that are structural rather than conventional

* **A determination is self-explaining.** `TaxAssessment` carries the jurisdiction, the tax type,
  the rate, the taxable base, the amount, the currency, the calculation basis, the rounding policy
  and scope, the source and its version, the effective period, the registration used, the exemption
  or override reason, the assessment version, and a digest. `TaxExplainer` answers "why was this tax
  amount applied?" **without calling the provider again** — which is what makes an assessment
  auditable years later, when the provider's answer would be different.
* **An assessment is deterministic and content-addressed.** Its id is a hash of its *basis* — the
  immutable inputs — so re-assessing identical inputs is one assessment, and a changed input
  produces a different id and is structurally incapable of overwriting what it changes.
* **A finalized assessment is never edited.** The lifecycle has **no edge out of `final`**. A
  correction is a *new* assessment that supersedes the previous one, and both remain readable —
  because the question an audit asks is not "what is the tax?" but "what tax did you charge, and
  what did you change it to?"
* **Rates are effective-dated evidence, never current state.** A `DeclaredTaxRate` has an effective
  window and is append-only; an assessment pins the exact rate rows it saw. Reading "today's rate"
  at reporting time is forbidden (TX4) and there is no method that would let you.

M9.2's `TaxRegime`, `TaxpayerKind`, `TaxTreatment`, `GstSplit`, `TaxRegistration`, `TaxProfile`, and
`TaxComponent` are **reused unchanged**, not restated. `TaxAssessment` sits beside M9.2's
`TaxDetermination` — the same call M9.13 made for `JournalEntry` beside `LedgerEntry` and M9.14 for
`CommercialInvoice` beside `Invoice` — and `to_determination()` is the **single** derivation of that
thinner record, so the surface M9.5 and M9.14 already read stays a projection of one truth rather
than a second copy of it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum

from core.fsm import StateMachineDefinition, define

from .accounts import BillingAddress
from .money import Currency, Money, Rounding
from .tax import (
    GstSplit,
    TaxComponent,
    TaxDetermination,
    TaxpayerKind,
    TaxRegime,
    TaxRegistration,
    TaxTreatment,
)

# ── identity ────────────────────────────────────────────────────────────────────


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def derive_assessment_id(org_id: str, subject_id: str, basis_digest: str) -> str:
    """The deterministic identity of one tax determination.

    Derived from the **basis** — the immutable inputs — rather than minted, so assessing the same
    facts twice is one assessment. The corollary is what matters for an audit: a *changed* input
    produces a different id, so a re-assessment is structurally incapable of overwriting the
    assessment it revises. Both remain readable, which is what lets a reader answer "what did you
    charge, and what did you change it to?"."""
    return "txa_" + _digest(org_id, subject_id, basis_digest)[:32]


def derive_rate_id(
    org_id: str,
    jurisdiction_code: str,
    tax_type: str,
    name: str,
    classification_code: str,
    effective_from: float,
) -> str:
    """A declared rate's identity.

    Includes the effective start, so superseding a rate appends a new row beside the one it replaces
    rather than editing it — an issued invoice must remain explainable against the rate that was in
    force when it was issued.

    Includes the **name**, and that is not decoration: an Indian intra-state supply carries CGST and
    SGST, two separately-named components of one `tax_type`, in one jurisdiction, over one window.
    Without the name they derive the same id, the second declaration is swallowed as a duplicate,
    and every intra-state invoice is taxed at half the correct rate. Found by the end-to-end smoke,
    which declared both and got one back."""
    return (
        "txr_"
        + _digest(
            org_id,
            jurisdiction_code,
            tax_type,
            name,
            classification_code,
            str(int(effective_from)),
        )[:32]
    )


def jurisdiction_key(country: str, region: str = "") -> str:
    """The canonical name of a jurisdiction. Upper-cased and colon-joined so `IN:KA` and `in:ka`
    are one jurisdiction rather than two — a duplicate jurisdiction is a duplicate nexus, and a
    duplicate nexus is a supplier registered twice in the same place."""
    return ":".join(p for p in (country.upper(), region.upper()) if p)


# ── jurisdictions, nexus, residency ─────────────────────────────────────────────


class JurisdictionLevel(StrEnum):
    """How deep a jurisdiction sits. Levels matter because rates compose: an American sale can
    carry state, county, and city tax simultaneously, and a model with only "country" cannot
    represent that without inventing one jurisdiction per combination."""

    COUNTRY = "country"
    STATE = "state"  # Indian state, US state, Canadian province
    COUNTY = "county"
    CITY = "city"
    SPECIAL = "special"  # economic zones, union territories, tax-free ports


@dataclass(frozen=True, slots=True)
class TaxJurisdiction:
    """A place with taxing authority.

    Declared configuration, not a rate. This says *that* Karnataka exists, is an Indian state, and
    operates under GST — never what its percentage is. Adding a jurisdiction is configuration, never
    a structural change (ADR-0023 TX5), which is why this is a row rather than a class."""

    id: str
    org_id: str
    code: str  # canonical, e.g. "IN:KA"
    display_name: str
    country: str  # ISO-3166 alpha-2
    region: str = ""
    level: JurisdictionLevel = JurisdictionLevel.COUNTRY
    regime: TaxRegime = TaxRegime.NONE
    parent_code: str = ""
    is_active: bool = True
    declared_at: float = 0.0
    declared_by: str = ""
    notes: str = ""

    @property
    def is_subnational(self) -> bool:
        return self.level is not JurisdictionLevel.COUNTRY


class NexusKind(StrEnum):
    """Why a supplier owes tax in a jurisdiction. Recorded rather than inferred, because the reason
    determines what evidence a challenge requires."""

    PHYSICAL = "physical"  # office, staff, inventory
    ECONOMIC = "economic"  # threshold crossed (US Wayfair, EU OSS)
    REGISTRATION = "registration"  # registered, whatever the reason
    VOLUNTARY = "voluntary"
    NONE = "none"  # explicitly assessed and found absent — a fact, not an absence of one


@dataclass(frozen=True, slots=True)
class TaxNexus:
    """The supplier's obligation in one jurisdiction, and the registration that discharges it.

    `NexusKind.NONE` is a real member and the point of the type: "we looked and there is no nexus"
    is evidence, and "we never looked" is not. An assessment that found no nexus records which."""

    id: str
    org_id: str
    entity_code: str  # the supplier legal entity, as M9.14 numbers documents for
    jurisdiction_code: str
    kind: NexusKind = NexusKind.REGISTRATION
    registration: TaxRegistration | None = None
    effective_from: float = 0.0
    effective_until: float | None = None
    established_reason: str = ""
    declared_at: float = 0.0
    declared_by: str = ""

    def is_live_at(self, ts: float) -> bool:
        return self.effective_from <= ts and (
            self.effective_until is None or ts < self.effective_until
        )

    @property
    def has_obligation(self) -> bool:
        return self.kind is not NexusKind.NONE


@dataclass(frozen=True, slots=True)
class TaxResidency:
    """Where the customer is, and what proves it.

    Two independent pieces of non-contradictory evidence are what EU VAT (and increasingly other
    regimes) require for a B2C digital supply. The model carries them rather than a single country
    string, because "we believe they are in Germany" and "we hold a billing address and an IP
    geolocation that both say Germany" are different positions in a challenge."""

    country: str = ""
    region: str = ""
    address: BillingAddress = field(default_factory=BillingAddress)
    evidence: tuple[str, ...] = ()  # e.g. ("billing_address", "payment_country", "ip_geolocation")

    @property
    def jurisdiction_code(self) -> str:
        return jurisdiction_key(self.country, self.region)

    @property
    def is_corroborated(self) -> bool:
        """Whether more than one independent item of evidence agrees."""
        return len(self.evidence) >= 2


# ── classification ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TaxClassification:
    """What is being supplied, in the vocabulary a tax authority uses.

    A rate is looked up *for a classification*, never for a product — because the same product is
    taxed differently in two jurisdictions, and the thing that varies is the classification, not the
    catalog. `codes` carries regime-specific identifiers (HSN/SAC for India, UNSPSC, CN codes)
    without this model needing to know any of them: adding a regime is a key in a dict."""

    id: str
    org_id: str
    code: str
    display_name: str
    default_treatment: TaxTreatment = TaxTreatment.STANDARD
    codes: tuple[tuple[str, str], ...] = ()  # (scheme, value) — "hsn_sac" → "998314"
    is_digital_service: bool = False  # drives place-of-supply in most regimes
    declared_at: float = 0.0
    declared_by: str = ""
    notes: str = ""

    def code_for(self, scheme: str) -> str:
        return next((value for key, value in self.codes if key == scheme), "")


# ── exemptions and overrides ────────────────────────────────────────────────────


class ExemptionKind(StrEnum):
    CERTIFICATE = "certificate"  # a resale or exemption certificate the customer holds
    REVERSE_CHARGE = "reverse_charge"  # the customer accounts for the tax
    ZERO_RATED_EXPORT = "zero_rated_export"
    TREATY = "treaty"
    STATUTORY = "statutory"


@dataclass(frozen=True, slots=True)
class TaxExemption:
    """A customer's relief from tax in one jurisdiction, and what evidences it.

    Effective-dated and revocable, never deleted: an invoice issued while an exemption was valid
    stays explainable after the certificate expires, and "was this customer exempt on 4 March?" is a
    question an audit asks."""

    id: str
    org_id: str
    account_id: str
    jurisdiction_code: str
    kind: ExemptionKind = ExemptionKind.CERTIFICATE
    certificate_ref: str = ""
    reason: str = ""
    effective_from: float = 0.0
    effective_until: float | None = None
    revoked_at: float | None = None
    revoked_reason: str = ""
    evidence_ref: str = ""
    recorded_at: float = 0.0
    recorded_by: str = ""

    def is_live_at(self, ts: float) -> bool:
        return (
            self.revoked_at is None
            and self.effective_from <= ts
            and (self.effective_until is None or ts < self.effective_until)
        )


@dataclass(frozen=True, slots=True)
class TaxOverride:
    """A human decision to depart from what determination concluded.

    Always attributed, always reasoned, and always **visible on the assessment** — an override that
    silently changed a figure would be the one thing an audit cannot forgive. The original
    conclusion is kept beside it, so the record shows both what was determined and what was
    decided."""

    treatment: TaxTreatment
    reason: str
    actor_id: str
    at: float = 0.0
    supersedes_treatment: TaxTreatment | None = None
    reference: str = ""

    @property
    def is_declared(self) -> bool:
        return bool(self.reason and self.actor_id)


# ── place of supply ─────────────────────────────────────────────────────────────


class SupplyDirection(StrEnum):
    """The geometry of a supply, which is what every regime's place-of-supply rule turns on."""

    DOMESTIC_INTRA_REGION = "domestic_intra_region"
    DOMESTIC_INTER_REGION = "domestic_inter_region"
    CROSS_BORDER_EXPORT = "cross_border_export"
    CROSS_BORDER_IMPORT = "cross_border_import"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PlaceOfSupply:
    """Where a supply is deemed to happen, and by which rule.

    `rule` is a free-text name of the rule applied — deliberately, because the set of rules is
    jurisdictional and open, and an enum here would make adding a regime a structural change
    (TX5). What is *not* free text is the direction and the resolved jurisdiction, because those
    are what the rest of determination reads."""

    origin_code: str
    destination_code: str
    resolved_code: str
    direction: SupplyDirection = SupplyDirection.UNKNOWN
    gst_split: GstSplit = GstSplit.NOT_APPLICABLE
    rule: str = ""
    is_digital_service: bool = False
    customer_registered: bool = False

    @property
    def is_cross_border(self) -> bool:
        return self.direction in (
            SupplyDirection.CROSS_BORDER_EXPORT,
            SupplyDirection.CROSS_BORDER_IMPORT,
        )

    @property
    def digest(self) -> str:
        return _digest(
            self.origin_code,
            self.destination_code,
            self.resolved_code,
            self.direction.value,
            self.gst_split.value,
            self.rule,
        )


# ── rate provenance ─────────────────────────────────────────────────────────────


class RateSource(StrEnum):
    """Where a rate came from. A closed vocabulary, because "who told us this percentage?" is the
    first question of a tax audit and free text would let the interesting answers hide."""

    ADAPTER = "adapter"  # an external tax engine
    DECLARED = "declared"  # an operator-declared, effective-dated rate held as Billing evidence
    CONTRACTUAL = "contractual"  # negotiated in an enterprise agreement
    STATUTORY_REFERENCE = "statutory_reference"  # cited directly to legislation
    NONE = "none"  # no source could supply one — never a silent zero


@dataclass(frozen=True, slots=True)
class RateProvenance:
    """Where one rate came from, when it applied, and how to find it again.

    This is the field that makes ADR-0023 TX1 real. Without it a determination records a number;
    with it, a determination records a number **and the authority for it**, which is the difference
    between evidence and an assertion."""

    source: RateSource = RateSource.NONE
    source_code: str = ""  # adapter code, or the declared-rate id
    source_version: str = ""
    reference: str = ""  # notification number, statute section, adapter response id
    published_at: float | None = None
    effective_from: float = 0.0
    effective_until: float | None = None
    retrieved_at: float = 0.0

    @property
    def is_sourced(self) -> bool:
        return self.source is not RateSource.NONE

    def covers(self, ts: float) -> bool:
        return self.effective_from <= ts and (
            self.effective_until is None or ts < self.effective_until
        )

    @property
    def digest(self) -> str:
        return _digest(
            self.source.value,
            self.source_code,
            self.source_version,
            self.reference,
            str(int(self.effective_from)),
            str(int(self.effective_until or 0)),
        )


@dataclass(frozen=True, slots=True)
class TaxRateQuote:
    """One rate a source offered, with its provenance attached. **Not stored as configuration** —
    this is what a provider returned for one determination, and it travels into the evidence."""

    name: str  # "CGST", "SGST", "IGST", "VAT", "State Sales Tax"
    tax_type: str  # "gst", "vat", "sales_tax", "withholding"
    rate_basis_points: int
    jurisdiction_code: str
    provenance: RateProvenance = field(default_factory=RateProvenance)
    treatment: TaxTreatment = TaxTreatment.STANDARD

    def __post_init__(self) -> None:
        if self.rate_basis_points < 0:
            raise ValueError("tax rate basis points must be >= 0")


@dataclass(frozen=True, slots=True)
class DeclaredTaxRate:
    """An operator-declared, effective-dated rate held as Billing evidence.

    Deliberately **not** a hard-coded table: nothing in this package knows that Indian GST on
    software is 18%. An operator declares it, with a reference to the notification that set it and
    the window it applies to, and that declaration is itself evidence a challenge can examine.
    Append-only — superseding a rate writes a new row beside it, because an issued invoice must stay
    explainable against the rate that was in force when it was issued."""

    id: str
    org_id: str
    jurisdiction_code: str
    tax_type: str
    name: str
    rate_basis_points: int
    classification_code: str = ""  # "" ⇒ applies to every classification in the jurisdiction
    treatment: TaxTreatment = TaxTreatment.STANDARD
    effective_from: float = 0.0
    effective_until: float | None = None
    reference: str = ""
    source_version: str = ""
    declared_at: float = 0.0
    declared_by: str = ""
    retired_at: float | None = None

    def is_live_at(self, ts: float) -> bool:
        return (
            self.retired_at is None
            and self.effective_from <= ts
            and (self.effective_until is None or ts < self.effective_until)
        )

    def applies_to(self, classification_code: str) -> bool:
        """A rate with no classification applies to everything in its jurisdiction; a rate with one
        applies only to that. Specificity wins, resolved by the rate resolver."""
        return not self.classification_code or self.classification_code == classification_code

    def to_quote(self, *, at: float) -> TaxRateQuote:
        return TaxRateQuote(
            name=self.name,
            tax_type=self.tax_type,
            rate_basis_points=self.rate_basis_points,
            jurisdiction_code=self.jurisdiction_code,
            treatment=self.treatment,
            provenance=RateProvenance(
                source=RateSource.DECLARED,
                source_code=self.id,
                source_version=self.source_version,
                reference=self.reference,
                effective_from=self.effective_from,
                effective_until=self.effective_until,
                retrieved_at=at,
            ),
        )


# ── calculation + rounding ──────────────────────────────────────────────────────


class TaxBasis(StrEnum):
    """How the taxable base relates to the amount that was charged.

    `INCLUSIVE` is not a display preference: where a market quotes tax-inclusive prices, the base is
    *extracted* from the charge rather than added to it, and getting that backwards overcharges
    every customer in the market by the tax rate."""

    EXCLUSIVE = "exclusive"  # tax is added to the amount
    INCLUSIVE = "inclusive"  # tax is contained within the amount
    NOT_APPLICABLE = "not_applicable"


class RoundingScope(StrEnum):
    """Where rounding is applied. Jurisdictional, and it changes the total.

    A hundred lines rounded individually and then summed does not equal their sum rounded once, and
    different authorities require different ones — so this is recorded on the assessment rather than
    left to whichever loop ran."""

    PER_COMPONENT = "per_component"
    PER_LINE = "per_line"
    PER_DOCUMENT = "per_document"


@dataclass(frozen=True, slots=True)
class TaxRoundingPolicy:
    """The declared rounding rule for a determination. Carried into the evidence, because a total
    that cannot be reproduced is a total that cannot be defended."""

    mode: Rounding = Rounding.HALF_UP
    scope: RoundingScope = RoundingScope.PER_LINE

    @property
    def digest(self) -> str:
        return _digest(self.mode.value, self.scope.value)


@dataclass(frozen=True, slots=True)
class AssessedComponent:
    """One computed tax component **with its authority attached**.

    M9.2's `TaxComponent` (name, rate, amount) is reused unchanged as the figure; everything around
    it here is the evidence that makes the figure defensible: which jurisdiction, which tax type,
    what base it was applied to, and where the rate came from."""

    component: TaxComponent
    tax_type: str
    jurisdiction_code: str
    taxable_base: Money
    provenance: RateProvenance = field(default_factory=RateProvenance)
    basis: TaxBasis = TaxBasis.EXCLUSIVE

    @property
    def name(self) -> str:
        return self.component.name

    @property
    def amount(self) -> Money:
        return self.component.amount

    @property
    def rate_basis_points(self) -> int:
        return self.component.rate_basis_points

    @property
    def digest(self) -> str:
        return _digest(
            self.component.name,
            str(self.component.rate_basis_points),
            str(self.component.amount.minor),
            self.component.amount.currency.value,
            self.tax_type,
            self.jurisdiction_code,
            str(self.taxable_base.minor),
            self.basis.value,
            self.provenance.digest,
        )


@dataclass(frozen=True, slots=True)
class AssessedLine:
    """One subject line's tax. `subject_line_id` points back at the invoice line or ledger entry
    this taxed — so a reader walks document → line → assessment → component → rate → notification
    without a single recomputation."""

    subject_line_id: str
    classification_code: str
    taxable_base: Money
    components: tuple[AssessedComponent, ...] = ()
    treatment: TaxTreatment = TaxTreatment.STANDARD
    exemption_reason: str = ""

    @property
    def tax_total(self) -> Money:
        total = Money.zero(self.taxable_base.currency)
        for component in self.components:
            total = total + component.amount
        return total

    @property
    def digest(self) -> str:
        return _digest(
            self.subject_line_id,
            self.classification_code,
            str(self.taxable_base.minor),
            self.treatment.value,
            *(c.digest for c in self.components),
        )


# ── the assessment ──────────────────────────────────────────────────────────────


class AssessmentState(StrEnum):
    """The lifecycle of one determination."""

    PROVISIONAL = "provisional"
    FINAL = "final"
    SUPERSEDED = "superseded"
    DISCARDED = "discarded"


class AssessmentTrigger(StrEnum):
    FINALIZE = "finalize"
    SUPERSEDE = "supersede"
    DISCARD = "discard"


# The assessment lifecycle. Three absences, each a decision:
#
#   * **no edge out of FINAL.** A finalized assessment is what an invoice was issued with and what a
#     return was filed from. Editing it would silently change a filed number; a correction is a
#     *new* assessment, and the superseded one stays readable — because the question an audit asks
#     is not "what is the tax?" but "what did you charge, and what did you change it to?";
#   * no `SUPERSEDED → PROVISIONAL` and no `DISCARDED → PROVISIONAL`. Reviving a determination would
#     make "which assessment produced this invoice?" a question with two answers;
#   * **no deletion state at all** (ADR-0023 TX2). Evidence is not deleted.
#
# `SUPERSEDE` and `DISCARD` leave `PROVISIONAL` only: a determination that has not been applied to a
# document can be abandoned freely, and one that has cannot be abandoned at all.
ASSESSMENT_LIFECYCLE: StateMachineDefinition = define(
    [
        (AssessmentState.PROVISIONAL, AssessmentTrigger.FINALIZE, AssessmentState.FINAL),
        (AssessmentState.PROVISIONAL, AssessmentTrigger.SUPERSEDE, AssessmentState.SUPERSEDED),
        (AssessmentState.PROVISIONAL, AssessmentTrigger.DISCARD, AssessmentState.DISCARDED),
    ]
)

#: The only state in which a determination may still be revised.
REVISABLE_ASSESSMENT_STATES: frozenset[AssessmentState] = frozenset({AssessmentState.PROVISIONAL})

#: The states in which a determination has been applied to something a customer holds.
SETTLED_ASSESSMENT_STATES: frozenset[AssessmentState] = frozenset({AssessmentState.FINAL})


class SubjectKind(StrEnum):
    """What is being taxed. An assessment names its subject rather than assuming an invoice, because
    a credit note and a debit note are taxed too, and a preview is taxed against nothing at all."""

    INVOICE = "invoice"
    CREDIT_NOTE = "credit_note"
    DEBIT_NOTE = "debit_note"
    PREVIEW = "preview"


@dataclass(frozen=True, slots=True)
class TaxBasisInputs:
    """The **immutable inputs** a determination was made from.

    Explicit and complete on purpose: this is what makes an assessment reproducible. Anything a
    determination depended on that is not in here is a hidden input, and a hidden input is how two
    replays of the same facts come to disagree."""

    org_id: str
    account_id: str
    entity_code: str  # supplier legal entity
    subject_id: str
    subject_kind: SubjectKind = SubjectKind.INVOICE
    currency: Currency = Currency.INR
    origin: TaxResidency = field(default_factory=TaxResidency)
    destination: TaxResidency = field(default_factory=TaxResidency)
    regime: TaxRegime = TaxRegime.NONE
    taxpayer_kind: TaxpayerKind = TaxpayerKind.BUSINESS
    customer_registration: str = ""
    supplier_registration: str = ""
    prices_include_tax: bool = False
    effective_at: float = 0.0
    lines: tuple[tuple[str, str, int], ...] = ()  # (line id, classification code, base minor)

    @property
    def taxable_total(self) -> Money:
        return Money(sum(base for _, _, base in self.lines), self.currency)

    @property
    def digest(self) -> str:
        """A deterministic fingerprint of everything the determination read.

        Two assessments with equal basis digests were made from identical inputs, which is checkable
        by comparing two strings rather than by re-deriving them."""
        return _digest(
            self.org_id,
            self.account_id,
            self.entity_code,
            self.subject_id,
            self.subject_kind.value,
            self.currency.value,
            self.origin.jurisdiction_code,
            self.destination.jurisdiction_code,
            self.regime.value,
            self.taxpayer_kind.value,
            self.customer_registration,
            self.supplier_registration,
            str(self.prices_include_tax),
            str(int(self.effective_at)),
            *(f"{line}:{classification}:{base}" for line, classification, base in self.lines),
        )


@dataclass(frozen=True, slots=True)
class TaxAssessment:
    """**The** fact the Tax Engine owns: one immutable, self-explaining determination.

    Named `TaxAssessment` beside M9.2's `TaxDetermination`, which stays exactly as it is and which
    the minimal M9.5 `/taxes` surface and M9.14's document assembly still read. The difference is
    real rather than cosmetic: an M9.2 `TaxDetermination` records *what was concluded*, and this one
    records **why** — the basis it was made from, the place-of-supply rule applied, the nexus and
    registration relied on, the exemption or override that changed it, the provenance of every rate,
    the rounding policy, and a digest over the lot.

    `to_determination()` is the **single** derivation of that thinner record. Keeping it here rather
    than letting a caller assemble one is what stops the projection becoming a second source of
    truth (§A2.5)."""

    id: str
    org_id: str
    account_id: str
    subject_id: str
    subject_kind: SubjectKind
    currency: Currency
    basis: TaxBasisInputs
    place_of_supply: PlaceOfSupply
    treatment: TaxTreatment = TaxTreatment.STANDARD
    regime: TaxRegime = TaxRegime.NONE
    state: AssessmentState = AssessmentState.PROVISIONAL
    version: int = 1
    lines: tuple[AssessedLine, ...] = ()
    rounding: TaxRoundingPolicy = field(default_factory=TaxRoundingPolicy)
    nexus_id: str = ""
    nexus_kind: NexusKind = NexusKind.NONE
    registration_used: str = ""
    exemption_id: str = ""
    exemption_reason: str = ""
    override: TaxOverride | None = None
    withholding_minor: int = 0  # a remittance obligation, never a discount (ADR-0023 TX6)
    supersedes_assessment_id: str = ""
    notes: tuple[str, ...] = ()
    assessed_at: float = 0.0
    assessed_by: str = ""
    finalized_at: float | None = None

    # ── derived figures ─────────────────────────────────────────────────────────

    @property
    def taxable_base(self) -> Money:
        total = Money.zero(self.currency)
        for line in self.lines:
            total = total + line.taxable_base
        return total

    @property
    def tax_total(self) -> Money:
        total = Money.zero(self.currency)
        for line in self.lines:
            total = total + line.tax_total
        return total

    @property
    def components(self) -> tuple[TaxComponent, ...]:
        """The M9.2 components, merged by name across lines — what a document prints."""
        merged: dict[tuple[str, int], Money] = {}
        for line in self.lines:
            for assessed in line.components:
                key = (assessed.name, assessed.rate_basis_points)
                merged[key] = merged.get(key, Money.zero(self.currency)) + assessed.amount
        return tuple(
            TaxComponent(name=name, rate_basis_points=rate, amount=amount)
            for (name, rate), amount in merged.items()
        )

    @property
    def assessed_components(self) -> tuple[AssessedComponent, ...]:
        return tuple(c for line in self.lines for c in line.components)

    # ── state ───────────────────────────────────────────────────────────────────

    @property
    def is_final(self) -> bool:
        return self.state in SETTLED_ASSESSMENT_STATES

    @property
    def is_revisable(self) -> bool:
        return self.state in REVISABLE_ASSESSMENT_STATES

    @property
    def is_zero(self) -> bool:
        return self.tax_total.is_zero

    @property
    def is_overridden(self) -> bool:
        return self.override is not None

    @property
    def expects_rates(self) -> bool:
        """Whether a rate *should* have been found.

        A zero-tax treatment is a conclusion — an exempt supply, an export, a reverse charge, a
        jurisdiction we are out of scope in — and no rate is sought for one. Any other treatment
        expects a rate, and finding none is a gap rather than a zero."""
        from .tax_rules import ZERO_TAX_TREATMENTS  # local: rules import the domain, not vice versa

        return self.treatment not in ZERO_TAX_TREATMENTS

    @property
    def is_sourced(self) -> bool:
        """Whether every applied rate names where it came from — **and whether one was found at all
        when one was expected.**

        The second half is the part that matters and the part that was missing: `all()` over an
        empty tuple is `True`, so a standard-rated supply for which no provider offered a rate
        reported itself as perfectly sourced while charging nothing. That is exactly the
        indistinguishability `NullTaxRateProvider` exists to prevent — a silent zero reading as a
        correctly zero-rated export. Found by the end-to-end smoke."""
        if self.expects_rates and not self.assessed_components:
            return False
        return all(c.provenance.is_sourced for c in self.assessed_components)

    @property
    def is_explainable(self) -> bool:
        """Whether this assessment answers "why was this tax applied?" from itself alone.

        Zero tax with no components is explainable — an exempt or zero-rated supply is a conclusion,
        not a gap. A standard-rated supply with no rate, or with a rate naming no source, is not."""
        if not self.expects_rates:
            return True
        return self.is_sourced and bool(self.place_of_supply.resolved_code)

    @property
    def digest(self) -> str:
        """A deterministic fingerprint of the determination's **content**.

        Excludes `assessed_at`, `assessed_by`, and `finalized_at`, which legitimately differ between
        a determination and a replay of it, and includes everything a figure depends on."""
        return _digest(
            self.id,
            self.org_id,
            self.account_id,
            self.subject_id,
            self.subject_kind.value,
            self.currency.value,
            self.basis.digest,
            self.place_of_supply.digest,
            self.treatment.value,
            self.regime.value,
            str(self.version),
            self.rounding.digest,
            self.nexus_id,
            self.registration_used,
            self.exemption_id,
            str(self.withholding_minor),
            *(line.digest for line in self.lines),
        )

    # ── the single projection onto M9.2's record ────────────────────────────────

    def to_determination(self, *, determination_id: str = "") -> TaxDetermination:
        """Project onto M9.2's `TaxDetermination` — the record M9.5's `/taxes` surface and M9.14's
        document assembly already read.

        **The only derivation of that record.** Letting a caller assemble one would make the thinner
        projection a second source of truth, and the two would eventually disagree about a number a
        customer was charged (§A2.5). The assessment is the fact; this is a view of it."""
        return TaxDetermination(
            id=determination_id or self.id,
            org_id=self.org_id,
            account_id=self.account_id,
            invoice_id=self.subject_id,
            regime=self.regime,
            treatment=self.treatment,
            taxable_amount=self.taxable_base,
            tax_total=self.tax_total,
            components=self.components,
            gst_split=self.place_of_supply.gst_split,
            place_of_supply=self.place_of_supply.resolved_code,
            customer_registration=self.basis.customer_registration,
            adapter_code=self._adapter_code(),
            adapter_version=self._adapter_version(),
            determined_at=self.assessed_at,
            withholding_minor=self.withholding_minor,
        )

    def _adapter_code(self) -> str:
        sources = {
            c.provenance.source_code for c in self.assessed_components if c.provenance.source_code
        }
        return sorted(sources)[0] if len(sources) == 1 else ",".join(sorted(sources))

    def _adapter_version(self) -> str:
        versions = {
            c.provenance.source_version
            for c in self.assessed_components
            if c.provenance.source_version
        }
        return sorted(versions)[0] if len(versions) == 1 else ",".join(sorted(versions))


# ── explanation, reconciliation, inspection ─────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TaxExplanationStep:
    """One step in the answer to "why was this tax amount applied?"."""

    stage: str  # "place_of_supply", "nexus", "treatment", "rate", "calculation", "rounding"
    conclusion: str
    evidence: str = ""
    reference: str = ""


@dataclass(frozen=True, slots=True)
class TaxExplanation:
    """The whole answer, assembled from the assessment alone.

    `answered_without_provider` is the claim being made and it is always `True` by construction —
    the explainer has no provider to call. It is stated on the DTO so a consumer can see that the
    guarantee is structural rather than take it on trust."""

    assessment_id: str
    subject_id: str
    steps: tuple[TaxExplanationStep, ...] = ()
    answered_without_provider: bool = True


class TaxDiscrepancyKind(StrEnum):
    """How an assessment and what it was applied to can disagree."""

    UNASSESSED_LINE = "unassessed_line"  # the document has a line the assessment never taxed
    ORPHAN_ASSESSED_LINE = "orphan_assessed_line"  # the assessment taxed a line nothing produced
    BASE_MISMATCH = "base_mismatch"
    TAX_TOTAL_MISMATCH = "tax_total_mismatch"
    MISSING_PROVENANCE = "missing_provenance"
    STALE_RATE = "stale_rate"  # a rate applied outside its effective window
    CURRENCY_MISMATCH = "currency_mismatch"
    NEXUS_WITHOUT_REGISTRATION = "nexus_without_registration"


@dataclass(frozen=True, slots=True)
class TaxDiscrepancy:
    kind: TaxDiscrepancyKind
    assessment_id: str = ""
    line_id: str = ""
    expected_minor: int = 0
    actual_minor: int = 0
    detail: str = ""


@dataclass(frozen=True, slots=True)
class TaxReconciliation:
    """Does the assessment agree with what it was applied to?"""

    org_id: str
    subject_id: str
    assessment_id: str = ""
    lines_examined: int = 0
    components_examined: int = 0
    discrepancies: tuple[TaxDiscrepancy, ...] = ()
    at: float = 0.0

    @property
    def is_clean(self) -> bool:
        return not self.discrepancies


@dataclass(frozen=True, slots=True)
class TaxInspection:
    """A structural read of one assessment: sourced, explainable, in-window, self-consistent."""

    assessment_id: str
    subject_id: str
    state: AssessmentState
    treatment: TaxTreatment
    version: int = 1
    is_sourced: bool = True
    is_explainable: bool = True
    rates_in_window: bool = True
    has_nexus: bool = True
    is_overridden: bool = False
    line_count: int = 0
    component_count: int = 0
    notes: tuple[str, ...] = ()

    @property
    def is_sound(self) -> bool:
        """What finalizing requires: a determination whose rates name no source, or whose rates were
        applied outside their effective window, must not become the tax on an issued document."""
        return self.is_explainable and self.rates_in_window


# ── history + search ────────────────────────────────────────────────────────────


class TaxAction(StrEnum):
    """What happened in the tax domain. The audit trail's closed vocabulary."""

    JURISDICTION_DECLARED = "jurisdiction.declared"
    JURISDICTION_RETIRED = "jurisdiction.retired"
    CLASSIFICATION_DECLARED = "classification.declared"
    NEXUS_REGISTERED = "nexus.registered"
    NEXUS_ENDED = "nexus.ended"
    EXEMPTION_RECORDED = "exemption.recorded"
    EXEMPTION_REVOKED = "exemption.revoked"
    RATE_DECLARED = "rate.declared"
    RATE_RETIRED = "rate.retired"
    ASSESSED = "assessment.assessed"
    ASSESSMENT_UNCHANGED = "assessment.unchanged"
    ASSESSMENT_FINALIZED = "assessment.finalized"
    ASSESSMENT_SUPERSEDED = "assessment.superseded"
    ASSESSMENT_DISCARDED = "assessment.discarded"
    TREATMENT_OVERRIDDEN = "assessment.overridden"


@dataclass(frozen=True, slots=True)
class TaxActivity:
    """One append-only entry in the tax audit trail — **including the assessments that changed
    nothing.** "We assessed this again and reached the same conclusion" is the evidence
    determination is deterministic, and it is exactly what an auditor asks for when a rate has
    changed and a figure has not."""

    id: str
    org_id: str
    action: TaxAction
    actor_id: str = ""
    at: float = 0.0
    account_id: str = ""
    subject_id: str = ""
    assessment_id: str = ""
    jurisdiction_code: str = ""
    amount_minor: int = 0
    currency: Currency | None = None
    reason: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class TaxSearchQuery:
    """A deterministic filter. No relevance scoring and no index — the same assessments and the same
    query always come back in the same order."""

    text: str = ""
    account_id: str = ""
    subject_id: str = ""
    subject_kinds: tuple[SubjectKind, ...] = ()
    states: tuple[AssessmentState, ...] = ()
    treatments: tuple[TaxTreatment, ...] = ()
    regimes: tuple[TaxRegime, ...] = ()
    jurisdiction_code: str = ""
    overridden_only: bool = False
    unsourced_only: bool = False
    assessed_from: float | None = None
    assessed_until: float | None = None
    limit: int = 100
    offset: int = 0


@dataclass(frozen=True, slots=True)
class TaxSearchResult:
    assessments: tuple[TaxAssessment, ...] = ()
    total: int = 0


__all__ = [
    "ASSESSMENT_LIFECYCLE",
    "REVISABLE_ASSESSMENT_STATES",
    "SETTLED_ASSESSMENT_STATES",
    "AssessedComponent",
    "AssessedLine",
    "AssessmentState",
    "AssessmentTrigger",
    "DeclaredTaxRate",
    "ExemptionKind",
    "JurisdictionLevel",
    "NexusKind",
    "PlaceOfSupply",
    "RateProvenance",
    "RateSource",
    "RoundingScope",
    "SubjectKind",
    "SupplyDirection",
    "TaxAction",
    "TaxActivity",
    "TaxAssessment",
    "TaxBasis",
    "TaxBasisInputs",
    "TaxClassification",
    "TaxDiscrepancy",
    "TaxDiscrepancyKind",
    "TaxExemption",
    "TaxExplanation",
    "TaxExplanationStep",
    "TaxInspection",
    "TaxJurisdiction",
    "TaxNexus",
    "TaxOverride",
    "TaxRateQuote",
    "TaxReconciliation",
    "TaxResidency",
    "TaxRoundingPolicy",
    "TaxSearchQuery",
    "TaxSearchResult",
    "derive_assessment_id",
    "derive_rate_id",
    "jurisdiction_key",
]
