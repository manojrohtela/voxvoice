"""Tax Determination rules (M9.15) — stateless determination services.

Every decision this milestone makes lives here: where a supply happens, whether the supplier has an
obligation there, what treatment applies, which rates are in force, how the tax is computed, how it
is rounded, and how the result explains itself.

**No I/O, no clock, no id minting, no persistence, and no rate.** Every service is a pure function
of what it is given, which is what makes "the same basis determines identically forever" testable
rather than merely intended.

## The rule that shapes the whole module

> **Determination is a rented edge. Evidence is an owned fact.** (ADR-0023 TX1)

So there is **no rate anywhere in this file**. Not a constant, not a default, not a fallback "18 if
India". A test greps this package for a percentage and fails on one, because a rate hard-coded to
make a test pass is a rate that is still there when the jurisdiction changes it — and the failure
mode is silent under-collection that surfaces as an assessment years later.

Rates arrive through `TaxRateProvider`, an adapter port. Two implementations ship:
`NullTaxRateProvider`, which offers nothing and makes the resulting determination say so loudly, and
`DeclaredRateTaxProvider`, which serves operator-declared, effective-dated rates held as Billing
*evidence*. Neither is a table of law.

## What is deliberately *not* here

* **No price, no charge, no balance, no invoice total.** The taxable base arrives on the basis and
  is never recomputed. Nothing in this module multiplies a quantity by a price, folds a balance, or
  reads a ledger entry — a test walks the AST to prove it.
* **No float.** `Money.scaled(rate_basis_points, 10_000, rounding)` is the whole calculation
  primitive: exact rational scaling with a declared policy, integer throughout. A tax computed with
  a float is a tax that disagrees with itself after enough lines.
* **No jurisdiction rule written as an `if country == ...`.** Place of supply is resolved from the
  *shape* of the supply — same region, same country, or across a border — plus declared
  configuration. Adding a country is a jurisdiction row, never a branch (TX5).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from core.fsm import StateMachineDefinition

from .commercial_tax import (
    ASSESSMENT_LIFECYCLE,
    REVISABLE_ASSESSMENT_STATES,
    AssessedComponent,
    AssessedLine,
    AssessmentState,
    AssessmentTrigger,
    DeclaredTaxRate,
    ExemptionKind,
    NexusKind,
    PlaceOfSupply,
    RoundingScope,
    SupplyDirection,
    TaxAssessment,
    TaxBasis,
    TaxBasisInputs,
    TaxClassification,
    TaxDiscrepancy,
    TaxDiscrepancyKind,
    TaxExemption,
    TaxExplanation,
    TaxExplanationStep,
    TaxInspection,
    TaxJurisdiction,
    TaxNexus,
    TaxOverride,
    TaxRateQuote,
    TaxReconciliation,
    TaxRoundingPolicy,
    TaxSearchQuery,
    TaxSearchResult,
    derive_assessment_id,
    jurisdiction_key,
)
from .diagnostics import BillingDiagnosticCode, BillingDiagnostics, Diagnostic, Severity
from .errors import BillingConflict, InvalidBillingTransition
from .money import Currency, Money, Rounding
from .tax import GstSplit, TaxComponent, TaxpayerKind, TaxRegime, TaxTreatment

#: The denominator basis points are expressed against. A named constant rather than a literal
#: `10000` scattered through the arithmetic — it is the one number in this module that is not a
#: rate, and naming it is what makes that visible.
BASIS_POINT_SCALE = 10_000

#: Treatments under which no tax is charged. Declared as a set rather than tested inline, because
#: "which conclusions mean zero?" is a question a reader of an assessment asks and a chain of `or`s
#: buried in a calculator does not answer.
ZERO_TAX_TREATMENTS: frozenset[TaxTreatment] = frozenset(
    {
        TaxTreatment.ZERO_RATED,
        TaxTreatment.EXEMPT,
        TaxTreatment.REVERSE_CHARGE,
        TaxTreatment.OUT_OF_SCOPE,
    }
)


# ── the adapter port ────────────────────────────────────────────────────────────


class TaxRateProvider:
    """The rented edge (ADR-0023 TX1). **The only way a rate enters this plane.**

    A port rather than a base class with behaviour, because the whole point is that Billing does not
    know how rates are obtained. An external tax engine, an operator's declared table, or a
    contractual schedule all satisfy it; none of them becomes the owner of Billing's tax truth,
    because what is *stored* is the evidence this returns and not the provider's opinion of today.

    `quotes` must be a pure function of its arguments for a fixed `at`: determination has to be
    reproducible, and a provider that answered differently for the same instant would make an
    assessment unfalsifiable."""

    #: The provider's identity, recorded into every `RateProvenance` it produces.
    code: str = "none"
    version: str = "0"

    def quotes(
        self,
        *,
        jurisdiction_code: str,
        classification_code: str,
        treatment: TaxTreatment,
        at: float,
    ) -> tuple[TaxRateQuote, ...]:
        """The rates in force for this jurisdiction and classification at this instant."""
        raise NotImplementedError


class NullTaxRateProvider(TaxRateProvider):
    """Offers nothing.

    The default, and deliberately not a "0%" provider: a determination made with no source records
    `RateSource.NONE`, `is_sourced` is `False`, and `NO_TAX_RATE_SOURCE` is raised as a diagnostic.
    A silent zero would be indistinguishable from a correctly zero-rated export, and those two are
    the most expensive pair in the domain to confuse."""

    code = "none"
    version = "0"

    def quotes(self, **_: object) -> tuple[TaxRateQuote, ...]:  # noqa: D102
        return ()


class DeclaredRateTaxProvider(TaxRateProvider):
    """Serves operator-declared, effective-dated rates held as Billing evidence.

    Not a table of law: nothing here knows any jurisdiction's percentage. An operator declares a
    rate with the notification that set it and the window it applies in, and that declaration is
    itself evidence a challenge can examine. **Specificity wins** — a rate declared for a
    classification beats one declared for the whole jurisdiction, so a reduced rate on one service
    does not require restating every other."""

    code = "declared"
    version = "1"

    def __init__(self, rates: Sequence[DeclaredTaxRate] = ()) -> None:
        self._rates = tuple(rates)

    def quotes(
        self,
        *,
        jurisdiction_code: str,
        classification_code: str,
        treatment: TaxTreatment,
        at: float,
    ) -> tuple[TaxRateQuote, ...]:
        live = [
            rate
            for rate in self._rates
            if rate.jurisdiction_code == jurisdiction_code
            and rate.is_live_at(at)
            and rate.applies_to(classification_code)
        ]
        if not live:
            return ()
        specific = [rate for rate in live if rate.classification_code]
        chosen = specific or live
        return tuple(rate.to_quote(at=at) for rate in sorted(chosen, key=lambda r: (r.name, r.id)))


# ── place of supply ─────────────────────────────────────────────────────────────


class PlaceOfSupplyResolver:
    """Where a supply is deemed to happen.

    Resolved from the **shape** of the supply — same region, same country, or across a border — plus
    declared jurisdiction configuration. There is no `if country == "IN"` anywhere: adding a country
    is a jurisdiction row (TX5), and a branch per country is how a tax engine becomes unmaintainable
    at the fourth market.

    The India GST split is the one regime-specific output, and it is *recorded* rather than decided
    by geography alone: `INTRA_STATE` splits CGST+SGST, `INTER_STATE` is IGST, and which applied is
    evidence a return depends on."""

    def resolve(
        self,
        basis: TaxBasisInputs,
        *,
        classification: TaxClassification | None = None,
        rule: str = "",
    ) -> PlaceOfSupply:
        origin = basis.origin.jurisdiction_code
        destination = basis.destination.jurisdiction_code
        direction = self._direction(basis)
        # Destination-based by default — the rule every consumption tax regime has converged on for
        # services. An origin-based jurisdiction overrides it by declaring the rule explicitly.
        resolved = destination or origin
        return PlaceOfSupply(
            origin_code=origin,
            destination_code=destination,
            resolved_code=resolved,
            direction=direction,
            gst_split=self._gst_split(basis, direction),
            rule=rule or self._rule_name(direction, basis),
            is_digital_service=bool(classification and classification.is_digital_service),
            customer_registered=bool(basis.customer_registration),
        )

    @staticmethod
    def _direction(basis: TaxBasisInputs) -> SupplyDirection:
        origin, destination = basis.origin, basis.destination
        if not origin.country or not destination.country:
            return SupplyDirection.UNKNOWN
        if origin.country.upper() != destination.country.upper():
            return SupplyDirection.CROSS_BORDER_EXPORT
        if (origin.region or "").upper() == (destination.region or "").upper():
            return SupplyDirection.DOMESTIC_INTRA_REGION
        return SupplyDirection.DOMESTIC_INTER_REGION

    @staticmethod
    def _gst_split(basis: TaxBasisInputs, direction: SupplyDirection) -> GstSplit:
        if basis.regime is not TaxRegime.INDIA_GST:
            return GstSplit.NOT_APPLICABLE
        if direction is SupplyDirection.DOMESTIC_INTRA_REGION:
            return GstSplit.INTRA_STATE
        if direction is SupplyDirection.DOMESTIC_INTER_REGION:
            return GstSplit.INTER_STATE
        return GstSplit.NOT_APPLICABLE

    @staticmethod
    def _rule_name(direction: SupplyDirection, basis: TaxBasisInputs) -> str:
        """A human-readable name for the rule applied, recorded into the evidence. Free text by
        design — the set of rules is jurisdictional and open, and an enum would make adding a regime
        a structural change."""
        base = {
            SupplyDirection.DOMESTIC_INTRA_REGION: "destination, same region",
            SupplyDirection.DOMESTIC_INTER_REGION: "destination, different region",
            SupplyDirection.CROSS_BORDER_EXPORT: "destination, cross-border",
            SupplyDirection.UNKNOWN: "origin fallback — destination unknown",
        }[
            SupplyDirection.CROSS_BORDER_EXPORT
            if direction is SupplyDirection.CROSS_BORDER_IMPORT
            else direction
        ]
        suffix = " (B2B)" if basis.taxpayer_kind is TaxpayerKind.BUSINESS else " (B2C)"
        return base + suffix


# ── nexus ───────────────────────────────────────────────────────────────────────


class NexusResolver:
    """Does the supplier have an obligation where the supply happened?

    Returns a `TaxNexus` with `NexusKind.NONE` rather than `None` when it does not — because "we
    looked and there is no nexus" is evidence and "we never looked" is not, and an assessment must
    be able to say which."""

    def resolve(
        self, jurisdiction_code: str, entity_code: str, nexus: Sequence[TaxNexus], *, at: float
    ) -> TaxNexus | None:
        live = [
            n
            for n in nexus
            if n.entity_code == entity_code
            and n.jurisdiction_code == jurisdiction_code
            and n.is_live_at(at)
        ]
        if not live:
            return None
        # A registration-backed nexus is the strongest evidence, so it wins when several apply.
        registered = [n for n in live if n.registration is not None]
        return sorted(registered or live, key=lambda n: (-n.effective_from, n.id))[0]

    @staticmethod
    def guard_registered(nexus: TaxNexus | None, jurisdiction_code: str) -> None:
        """Fail fast when a supplier would charge tax somewhere it is not registered.

        Charging tax without a registration is not a rounding problem: it is money collected on
        behalf of an authority that has no record of the collector."""
        if nexus is not None and nexus.has_obligation and nexus.registration is None:
            raise BillingConflict(
                f"nexus in {jurisdiction_code} has no registration; tax cannot be charged on "
                "behalf of an authority the supplier is not registered with"
            )


# ── treatment ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TreatmentDecision:
    """What treatment applies, and why — the reason travels with the conclusion."""

    treatment: TaxTreatment
    reason: str = ""
    exemption: TaxExemption | None = None
    override: TaxOverride | None = None
    nexus: TaxNexus | None = None

    @property
    def is_zero(self) -> bool:
        return self.treatment in ZERO_TAX_TREATMENTS


class TreatmentResolver:
    """Decides the tax treatment of a supply.

    **Precedence is declared, not emergent**, and it is the load-bearing part of the milestone:

    1. an explicit **override** — a human decision, attributed and reasoned, beats every rule;
    2. **cross-border** → `ZERO_RATED` for an export, or `REVERSE_CHARGE` where the customer is a
       registered business and the regime shifts the liability. Ahead of the nexus test because an
       export is zero-rated by the *supplier's* regime: a missing registration in the buyer's
       country makes a supply an export, not a supply out of scope;
    3. **no nexus** → `OUT_OF_SCOPE`. A supplier with no obligation in a domestic jurisdiction does
       not charge its tax, and recording this as out-of-scope rather than zero-rated is the
       difference between "not our tax" and "our tax, at nothing";
    4. a live **exemption** → its kind's treatment. A certificate the customer holds beats a
       default;
    5. the **classification's** declared default;
    6. `STANDARD`.

    Order matters financially, and every adjacent pair was chosen rather than fallen into. Putting
    the exemption above the override would let a stale certificate defeat a deliberate correction.
    Putting nexus above cross-border reported every export as out-of-scope, losing the zero-rating
    from the evidence a return is filed from — the ordering defect the end-to-end smoke found."""

    def resolve(
        self,
        basis: TaxBasisInputs,
        place: PlaceOfSupply,
        *,
        nexus: TaxNexus | None = None,
        exemptions: Sequence[TaxExemption] = (),
        classification: TaxClassification | None = None,
        override: TaxOverride | None = None,
    ) -> TreatmentDecision:
        if override is not None and override.is_declared:
            return TreatmentDecision(
                treatment=override.treatment,
                reason=f"override: {override.reason}",
                override=override,
                nexus=nexus,
            )
        if place.is_cross_border:
            # Before the nexus test, deliberately. An export is zero-rated by the *supplier's* own
            # regime, so a missing registration in the buyer's country does not make it out of
            # scope — it makes it an export. Testing nexus first reported every export as
            # `out_of_scope`, which loses the zero-rating from the evidence a return is filed from.
            # Found by the end-to-end smoke.
            if place.customer_registered and basis.taxpayer_kind is TaxpayerKind.BUSINESS:
                return TreatmentDecision(
                    treatment=TaxTreatment.REVERSE_CHARGE,
                    reason="cross-border B2B supply to a registered customer",
                    nexus=nexus,
                )
            return TreatmentDecision(
                treatment=TaxTreatment.ZERO_RATED,
                reason="cross-border supply",
                nexus=nexus,
            )
        if nexus is None or not nexus.has_obligation:
            return TreatmentDecision(
                treatment=TaxTreatment.OUT_OF_SCOPE,
                reason=f"no nexus in {place.resolved_code}",
                nexus=nexus,
            )
        live = [e for e in exemptions if e.is_live_at(basis.effective_at)]
        matching = [e for e in live if e.jurisdiction_code == place.resolved_code] or live
        if matching:
            exemption = matching[0]
            return TreatmentDecision(
                treatment=_EXEMPTION_TREATMENTS[exemption.kind],
                reason=f"{exemption.kind.value}: {exemption.reason or exemption.certificate_ref}",
                exemption=exemption,
                nexus=nexus,
            )
        default = classification.default_treatment if classification else TaxTreatment.STANDARD
        if default is not TaxTreatment.STANDARD:
            return TreatmentDecision(
                treatment=default,
                reason=f"classification {classification.code} default",
                nexus=nexus,
            )
        return TreatmentDecision(treatment=TaxTreatment.STANDARD, reason="standard", nexus=nexus)


#: Which treatment each kind of exemption produces. A declared table rather than a chain of `if`s,
#: so "what does a resale certificate do to a supply?" is answerable by reading one mapping — and so
#: adding a kind is a row here plus a member there, never a branch in a resolver.
_EXEMPTION_TREATMENTS: dict[ExemptionKind, TaxTreatment] = {
    ExemptionKind.CERTIFICATE: TaxTreatment.EXEMPT,
    ExemptionKind.REVERSE_CHARGE: TaxTreatment.REVERSE_CHARGE,
    ExemptionKind.ZERO_RATED_EXPORT: TaxTreatment.ZERO_RATED,
    ExemptionKind.TREATY: TaxTreatment.EXEMPT,
    ExemptionKind.STATUTORY: TaxTreatment.EXEMPT,
}


# ── calculation ─────────────────────────────────────────────────────────────────


class TaxCalculator:
    """Applies rates to a base. **Integer throughout; no float ever participates.**

    `Money.scaled(rate, 10_000, rounding)` is the whole exclusive primitive — exact rational scaling
    with a declared policy, which is why the rounding mode is on the assessment rather than implicit
    in a library call.

    ## Why the whole line is computed at once

    Inclusive pricing is handled by *extraction* rather than addition, and it must be extracted
    against the **combined** rate of every component on the line, not once per component.

    An Indian intra-state supply carries CGST and SGST at 9% each. Extracting independently gives
    ``1000 × 900/10900`` twice — ₹82.56 + ₹82.56 = ₹165.12 — against a correct ₹152.54 of
    ``1000 × 1800/11800``. The parts then do not sum back to the gross, and every tax-inclusive sale
    in the market over-collects. Found by the end-to-end smoke, which printed a taxable base and a
    tax that added up to more than the price.

    So the extracted total is computed once and **allocated** across components by rate weight with
    `Money.allocate`, whose largest-remainder split guarantees the parts sum exactly to the whole —
    no rounding leakage, at any number of components."""

    def line_components(
        self,
        base: Money,
        quotes: Sequence[TaxRateQuote],
        *,
        basis: TaxBasis,
        rounding: Rounding,
    ) -> tuple[AssessedComponent, ...]:
        if not quotes:
            return ()
        if basis is TaxBasis.INCLUSIVE:
            return self._inclusive(base, quotes, rounding)
        return tuple(
            self._component(
                quote,
                base,
                base.scaled(quote.rate_basis_points, BASIS_POINT_SCALE, rounding),
                basis,
            )
            for quote in quotes
        )

    def _inclusive(
        self, gross: Money, quotes: Sequence[TaxRateQuote], rounding: Rounding
    ) -> tuple[AssessedComponent, ...]:
        combined = sum(q.rate_basis_points for q in quotes)
        if combined == 0:
            zero = Money.zero(gross.currency)
            return tuple(self._component(q, gross, zero, TaxBasis.INCLUSIVE) for q in quotes)
        total = gross.scaled(combined, BASIS_POINT_SCALE + combined, rounding)
        taxable = gross - total
        shares = total.allocate(tuple(q.rate_basis_points for q in quotes))
        return tuple(
            self._component(quote, taxable, share, TaxBasis.INCLUSIVE)
            for quote, share in zip(quotes, shares, strict=True)
        )

    @staticmethod
    def _component(
        quote: TaxRateQuote, taxable: Money, amount: Money, basis: TaxBasis
    ) -> AssessedComponent:
        return AssessedComponent(
            component=TaxComponent(
                name=quote.name,
                rate_basis_points=quote.rate_basis_points,
                amount=amount,
            ),
            tax_type=quote.tax_type,
            jurisdiction_code=quote.jurisdiction_code,
            taxable_base=taxable,
            provenance=quote.provenance,
            basis=basis,
        )


class TaxRoundingService:
    """Applies the declared rounding scope.

    `PER_COMPONENT` and `PER_LINE` are already satisfied by computing each component with the
    declared mode. `PER_DOCUMENT` is the one that needs work: the document total is rounded once and
    the difference redistributed across components with `Money.allocate`, so the parts still sum
    exactly to the whole. Without that redistribution a document-scope total and its own lines
    disagree by a minor unit — precisely the discrepancy an authority's validator reports."""

    def apply(
        self, lines: Sequence[AssessedLine], policy: TaxRoundingPolicy, currency: Currency
    ) -> tuple[AssessedLine, ...]:
        if policy.scope is not RoundingScope.PER_DOCUMENT:
            return tuple(lines)
        flat = [c for line in lines for c in line.components]
        if not flat:
            return tuple(lines)
        gross = Money.zero(currency)
        for component in flat:
            gross = gross + component.amount
        weights = tuple(max(c.amount.minor, 0) for c in flat)
        if sum(weights) == 0:
            return tuple(lines)
        shares = gross.allocate(weights)
        redistributed = iter(shares)
        return tuple(
            replace(
                line,
                components=tuple(
                    replace(c, component=replace(c.component, amount=next(redistributed)))
                    for c in line.components
                ),
            )
            for line in lines
        )


# ── determination ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DeterminationRequest:
    """Everything a determination needs, assembled once so both a preview and a real assessment read
    the same inputs. A preview that assembled its inputs differently would be a preview of something
    else."""

    basis: TaxBasisInputs
    jurisdictions: tuple[TaxJurisdiction, ...] = ()
    classifications: tuple[TaxClassification, ...] = ()
    nexus: tuple[TaxNexus, ...] = ()
    exemptions: tuple[TaxExemption, ...] = ()
    rounding: TaxRoundingPolicy = TaxRoundingPolicy()
    override: TaxOverride | None = None


class TaxDeterminationService:
    """The determination itself. Pure, deterministic, and rate-free.

    Reads the basis, resolves the place of supply, finds the nexus, decides the treatment, asks the
    **provider** for rates, computes with integers, rounds by the declared policy, and assembles an
    assessment that explains all of it. It reaches backward for nothing: usage, quantity, price,
    discount, charge, and balance are inputs on the basis and none is recomputed here."""

    def __init__(
        self,
        *,
        places: PlaceOfSupplyResolver | None = None,
        nexus: NexusResolver | None = None,
        treatments: TreatmentResolver | None = None,
        calculator: TaxCalculator | None = None,
        rounding: TaxRoundingService | None = None,
    ) -> None:
        self._places = places or PlaceOfSupplyResolver()
        self._nexus = nexus or NexusResolver()
        self._treatments = treatments or TreatmentResolver()
        self._calculator = calculator or TaxCalculator()
        self._rounding = rounding or TaxRoundingService()

    def determine(
        self,
        request: DeterminationRequest,
        provider: TaxRateProvider,
        *,
        assessed_at: float = 0.0,
        assessed_by: str = "",
        version: int = 1,
        supersedes: str = "",
    ) -> TaxAssessment:
        basis = request.basis
        by_code = {c.code: c for c in request.classifications}
        primary = next(iter(request.classifications), None)
        place = self._places.resolve(basis, classification=primary)
        nexus = self._nexus.resolve(
            place.resolved_code, basis.entity_code, request.nexus, at=basis.effective_at
        )
        decision = self._treatments.resolve(
            basis,
            place,
            nexus=nexus,
            exemptions=request.exemptions,
            classification=primary,
            override=request.override,
        )
        tax_basis = TaxBasis.INCLUSIVE if basis.prices_include_tax else TaxBasis.EXCLUSIVE
        lines = tuple(
            self._line(
                line_id,
                by_code.get(classification_code),
                classification_code,
                Money(base_minor, basis.currency),
                decision,
                place,
                provider,
                tax_basis=tax_basis,
                rounding=request.rounding.mode,
                at=basis.effective_at,
            )
            for line_id, classification_code, base_minor in basis.lines
        )
        lines = self._rounding.apply(lines, request.rounding, basis.currency)
        return TaxAssessment(
            id=derive_assessment_id(basis.org_id, basis.subject_id, basis.digest),
            org_id=basis.org_id,
            account_id=basis.account_id,
            subject_id=basis.subject_id,
            subject_kind=basis.subject_kind,
            currency=basis.currency,
            basis=basis,
            place_of_supply=place,
            treatment=decision.treatment,
            regime=basis.regime,
            state=AssessmentState.PROVISIONAL,
            version=version,
            lines=lines,
            rounding=request.rounding,
            nexus_id=nexus.id if nexus else "",
            nexus_kind=nexus.kind if nexus else NexusKind.NONE,
            registration_used=(nexus.registration.number if nexus and nexus.registration else ""),
            exemption_id=decision.exemption.id if decision.exemption else "",
            exemption_reason=decision.reason if decision.exemption else "",
            override=decision.override,
            supersedes_assessment_id=supersedes,
            notes=self._notes(decision, place, lines),
            assessed_at=assessed_at,
            assessed_by=assessed_by,
        )

    def _line(
        self,
        line_id: str,
        classification: TaxClassification | None,
        classification_code: str,
        base: Money,
        decision: TreatmentDecision,
        place: PlaceOfSupply,
        provider: TaxRateProvider,
        *,
        tax_basis: TaxBasis,
        rounding: Rounding,
        at: float,
    ) -> AssessedLine:
        if decision.is_zero:
            # A zero-tax treatment is a *conclusion*, so no rate is sought. Asking a provider for a
            # rate we have already decided not to apply would put an unused quote into the evidence.
            return AssessedLine(
                subject_line_id=line_id,
                classification_code=classification_code,
                taxable_base=base,
                treatment=decision.treatment,
                exemption_reason=decision.reason,
            )
        quotes = provider.quotes(
            jurisdiction_code=place.resolved_code,
            classification_code=classification_code,
            treatment=decision.treatment,
            at=at,
        )
        components = self._calculator.line_components(
            base, quotes, basis=tax_basis, rounding=rounding
        )
        return AssessedLine(
            subject_line_id=line_id,
            classification_code=classification_code,
            # Under inclusive pricing the taxable base is what remains after extraction, so it is
            # read back from the components rather than restated — a line whose base disagreed with
            # its own components would be a line that cannot be reconciled.
            taxable_base=components[0].taxable_base if components else base,
            components=components,
            treatment=decision.treatment,
        )

    @staticmethod
    def _notes(
        decision: TreatmentDecision, place: PlaceOfSupply, lines: Sequence[AssessedLine]
    ) -> tuple[str, ...]:
        notes = [decision.reason, f"place of supply: {place.rule}"]
        if any(line.components for line in lines) is False and not decision.is_zero:
            notes.append("no rate source offered a rate for this jurisdiction")
        return tuple(n for n in notes if n)


class AssessmentLifecycleService:
    """Applies assessment lifecycle triggers through the shared FSM.

    Illegal edges are rejected by the table, not by an `if` — which is what makes "a finalized
    determination is never edited" a property of the machine rather than of whoever wrote the last
    branch."""

    def __init__(self, definition: StateMachineDefinition = ASSESSMENT_LIFECYCLE) -> None:
        self._definition = definition

    def can(self, current: AssessmentState, trigger: AssessmentTrigger) -> bool:
        return self._definition.get(current, trigger) is not None

    def next_state(self, current: AssessmentState, trigger: AssessmentTrigger) -> AssessmentState:
        edge = self._definition.get(current, trigger)
        if edge is None:
            legal = sorted(t.value for t in self._definition.triggers_from(current))
            raise InvalidBillingTransition(
                f"a tax assessment cannot {trigger.value} from {current.value}; "
                f"legal triggers: {legal}"
            )
        return edge.target

    def allowed(self, current: AssessmentState) -> tuple[AssessmentTrigger, ...]:
        return tuple(sorted(self._definition.triggers_from(current), key=lambda t: t.value))

    def apply(
        self,
        assessment: TaxAssessment,
        trigger: AssessmentTrigger,
        *,
        at: float,
        actor_id: str = "",
    ) -> TaxAssessment:
        target = self.next_state(assessment.state, trigger)
        changes: dict = {"state": target}
        if target is AssessmentState.FINAL:
            changes.update(finalized_at=at, assessed_by=assessment.assessed_by or actor_id)
        return replace(assessment, **changes)

    def guard_revisable(self, assessment: TaxAssessment) -> None:
        """Fail fast when something would revise a determination already applied to a document."""
        if assessment.state not in REVISABLE_ASSESSMENT_STATES:
            raise BillingConflict(
                f"tax assessment {assessment.id} is {assessment.state.value}; a finalized "
                "determination is never edited — supersede it with a new assessment"
            )


# ── explanation ─────────────────────────────────────────────────────────────────


class TaxExplainer:
    """Answers "why was this tax amount applied?" **from the assessment alone.**

    It holds no provider and takes none, so the guarantee is structural: there is nothing to call
    even if a future maintainer wanted to. That matters because the answer must still be available
    in five years, when the provider's answer would be different and the invoice's would not."""

    def explain(self, assessment: TaxAssessment) -> TaxExplanation:
        place = assessment.place_of_supply
        steps = [
            TaxExplanationStep(
                stage="place_of_supply",
                conclusion=f"supply taxed in {place.resolved_code or 'an unresolved jurisdiction'}",
                evidence=f"origin {place.origin_code or '—'} → destination "
                f"{place.destination_code or '—'} ({place.direction.value})",
                reference=place.rule,
            ),
            TaxExplanationStep(
                stage="nexus",
                conclusion=(
                    f"supplier obligation: {assessment.nexus_kind.value}"
                    if assessment.nexus_id
                    else "no supplier obligation found"
                ),
                evidence=(
                    f"registration {assessment.registration_used}"
                    if assessment.registration_used
                    else "no registration relied on"
                ),
                reference=assessment.nexus_id,
            ),
            TaxExplanationStep(
                stage="treatment",
                conclusion=assessment.treatment.value,
                evidence=" · ".join(assessment.notes) or "standard treatment",
                reference=assessment.exemption_id
                or (assessment.override.reference if assessment.override else ""),
            ),
        ]
        for component in assessment.assessed_components:
            provenance = component.provenance
            steps.append(
                TaxExplanationStep(
                    stage="rate",
                    conclusion=f"{component.name} at {component.rate_basis_points} bp "
                    f"on {component.taxable_base} = {component.amount}",
                    evidence=f"source {provenance.source.value}"
                    + (f" v{provenance.source_version}" if provenance.source_version else "")
                    + f", effective from {int(provenance.effective_from)}",
                    reference=provenance.reference or provenance.source_code,
                )
            )
        steps.append(
            TaxExplanationStep(
                stage="rounding",
                conclusion=f"{assessment.rounding.mode.value} at {assessment.rounding.scope.value}",
                evidence=f"total tax {assessment.tax_total} on base {assessment.taxable_base}",
            )
        )
        if assessment.withholding_minor:
            steps.append(
                TaxExplanationStep(
                    stage="withholding",
                    conclusion=f"{assessment.withholding_minor} minor units withheld "
                    "by the customer",
                    evidence="a remittance obligation, never a discount (ADR-0023 TX6)",
                )
            )
        return TaxExplanation(
            assessment_id=assessment.id,
            subject_id=assessment.subject_id,
            steps=tuple(steps),
        )


# ── reconciliation, inspection, validation, search ──────────────────────────────


class TaxReconciliationService:
    """Does the assessment agree with what it was applied to?"""

    def reconcile(
        self,
        assessment: TaxAssessment | None,
        *,
        org_id: str,
        subject_id: str,
        subject_lines: Sequence[tuple[str, int]] = (),
        at: float = 0.0,
    ) -> TaxReconciliation:
        found: list[TaxDiscrepancy] = []
        lines = assessment.lines if assessment else ()
        assessed = {line.subject_line_id: line for line in lines}
        for line_id, base_minor in subject_lines:
            line = assessed.get(line_id)
            if line is None:
                found.append(
                    TaxDiscrepancy(
                        kind=TaxDiscrepancyKind.UNASSESSED_LINE,
                        line_id=line_id,
                        expected_minor=base_minor,
                        detail="the subject has this line; the assessment never taxed it",
                    )
                )
            elif line.taxable_base.minor != base_minor:
                found.append(
                    TaxDiscrepancy(
                        kind=TaxDiscrepancyKind.BASE_MISMATCH,
                        line_id=line_id,
                        expected_minor=base_minor,
                        actual_minor=line.taxable_base.minor,
                    )
                )
        known = {line_id for line_id, _ in subject_lines}
        for line in lines:
            if subject_lines and line.subject_line_id not in known:
                found.append(
                    TaxDiscrepancy(
                        kind=TaxDiscrepancyKind.ORPHAN_ASSESSED_LINE,
                        line_id=line.subject_line_id,
                        actual_minor=line.taxable_base.minor,
                        detail="the assessment taxed this; the subject has no such line",
                    )
                )
        if assessment is not None:
            found.extend(self._evidence_checks(assessment))
        return TaxReconciliation(
            org_id=org_id,
            subject_id=subject_id,
            assessment_id=assessment.id if assessment else "",
            lines_examined=len(lines),
            components_examined=len(assessment.assessed_components) if assessment else 0,
            discrepancies=tuple(found),
            at=at,
        )

    @staticmethod
    def _evidence_checks(assessment: TaxAssessment) -> list[TaxDiscrepancy]:
        found: list[TaxDiscrepancy] = []
        for component in assessment.assessed_components:
            if not component.provenance.is_sourced:
                found.append(
                    TaxDiscrepancy(
                        kind=TaxDiscrepancyKind.MISSING_PROVENANCE,
                        assessment_id=assessment.id,
                        actual_minor=component.amount.minor,
                        detail=f"{component.name} names no rate source",
                    )
                )
            elif not component.provenance.covers(assessment.basis.effective_at):
                found.append(
                    TaxDiscrepancy(
                        kind=TaxDiscrepancyKind.STALE_RATE,
                        assessment_id=assessment.id,
                        actual_minor=component.rate_basis_points,
                        detail=f"{component.name} was applied outside its effective window",
                    )
                )
            if component.amount.currency is not assessment.currency:
                found.append(
                    TaxDiscrepancy(
                        kind=TaxDiscrepancyKind.CURRENCY_MISMATCH,
                        assessment_id=assessment.id,
                        detail=f"{component.name} is in {component.amount.currency}",
                    )
                )
        if assessment.nexus_id and not assessment.registration_used and not assessment.is_zero:
            found.append(
                TaxDiscrepancy(
                    kind=TaxDiscrepancyKind.NEXUS_WITHOUT_REGISTRATION,
                    assessment_id=assessment.id,
                    detail="tax charged where the supplier holds no registration",
                )
            )
        return found


class TaxInspector:
    """A structural read of one assessment."""

    def inspect(self, assessment: TaxAssessment) -> TaxInspection:
        notes: list[str] = []
        in_window = all(
            c.provenance.covers(assessment.basis.effective_at)
            for c in assessment.assessed_components
            if c.provenance.is_sourced
        )
        if assessment.expects_rates and not assessment.assessed_components:
            notes.append(
                "no source offered a rate — a silent zero, not a zero-rated supply"
            )
        elif not assessment.is_sourced:
            notes.append("one or more rates name no source")
        if not in_window:
            notes.append("one or more rates were applied outside their effective window")
        if not assessment.place_of_supply.resolved_code:
            notes.append("place of supply could not be resolved")
        if assessment.is_overridden:
            notes.append(f"treatment overridden: {assessment.override.reason}")
        return TaxInspection(
            assessment_id=assessment.id,
            subject_id=assessment.subject_id,
            state=assessment.state,
            treatment=assessment.treatment,
            version=assessment.version,
            is_sourced=assessment.is_sourced,
            is_explainable=assessment.is_explainable,
            rates_in_window=in_window,
            has_nexus=bool(assessment.nexus_id),
            is_overridden=assessment.is_overridden,
            line_count=len(assessment.lines),
            component_count=len(assessment.assessed_components),
            notes=tuple(notes),
        )


class TaxValidator:
    """Structural validation. **Never raises** (§A2.6) — it reports, and a factory decides."""

    def validate_jurisdiction(self, jurisdiction: TaxJurisdiction) -> BillingDiagnostics:
        found: list[Diagnostic] = []
        if not jurisdiction.country:
            found.append(
                _d(
                    "country",
                    BillingDiagnosticCode.MISSING_REQUIRED_FIELD,
                    "a jurisdiction must name its country",
                )
            )
        expected = jurisdiction_key(jurisdiction.country, jurisdiction.region)
        if jurisdiction.code != expected:
            found.append(
                _d(
                    "code",
                    BillingDiagnosticCode.INVALID_JURISDICTION_CODE,
                    f"code {jurisdiction.code!r} is not the canonical {expected!r}",
                    remediation="a duplicate jurisdiction is a duplicate nexus",
                )
            )
        return BillingDiagnostics.of(found)

    def validate_rate(self, rate: DeclaredTaxRate) -> BillingDiagnostics:
        found: list[Diagnostic] = []
        if rate.rate_basis_points < 0:
            found.append(
                _d("rate_basis_points", BillingDiagnosticCode.INVALID_TAX_RATE, "a rate is >= 0")
            )
        if rate.effective_until is not None and rate.effective_until <= rate.effective_from:
            found.append(
                _d(
                    "effective_until",
                    BillingDiagnosticCode.INVALID_TAX_RATE_WINDOW,
                    "a rate's effective window must be non-empty",
                )
            )
        if not rate.reference:
            found.append(
                _d(
                    "reference",
                    BillingDiagnosticCode.MISSING_RATE_PROVENANCE,
                    "a declared rate with no reference is a number nobody can defend",
                    Severity.WARNING,
                    remediation="cite the notification, statute, or schedule that set it",
                )
            )
        return BillingDiagnostics.of(found)

    def validate_assessment(self, assessment: TaxAssessment) -> BillingDiagnostics:
        found: list[Diagnostic] = []
        if not assessment.place_of_supply.resolved_code:
            found.append(
                _d(
                    "place_of_supply",
                    BillingDiagnosticCode.UNRESOLVED_PLACE_OF_SUPPLY,
                    "no jurisdiction could be resolved for this supply",
                )
            )
        if assessment.expects_rates and not assessment.assessed_components:
            found.append(
                _d(
                    "components",
                    BillingDiagnosticCode.NO_TAX_RATE_SOURCE,
                    f"treatment is {assessment.treatment.value} but no source offered a rate for "
                    f"{assessment.place_of_supply.resolved_code or 'this jurisdiction'}",
                    remediation="a silent zero is indistinguishable from a correctly zero-rated "
                    "supply; declare a rate or record an exemption",
                )
            )
        for component in assessment.assessed_components:
            if not component.provenance.is_sourced:
                found.append(
                    _d(
                        f"components[{component.name}].provenance",
                        BillingDiagnosticCode.NO_TAX_RATE_SOURCE,
                        f"{component.name} names no rate source",
                        remediation="a rate with no provenance cannot be defended in an audit",
                    )
                )
            elif not component.provenance.covers(assessment.basis.effective_at):
                found.append(
                    _d(
                        f"components[{component.name}].provenance",
                        BillingDiagnosticCode.STALE_TAX_RATE,
                        f"{component.name} was applied outside its effective window",
                    )
                )
            if component.amount.currency is not assessment.currency:
                found.append(
                    _d(
                        f"components[{component.name}].amount",
                        BillingDiagnosticCode.TAX_CURRENCY_MISMATCH,
                        f"component is in {component.amount.currency}, assessment is in "
                        f"{assessment.currency}",
                    )
                )
        if assessment.is_overridden and not assessment.override.is_declared:
            found.append(
                _d(
                    "override",
                    BillingDiagnosticCode.UNDECLARED_TAX_OVERRIDE,
                    "an override must carry a reason and an actor",
                )
            )
        if assessment.nexus_id and not assessment.registration_used and not assessment.is_zero:
            found.append(
                _d(
                    "registration_used",
                    BillingDiagnosticCode.TAX_WITHOUT_REGISTRATION,
                    "tax charged where the supplier holds no registration",
                )
            )
        return BillingDiagnostics.of(found)

    def validate_finalize(self, assessment: TaxAssessment) -> BillingDiagnostics:
        """Finalizing is a one-way door, so everything checkable is checked before it opens."""
        found = list(self.validate_assessment(assessment).findings)
        if not assessment.is_explainable:
            found.append(
                _d(
                    "assessment",
                    BillingDiagnosticCode.UNEXPLAINABLE_TAX,
                    "this determination cannot answer 'why was this tax applied?' from itself",
                )
            )
        return BillingDiagnostics.of(found)


class TaxSearchService:
    """Deterministic filtering over a bounded set. No index, no scoring."""

    def search(
        self, assessments: Sequence[TaxAssessment], query: TaxSearchQuery
    ) -> TaxSearchResult:
        text = query.text.lower()
        found = [a for a in assessments if self._matches(a, query, text)]
        found.sort(key=lambda a: (-a.assessed_at, a.id))
        return TaxSearchResult(
            assessments=tuple(found[query.offset : query.offset + query.limit]),
            total=len(found),
        )

    @staticmethod
    def _matches(assessment: TaxAssessment, query: TaxSearchQuery, text: str) -> bool:
        if text and text not in f"{assessment.id} {assessment.subject_id}".lower():
            return False
        if query.account_id and assessment.account_id != query.account_id:
            return False
        if query.subject_id and assessment.subject_id != query.subject_id:
            return False
        if query.subject_kinds and assessment.subject_kind not in query.subject_kinds:
            return False
        if query.states and assessment.state not in query.states:
            return False
        if query.treatments and assessment.treatment not in query.treatments:
            return False
        if query.regimes and assessment.regime not in query.regimes:
            return False
        if (
            query.jurisdiction_code
            and assessment.place_of_supply.resolved_code != query.jurisdiction_code
        ):
            return False
        if query.overridden_only and not assessment.is_overridden:
            return False
        if query.unsourced_only and assessment.is_sourced:
            return False
        if query.assessed_from is not None and assessment.assessed_at < query.assessed_from:
            return False
        if query.assessed_until is not None and assessment.assessed_at > query.assessed_until:
            return False
        return True


def _d(
    path: str,
    code: BillingDiagnosticCode,
    message: str,
    severity: Severity = Severity.ERROR,
    *,
    remediation: str = "",
) -> Diagnostic:
    return Diagnostic(
        path=path, code=code, severity=severity, message=message, remediation=remediation
    )


__all__ = [
    "BASIS_POINT_SCALE",
    "ZERO_TAX_TREATMENTS",
    "AssessmentLifecycleService",
    "DeclaredRateTaxProvider",
    "DeterminationRequest",
    "NexusResolver",
    "NullTaxRateProvider",
    "PlaceOfSupplyResolver",
    "TaxCalculator",
    "TaxDeterminationService",
    "TaxExplainer",
    "TaxInspector",
    "TaxRateProvider",
    "TaxReconciliationService",
    "TaxRoundingService",
    "TaxSearchService",
    "TaxValidator",
    "TreatmentDecision",
    "TreatmentResolver",
]
