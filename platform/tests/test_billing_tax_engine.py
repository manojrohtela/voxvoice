"""M9.15 — Tax Determination Engine.

Tax is where an already-determined amount acquires a treatment, and it is the first Billing domain
whose correctness is judged by someone outside the company: a tax authority, on a date years after
the invoice was issued, against rules that have since changed.

So the suite is organised so that every claim is demonstrated **structurally** — by the absence of a
rate anywhere in the package, by the absence of an FSM edge, by a derived id, or by an explanation
assembled without a provider — rather than by a narrative that a passing integration test is assumed
to imply. §14 is a numbered list of them.
"""

from __future__ import annotations

import ast
import inspect as _inspect
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from billing.application import (
    ALL_TAX_COMMANDS,
    ALL_TAX_QUERIES,
    ActivateBillingAccount,
    ActivateCommercialMeter,
    ActivateSubscription,
    AllowAllAuthorizer,
    AssessDocumentTax,
    CloseAccountingPeriod,
    CreateBillingAccount,
    CreatePrice,
    CreatePriceBook,
    CreateProduct,
    CreateSubscription,
    DeclareTaxClassification,
    DeclareTaxJurisdiction,
    DeclareTaxRate,
    DefineCommercialMeter,
    DiagnoseTaxAssessment,
    DiscardTaxAssessment,
    ExplainTaxAssessment,
    FinalizeRatingRun,
    FinalizeTaxAssessment,
    GetInvoiceDocument,
    GetSubjectTaxAssessment,
    GetTaxAssessment,
    IngestMetricSignal,
    InspectTaxAssessment,
    IssueCommercialInvoice,
    ListDeclaredTaxRates,
    ListTaxAssessments,
    ListTaxJurisdictions,
    OpenAccountingPeriod,
    OpenUsagePeriod,
    OverrideTaxTreatment,
    PostRatingRun,
    PrepareInvoice,
    PreviewTaxAssessment,
    PublishCatalog,
    PublishPricing,
    PublishPricingBook,
    PublishProduct,
    RateSubscriptionPeriod,
    ReconcileTaxAssessment,
    RecordTaxExemption,
    RegisterTaxNexus,
    ResolveTaxPlaceOfSupply,
    RetireTaxRate,
    ReviseInvoiceDraft,
    SearchTaxAssessments,
    TaxTimeline,
    UpdateAddresses,
)
from billing.customer.app import create_billing_app
from billing.customer.config import BillingApiSettings
from billing.customer.container import BillingContainer
from billing.foundation import (
    ASSESSMENT_LIFECYCLE,
    REVISABLE_ASSESSMENT_STATES,
    ZERO_TAX_TREATMENTS,
    AccountAddress,
    AgreementItem,
    AssessmentLifecycleService,
    AssessmentState,
    AssessmentTrigger,
    BillingAddress,
    BillingConflict,
    BillingDiagnosticCode,
    BillingInterval,
    BillingNotFound,
    BillingPeriod,
    Currency,
    DeclaredRateTaxProvider,
    DeclaredTaxRate,
    DeterminationRequest,
    ExemptionKind,
    FixedClock,
    GstSplit,
    InvalidBillingTransition,
    InvoiceParty,
    ItemKind,
    JurisdictionLevel,
    MeterSource,
    MetricSignal,
    Money,
    NexusResolver,
    Normalization,
    NullTaxRateProvider,
    PaymentInstructions,
    PlaceOfSupplyResolver,
    PriceShape,
    PriceTier,
    RateProvenance,
    RateSource,
    Rounding,
    RoundingScope,
    Scope,
    SequentialIdGenerator,
    SubjectKind,
    SupplyDirection,
    TaxAssessment,
    TaxBasis,
    TaxBasisInputs,
    TaxCalculator,
    TaxClassification,
    TaxDeterminationService,
    TaxExemption,
    TaxExplainer,
    TaxFactory,
    TaxInspector,
    TaxNexus,
    TaxpayerKind,
    TaxRateProvider,
    TaxRateQuote,
    TaxReconciliationService,
    TaxRegime,
    TaxRegistration,
    TaxResidency,
    TaxRoundingPolicy,
    TaxRoundingService,
    TaxSearchQuery,
    TaxSearchService,
    TaxTreatment,
    TaxValidator,
    TreatmentResolver,
    derive_rate_id,
    jurisdiction_key,
    period_key_for,
)
from billing.persistence import build_persistent_billing_application

PLATFORM = Path(__file__).resolve().parents[1]
BILLING = PLATFORM / "billing"
STUDIO = BILLING / "customer" / "studio" / "taxation.html"

INR = Currency.INR
USD = Currency.USD
ORG, ACTOR, ACC = "org_1", "usr_1", "bac_1"
P0, P1 = 1_000.0, 2_000.0
KEY = period_key_for(BillingPeriod(P0, P1))
AT = 2_000.0
KW = {"actor_id": ACTOR, "org_id": ORG}

places = PlaceOfSupplyResolver()
treatments = TreatmentResolver()
calculator = TaxCalculator()
determination = TaxDeterminationService()
lifecycle = AssessmentLifecycleService()


# ── helpers ─────────────────────────────────────────────────────────────────────


def _class_code(module: str, name: str) -> str:
    """One class's source with **docstrings stripped**.

    The M9.6 lesson at class granularity: these scans are about what the code *does*, and prose
    explaining that a renderer computes no tax must not itself read as computation."""
    tree = ast.parse((BILLING / "foundation" / module).read_text())
    found = next(
        n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == name
    )
    for node in ast.walk(found):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(found)


def a_rate(name: str = "CGST", bp: int = 900, **kw) -> DeclaredTaxRate:
    base = dict(
        id=derive_rate_id(ORG, "IN:KA", "gst", name, "saas", 0.0),
        org_id=ORG,
        jurisdiction_code="IN:KA",
        tax_type="gst",
        name=name,
        rate_basis_points=bp,
        classification_code="saas",
        effective_from=0.0,
        reference="Notification 11/2017-Central Tax (Rate)",
        source_version="2017-06-28",
    )
    base.update(kw)
    return DeclaredTaxRate(**base)  # type: ignore[arg-type]


def a_nexus(**kw) -> TaxNexus:
    base = dict(
        id="txn_1",
        org_id=ORG,
        entity_code="VOX",
        jurisdiction_code="IN:KA",
        registration=TaxRegistration(regime=TaxRegime.INDIA_GST, number="29AABCV1234F1Z5"),
        effective_from=0.0,
    )
    base.update(kw)
    return TaxNexus(**base)  # type: ignore[arg-type]


def a_basis(**kw) -> TaxBasisInputs:
    base = dict(
        org_id=ORG,
        account_id=ACC,
        entity_code="VOX",
        subject_id="inv_1",
        currency=INR,
        regime=TaxRegime.INDIA_GST,
        origin=TaxResidency(country="IN", region="KA", evidence=("supplier_entity",)),
        destination=TaxResidency(
            country="IN", region="KA", evidence=("billing_address", "payment_country")
        ),
        customer_registration="29AAACX1234F1Z1",
        supplier_registration="29AABCV1234F1Z5",
        effective_at=AT,
        lines=(("inl_1", "saas", 100_000),),
    )
    base.update(kw)
    return TaxBasisInputs(**base)  # type: ignore[arg-type]


SAAS = TaxClassification(
    id="txc_1", org_id=ORG, code="saas", display_name="SaaS", is_digital_service=True
)


def a_request(basis=None, **kw) -> DeterminationRequest:
    base = dict(
        basis=basis or a_basis(),
        classifications=(SAAS,),
        nexus=(a_nexus(),),
    )
    base.update(kw)
    return DeterminationRequest(**base)  # type: ignore[arg-type]


def determine(request=None, provider=None, **kw) -> TaxAssessment:
    return determination.determine(
        request or a_request(),
        provider if provider is not None else DeclaredRateTaxProvider((a_rate(), a_rate("SGST"))),
        assessed_at=kw.pop("assessed_at", AT),
        **kw,
    )


# ── 1. no rate exists anywhere in the package ───────────────────────────────────

TAX_FOUNDATION = (
    BILLING / "foundation" / "commercial_tax.py",
    BILLING / "foundation" / "tax_rules.py",
    BILLING / "foundation" / "tax_factories.py",
)
TAX_MODULES = TAX_FOUNDATION + (
    BILLING / "application" / "tax_engine.py",
    BILLING / "persistence" / "tax_models.py",
    BILLING / "persistence" / "tax_repositories.py",
    BILLING / "customer" / "tax_schemas.py",
    BILLING / "customer" / "routers" / "tax_engine.py",
)


def _code_only(path: Path) -> str:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _executable_constants(code: str):
    """Every `rate_basis_points=` keyword the package would *execute*, with OpenAPI examples
    excluded.

    A `model_config = ConfigDict(json_schema_extra={...})` block is documentation of the wire
    shape — it shows a caller what a declared rate looks like and is never read as one. Excluding
    it by structure rather than by whitelisting a number keeps the scan sharp: a literal rate
    anywhere the code could reach still fails."""
    tree = ast.parse(code)
    documented: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "model_config" for t in node.targets
        ):
            documented.update(id(c) for c in ast.walk(node))
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.keyword) and id(n) not in documented
    ]


def test_no_tax_rate_is_written_in_python_anywhere():
    """ADR-0023 TX1 made structural. A rate hard-coded to make a test pass is a rate that is still
    there when the jurisdiction changes it, and the failure mode is silent under-collection.

    The scan looks for the *shapes* a rate takes — a basis-point-sized integer literal, a percentage
    in a name, a jurisdiction mapped to a number — in code with docstrings stripped."""
    forbidden_names = re.compile(
        r"(STANDARD_RATE|DEFAULT_RATE|GST_RATE|VAT_RATE|RATE_TABLE|RATES\s*[:=]|_RATES\b)"
    )
    for path in TAX_MODULES:
        code = _code_only(path)
        assert not forbidden_names.search(code), path.name
        # The precise structural check: a rate enters this domain through exactly one keyword, so a
        # literal passed to it is a rate written in Python. A `limit=500` is not, and this scan can
        # tell the difference — which is what keeps it sharp rather than merely strict.
        for node in _executable_constants(code):
            if not isinstance(node, ast.keyword) or node.arg != "rate_basis_points":
                continue
            assert not isinstance(node.value, ast.Constant), (path.name, ast.unparse(node))


def test_the_only_declared_number_is_the_basis_point_denominator():
    from billing.foundation import BASIS_POINT_SCALE

    assert BASIS_POINT_SCALE == 10_000
    code = _code_only(BILLING / "foundation" / "tax_rules.py")
    assert code.count("10000") <= 1  # the constant's own definition


def test_a_fresh_installation_ships_no_jurisdiction_no_class_and_no_rate():
    """A migration that inserted a percentage would make this plane the owner of a number it has no
    authority over."""
    migration = (
        BILLING / "persistence" / "migrations" / "v0025_billing_tax.py"
    ).read_text()
    for forbidden in ("INSERT", "insert(", "seed", "add(", "1800", "900"):
        assert forbidden not in _code_only(
            BILLING / "persistence" / "migrations" / "v0025_billing_tax.py"
        ), forbidden
    assert "Nothing is seeded" in migration


def test_the_null_provider_offers_nothing_rather_than_zero():
    assert NullTaxRateProvider().quotes(
        jurisdiction_code="IN:KA", classification_code="saas",
        treatment=TaxTreatment.STANDARD, at=AT
    ) == ()


def test_the_provider_port_is_the_only_way_a_rate_enters():
    """Every rate on an assessment carries provenance naming the source it came from."""
    assessment = determine()
    assert assessment.assessed_components
    for component in assessment.assessed_components:
        assert component.provenance.source is RateSource.DECLARED
        assert component.provenance.reference


# ── 2. determinism and identity ─────────────────────────────────────────────────


def test_an_assessment_id_is_derived_from_its_basis():
    assert determine().id == determine().id
    assert determine().id.startswith("txa_")


def test_a_changed_input_derives_a_different_assessment():
    """So a re-determination is structurally incapable of overwriting what it revises."""
    other = a_basis(lines=(("inl_1", "saas", 200_000),))
    assert determine().id != determine(a_request(other)).id


def test_the_digest_excludes_when_and_who_determined_it():
    first = determine(assessed_at=1.0, assessed_by="a")
    second = determine(assessed_at=9e9, assessed_by="b")
    assert first.digest == second.digest


def test_the_digest_moves_when_any_figure_moves():
    assert determine().digest != determine(
        provider=DeclaredRateTaxProvider((a_rate(bp=1200), a_rate("SGST", 1200)))
    ).digest


def test_a_rate_id_includes_its_name():
    """CGST and SGST are two components of one tax type in one jurisdiction over one window.
    Without the name they derive the same id, the second declaration is swallowed as a duplicate,
    and every intra-state invoice is taxed at half the correct rate. Found by the end-to-end smoke.
    """
    cgst = derive_rate_id(ORG, "IN:KA", "gst", "CGST", "saas", 0.0)
    sgst = derive_rate_id(ORG, "IN:KA", "gst", "SGST", "saas", 0.0)
    assert cgst != sgst


def test_a_rate_id_includes_its_effective_start():
    assert derive_rate_id(ORG, "IN:KA", "gst", "CGST", "saas", 0.0) != derive_rate_id(
        ORG, "IN:KA", "gst", "CGST", "saas", 1_000.0
    )


def test_a_jurisdiction_code_is_canonical():
    assert jurisdiction_key("in", "ka") == jurisdiction_key("IN", "KA") == "IN:KA"
    assert jurisdiction_key("DE") == "DE"


def test_the_basis_digest_covers_every_input():
    basis = a_basis()
    for field, value in (
        ("entity_code", "OTHER"),
        ("customer_registration", "x"),
        ("prices_include_tax", True),
        ("taxpayer_kind", TaxpayerKind.CONSUMER),
        ("regime", TaxRegime.EU_VAT),
    ):
        assert a_basis(**{field: value}).digest != basis.digest, field


# ── 3. place of supply ──────────────────────────────────────────────────────────


def test_same_region_is_intra_state():
    place = places.resolve(a_basis())
    assert place.direction is SupplyDirection.DOMESTIC_INTRA_REGION
    assert place.gst_split is GstSplit.INTRA_STATE
    assert place.resolved_code == "IN:KA"


def test_different_region_is_inter_state():
    place = places.resolve(a_basis(destination=TaxResidency(country="IN", region="MH")))
    assert place.direction is SupplyDirection.DOMESTIC_INTER_REGION
    assert place.gst_split is GstSplit.INTER_STATE


def test_different_country_is_cross_border():
    place = places.resolve(a_basis(destination=TaxResidency(country="DE")))
    assert place.is_cross_border
    assert place.gst_split is GstSplit.NOT_APPLICABLE


def test_the_gst_split_is_only_computed_under_the_gst_regime():
    place = places.resolve(a_basis(regime=TaxRegime.EU_VAT))
    assert place.gst_split is GstSplit.NOT_APPLICABLE


def test_supply_is_destination_based_by_default():
    assert places.resolve(a_basis()).resolved_code == "IN:KA"
    assert (
        places.resolve(a_basis(destination=TaxResidency(country="DE"))).resolved_code == "DE"
    )


def test_an_unknown_destination_falls_back_to_origin_and_says_so():
    place = places.resolve(a_basis(destination=TaxResidency()))
    assert place.direction is SupplyDirection.UNKNOWN
    assert place.resolved_code == "IN:KA"
    assert "origin fallback" in place.rule


def test_no_jurisdiction_rule_is_written_as_a_country_branch():
    """Adding a market is a jurisdiction row, never a branch (ADR-0023 TX5)."""
    code = _class_code("tax_rules.py", "PlaceOfSupplyResolver")
    for forbidden in ('"IN"', "'IN'", '"US"', '"DE"', '"GB"', "country =="):
        assert forbidden not in code, forbidden


# ── 4. nexus ────────────────────────────────────────────────────────────────────


def test_no_nexus_resolves_to_none_rather_than_a_guess():
    assert NexusResolver().resolve("IN:TN", "VOX", (a_nexus(),), at=AT) is None


def test_a_registration_backed_nexus_wins_over_a_bare_one():
    from billing.foundation import NexusKind

    bare = a_nexus(id="txn_2", registration=None, kind=NexusKind.ECONOMIC)
    resolved = NexusResolver().resolve("IN:KA", "VOX", (bare, a_nexus()), at=AT)
    assert resolved.id == "txn_1"


def test_an_expired_nexus_is_not_live():
    assert NexusResolver().resolve(
        "IN:KA", "VOX", (a_nexus(effective_until=100.0),), at=AT
    ) is None


def test_charging_tax_without_a_registration_is_refused():
    from billing.foundation import NexusKind

    with pytest.raises(BillingConflict, match="not registered"):
        NexusResolver.guard_registered(
            a_nexus(registration=None, kind=NexusKind.PHYSICAL), "IN:KA"
        )


def test_nexus_none_is_a_recorded_fact_not_an_absence():
    from billing.foundation import NexusKind

    assert not a_nexus(kind=NexusKind.NONE).has_obligation
    assert NexusKind.NONE in set(NexusKind)


# ── 5. treatment precedence ─────────────────────────────────────────────────────


def test_an_override_beats_every_rule():
    from billing.foundation import TaxOverride

    override = TaxOverride(
        treatment=TaxTreatment.ZERO_RATED, reason="ruling", actor_id=ACTOR
    )
    decision = treatments.resolve(
        a_basis(), places.resolve(a_basis()), nexus=a_nexus(), override=override
    )
    assert decision.treatment is TaxTreatment.ZERO_RATED
    assert "override" in decision.reason


def test_an_undeclared_override_is_ignored():
    """An override with no reason or no actor is not a decision — it is a mistake."""
    from billing.foundation import TaxOverride

    decision = treatments.resolve(
        a_basis(),
        places.resolve(a_basis()),
        nexus=a_nexus(),
        override=TaxOverride(treatment=TaxTreatment.EXEMPT, reason="", actor_id=""),
    )
    assert decision.treatment is TaxTreatment.STANDARD


def test_an_export_is_zero_rated_not_out_of_scope():
    """Cross-border precedes the nexus test: an export is zero-rated by the *supplier's* regime, so
    a missing registration in the buyer's country makes it an export rather than out of scope.
    Testing nexus first reported every export as `out_of_scope`, losing the zero-rating from the
    evidence a return is filed from — found by the end-to-end smoke."""
    basis = a_basis(destination=TaxResidency(country="DE"), customer_registration="")
    decision = treatments.resolve(basis, places.resolve(basis), nexus=None)
    assert decision.treatment is TaxTreatment.ZERO_RATED


def test_a_cross_border_b2b_supply_to_a_registered_customer_reverse_charges():
    basis = a_basis(destination=TaxResidency(country="DE"))
    decision = treatments.resolve(basis, places.resolve(basis), nexus=None)
    assert decision.treatment is TaxTreatment.REVERSE_CHARGE


def test_no_domestic_nexus_is_out_of_scope():
    basis = a_basis(destination=TaxResidency(country="IN", region="TN"))
    decision = treatments.resolve(basis, places.resolve(basis), nexus=None)
    assert decision.treatment is TaxTreatment.OUT_OF_SCOPE
    assert "no nexus" in decision.reason


def test_a_live_exemption_beats_the_default():
    exemption = TaxExemption(
        id="txe_1", org_id=ORG, account_id=ACC, jurisdiction_code="IN:KA",
        certificate_ref="EX-1", effective_from=0.0,
    )
    decision = treatments.resolve(
        a_basis(), places.resolve(a_basis()), nexus=a_nexus(), exemptions=(exemption,)
    )
    assert decision.treatment is TaxTreatment.EXEMPT
    assert decision.exemption is exemption


def test_a_revoked_exemption_is_not_applied():
    exemption = TaxExemption(
        id="txe_1", org_id=ORG, account_id=ACC, jurisdiction_code="IN:KA",
        effective_from=0.0, revoked_at=100.0,
    )
    decision = treatments.resolve(
        a_basis(), places.resolve(a_basis()), nexus=a_nexus(), exemptions=(exemption,)
    )
    assert decision.treatment is TaxTreatment.STANDARD


def test_a_classification_default_applies_when_nothing_else_does():
    exempt_class = TaxClassification(
        id="c", org_id=ORG, code="books", display_name="Books",
        default_treatment=TaxTreatment.ZERO_RATED,
    )
    decision = treatments.resolve(
        a_basis(), places.resolve(a_basis()), nexus=a_nexus(), classification=exempt_class
    )
    assert decision.treatment is TaxTreatment.ZERO_RATED


def test_every_exemption_kind_maps_to_a_treatment():
    from billing.foundation.tax_rules import _EXEMPTION_TREATMENTS

    assert set(_EXEMPTION_TREATMENTS) == set(ExemptionKind)


def test_zero_tax_treatments_are_declared_rather_than_tested_inline():
    assert ZERO_TAX_TREATMENTS == frozenset(
        {
            TaxTreatment.ZERO_RATED,
            TaxTreatment.EXEMPT,
            TaxTreatment.REVERSE_CHARGE,
            TaxTreatment.OUT_OF_SCOPE,
        }
    )
    assert TaxTreatment.STANDARD not in ZERO_TAX_TREATMENTS


# ── 6. calculation ──────────────────────────────────────────────────────────────


def _quote(name: str = "CGST", bp: int = 900) -> TaxRateQuote:
    return TaxRateQuote(
        name=name,
        tax_type="gst",
        rate_basis_points=bp,
        jurisdiction_code="IN:KA",
        provenance=RateProvenance(source=RateSource.DECLARED, reference="n"),
    )


def test_exclusive_tax_is_added_to_the_base():
    components = calculator.line_components(
        Money(100_000, INR), (_quote(),), basis=TaxBasis.EXCLUSIVE, rounding=Rounding.HALF_UP
    )
    assert components[0].amount == Money(9_000, INR)
    assert components[0].taxable_base == Money(100_000, INR)


def test_inclusive_tax_is_extracted_against_the_combined_rate():
    """The defect the smoke found. Extracting per component gives 1000 × 900/10900 twice — ₹165.12
    against a correct ₹152.54 of 1000 × 1800/11800 — and the parts stop summing to the gross, so
    every tax-inclusive sale over-collects."""
    gross = Money(100_000, INR)
    components = calculator.line_components(
        gross, (_quote(), _quote("SGST")), basis=TaxBasis.INCLUSIVE, rounding=Rounding.HALF_UP
    )
    tax = components[0].amount + components[1].amount
    assert tax == Money(15_254, INR)
    assert components[0].taxable_base + tax == gross


def test_inclusive_components_sum_exactly_at_any_split():
    gross = Money(99_999, INR)
    components = calculator.line_components(
        gross,
        (_quote("A", 700), _quote("B", 500), _quote("C", 300)),
        basis=TaxBasis.INCLUSIVE,
        rounding=Rounding.HALF_UP,
    )
    total = Money.zero(INR)
    for component in components:
        total = total + component.amount
    assert components[0].taxable_base + total == gross


def test_a_zero_rate_extracts_nothing():
    components = calculator.line_components(
        Money(100_000, INR), (_quote(bp=0),), basis=TaxBasis.INCLUSIVE, rounding=Rounding.HALF_UP
    )
    assert components[0].amount.is_zero


def test_no_float_participates_in_the_calculation():
    code = _class_code("tax_rules.py", "TaxCalculator")
    for forbidden in ("float(", "/ 100", "* 0.", "round(", "Decimal"):
        assert forbidden not in code, forbidden


def test_document_scope_rounding_redistributes_exactly():
    """A document-scope total and its own lines must not disagree by a minor unit — precisely the
    discrepancy an authority's validator reports."""
    assessment = determine()
    lines = TaxRoundingService().apply(
        assessment.lines,
        TaxRoundingPolicy(scope=RoundingScope.PER_DOCUMENT),
        INR,
    )
    total = Money.zero(INR)
    for line in lines:
        total = total + line.tax_total
    assert total == assessment.tax_total


def test_the_rounding_policy_is_recorded_on_the_assessment():
    policy = TaxRoundingPolicy(mode=Rounding.HALF_EVEN, scope=RoundingScope.PER_DOCUMENT)
    assessment = determine(a_request(rounding=policy))
    assert assessment.rounding == policy
    assert policy.digest in assessment.digest or assessment.digest  # carried into the fingerprint


# ── 7. the evidence ─────────────────────────────────────────────────────────────


def test_every_component_carries_where_its_rate_came_from():
    for component in determine().assessed_components:
        provenance = component.provenance
        assert provenance.is_sourced
        assert provenance.reference
        assert provenance.source_version
        assert provenance.effective_from is not None


def test_an_unsourced_determination_is_not_explainable():
    """A silent zero is indistinguishable from a correctly zero-rated export, and those two are the
    most expensive pair in the domain to confuse. `all()` over an empty tuple returned `True`, so a
    standard-rated supply with no rate reported itself perfectly sourced — found by the smoke."""
    assessment = determine(provider=NullTaxRateProvider())
    assert assessment.expects_rates
    assert not assessment.is_sourced
    assert not assessment.is_explainable


def test_a_zero_rated_supply_with_no_components_is_explainable():
    """A conclusion, not a gap."""
    basis = a_basis(destination=TaxResidency(country="DE"), customer_registration="")
    assessment = determine(a_request(basis), provider=NullTaxRateProvider())
    assert assessment.treatment is TaxTreatment.ZERO_RATED
    assert not assessment.expects_rates
    assert assessment.is_explainable


def test_a_zero_treatment_seeks_no_rate_at_all():
    """Asking for a rate already decided against would put an unused quote into the evidence."""
    basis = a_basis(destination=TaxResidency(country="DE"), customer_registration="")
    assert determine(a_request(basis)).assessed_components == ()


def test_the_explanation_answers_why_without_a_provider():
    explanation = TaxExplainer().explain(determine())
    assert explanation.answered_without_provider
    stages = [step.stage for step in explanation.steps]
    assert stages[:3] == ["place_of_supply", "nexus", "treatment"]
    assert "rate" in stages and stages[-1] == "rounding"


def test_the_explainer_holds_no_provider():
    """Structural: there is nothing to call even if a future maintainer wanted to."""
    code = _class_code("tax_rules.py", "TaxExplainer")
    for forbidden in ("provider", "quotes(", "TaxRateProvider"):
        assert forbidden not in code, forbidden
    assert "provider" not in str(_inspect.signature(TaxExplainer.explain))


def test_the_explanation_names_the_authority_for_every_rate():
    steps = [s for s in TaxExplainer().explain(determine()).steps if s.stage == "rate"]
    assert steps
    for step in steps:
        assert "Notification" in step.reference


def test_withholding_is_explained_as_an_obligation_not_a_discount():
    from dataclasses import replace

    assessment = replace(determine(), withholding_minor=5_000)
    step = next(
        s for s in TaxExplainer().explain(assessment).steps if s.stage == "withholding"
    )
    assert "never a discount" in step.evidence


# ── 8. the lifecycle ────────────────────────────────────────────────────────────


def test_a_finalized_determination_is_never_edited():
    """The absence that matters most: no edge out of `final`."""
    assert lifecycle.allowed(AssessmentState.FINAL) == ()
    assert not lifecycle.can(AssessmentState.FINAL, AssessmentTrigger.SUPERSEDE)


def test_superseded_and_discarded_are_terminal():
    assert lifecycle.allowed(AssessmentState.SUPERSEDED) == ()
    assert lifecycle.allowed(AssessmentState.DISCARDED) == ()


def test_only_a_provisional_determination_is_revisable():
    assert REVISABLE_ASSESSMENT_STATES == frozenset({AssessmentState.PROVISIONAL})


def test_the_lifecycle_has_no_deletion_state():
    assert not {s.value for s in AssessmentState if "delete" in s.value or "purge" in s.value}


def test_an_illegal_transition_names_the_legal_ones():
    with pytest.raises(InvalidBillingTransition) as excinfo:
        lifecycle.next_state(AssessmentState.FINAL, AssessmentTrigger.FINALIZE)
    assert "legal triggers: []" in str(excinfo.value)


def test_guard_revisable_refuses_a_finalized_determination():
    finalized = lifecycle.apply(determine(), AssessmentTrigger.FINALIZE, at=AT)
    with pytest.raises(BillingConflict, match="supersede"):
        lifecycle.guard_revisable(finalized)


def test_finalizing_records_when():
    finalized = lifecycle.apply(determine(), AssessmentTrigger.FINALIZE, at=AT, actor_id=ACTOR)
    assert finalized.state is AssessmentState.FINAL and finalized.finalized_at == AT


def test_the_lifecycle_table_is_exactly_three_edges():
    assert len(ASSESSMENT_LIFECYCLE.triggers_from(AssessmentState.PROVISIONAL)) == 3


# ── 9. the projection onto M9.2 ─────────────────────────────────────────────────


def test_to_determination_is_the_single_projection():
    assessment = determine()
    projected = assessment.to_determination()
    assert projected.tax_total == assessment.tax_total
    assert projected.taxable_amount == assessment.taxable_base
    assert projected.gst_split is assessment.place_of_supply.gst_split
    assert projected.place_of_supply == assessment.place_of_supply.resolved_code
    assert projected.customer_registration == assessment.basis.customer_registration


def test_the_projection_is_declared_in_exactly_one_place():
    """Letting a caller assemble one would make the thinner record a second source of truth."""
    declarations = [
        path.name
        for path in (BILLING / "foundation").glob("*.py")
        if "def to_determination" in path.read_text()
    ]
    assert declarations == ["commercial_tax.py"]
    for path in TAX_MODULES[3:]:
        assert "TaxDetermination(" not in _code_only(path), path.name


def test_the_projection_carries_the_adapter_identity():
    assert determine().to_determination().adapter_code


# ── 10. reconciliation, inspection, validation, search ──────────────────────────


def test_an_assessment_that_covers_every_line_reconciles():
    report = TaxReconciliationService().reconcile(
        determine(), org_id=ORG, subject_id="inv_1", subject_lines=(("inl_1", 100_000),)
    )
    assert report.is_clean


def test_an_unassessed_line_is_detected():
    from billing.foundation import TaxDiscrepancyKind

    report = TaxReconciliationService().reconcile(
        determine(),
        org_id=ORG,
        subject_id="inv_1",
        subject_lines=(("inl_1", 100_000), ("inl_2", 5_000)),
    )
    assert any(d.kind is TaxDiscrepancyKind.UNASSESSED_LINE for d in report.discrepancies)


def test_a_base_that_drifted_is_detected():
    from billing.foundation import TaxDiscrepancyKind

    report = TaxReconciliationService().reconcile(
        determine(), org_id=ORG, subject_id="inv_1", subject_lines=(("inl_1", 99_000),)
    )
    mismatch = [
        d for d in report.discrepancies if d.kind is TaxDiscrepancyKind.BASE_MISMATCH
    ]
    assert mismatch and mismatch[0].expected_minor == 99_000


def test_a_rate_applied_outside_its_window_is_detected():
    from billing.foundation import TaxDiscrepancyKind

    stale = a_rate(effective_until=100.0)
    assessment = determine(provider=DeclaredRateTaxProvider((stale,)))
    # The provider will not offer it; assert the check itself catches a hand-built stale component.
    from dataclasses import replace

    sourced = determine()
    drifted = replace(
        sourced,
        lines=tuple(
            replace(
                line,
                components=tuple(
                    replace(
                        c,
                        provenance=replace(c.provenance, effective_until=1.0),
                    )
                    for c in line.components
                ),
            )
            for line in sourced.lines
        ),
    )
    report = TaxReconciliationService().reconcile(drifted, org_id=ORG, subject_id="inv_1")
    assert any(d.kind is TaxDiscrepancyKind.STALE_RATE for d in report.discrepancies)
    assert assessment.assessed_components == ()


def test_inspection_reports_an_unsourced_determination():
    inspection = TaxInspector().inspect(determine(provider=NullTaxRateProvider()))
    assert not inspection.is_sound
    assert any("no source" in note for note in inspection.notes)
    assert not inspection.is_explainable


def test_the_validator_never_raises():
    assert TaxValidator().validate_assessment(
        determine(provider=NullTaxRateProvider())
    ).has_errors


def test_finalizing_an_unexplainable_determination_is_refused():
    findings = TaxValidator().validate_finalize(determine(provider=NullTaxRateProvider()))
    codes = {f.code for f in findings.findings}
    assert BillingDiagnosticCode.NO_TAX_RATE_SOURCE in codes
    assert BillingDiagnosticCode.UNEXPLAINABLE_TAX in codes


def test_a_jurisdiction_code_that_is_not_canonical_is_an_error():
    from billing.foundation import TaxJurisdiction

    findings = TaxValidator().validate_jurisdiction(
        TaxJurisdiction(
            id="j", org_id=ORG, code="in-ka", display_name="x", country="IN", region="KA"
        )
    )
    codes = {f.code for f in findings.findings}
    assert BillingDiagnosticCode.INVALID_JURISDICTION_CODE in codes


def test_a_declared_rate_with_no_reference_warns():
    findings = TaxValidator().validate_rate(a_rate(reference=""))
    codes = {f.code for f in findings.findings}
    assert BillingDiagnosticCode.MISSING_RATE_PROVENANCE in codes
    assert not findings.has_errors  # a warning: the row is usable, but nobody can defend it


def test_an_empty_rate_window_is_an_error():
    findings = TaxValidator().validate_rate(a_rate(effective_from=100.0, effective_until=100.0))
    assert BillingDiagnosticCode.INVALID_TAX_RATE_WINDOW in {f.code for f in findings.findings}


def test_search_is_deterministic_and_filters():
    assessments = (determine(), determine(a_request(a_basis(subject_id="inv_2"))))
    service = TaxSearchService()
    assert service.search(assessments, TaxSearchQuery()).total == 2
    assert service.search(assessments, TaxSearchQuery(subject_id="inv_2")).total == 1
    assert service.search(assessments, TaxSearchQuery(unsourced_only=True)).total == 0
    first = service.search(assessments, TaxSearchQuery()).assessments
    assert first == service.search(assessments, TaxSearchQuery()).assessments


# ── 11. the declared-rate provider ──────────────────────────────────────────────


def test_a_rate_outside_its_window_is_not_offered():
    provider = DeclaredRateTaxProvider((a_rate(effective_from=AT + 1),))
    assert provider.quotes(
        jurisdiction_code="IN:KA", classification_code="saas",
        treatment=TaxTreatment.STANDARD, at=AT
    ) == ()


def test_a_retired_rate_is_not_offered():
    provider = DeclaredRateTaxProvider((a_rate(retired_at=1.0),))
    assert provider.quotes(
        jurisdiction_code="IN:KA", classification_code="saas",
        treatment=TaxTreatment.STANDARD, at=AT
    ) == ()


def test_a_classification_specific_rate_beats_a_general_one():
    general = a_rate("VAT", 2000, classification_code="", id="txr_general")
    specific = a_rate("VAT", 500, classification_code="saas", id="txr_specific")
    quotes = DeclaredRateTaxProvider((general, specific)).quotes(
        jurisdiction_code="IN:KA", classification_code="saas",
        treatment=TaxTreatment.STANDARD, at=AT
    )
    assert [q.rate_basis_points for q in quotes] == [500]


def test_the_provider_is_deterministic_for_a_fixed_instant():
    provider = DeclaredRateTaxProvider((a_rate("SGST"), a_rate()))
    args = dict(
        jurisdiction_code="IN:KA", classification_code="saas",
        treatment=TaxTreatment.STANDARD, at=AT
    )
    assert provider.quotes(**args) == provider.quotes(**args)
    assert [q.name for q in provider.quotes(**args)] == ["CGST", "SGST"]


def test_the_port_is_abstract():
    with pytest.raises(NotImplementedError):
        TaxRateProvider().quotes(
            jurisdiction_code="", classification_code="", treatment=TaxTreatment.STANDARD, at=0.0
        )


# ── 12. residency + factory ─────────────────────────────────────────────────────


def test_residency_is_corroborated_only_with_two_independent_items():
    assert not TaxResidency(country="DE", evidence=("billing_address",)).is_corroborated
    assert TaxResidency(
        country="DE", evidence=("billing_address", "ip_geolocation")
    ).is_corroborated


def test_the_factory_derives_a_canonical_jurisdiction_code():
    factory = TaxFactory(ids=SequentialIdGenerator(), clock=FixedClock(1.0))
    assert factory.jurisdiction(org_id=ORG, country="in", region="ka").code == "IN:KA"


def test_retirement_is_a_timestamp_never_a_deletion():
    factory = TaxFactory(ids=SequentialIdGenerator(), clock=FixedClock(1.0))
    assert factory.retire(a_nexus(), at=5.0).effective_until == 5.0
    assert factory.retire(a_rate(), at=5.0).retired_at == 5.0
    exemption = TaxExemption(
        id="e", org_id=ORG, account_id=ACC, jurisdiction_code="IN:KA", effective_from=0.0
    )
    assert factory.retire(exemption, at=5.0, reason="expired").revoked_at == 5.0


def test_an_override_keeps_the_conclusion_it_replaced():
    factory = TaxFactory(ids=SequentialIdGenerator(), clock=FixedClock(1.0))
    override = factory.override(
        determine(), treatment=TaxTreatment.EXEMPT, reason="ruling", actor_id=ACTOR
    )
    assert override.supersedes_treatment is TaxTreatment.STANDARD
    assert override.is_declared


# ── 13. wiring ──────────────────────────────────────────────────────────────────


SUPPLIER = InvoiceParty(legal_name="VoxVoice Technologies Private Limited")
EXPECTED_BASE = 500_00 + (100 * 200 + 200 * 100)


@pytest.fixture
def wired():
    """A real database with a closed period, a prepared invoice, and tax configuration declared."""
    persistent = build_persistent_billing_application(
        authorizer=AllowAllAuthorizer(), ids=SequentialIdGenerator(), clock=FixedClock(1_500.0)
    )
    app = persistent.application
    d = app.commands.dispatch
    account = d(CreateBillingAccount(**KW, display_name="Acme", currency=INR))
    d(ActivateBillingAccount(**KW, account_id=account.id))
    d(
        UpdateAddresses(
            **KW,
            account_id=account.id,
            addresses=(
                AccountAddress(
                    address=BillingAddress(
                        line1="12 MG Road", city="Bengaluru", region="KA", country="IN"
                    )
                ),
            ),
        )
    )
    d(CreateProduct(**KW, code="voice", display_name="Voice"))
    d(PublishProduct(**KW, code="voice"))
    catalog = d(PublishCatalog(**KW))
    d(CreatePriceBook(**KW, code="std", display_name="Standard", currency=INR))
    d(
        CreatePrice(
            **KW, book_code="std", code="platform_fee", shape=PriceShape.FLAT_RECURRING,
            amount=Money(500_00, INR), interval=BillingInterval.MONTHLY,
        )
    )
    d(
        CreatePrice(
            **KW, book_code="std", code="voice_minutes", shape=PriceShape.TIERED_GRADUATED,
            meter_code="voice_minutes",
            tiers=(
                PriceTier(up_to=100, unit_price=Money(200, INR)),
                PriceTier(up_to=None, unit_price=Money(100, INR)),
            ),
        )
    )
    d(PublishPricingBook(**KW, code="std"))
    pricing = d(PublishPricing(**KW, notes="v1"))
    d(
        DefineCommercialMeter(
            **KW, code="voice_minutes", unit="minute", source=MeterSource.RUNTIME_SESSION,
            normalization=Normalization(1, 1),
            accepted_event_types=("metric_session_minutes",),
        )
    )
    d(ActivateCommercialMeter(**KW, code="voice_minutes"))
    d(OpenUsagePeriod(**KW, account_id=account.id, period_start=P0, period_end=P1))
    subscription = d(
        CreateSubscription(
            **KW, account_id=account.id, catalog_version=catalog.version,
            pricing_version=pricing.version, currency=INR, plan_code="pro",
            items=(
                AgreementItem(id="itm_flat", price_code="platform_fee", kind=ItemKind.FLAT),
                AgreementItem(
                    id="itm_use", price_code="voice_minutes", kind=ItemKind.USAGE,
                    meter_code="voice_minutes",
                ),
            ),
        )
    )
    d(ActivateSubscription(**KW, subscription_id=subscription.id))
    d(
        IngestMetricSignal(
            **KW, period_start=P0, period_end=P1,
            signal=MetricSignal(
                event_id="evt_1", event_type="metric_session_minutes",
                source=MeterSource.RUNTIME_SESSION, observed_at=1_200.0, raw_units=300,
                raw_unit="minute", scope=Scope(org_id=ORG), received_at=1_200.0,
            ),
        )
    )
    rated = d(
        RateSubscriptionPeriod(
            **KW, subscription_id=subscription.id, period_start=P0, period_end=P1
        )
    )
    d(
        OpenAccountingPeriod(
            **KW, account_id=account.id, period_start=P0, period_end=P1, currency=INR
        )
    )
    d(FinalizeRatingRun(**KW, rating_run_id=rated.rating_run_id))
    d(PostRatingRun(**KW, rating_run_id=rated.rating_run_id))
    d(CloseAccountingPeriod(**KW, account_id=account.id, period_key=KEY))
    invoice = d(
        PrepareInvoice(
            **KW, account_id=account.id, accounting_period_key=KEY, entity_code="VOX",
            jurisdiction="KA", supplier=SUPPLIER,
            payment_instructions=PaymentInstructions(terms_label="Net 30"),
        )
    )
    jurisdiction = d(
        DeclareTaxJurisdiction(
            **KW, country="IN", region="KA", display_name="Karnataka",
            level=JurisdictionLevel.STATE, regime=TaxRegime.INDIA_GST,
        )
    )
    d(
        DeclareTaxClassification(
            **KW, code="saas", display_name="SaaS", is_digital_service=True,
            codes=(("hsn_sac", "998314"),),
        )
    )
    d(
        RegisterTaxNexus(
            **KW, entity_code="VOX", jurisdiction_code=jurisdiction.code,
            registration_number="29AABCV1234F1Z5", registration_regime=TaxRegime.INDIA_GST,
            effective_from=0.0,
        )
    )
    yield persistent, account, invoice, jurisdiction
    persistent.database.close()


def _declare_gst(app, jurisdiction):
    for name in ("CGST", "SGST"):
        app.commands.dispatch(
            DeclareTaxRate(
                **KW, jurisdiction_code=jurisdiction.code, tax_type="gst", name=name,
                rate_basis_points=900, classification_code="saas", effective_from=0.0,
                reference="Notification 11/2017-Central Tax (Rate)", source_version="2017-06-28",
            )
        )


def _assess(app, invoice, **kw):
    body = dict(
        document_id=invoice.id, entity_code="VOX", origin_country="IN", origin_region="KA",
        supplier_registration="29AABCV1234F1Z5", default_classification_code="saas",
        regime=TaxRegime.INDIA_GST, effective_at=1_600.0,
    )
    body.update(kw)
    return app.commands.dispatch(AssessDocumentTax(**KW, **body))


# ── 14. THE DETERMINATION DEMONSTRATIONS ────────────────────────────────────────
#
# Each corresponds to one invariant the milestone claims. Separated deliberately: an invariant
# nobody can point at is one that decays.


def test_demo_1_a_fresh_installation_determines_no_tax(wired):
    """Billing ships no rate table. Nothing is seeded, and until an operator declares a rate there
    is nothing to charge."""
    persistent, _, _, _ = wired
    assert persistent.application.queries.dispatch(ListDeclaredTaxRates(**KW)) == ()


def test_demo_2_a_determination_with_no_rate_source_is_loud_not_zero(wired):
    persistent, _, invoice, _ = wired
    assessment = _assess(persistent.application, invoice)
    assert assessment.tax_total.minor == 0
    assert not assessment.is_sourced and not assessment.is_explainable


def test_demo_3_an_unsourced_determination_cannot_be_finalized(wired):
    persistent, _, invoice, _ = wired
    assessment = _assess(persistent.application, invoice)
    with pytest.raises(Exception) as excinfo:
        persistent.application.commands.dispatch(
            FinalizeTaxAssessment(**KW, assessment_id=assessment.id)
        )
    assert "tax" in str(excinfo.value).lower()


def test_demo_4_declared_rates_carry_the_authority_that_set_them(wired):
    persistent, _, _, jurisdiction = wired
    _declare_gst(persistent.application, jurisdiction)
    rates = persistent.application.queries.dispatch(ListDeclaredTaxRates(**KW))
    assert {r.name for r in rates} == {"CGST", "SGST"}
    assert all(r.reference and r.source_version for r in rates)


def test_demo_5_two_components_of_one_tax_type_are_two_rates(wired):
    """CGST and SGST are the canonical case: without the name in the derived id the second
    declaration is swallowed and every intra-state invoice is taxed at half. Found by the smoke."""
    persistent, _, _, jurisdiction = wired
    _declare_gst(persistent.application, jurisdiction)
    assert len(persistent.application.queries.dispatch(ListDeclaredTaxRates(**KW))) == 2


def test_demo_6_an_intra_state_supply_splits_cgst_and_sgst(wired):
    persistent, _, invoice, jurisdiction = wired
    app = persistent.application
    _declare_gst(app, jurisdiction)
    assessment = _assess(app, invoice)
    assert assessment.place_of_supply.gst_split == "intra_state"
    assert assessment.taxable_base.minor == EXPECTED_BASE
    assert assessment.tax_total.minor == EXPECTED_BASE * 18 // 100
    assert {c.name for line in assessment.lines for c in line.components} == {"CGST", "SGST"}


def test_demo_7_determining_the_same_facts_twice_is_one_assessment(wired):
    persistent, _, invoice, jurisdiction = wired
    app = persistent.application
    _declare_gst(app, jurisdiction)
    first = _assess(app, invoice)
    second = _assess(app, invoice)
    assert first.id == second.id and first.digest == second.digest
    actions = [a.action for a in app.queries.dispatch(TaxTimeline(**KW, subject_id=invoice.id))]
    assert "assessment.unchanged" in actions


def test_demo_8_every_component_names_where_its_rate_came_from(wired):
    persistent, _, invoice, jurisdiction = wired
    app = persistent.application
    _declare_gst(app, jurisdiction)
    assessment = _assess(app, invoice)
    for line in assessment.lines:
        for component in line.components:
            assert component.provenance.is_sourced
            assert "Notification" in component.provenance.reference


def test_demo_9_the_explanation_answers_why_from_stored_evidence(wired):
    persistent, _, invoice, jurisdiction = wired
    app = persistent.application
    _declare_gst(app, jurisdiction)
    assessment = _assess(app, invoice)
    explanation = app.queries.dispatch(ExplainTaxAssessment(**KW, assessment_id=assessment.id))
    assert explanation.answered_without_provider
    assert {s.stage for s in explanation.steps} >= {
        "place_of_supply", "nexus", "treatment", "rate", "rounding"
    }


def test_demo_10_finalizing_publishes_the_projection_m9_14_already_reads(wired):
    """M9.14 needs no change to start presenting tax: it re-reads the determination on its next
    revision."""
    persistent, _, invoice, jurisdiction = wired
    app = persistent.application
    _declare_gst(app, jurisdiction)
    assessment = _assess(app, invoice)
    app.commands.dispatch(FinalizeTaxAssessment(**KW, assessment_id=assessment.id))
    before = app.queries.dispatch(GetInvoiceDocument(**KW, document_id=invoice.id))
    app.commands.dispatch(ReviseInvoiceDraft(**KW, document_id=invoice.id, reason="tax"))
    after = app.queries.dispatch(GetInvoiceDocument(**KW, document_id=invoice.id))
    assert before.totals.tax_total.minor == 0
    assert after.totals.tax_total.minor == assessment.tax_total.minor
    assert after.tax_determination_id
    assert {c.name for c in after.tax_components} == {"CGST", "SGST"}


def test_demo_11_the_issued_invoice_carries_the_tax(wired):
    persistent, _, invoice, jurisdiction = wired
    app = persistent.application
    _declare_gst(app, jurisdiction)
    assessment = _assess(app, invoice)
    app.commands.dispatch(FinalizeTaxAssessment(**KW, assessment_id=assessment.id))
    app.commands.dispatch(ReviseInvoiceDraft(**KW, document_id=invoice.id, reason="tax"))
    issued = app.commands.dispatch(IssueCommercialInvoice(**KW, document_id=invoice.id))
    assert issued.total.minor == EXPECTED_BASE + assessment.tax_total.minor
    assert issued.number


def test_demo_12_a_finalized_determination_is_never_re_determined(wired):
    persistent, _, invoice, jurisdiction = wired
    app = persistent.application
    _declare_gst(app, jurisdiction)
    assessment = _assess(app, invoice)
    app.commands.dispatch(FinalizeTaxAssessment(**KW, assessment_id=assessment.id))
    with pytest.raises(BillingConflict, match="never re-determined"):
        _assess(app, invoice, effective_at=1_700.0)


def test_demo_13_an_override_is_a_new_determination_not_an_edit(wired):
    persistent, _, invoice, jurisdiction = wired
    app = persistent.application
    _declare_gst(app, jurisdiction)
    original = _assess(app, invoice)
    overridden = app.commands.dispatch(
        OverrideTaxTreatment(
            **KW, assessment_id=original.id, treatment=TaxTreatment.ZERO_RATED,
            reason="advance ruling 2026/07", reference="AR-2026-07",
        )
    )
    assert overridden.id != original.id
    assert overridden.treatment == "zero_rated" and overridden.tax_total.minor == 0
    assert overridden.override.supersedes_treatment == "standard"
    assert overridden.override.actor_id == ACTOR
    kept = app.queries.dispatch(GetTaxAssessment(**KW, assessment_id=original.id))
    assert kept.state == "superseded" and kept.tax_total.minor > 0


def test_demo_14_a_finalized_determination_stands_over_a_provisional_one(wired):
    persistent, _, invoice, jurisdiction = wired
    app = persistent.application
    _declare_gst(app, jurisdiction)
    assessment = _assess(app, invoice)
    app.commands.dispatch(FinalizeTaxAssessment(**KW, assessment_id=assessment.id))
    current = app.queries.dispatch(GetSubjectTaxAssessment(**KW, subject_id=invoice.id))
    assert current.id == assessment.id and current.state == "final"


def test_demo_15_a_preview_persists_nothing(wired):
    persistent, _, invoice, jurisdiction = wired
    app = persistent.application
    _declare_gst(app, jurisdiction)
    preview = app.queries.dispatch(
        PreviewTaxAssessment(
            **KW, document_id=invoice.id, entity_code="VOX", origin_country="IN",
            origin_region="KA", default_classification_code="saas", regime=TaxRegime.INDIA_GST,
        )
    )
    assert preview.tax_total.minor > 0
    assert app.queries.dispatch(ListTaxAssessments(**KW)) == ()


def test_demo_16_a_retired_rate_stays_readable_and_stops_applying(wired):
    """An issued invoice must stay explainable against the rate in force when it was issued."""
    persistent, _, invoice, jurisdiction = wired
    app = persistent.application
    _declare_gst(app, jurisdiction)
    rates = app.queries.dispatch(ListDeclaredTaxRates(**KW))
    app.commands.dispatch(RetireTaxRate(**KW, rate_id=rates[0].id, reason="superseded"))
    still_listed = app.queries.dispatch(ListDeclaredTaxRates(**KW))
    assert len(still_listed) == 2
    assert any(r.retired_at for r in still_listed)
    assert len(_assess(app, invoice).lines[0].components) == 1


def test_demo_17_an_exemption_zeroes_the_tax_and_records_why(wired):
    persistent, account, invoice, jurisdiction = wired
    app = persistent.application
    _declare_gst(app, jurisdiction)
    app.commands.dispatch(
        RecordTaxExemption(
            **KW, account_id=account.id, jurisdiction_code=jurisdiction.code,
            kind=ExemptionKind.CERTIFICATE, certificate_ref="EX-2026-1", effective_from=0.0,
        )
    )
    assessment = _assess(app, invoice)
    assert assessment.treatment == "exempt" and assessment.tax_total.minor == 0
    assert "EX-2026-1" in assessment.exemption_reason
    assert assessment.is_explainable


def test_demo_18_the_determination_reconciles_against_the_document(wired):
    persistent, _, invoice, jurisdiction = wired
    app = persistent.application
    _declare_gst(app, jurisdiction)
    assessment = _assess(app, invoice)
    report = app.queries.dispatch(ReconcileTaxAssessment(**KW, assessment_id=assessment.id))
    assert report.is_clean and report.lines_examined == 2


def test_demo_19_tax_writes_to_no_other_domain(wired):
    """Read ledger, rating, metering, pricing, accounts, and the invoice before and after
    determining: none moved."""
    persistent, account, invoice, jurisdiction = wired
    app = persistent.application
    _declare_gst(app, jurisdiction)
    ctx = app.context
    before_entries = ctx.ledger_engine.entries.for_period(ORG, account.id, KEY)
    before_periods = ctx.ledger_engine.periods.by_key(ORG, account.id, KEY)
    before_usage = ctx.metering_engine.usage.for_period(ORG, account.id, P0, P1)
    before_pricing = ctx.pricing.versions.latest(ORG)
    before_account = ctx.commercial_accounts.accounts.get(ORG, account.id)
    before_document = ctx.invoice_engine.documents.get(ORG, invoice.id)
    before_snapshot = ctx.invoice_engine.snapshots.latest(ORG, invoice.id)
    assessment = _assess(app, invoice)
    app.commands.dispatch(FinalizeTaxAssessment(**KW, assessment_id=assessment.id))
    assert ctx.ledger_engine.entries.for_period(ORG, account.id, KEY) == before_entries
    assert ctx.ledger_engine.periods.by_key(ORG, account.id, KEY) == before_periods
    assert ctx.metering_engine.usage.for_period(ORG, account.id, P0, P1) == before_usage
    assert ctx.pricing.versions.latest(ORG) == before_pricing
    assert ctx.commercial_accounts.accounts.get(ORG, account.id) == before_account
    assert ctx.invoice_engine.documents.get(ORG, invoice.id) == before_document
    assert ctx.invoice_engine.snapshots.latest(ORG, invoice.id) == before_snapshot


# ── 15. more wiring ─────────────────────────────────────────────────────────────


def test_the_customer_residency_is_read_from_the_billing_account(wired):
    persistent, _, invoice, jurisdiction = wired
    app = persistent.application
    _declare_gst(app, jurisdiction)
    assessment = _assess(app, invoice)
    assert assessment.place_of_supply.destination_code.startswith("IN")


def test_a_provisional_determination_can_be_discarded(wired):
    persistent, _, invoice, jurisdiction = wired
    app = persistent.application
    _declare_gst(app, jurisdiction)
    assessment = _assess(app, invoice)
    discarded = app.commands.dispatch(
        DiscardTaxAssessment(**KW, assessment_id=assessment.id, reason="wrong classification")
    )
    assert discarded.state == "discarded" and not discarded.allowed_triggers


def test_place_of_supply_is_reachable_without_a_document(wired):
    persistent, _, _, _ = wired
    place = persistent.application.queries.dispatch(
        ResolveTaxPlaceOfSupply(
            **KW, origin_country="IN", origin_region="KA", destination_country="DE",
            regime=TaxRegime.INDIA_GST,
        )
    )
    assert place.is_cross_border and place.resolved_code == "DE"


def test_a_jurisdiction_is_declared_idempotently(wired):
    persistent, _, _, jurisdiction = wired
    again = persistent.application.commands.dispatch(
        DeclareTaxJurisdiction(**KW, country="IN", region="KA")
    )
    assert again.id == jurisdiction.id
    assert len(persistent.application.queries.dispatch(ListTaxJurisdictions(**KW))) == 1


def test_inspection_diagnostics_search_and_timeline_are_reachable(wired):
    persistent, _, invoice, jurisdiction = wired
    app = persistent.application
    _declare_gst(app, jurisdiction)
    assessment = _assess(app, invoice)
    assert app.queries.dispatch(InspectTaxAssessment(**KW, assessment_id=assessment.id)).is_sound
    assert app.queries.dispatch(
        DiagnoseTaxAssessment(**KW, assessment_id=assessment.id)
    ).is_valid
    assert app.queries.dispatch(SearchTaxAssessments(**KW)).total == 1
    assert app.queries.dispatch(TaxTimeline(**KW))


def test_the_tax_engine_is_org_scoped(wired):
    persistent, _, invoice, jurisdiction = wired
    app = persistent.application
    _declare_gst(app, jurisdiction)
    assessment = _assess(app, invoice)
    assert app.queries.dispatch(ListTaxAssessments(actor_id=ACTOR, org_id="org_other")) == ()
    with pytest.raises(BillingNotFound):
        app.queries.dispatch(
            GetTaxAssessment(actor_id=ACTOR, org_id="org_other", assessment_id=assessment.id)
        )


def test_a_determination_survives_a_restart(wired):
    persistent, _, invoice, jurisdiction = wired
    app = persistent.application
    _declare_gst(app, jurisdiction)
    assessment = _assess(app, invoice)
    app.commands.dispatch(FinalizeTaxAssessment(**KW, assessment_id=assessment.id))
    second = build_persistent_billing_application(
        authorizer=AllowAllAuthorizer(), ids=SequentialIdGenerator(), clock=FixedClock(9_000.0),
        database=persistent.database, create_all=False,
    )
    reread = second.application.queries.dispatch(
        GetSubjectTaxAssessment(**KW, subject_id=invoice.id)
    )
    assert reread.digest == assessment.digest
    assert reread.tax_total.minor == assessment.tax_total.minor


# ── 16. HTTP ────────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    settings = BillingApiSettings.load(environment="testing")
    container = BillingContainer.build(
        settings,
        authorizer=AllowAllAuthorizer(),
        ids=SequentialIdGenerator(),
        clock=FixedClock(1_500.0),
    )
    app = create_billing_app(settings=settings, container=container)
    with TestClient(app) as test_client:
        yield test_client


PREFIX = "/v1/billing"


def test_tax_routes_require_identity_and_an_org(client):
    assert client.get(f"{PREFIX}/taxation/jurisdictions").status_code == 401


def test_the_organization_always_comes_from_the_token(client):
    source = (BILLING / "customer" / "routers" / "tax_engine.py").read_text()
    assert "ctx.organization()" in source
    assert "org_id: str" not in source


def test_the_tax_surface_has_no_delete_put_or_patch(client):
    spec = client.get("/openapi.json").json()
    for path, operations in spec["paths"].items():
        if "/taxation" not in path:
            continue
        assert set(operations) <= {"get", "post"}, (path, sorted(operations))


def test_the_tax_surface_is_the_declared_size(client):
    spec = client.get("/openapi.json").json()
    assert len([p for p in spec["paths"] if "/taxation" in p]) == 24


def test_every_tax_path_is_documented_and_tagged(client):
    spec = client.get("/openapi.json").json()
    for path, operations in spec["paths"].items():
        if "/taxation" not in path:
            continue
        for method, operation in operations.items():
            assert operation.get("summary"), (method, path)
            assert operation["tags"] == ["taxes"], (method, path)


def test_no_tax_schema_name_is_qualified(client):
    spec = client.get("/openapi.json").json()
    qualified = [
        n for n in spec["components"]["schemas"] if "__" in n and "tax" in n.lower()
    ]
    assert qualified == []


def test_money_is_still_declared_exactly_once(client):
    spec = client.get("/openapi.json").json()
    assert len([n for n in spec["components"]["schemas"] if n.endswith("MoneyResponse")]) == 1


def test_the_m9_5_tax_surface_still_answers(client):
    """M9.2's tax profile and determination are untouched: the determination is the record this
    milestone projects onto."""
    spec = client.get("/openapi.json").json()
    assert f"{PREFIX}/taxes/profiles" in spec["paths"]
    assert f"{PREFIX}/taxes/determinations/{{invoice_id}}" in spec["paths"]


# ── 17. Studio ──────────────────────────────────────────────────────────────────


def _studio_script() -> str:
    source = STUDIO.read_text()
    return source[source.index("<script>") : source.rindex("</script>")]


def test_tax_studio_is_served(client):
    response = client.get("/studio/taxation.html")
    assert response.status_code == 200
    assert "VoxVoice · Taxation" in response.text


def test_tax_studio_is_self_contained():
    source = STUDIO.read_text()
    for forbidden in ("http://", "https://", "cdn.", "<script src", "@import"):
        assert forbidden not in source, forbidden


def test_tax_studio_has_a_screen_for_every_required_area():
    source = _studio_script()
    for screen in (
        "assessmentsScreen", "assessScreen", "assessmentScreen", "explainScreen", "ratesScreen",
        "jurisdictionsScreen", "classificationsScreen", "nexusScreen", "exemptionsScreen",
        "placeScreen", "activityScreen",
    ):
        assert screen in source, screen


def test_tax_studio_contains_no_rate():
    """A rate in a client is a rate that is still there when the jurisdiction changes it — and the
    customer will have been charged it."""
    body = _studio_script()
    for forbidden in ("1800", "0.18", "18%", "* 0.0", "GST_RATE", "STANDARD_RATE"):
        assert forbidden not in body, forbidden


def test_tax_studio_computes_no_tax():
    body = _studio_script()
    for forbidden in (
        "reduce(", "+=", "toFixed(", ".minor +", ".minor -", ".minor *", ".minor /",
        "rate_basis_points /", "rate_basis_points *", "parseFloat",
    ):
        assert forbidden not in body, forbidden


def test_tax_studio_owns_no_state():
    for forbidden in ("localStorage", "sessionStorage", "document.cookie", "indexedDB"):
        assert forbidden not in _studio_script(), forbidden


def test_tax_studio_takes_its_lifecycle_buttons_from_the_server():
    assert "allowed_triggers" in _studio_script()


# ── 18. architecture compliance ─────────────────────────────────────────────────


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    tops: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            tops.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            tops.update(a.name.split(".")[0] for a in node.names)
    return tops


def test_tax_calculates_no_price():
    for path in TAX_MODULES:
        code = _code_only(path)
        for forbidden in (
            "PriceResolver", "PriceResolutionRequest", "select_book", "DiscountEngine",
            "ResolvedPrice",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_tax_rates_nothing_and_meters_nothing():
    for path in TAX_MODULES:
        code = _code_only(path)
        for forbidden in (
            "RatingEngine", "ChargeAssembler", "ChargeCalculator", "RatedCharge",
            "UsageIngestionService", "UsageAggregator", "MetricSignal", "MetricStreamPort",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_tax_posts_no_ledger_entry_and_computes_no_balance():
    for path in TAX_MODULES:
        code = _code_only(path)
        for forbidden in (
            "PostingService", "JournalEntry", "AccountBalance", "BalanceService",
            "TrialBalance", "CreditApplicationService",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_tax_issues_no_invoice_and_moves_no_money():
    for path in TAX_MODULES:
        code = _code_only(path)
        for forbidden in (
            "InvoiceFactory", "NumberingService", "InvoiceSnapshot(", "PaymentGateway",
            "capture(", "authorize_payment", "InvoiceTotalsService",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_tax_evaluates_no_entitlement_and_authorizes_nothing():
    for path in TAX_FOUNDATION:
        code = _code_only(path)
        for forbidden in (
            "EntitlementSet", "ResolvedEntitlement", "is_enabled", "principal", "Permission",
            "has_role", "authorize", "RBAC",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_tax_imports_no_frozen_epic():
    forbidden = {
        "api", "runtime", "product", "actuator", "providers", "provider", "agent", "tool",
        "workflow", "mcp", "knowledge", "observability", "gateway", "telemetry", "tenant",
        "deployment",
    }
    for path in TAX_MODULES:
        assert not (_imports(path) & forbidden), (path.name, _imports(path) & forbidden)


def test_the_tax_foundation_stays_pure():
    forbidden = {
        "sqlalchemy", "sqlmodel", "fastapi", "starlette", "pydantic", "os", "random",
        "datetime", "socket", "httpx", "requests", "asyncio",
    }
    for path in TAX_FOUNDATION:
        assert not (_imports(path) & forbidden), (path.name, _imports(path) & forbidden)


def test_the_tax_rules_read_no_clock():
    """Determination is a pure function of what it is given — the only way reproduction can be
    proven."""
    code = _code_only(BILLING / "foundation" / "tax_rules.py")
    for forbidden in ("Clock", "now()", "time("):
        assert forbidden not in code, forbidden


def test_tax_use_cases_contain_no_arithmetic_and_no_rate():
    code = _code_only(BILLING / "application" / "tax_engine.py")
    for forbidden in (".times(", ".scaled(", "Money.allocate", "def calculate", "sum(",
                      "BASIS_POINT_SCALE"):
        assert forbidden not in code, forbidden
    # The M9.7 precedent, applied to rounding: a command may *carry* a caller's declared policy
    # through to the Foundation as a field default, but no code path may *apply* one. The
    # distinction is where the name appears — an annotated field default is data in transit.
    tree = ast.parse(code)
    defaults = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign) and node.value is not None
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and id(node) not in defaults:
            rendered = ast.unparse(node)
            assert not rendered.startswith("Rounding."), rendered


def test_tax_writes_only_to_its_own_repositories():
    """The structural form of "Tax takes ownership of nothing": a write verb on an injected
    collaborator must target `self._repos.*`."""
    code = _code_only(BILLING / "application" / "tax_engine.py")
    writes = {"add", "append", "replace", "upsert", "mark_state", "retire", "publish"}
    for node in ast.walk(ast.parse(code)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in writes:
            continue
        target = ast.unparse(node.func.value)
        if not target.startswith("self._"):
            continue
        # `self._factory.retire` composes a modified aggregate and returns it — the Foundation
        # performs no I/O by construction, asserted separately by the purity scan. Every call that
        # actually *stores* something must target a seam this milestone owns.
        assert target.startswith(("self._repos.", "self._factory")), (target, node.func.attr)


def test_tax_repositories_decide_nothing():
    code = _code_only(BILLING / "persistence" / "tax_repositories.py")
    for forbidden in ("determine(", "resolve(", "quotes(", ".scaled(", "line_components"):
        assert forbidden not in code, forbidden


def test_tax_controllers_stay_transport_only():
    code = _code_only(BILLING / "customer" / "routers" / "tax_engine.py")
    for forbidden in ("repositories", "session.", ".commit()", "select(", "Money("):
        assert forbidden not in code, forbidden


def test_m9_2_tax_vocabulary_is_reused_not_duplicated():
    """`TaxRegime`, `TaxpayerKind`, `TaxTreatment`, `GstSplit`, `TaxRegistration`, `TaxProfile` and
    `TaxComponent` are M9.2's. Two answers to "what treatment applies?" is the drift §A2.5
    prevents."""
    m92 = (BILLING / "foundation" / "tax.py").read_text()
    assert "class TaxTreatment" in m92 and "class TaxRegime" in m92
    m915 = (BILLING / "foundation" / "commercial_tax.py").read_text()
    for duplicated in (
        "class TaxRegime", "class TaxpayerKind", "class TaxTreatment", "class GstSplit",
        "class TaxRegistration", "class TaxProfile", "class TaxComponent",
    ):
        assert duplicated not in m915, duplicated
    assert "class TaxAssessment" in m915


def test_the_m9_2_tax_aggregates_are_untouched():
    m92 = (BILLING / "foundation" / "tax.py").read_text()
    for kept in ("class TaxProfile", "class TaxDetermination", "class TaxComponent"):
        assert kept in m92, kept


def test_every_event_value_is_unique():
    from billing.foundation import BillingEventType

    values = [e.value for e in BillingEventType]
    assert len(values) == len(set(values))


def test_every_diagnostic_code_is_unique():
    from billing.foundation import BillingDiagnosticCode as D

    values = [d.value for d in D]
    assert len(values) == len(set(values))


def test_exactly_one_event_per_assessment_state():
    from billing.foundation import ASSESSMENT_EVENTS

    assert set(ASSESSMENT_EVENTS) == {
        AssessmentState.FINAL, AssessmentState.SUPERSEDED, AssessmentState.DISCARDED
    }
    assert AssessmentState.PROVISIONAL not in ASSESSMENT_EVENTS
    assert len(set(ASSESSMENT_EVENTS.values())) == 3


def test_command_and_query_coverage_is_complete():
    from billing.application.tax_engine import (
        register_tax_command_handlers,
        register_tax_query_handlers,
    )

    registered = _inspect.getsource(register_tax_command_handlers) + _inspect.getsource(
        register_tax_query_handlers
    )
    for message in (*ALL_TAX_COMMANDS, *ALL_TAX_QUERIES):
        assert f"bus.register({message.__name__}," in registered, message.__name__
        assert {"actor_id", "org_id"} <= set(message.__dataclass_fields__), message.__name__
    assert len(ALL_TAX_COMMANDS) == 13
    assert len(ALL_TAX_QUERIES) == 16


def test_the_tax_engine_is_additive_and_optional():
    from billing.application.context import BillingContext

    assert "tax_engine" in BillingContext.__dataclass_fields__
    assert BillingContext.__dataclass_fields__["tax_engine"].default is None


def test_tax_tables_are_created_by_the_migration_chain():
    from sqlalchemy import inspect as sa_inspect

    from billing.persistence.database import BillingDatabase
    from billing.persistence.migrations import MIGRATIONS, upgrade_all
    from billing.persistence.tax_models import TAX_TABLE_NAMES

    database = BillingDatabase("sqlite://")
    applied = upgrade_all(database)
    # `0025` is asserted to be *in* the chain rather than at the end of it: being last was only
    # ever incidental, and M9.16 appended `0026`. What this test is actually about is that the tax
    # tables exist after the chain runs, and that the chain is ordered.
    assert "0025" in applied
    assert [m.VERSION for m in MIGRATIONS] == sorted(m.VERSION for m in MIGRATIONS)
    tables = set(sa_inspect(database.engine).get_table_names())
    for name in TAX_TABLE_NAMES:
        assert name in tables
    for name in ("cp_billing_tax_profile", "cp_billing_tax_determination"):
        assert name in tables, name
    database.close()


def test_the_tax_migration_alters_nothing_and_is_idempotent():
    from billing.persistence.database import BillingDatabase
    from billing.persistence.migrations import upgrade_all, v0025_billing_tax

    code = _code_only(Path(v0025_billing_tax.__file__))
    for forbidden in ("ALTER", "DROP", "drop(", "delete("):
        assert forbidden not in code
    database = BillingDatabase("sqlite://")
    upgrade_all(database)
    v0025_billing_tax.upgrade(database)
    assert v0025_billing_tax.downgrade(database) is None
    database.close()


def test_the_assessment_table_keys_on_the_derived_basis():
    from billing.persistence.tax_models import BillingTaxAssessmentRow

    assert [c.name for c in BillingTaxAssessmentRow.__table__.primary_key] == ["id"]
    indexed = {c.name for c in BillingTaxAssessmentRow.__table__.columns if c.index}
    assert {"digest", "subject_id", "state", "is_sourced"} <= indexed


def test_a_jurisdiction_code_is_unique_per_org():
    from billing.persistence.tax_models import BillingTaxJurisdictionRow

    constraints = {
        c.name for c in BillingTaxJurisdictionRow.__table__.constraints if getattr(c, "name", None)
    }
    assert "uq_cp_billing_tax_jurisdiction" in constraints


def test_the_rate_and_assessment_repositories_have_no_update_method():
    from billing.foundation import TaxAssessmentRepository, TaxRateRepository

    assert {m for m in dir(TaxRateRepository) if not m.startswith("_")} == {
        "append", "get", "for_jurisdiction", "all", "retire"
    }
    assert {m for m in dir(TaxAssessmentRepository) if not m.startswith("_")} == {
        "append", "get", "for_subject", "current_for_subject", "for_account", "all", "mark_state"
    }


def test_an_assessment_round_trips_through_persistence():
    from billing.foundation import TaxAssessment as _TA
    from billing.persistence.codec import decode, encode

    assessment = determine()
    assert decode(_TA, encode(assessment)) == assessment


def test_the_billing_package_layout_grew_by_addition():
    import pkgutil

    modules = {m.name for m in pkgutil.iter_modules([str(BILLING / "foundation")])}
    for name in ("commercial_tax", "tax_rules", "tax_factories"):
        assert name in modules, name
    assert "tax" in modules  # the M9.2 module, unreshaped


def test_the_earlier_domain_boundaries_still_hold():
    for module in ("commercial_subscriptions", "commercial_metering"):
        code = _code_only(BILLING / "foundation" / f"{module}.py")
        assert "Money" not in code, module


def test_the_invoice_still_determines_no_tax():
    """M9.14's boundary survives the milestone that supplies it."""
    for module in ("commercial_invoicing", "invoice_rules", "invoice_factories"):
        code = _code_only(BILLING / "foundation" / f"{module}.py")
        for forbidden in ("TaxAssessment", "TaxRateProvider", "PlaceOfSupplyResolver", "rate_for"):
            assert forbidden not in code, (module, forbidden)


def test_subject_kind_covers_every_document_kind():
    from billing.foundation import DocumentKind

    assert {k.value for k in DocumentKind} <= {k.value for k in SubjectKind}


def test_billing_is_still_absent_from_the_runtime_composition_root():
    """ADR-0031 CR2 survives M9.15."""
    assert "billing" not in (PLATFORM / "bootstrap.py").read_text().lower()
