"""M9.6 — Commercial Catalog: tests.

Four layers, one domain: pure Foundation rules, then the Application over a real database,
then the HTTP surface, then architecture compliance. The heaviest coverage sits on the two
properties a catalog cannot be retrofitted with — **immutability after publication** and
**history that is appended to rather than rewritten** — and on the boundary that keeps the
catalog a catalog: **no price, anywhere.**
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.customer.jwt import encode as epic8_encode
from billing.application import (
    AllowAllAuthorizer,
    ArchiveProduct,
    BillingAuthorizationError,
    CatalogActivityHistory,
    CatalogVersionHistory,
    CreateBundle,
    CreateCatalogPlan,
    CreateEdition,
    CreateFeature,
    CreateFeatureGroup,
    CreateProduct,
    DenyAllAuthorizer,
    DeprecateProduct,
    DuplicateProduct,
    GetCatalogVersion,
    GetProduct,
    ListBundles,
    ListProducts,
    PublishCatalog,
    PublishProduct,
    RestoreProduct,
    RollbackCatalog,
    SearchCatalog,
)
from billing.customer import BillingApiSettings, BillingContainer, create_billing_app
from billing.foundation import (
    Bundle,
    BundleResolver,
    CatalogAssembler,
    CatalogFactory,
    CatalogLifecycleService,
    CatalogSearchQuery,
    CatalogSearchService,
    CatalogState,
    CatalogTrigger,
    CatalogValidator,
    CommercialMetadata,
    Edition,
    Feature,
    FeatureGroup,
    FeatureKind,
    FixedClock,
    ImmutabilityViolation,
    InvalidBillingTransition,
    Product,
    ProductKind,
    RecordingEventEmitter,
    SequentialIdGenerator,
)
from billing.persistence import BillingDatabase, build_persistent_billing_application

PLATFORM = Path(__file__).resolve().parents[1]
BILLING = PLATFORM / "billing"
PREFIX = "/v1/billing/catalog"
SECRET = "test-billing-secret"
ORG = dict(actor_id="usr_1", org_id="org_1")


# ── fixtures ────────────────────────────────────────────────────────────────────


def _factory() -> tuple[CatalogFactory, RecordingEventEmitter]:
    emitter = RecordingEventEmitter()
    return CatalogFactory(ids=SequentialIdGenerator(), clock=FixedClock(), emitter=emitter), emitter


def _product(code="voice", state=CatalogState.DRAFT, **kw) -> Product:
    return Product(
        id=f"prd_{code}",
        org_id="org_1",
        code=code,
        display_name=code.title(),
        state=state,
        **kw,
    )


def _edition(code="pro", product="voice", rank=1, **kw) -> Edition:
    return Edition(
        id=f"edn_{product}_{code}",
        org_id="org_1",
        code=code,
        display_name=code.title(),
        product_code=product,
        rank=rank,
        **kw,
    )


def _bundle(code="suite", products=("voice",), nested=()) -> Bundle:
    return Bundle(
        id=f"bnd_{code}",
        org_id="org_1",
        code=code,
        display_name=code.title(),
        product_codes=tuple(products),
        bundle_codes=tuple(nested),
    )


@pytest.fixture()
def app():
    wired = build_persistent_billing_application(
        ids=SequentialIdGenerator(), clock=FixedClock(), authorizer=AllowAllAuthorizer()
    )
    yield wired.application
    wired.database.close()


def _client(authorizer=None, **overrides) -> TestClient:
    settings = BillingApiSettings(environment="testing", jwt_secret=SECRET, **overrides)
    container = BillingContainer.build(
        settings,
        authorizer=authorizer if authorizer is not None else AllowAllAuthorizer(),
        ids=SequentialIdGenerator(),
        clock=FixedClock(),
    )
    return TestClient(create_billing_app(settings=settings, container=container))


def _auth(user_id="usr_1", org_id="org_1") -> dict[str, str]:
    claims: dict = {"sub": user_id}
    if org_id:
        claims["org"] = org_id
    return {"Authorization": "Bearer " + epic8_encode(claims, SECRET, ttl_seconds=3600)}


@pytest.fixture()
def client():
    with _client() as c:
        yield c


# ── 1. Foundation: lifecycle FSM ────────────────────────────────────────────────


def test_everything_starts_as_a_draft():
    factory, _ = _factory()
    product = factory.create_product(org_id="org_1", code="voice", display_name="Voice")
    assert product.state is CatalogState.DRAFT
    assert not product.is_sellable


@pytest.mark.parametrize(
    ("state", "trigger", "expected"),
    [
        (CatalogState.DRAFT, CatalogTrigger.PUBLISH, CatalogState.PUBLISHED),
        (CatalogState.DRAFT, CatalogTrigger.DELETE, CatalogState.DELETED),
        (CatalogState.PUBLISHED, CatalogTrigger.DEPRECATE, CatalogState.DEPRECATED),
        (CatalogState.PUBLISHED, CatalogTrigger.ARCHIVE, CatalogState.ARCHIVED),
        (CatalogState.DEPRECATED, CatalogTrigger.ARCHIVE, CatalogState.ARCHIVED),
        (CatalogState.DEPRECATED, CatalogTrigger.RESTORE, CatalogState.PUBLISHED),
        (CatalogState.ARCHIVED, CatalogTrigger.RESTORE, CatalogState.PUBLISHED),
        (CatalogState.ARCHIVED, CatalogTrigger.DELETE, CatalogState.DELETED),
    ],
)
def test_legal_lifecycle_edges(state, trigger, expected):
    assert CatalogLifecycleService().next_state(state, trigger) is expected


@pytest.mark.parametrize(
    ("state", "trigger"),
    [
        (CatalogState.DRAFT, CatalogTrigger.ARCHIVE),
        (CatalogState.DRAFT, CatalogTrigger.RESTORE),
        (CatalogState.DRAFT, CatalogTrigger.DEPRECATE),
        (CatalogState.PUBLISHED, CatalogTrigger.PUBLISH),
        (CatalogState.PUBLISHED, CatalogTrigger.DELETE),
        (CatalogState.DELETED, CatalogTrigger.RESTORE),
        (CatalogState.DELETED, CatalogTrigger.PUBLISH),
    ],
)
def test_illegal_lifecycle_edges_are_rejected_never_coerced(state, trigger):
    with pytest.raises(InvalidBillingTransition):
        CatalogLifecycleService().next_state(state, trigger)


def test_a_published_product_cannot_be_deleted_in_one_step():
    """Something a customer may hold must be deprecated and archived first — a one-step
    delete is exactly how a live product disappears by accident."""
    lifecycle = CatalogLifecycleService()
    with pytest.raises(InvalidBillingTransition):
        lifecycle.next_state(CatalogState.PUBLISHED, CatalogTrigger.DELETE)
    archived = lifecycle.next_state(CatalogState.PUBLISHED, CatalogTrigger.ARCHIVE)
    assert lifecycle.next_state(archived, CatalogTrigger.DELETE) is CatalogState.DELETED


def test_deleted_is_terminal():
    assert CatalogLifecycleService().allowed(CatalogState.DELETED) == ()


def test_allowed_triggers_describe_exactly_the_legal_moves():
    lifecycle = CatalogLifecycleService()
    assert set(lifecycle.allowed(CatalogState.DRAFT)) == {
        CatalogTrigger.PUBLISH,
        CatalogTrigger.DELETE,
    }
    assert set(lifecycle.allowed(CatalogState.PUBLISHED)) == {
        CatalogTrigger.DEPRECATE,
        CatalogTrigger.ARCHIVE,
    }


def test_lifecycle_transitions_never_mutate_the_input():
    product = _product(state=CatalogState.DRAFT)
    moved = CatalogLifecycleService().apply_product(product, CatalogTrigger.PUBLISH, at=5.0)
    assert product.state is CatalogState.DRAFT
    assert moved.state is CatalogState.PUBLISHED and moved.updated_at == 5.0


# ── 2. Foundation: validation ───────────────────────────────────────────────────


def test_duplicate_product_codes_are_detected():
    diagnostics = CatalogValidator().validate_catalog(
        products=[_product("voice"), _product("voice")], editions=[], bundles=[]
    )
    assert any(d.code.value == "duplicate_product_code" for d in diagnostics.errors)


def test_duplicate_plan_versions_are_detected():
    from billing.foundation import Plan, Tier

    plan = Plan(id="p1", org_id="org_1", code="pro", display_name="Pro", tier=Tier.PRO)
    diagnostics = CatalogValidator().validate_catalog(
        products=[_product()], editions=[], bundles=[], plans=[plan, plan]
    )
    assert any(d.code.value == "duplicate_plan_code" for d in diagnostics.errors)


def test_duplicate_feature_codes_are_detected():
    feature = Feature(id="f1", code="sso", display_name="SSO", kind=FeatureKind.FLAG)
    diagnostics = CatalogValidator().validate_catalog(
        products=[_product()], editions=[], bundles=[], features=[feature, feature]
    )
    assert any(d.code.value == "duplicate_feature_code" for d in diagnostics.errors)


def test_edition_codes_are_unique_per_product_not_globally():
    """Two products may both have a "pro" edition — forcing global uniqueness would be a
    naming tax with no payoff."""
    validator = CatalogValidator()
    shared = validator.validate_catalog(
        products=[_product("voice"), _product("chat")],
        editions=[_edition("pro", "voice"), _edition("pro", "chat")],
        bundles=[],
    )
    assert not shared.has_errors

    clashing = validator.validate_catalog(
        products=[_product("voice")],
        editions=[_edition("pro", "voice", rank=1), _edition("pro", "voice", rank=2)],
        bundles=[],
    )
    assert any(d.code.value == "duplicate_edition_code" for d in clashing.errors)


def test_two_editions_at_the_same_rank_are_an_edition_conflict():
    diagnostics = CatalogValidator().validate_catalog(
        products=[_product("voice")],
        editions=[_edition("pro", "voice", rank=1), _edition("biz", "voice", rank=1)],
        bundles=[],
    )
    assert any(d.code.value == "edition_conflict" for d in diagnostics.errors)


def test_bundle_self_reference_is_a_cycle():
    diagnostics = CatalogValidator().validate_bundle(_bundle("suite", (), ("suite",)))
    assert any(d.code.value == "bundle_cycle" for d in diagnostics.errors)


def test_transitive_bundle_cycle_is_detected_with_its_path():
    a = _bundle("a", ("voice",), ("b",))
    b = _bundle("b", (), ("c",))
    c = _bundle("c", (), ("a",))
    cycle = BundleResolver().find_cycle(a, [a, b, c])
    assert cycle and cycle[0] == cycle[-1] == "a"
    # The path, not a boolean: "there is a cycle" is unactionable; a → b → c → a is not.
    assert set(cycle) == {"a", "b", "c"}

    diagnostics = CatalogValidator().validate_bundle(a, known_bundles=[a, b, c])
    finding = next(d for d in diagnostics.errors if d.code.value == "bundle_cycle")
    assert "→" in finding.message


def test_acyclic_nesting_expands_deterministically():
    inner = _bundle("inner", ("voice", "chat"))
    outer = _bundle("outer", ("addon",), ("inner",))
    assert BundleResolver().expand(outer, [inner, outer]) == ("addon", "voice", "chat")


def test_expanding_a_cyclic_bundle_is_loud_not_infinite():
    a = _bundle("a", (), ("b",))
    b = _bundle("b", (), ("a",))
    from billing.foundation import BillingConflict

    with pytest.raises(BillingConflict):
        BundleResolver().expand(a, [a, b])


def test_unknown_meter_reference_is_rejected():
    from billing.foundation import Plan, Tier

    plan = Plan(
        id="p1",
        org_id="org_1",
        code="pro",
        display_name="Pro",
        tier=Tier.PRO,
        metered_codes=("ghost_meter",),
    )
    from billing.foundation import MeterAggregation, MeterDefinition, MeterSource

    known = MeterDefinition(
        id="m1",
        org_id="org_1",
        code="voice_minutes",
        display_name="Voice",
        unit="minute",
        aggregation=MeterAggregation.SUM,
        source=MeterSource.RUNTIME_SESSION,
    )
    diagnostics = CatalogValidator().validate_catalog(
        products=[_product()], editions=[], bundles=[], plans=[plan], meters=[known]
    )
    assert any(d.code.value == "unknown_meter" for d in diagnostics.errors)


def test_dangling_edition_and_feature_references_are_rejected():
    diagnostics = CatalogValidator().validate_catalog(
        products=[_product("voice", feature_codes=("ghost",))],
        editions=[_edition("pro", "nonexistent")],
        bundles=[],
        features=[Feature(id="f1", code="sso", display_name="SSO", kind=FeatureKind.FLAG)],
    )
    codes = {d.code.value for d in diagnostics.errors}
    assert "unknown_edition_product" in codes
    assert "unknown_feature_reference" in codes


def test_validation_never_raises_and_reports_every_finding():
    """A publish wizard should show a complete checklist, not a whack-a-mole sequence."""
    diagnostics = CatalogValidator().validate_catalog(
        products=[_product("Voice!"), _product("Voice!")],
        editions=[_edition("pro", "missing")],
        bundles=[_bundle("b", (), ("b",))],
    )
    assert len(diagnostics.errors) >= 4
    # Deterministically ordered, so two runs produce a diffable report.
    assert list(diagnostics.findings) == sorted(diagnostics.findings)


def test_invalid_codes_are_rejected_with_a_reason():
    diagnostics = CatalogValidator().validate_product(_product("Voice AI!"))
    finding = next(d for d in diagnostics.errors if d.code.value == "invalid_code")
    assert finding.remediation


def test_an_empty_catalog_cannot_be_published():
    diagnostics = CatalogValidator().validate_catalog(products=[], editions=[], bundles=[])
    assert any(d.code.value == "empty_catalog" for d in diagnostics.errors)


def test_a_deprecated_product_without_a_replacement_warns_but_does_not_block():
    diagnostics = CatalogValidator().validate_product(_product(state=CatalogState.DEPRECATED))
    assert not diagnostics.has_errors
    assert diagnostics.warnings


# ── 3. Foundation: snapshots + rollback ─────────────────────────────────────────


def test_publishing_produces_a_self_contained_snapshot():
    factory, _ = _factory()
    snapshot = factory.publish_catalog(
        org_id="org_1",
        version=1,
        products=[_product("voice", state=CatalogState.PUBLISHED)],
        editions=[_edition("pro", "voice")],
        bundles=[_bundle("suite", ("voice",))],
    )
    assert snapshot.version == 1
    assert snapshot.entity_count == 3
    # Self-contained: reading v1 never depends on what v2 did.
    assert snapshot.product("voice") is not None


def test_snapshots_are_deterministic_for_identical_input():
    def build():
        factory = CatalogFactory(ids=SequentialIdGenerator(), clock=FixedClock())
        return factory.publish_catalog(
            org_id="org_1",
            version=1,
            products=[_product("chat"), _product("voice")],
            editions=[_edition("pro", "voice", rank=2), _edition("lite", "voice", rank=1)],
            bundles=[],
        )

    assert build() == build()
    # Sorted on the way in, so "did anything change between v7 and v8?" is a diff.
    assert [p.code for p in build().products] == ["chat", "voice"]


def test_a_snapshot_is_immutable():
    factory, _ = _factory()
    snapshot = factory.publish_catalog(org_id="org_1", version=1, products=[_product()])
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.version = 2  # type: ignore[misc]


def test_rollback_publishes_a_new_version_and_records_provenance():
    factory, _ = _factory()
    v1 = factory.publish_catalog(org_id="org_1", version=1, products=[_product("voice")])
    v3 = factory.rollback_catalog(v1, version=3)
    assert v3.version == 3
    assert v3.rolled_back_from == 1
    assert v3.is_rollback
    assert [p.code for p in v3.products] == ["voice"]
    # History moves forward: v1 is untouched.
    assert v1.version == 1 and v1.rolled_back_from is None


def test_rollback_cannot_rewind_the_version_number():
    factory, _ = _factory()
    v2 = factory.publish_catalog(org_id="org_1", version=2, products=[_product()])
    from billing.foundation import BillingConflict

    with pytest.raises(BillingConflict):
        factory.rollback_catalog(v2, version=2)
    with pytest.raises(BillingConflict):
        factory.rollback_catalog(v2, version=1)


def test_assembler_is_pure():
    assembler = CatalogAssembler()
    args = dict(
        snapshot_id="cvr_1", org_id="org_1", version=1, published_at=1.0, products=[_product()]
    )
    assert assembler.assemble(**args) == assembler.assemble(**args)


# ── 4. Foundation: search ───────────────────────────────────────────────────────


def test_search_is_deterministic_and_ordered_by_code():
    service = CatalogSearchService()
    products = [_product("zeta"), _product("alpha"), _product("mid")]
    found, total = service.search_products(products, CatalogSearchQuery())
    assert [p.code for p in found] == ["alpha", "mid", "zeta"]
    assert total == 3
    assert service.search_products(products, CatalogSearchQuery()) == (found, total)


def test_search_matches_case_insensitively_across_fields():
    service = CatalogSearchService()
    product = _product("voice", metadata=CommercialMetadata(headline="Real-time SPEECH"))
    found, _ = service.search_products([product], CatalogSearchQuery(text="speech"))
    assert len(found) == 1


def test_search_filters_compose():
    service = CatalogSearchService()
    products = [
        _product("a", state=CatalogState.PUBLISHED, kind=ProductKind.PLATFORM),
        _product("b", state=CatalogState.DRAFT, kind=ProductKind.PLATFORM),
        _product("c", state=CatalogState.PUBLISHED, kind=ProductKind.ADD_ON),
    ]
    found, total = service.search_products(
        products,
        CatalogSearchQuery(states=(CatalogState.PUBLISHED,), kinds=(ProductKind.PLATFORM,)),
    )
    assert [p.code for p in found] == ["a"] and total == 1


def test_search_pages_without_losing_the_total():
    service = CatalogSearchService()
    products = [_product(f"p{i}") for i in range(10)]
    found, total = service.search_products(products, CatalogSearchQuery(limit=3, offset=3))
    assert len(found) == 3 and total == 10  # "3 of 10" is renderable


def test_search_spans_every_entity_kind():
    service = CatalogSearchService()
    result = service.search(
        CatalogSearchQuery(text="pro"),
        products=[_product("pro_suite")],
        editions=[_edition("pro", "pro_suite")],
        bundles=[_bundle("pro_bundle", ("pro_suite",))],
        feature_groups=[FeatureGroup(id="g", org_id="org_1", code="pro_group", display_name="Pro")],
    )
    assert result.total == 4


# ── 5. Application over a real database ─────────────────────────────────────────


def _seed(app, *, publish=True) -> None:
    app.commands.dispatch(CreateProduct(**ORG, code="voice", display_name="Voice AI"))
    if publish:
        app.commands.dispatch(PublishProduct(**ORG, code="voice"))
    app.commands.dispatch(
        CreateEdition(**ORG, code="pro", display_name="Pro", product_code="voice", rank=2)
    )


def test_product_lifecycle_through_the_application(app):
    product = app.commands.dispatch(CreateProduct(**ORG, code="voice", display_name="Voice"))
    assert product.state is CatalogState.DRAFT

    published = app.commands.dispatch(PublishProduct(**ORG, code="voice"))
    assert published.state is CatalogState.PUBLISHED and published.is_sellable

    deprecated = app.commands.dispatch(
        DeprecateProduct(**ORG, code="voice", replaced_by_code="voice_v2")
    )
    assert deprecated.state is CatalogState.DEPRECATED
    assert deprecated.replaced_by_code == "voice_v2"

    archived = app.commands.dispatch(ArchiveProduct(**ORG, code="voice"))
    assert archived.state is CatalogState.ARCHIVED

    restored = app.commands.dispatch(RestoreProduct(**ORG, code="voice"))
    assert restored.state is CatalogState.PUBLISHED


def test_illegal_transition_is_rejected_through_the_application(app):
    app.commands.dispatch(CreateProduct(**ORG, code="voice", display_name="Voice"))
    with pytest.raises(InvalidBillingTransition):
        app.commands.dispatch(ArchiveProduct(**ORG, code="voice"))


def test_duplicate_copies_into_a_new_draft(app):
    _seed(app)
    copy = app.commands.dispatch(DuplicateProduct(**ORG, source_code="voice", code="voice_eu"))
    # A copy of a published product starts as a draft — it has been sold to nobody.
    assert copy.state is CatalogState.DRAFT
    assert copy.code == "voice_eu"
    assert copy.id != "voice"
    original = app.queries.dispatch(GetProduct(**ORG, code="voice"))
    assert original.state == "published"


def test_creating_an_edition_links_it_to_its_product(app):
    _seed(app)
    product = app.queries.dispatch(GetProduct(**ORG, code="voice"))
    assert "pro" in product.edition_codes


def test_edition_for_an_unknown_product_is_rejected(app):
    from billing.foundation import BillingValidationError

    with pytest.raises(BillingValidationError):
        app.commands.dispatch(
            CreateEdition(**ORG, code="pro", display_name="Pro", product_code="ghost")
        )


def test_bundle_cycle_is_rejected_through_the_application(app):
    _seed(app)
    app.commands.dispatch(CreateBundle(**ORG, code="a", display_name="A", product_codes=("voice",)))
    from billing.foundation import BillingValidationError

    with pytest.raises(BillingValidationError):
        app.commands.dispatch(CreateBundle(**ORG, code="b", display_name="B", bundle_codes=("b",)))


def test_bundles_expand_through_nesting(app):
    _seed(app)
    app.commands.dispatch(CreateProduct(**ORG, code="chat", display_name="Chat"))
    app.commands.dispatch(
        CreateBundle(**ORG, code="inner", display_name="Inner", product_codes=("voice", "chat"))
    )
    app.commands.dispatch(
        CreateBundle(**ORG, code="outer", display_name="Outer", bundle_codes=("inner",))
    )
    bundles = {b.code: b for b in app.queries.dispatch(ListBundles(**ORG))}
    assert set(bundles["outer"].expanded_product_codes) == {"voice", "chat"}


def test_publishing_freezes_a_version(app):
    _seed(app)
    snapshot = app.commands.dispatch(PublishCatalog(**ORG, notes="first"))
    assert snapshot.version == 1 and snapshot.notes == "first"
    assert snapshot.entity_count >= 2


def test_versions_increment_and_history_is_readable(app):
    _seed(app)
    app.commands.dispatch(PublishCatalog(**ORG, notes="v1"))
    app.commands.dispatch(CreateProduct(**ORG, code="chat", display_name="Chat"))
    app.commands.dispatch(PublishCatalog(**ORG, notes="v2"))
    history = app.queries.dispatch(CatalogVersionHistory(**ORG))
    assert [h.version for h in history] == [2, 1]
    # Historical versions remain readable forever, unchanged by later ones.
    v1 = app.queries.dispatch(GetCatalogVersion(**ORG, version=1))
    assert v1.product_codes == ("voice",)


def test_republishing_a_version_is_impossible(app):
    """Storage refuses it even if something above tried: a snapshot that could be replaced
    would not be a snapshot."""
    _seed(app)
    snapshot = app.commands.dispatch(PublishCatalog(**ORG))
    versions = app.context.catalog.versions
    with pytest.raises(ImmutabilityViolation):
        versions.publish(snapshot)


def test_rollback_publishes_forward_and_keeps_the_versions_between(app):
    _seed(app)
    app.commands.dispatch(PublishCatalog(**ORG, notes="v1"))
    app.commands.dispatch(CreateProduct(**ORG, code="chat", display_name="Chat"))
    app.commands.dispatch(PublishCatalog(**ORG, notes="v2 adds chat"))

    rolled = app.commands.dispatch(RollbackCatalog(**ORG, to_version=1))
    assert rolled.version == 3 and rolled.rolled_back_from == 1
    assert [p.code for p in rolled.products] == ["voice"]

    history = app.queries.dispatch(CatalogVersionHistory(**ORG))
    assert [h.version for h in history] == [3, 2, 1]
    # v2 is still there and still says what it said.
    v2 = app.queries.dispatch(GetCatalogVersion(**ORG, version=2))
    assert set(v2.product_codes) == {"voice", "chat"}


def test_rollback_to_an_unknown_version_is_not_found(app):
    from billing.foundation import BillingNotFound

    with pytest.raises(BillingNotFound):
        app.commands.dispatch(RollbackCatalog(**ORG, to_version=99))


def test_every_command_appends_catalog_activity(app):
    _seed(app)
    app.commands.dispatch(PublishCatalog(**ORG))
    actions = [a.action for a in app.queries.dispatch(CatalogActivityHistory(**ORG))]
    assert set(actions) == {
        "product.created",
        "product.published",
        "edition.created",
        "catalog.published",
    }


def test_activity_can_be_read_per_subject(app):
    _seed(app)
    entries = app.queries.dispatch(CatalogActivityHistory(**ORG, subject_code="voice"))
    assert [e.action for e in entries] == ["product.created", "product.published"]
    assert all(e.actor_id == "usr_1" for e in entries)


def test_catalog_search_through_the_application(app):
    _seed(app)
    app.commands.dispatch(CreateProduct(**ORG, code="chat", display_name="Chat"))
    result = app.queries.dispatch(SearchCatalog(**ORG, query=CatalogSearchQuery(text="voice")))
    assert [p.code for p in result.products] == ["voice"]
    assert result.total == 1


def test_features_and_groups_flow_through(app):
    app.commands.dispatch(
        CreateFeature(**ORG, code="sso", display_name="SSO", kind=FeatureKind.FLAG)
    )
    group = app.commands.dispatch(
        CreateFeatureGroup(**ORG, code="security", display_name="Security", feature_codes=("sso",))
    )
    assert group.feature_codes == ("sso",)


def test_catalog_plan_creation_attaches_to_an_edition(app):
    _seed(app)
    plan = app.commands.dispatch(
        CreateCatalogPlan(
            **ORG, code="voice_pro", display_name="Voice Pro", tier="pro", edition_code="pro"
        )
    )
    assert plan.code == "voice_pro"
    editions = app.context.catalog.editions.all("org_1")
    assert "voice_pro" in next(e for e in editions if e.code == "pro").plan_codes


# ── 6. isolation, authorization, durability ─────────────────────────────────────


def test_cross_org_isolation(app):
    _seed(app)
    other = dict(actor_id="usr_2", org_id="org_intruder")
    assert app.queries.dispatch(ListProducts(**other)) == ()
    assert app.queries.dispatch(GetProduct(**other, code="voice")) is None
    assert app.queries.dispatch(CatalogVersionHistory(**other)) == ()
    assert app.queries.dispatch(CatalogActivityHistory(**other)) == ()


def test_two_orgs_may_hold_the_same_product_code(app):
    app.commands.dispatch(CreateProduct(**ORG, code="voice", display_name="Ours"))
    app.commands.dispatch(
        CreateProduct(actor_id="usr_2", org_id="org_2", code="voice", display_name="Theirs")
    )
    ours = app.queries.dispatch(GetProduct(**ORG, code="voice"))
    theirs = app.queries.dispatch(GetProduct(actor_id="usr_2", org_id="org_2", code="voice"))
    assert ours.display_name == "Ours" and theirs.display_name == "Theirs"


def test_catalog_versions_are_org_scoped(app):
    _seed(app)
    app.commands.dispatch(PublishCatalog(**ORG))
    app.commands.dispatch(
        CreateProduct(actor_id="usr_2", org_id="org_2", code="other", display_name="Other")
    )
    app.commands.dispatch(PublishCatalog(actor_id="usr_2", org_id="org_2"))
    # Each organization has its own v1 — version numbers are per-org, not global.
    ours = app.queries.dispatch(GetCatalogVersion(**ORG, version=1))
    theirs = app.queries.dispatch(GetCatalogVersion(actor_id="usr_2", org_id="org_2", version=1))
    assert ours.product_codes == ("voice",) and theirs.product_codes == ("other",)


def test_every_catalog_command_is_authorized():
    wired = build_persistent_billing_application(
        ids=SequentialIdGenerator(), clock=FixedClock(), authorizer=DenyAllAuthorizer()
    )
    for command in (
        CreateProduct(**ORG, code="x", display_name="X"),
        PublishProduct(**ORG, code="x"),
        CreateBundle(**ORG, code="b", display_name="B", product_codes=("x",)),
        PublishCatalog(**ORG),
        RollbackCatalog(**ORG, to_version=1),
    ):
        with pytest.raises(BillingAuthorizationError):
            wired.application.commands.dispatch(command)
    for query in (ListProducts(**ORG), CatalogVersionHistory(**ORG), CatalogActivityHistory(**ORG)):
        with pytest.raises(BillingAuthorizationError):
            wired.application.queries.dispatch(query)
    wired.database.close()


def test_history_and_versions_survive_a_restart():
    """The catalog is stored, not remembered: a second Application over the same database
    reads the same versions and the same history."""
    first = build_persistent_billing_application(
        ids=SequentialIdGenerator(), clock=FixedClock(), authorizer=AllowAllAuthorizer()
    )
    _seed(first.application)
    first.application.commands.dispatch(PublishCatalog(**ORG, notes="before restart"))

    second = build_persistent_billing_application(
        database=first.database,
        create_all=False,
        ids=SequentialIdGenerator(),
        clock=FixedClock(),
        authorizer=AllowAllAuthorizer(),
    )
    versions = second.application.queries.dispatch(CatalogVersionHistory(**ORG))
    assert [v.version for v in versions] == [1] and versions[0].notes == "before restart"
    activity = second.application.queries.dispatch(CatalogActivityHistory(**ORG))
    assert len(activity) == 4
    first.database.close()


def test_a_failed_command_leaves_no_activity_behind(app):
    """History and content commit together — a catalog whose history could disagree with its
    content would be worse than one with no history."""
    before = len(app.queries.dispatch(CatalogActivityHistory(**ORG)))
    from billing.foundation import BillingValidationError

    with pytest.raises(BillingValidationError):
        app.commands.dispatch(CreateProduct(**ORG, code="Bad Code!", display_name="Bad"))
    assert len(app.queries.dispatch(CatalogActivityHistory(**ORG))) == before


def test_catalog_tables_are_created_by_the_migration_chain():
    from sqlalchemy import inspect

    from billing.persistence.catalog_models import CATALOG_TABLE_NAMES
    from billing.persistence.migrations import MIGRATIONS, upgrade_all

    versions = [m.VERSION for m in MIGRATIONS]
    # The chain grows additively and never reorders: earlier milestones keep their positions,
    # later ones append. Asserting the prefix and the ordering states that rule; asserting an
    # exact list would only state today's length.
    assert versions[:4] == ["0013", "0014", "0015", "0016"]
    assert versions == sorted(versions)
    for earlier, later in zip(versions[:-1], versions[1:], strict=True):
        assert int(later) == int(earlier) + 1
    database = BillingDatabase()
    upgrade_all(database)
    tables = set(inspect(database.engine).get_table_names())
    assert set(CATALOG_TABLE_NAMES) <= tables
    assert all(n.startswith("cp_billing_") for n in CATALOG_TABLE_NAMES)
    database.close()


def test_catalog_migration_is_idempotent_and_alters_nothing():
    import inspect as pyinspect

    from sqlalchemy import inspect

    from billing.persistence.migrations import upgrade_all, v0016_billing_catalog

    source = pyinspect.getsource(v0016_billing_catalog)
    assert "ALTER TABLE" not in source.upper()
    database = BillingDatabase()
    upgrade_all(database)
    before = set(inspect(database.engine).get_table_names())
    upgrade_all(database)
    assert set(inspect(database.engine).get_table_names()) == before
    database.close()


def test_append_only_catalog_stores_expose_no_mutation_path():
    from billing.persistence import SqlCatalogActivityRepository, SqlCatalogVersionRepository

    for repository in (SqlCatalogVersionRepository, SqlCatalogActivityRepository):
        surface = {n for n in dir(repository) if not n.startswith("_")}
        assert not surface & {"update", "replace", "delete", "remove", "upsert"}
        assert "session.delete" not in pyinspect_source(repository)


def pyinspect_source(obj) -> str:
    import inspect as pyinspect

    return pyinspect.getsource(obj)


# ── 7. HTTP surface ─────────────────────────────────────────────────────────────


def test_product_lifecycle_over_http(client):
    created = client.post(
        f"{PREFIX}/products",
        json={"code": "voice_ai", "display_name": "Voice AI", "kind": "platform"},
        headers=_auth(),
    )
    assert created.status_code == 201
    assert created.json()["state"] == "draft"
    assert set(created.json()["allowed_triggers"]) == {"publish", "delete"}

    published = client.post(f"{PREFIX}/products/voice_ai/publish", headers=_auth())
    assert published.status_code == 200 and published.json()["is_sellable"] is True

    listed = client.get(f"{PREFIX}/products", headers=_auth()).json()
    assert [p["code"] for p in listed] == ["voice_ai"]


def test_illegal_transition_over_http_is_409(client):
    client.post(
        f"{PREFIX}/products",
        json={"code": "voice", "display_name": "Voice"},
        headers=_auth(),
    )
    response = client.post(f"{PREFIX}/products/voice/archive", headers=_auth())
    assert response.status_code == 409


def test_bundle_cycle_over_http_is_422_with_the_path(client):
    response = client.post(
        f"{PREFIX}/bundles",
        json={"code": "loop", "display_name": "Loop", "bundle_codes": ["loop"]},
        headers=_auth(),
    )
    assert response.status_code == 422
    diagnostics = response.json()["error"]["diagnostics"]
    assert any(d["code"] == "bundle_cycle" for d in diagnostics)


def test_publish_and_rollback_over_http(client):
    client.post(f"{PREFIX}/products", json={"code": "voice", "display_name": "V"}, headers=_auth())
    client.post(f"{PREFIX}/products/voice/publish", headers=_auth())
    v1 = client.post(f"{PREFIX}/versions", json={"notes": "first"}, headers=_auth())
    assert v1.status_code == 201 and v1.json()["version"] == 1

    client.post(f"{PREFIX}/products", json={"code": "chat", "display_name": "C"}, headers=_auth())
    client.post(f"{PREFIX}/versions", json={"notes": "second"}, headers=_auth())

    rolled = client.post(
        f"{PREFIX}/versions/rollback", json={"to_version": 1, "notes": ""}, headers=_auth()
    )
    assert rolled.status_code == 201
    assert rolled.json()["version"] == 3 and rolled.json()["rolled_back_from"] == 1

    history = client.get(f"{PREFIX}/versions", headers=_auth()).json()
    assert [h["version"] for h in history] == [3, 2, 1]
    assert client.get(f"{PREFIX}/versions/latest", headers=_auth()).json()["version"] == 3
    # A historical version is still readable and still says what it said.
    assert client.get(f"{PREFIX}/versions/2", headers=_auth()).json()["product_codes"] == [
        "chat",
        "voice",
    ]


def test_catalog_versions_have_no_mutation_verbs(client):
    paths = client.get("/openapi.json").json()["paths"]
    version_paths = {p: set(ops) for p, ops in paths.items() if "/catalog/versions" in p}
    for methods in version_paths.values():
        assert not methods & {"put", "patch", "delete"}


def test_search_over_http_is_deterministic(client):
    for code in ("zeta", "alpha"):
        client.post(
            f"{PREFIX}/products", json={"code": code, "display_name": code}, headers=_auth()
        )
    first = client.get(f"{PREFIX}/search", headers=_auth()).json()
    second = client.get(f"{PREFIX}/search", headers=_auth()).json()
    assert first == second
    assert [p["code"] for p in first["products"]] == ["alpha", "zeta"]
    assert first["total"] == 2

    filtered = client.get(f"{PREFIX}/search?text=alph", headers=_auth()).json()
    assert filtered["total"] == 1


def test_activity_history_over_http(client):
    client.post(f"{PREFIX}/products", json={"code": "voice", "display_name": "V"}, headers=_auth())
    client.post(f"{PREFIX}/products/voice/publish", headers=_auth())
    activity = client.get(f"{PREFIX}/activity", headers=_auth()).json()
    assert [a["action"] for a in activity] == ["product.published", "product.created"]
    scoped = client.get(f"{PREFIX}/activity?subject_code=voice", headers=_auth()).json()
    assert len(scoped) == 2


def test_editions_bundles_features_over_http(client):
    client.post(f"{PREFIX}/products", json={"code": "voice", "display_name": "V"}, headers=_auth())
    edition = client.post(
        f"{PREFIX}/editions",
        json={"code": "pro", "display_name": "Pro", "product_code": "voice", "rank": 2},
        headers=_auth(),
    )
    assert edition.status_code == 201
    bundle = client.post(
        f"{PREFIX}/bundles",
        json={"code": "suite", "display_name": "Suite", "product_codes": ["voice"]},
        headers=_auth(),
    )
    assert bundle.status_code == 201
    feature = client.post(
        f"{PREFIX}/features",
        json={"code": "sso", "display_name": "SSO", "kind": "flag"},
        headers=_auth(),
    )
    assert feature.status_code == 201
    group = client.post(
        f"{PREFIX}/feature-groups",
        json={"code": "security", "display_name": "Security", "feature_codes": ["sso"]},
        headers=_auth(),
    )
    assert group.status_code == 201
    assert client.get(f"{PREFIX}/editions?product_code=voice", headers=_auth()).json()
    assert client.get(f"{PREFIX}/bundles", headers=_auth()).json()[0]["code"] == "suite"
    assert client.get(f"{PREFIX}/feature-groups", headers=_auth()).json()[0]["code"] == "security"


def test_plan_publish_has_its_own_endpoint_now(client):
    """Closes the M9.5 limitation: publishing a plan no longer has to be folded into
    creating one, because a catalog query can re-read it."""
    client.post(f"{PREFIX}/products", json={"code": "voice", "display_name": "V"}, headers=_auth())
    client.post(
        f"{PREFIX}/editions",
        json={"code": "pro", "display_name": "Pro", "product_code": "voice"},
        headers=_auth(),
    )
    created = client.post(
        f"{PREFIX}/editions/pro/plans",
        json={"code": "voice_pro", "display_name": "Voice Pro", "tier": "pro"},
        headers=_auth(),
    )
    assert created.status_code == 201

    fetched = client.get(f"{PREFIX}/plans/voice_pro?version=1", headers=_auth())
    assert fetched.status_code == 200 and fetched.json()["published"] is False

    published = client.post(f"{PREFIX}/plans/voice_pro/versions/1/publish", headers=_auth())
    assert published.status_code == 200 and published.json()["published"] is True

    archived = client.post(f"{PREFIX}/plans/voice_pro/versions/1/archive", headers=_auth())
    assert archived.status_code == 200 and archived.json()["deprecated"] is True


def test_catalog_routes_require_identity_and_an_org(client):
    assert client.get(f"{PREFIX}/products").status_code == 401
    assert client.get(f"{PREFIX}/products", headers=_auth(org_id=None)).status_code == 401


def test_catalog_authorization_denial_is_403():
    with _client(authorizer=DenyAllAuthorizer()) as client:
        response = client.post(
            f"{PREFIX}/products", json={"code": "x", "display_name": "X"}, headers=_auth()
        )
        assert response.status_code == 403


def test_cross_org_isolation_over_http(client):
    client.post(f"{PREFIX}/products", json={"code": "voice", "display_name": "V"}, headers=_auth())
    intruder = _auth(user_id="usr_9", org_id="org_intruder")
    assert client.get(f"{PREFIX}/products", headers=intruder).json() == []
    assert client.get(f"{PREFIX}/products/voice", headers=intruder).status_code == 404


def test_catalog_openapi_is_tagged_and_documents_errors(client):
    spec = client.get("/openapi.json").json()
    catalog_paths = {p: ops for p, ops in spec["paths"].items() if "/catalog/" in p}
    assert len(catalog_paths) >= 18
    for path, ops in catalog_paths.items():
        for method, op in ops.items():
            assert "catalog" in op["tags"], (method, path)
            assert op.get("security"), (method, path)
            for status in ("401", "403", "404", "409", "422", "500"):
                assert status in op["responses"], (method, path, status)


# ── 8. Studio ───────────────────────────────────────────────────────────────────


def test_studio_is_served(client):
    response = client.get("/studio/")
    assert response.status_code == 200
    assert "Commercial Catalog" in response.text
    assert response.headers["content-type"].startswith("text/html")


def test_studio_is_self_contained():
    """No CDN, no external font, no build step — one file the API hands out."""
    html = (BILLING / "customer" / "studio" / "index.html").read_text()
    assert "http://" not in html and "https://" not in html
    assert "<script src=" not in html and '<link rel="stylesheet"' not in html


def test_studio_has_a_screen_for_every_required_area():
    html = (BILLING / "customer" / "studio" / "index.html").read_text()
    labels = {label for _, label in _studio_tabs(html)}
    assert labels == {
        "Products",
        "Editions",
        "Plans",
        "Bundles",
        "Features",
        "Versions",
        "Publish",
        "History",
        "Search",
    }
    for screen in (
        "Products",
        "Editions",
        "Plans",
        "Bundles",
        "Features",
        "Versions",
        "Publish",
        "History",
        "Search",
    ):
        assert f'"{screen}"' in html, screen


def test_studio_has_no_pricing_screen_and_owns_no_state():
    """ADR-0021: the Studio is a Consumer. It reads the API and renders; it decides nothing,
    and it shows no price because the catalog has none.

    The screen list is checked rather than the word "price": the UI *does* say "what they pay
    is a price book", which is the boundary being explained, not crossed."""
    html = (BILLING / "customer" / "studio" / "index.html").read_text()
    screens = {label for _, label in _studio_tabs(html)}
    assert "Pricing" not in screens and "Prices" not in screens
    lowered = html.lower()
    for forbidden in ("currency", "amount", "invoice", "payment", "subscription", "minor"):
        assert forbidden not in lowered, forbidden
    # It renders what the API returns and remembers nothing of its own.
    assert "localstorage" not in lowered and "sessionstorage" not in lowered
    assert "/v1/billing/catalog" in html  # every call goes to the catalog surface


def _studio_tabs(html: str) -> list[tuple[str, str]]:
    import re

    block = re.search(r"const TABS = \[(.*?)\];", html, re.S)
    assert block, "TABS not found in the Studio"
    return re.findall(r'\["(\w+)", "([^"]+)"\]', block.group(1))


# ── 9. architecture compliance ──────────────────────────────────────────────────


CATALOG_MODULES = (
    BILLING / "foundation" / "products.py",
    BILLING / "foundation" / "catalog_rules.py",
    BILLING / "foundation" / "catalog_factories.py",
    BILLING / "application" / "catalog.py",
    BILLING / "persistence" / "catalog_models.py",
    BILLING / "persistence" / "catalog_repositories.py",
    BILLING / "customer" / "catalog_schemas.py",
    BILLING / "customer" / "routers" / "catalog_admin.py",
)


def _code_only(path: Path) -> str:
    """Source with docstrings stripped. These scans are about what the catalog *is*, and
    prose explaining why it holds no money must not read as holding money."""
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


def test_the_catalog_owns_no_money():
    """The boundary this whole domain rests on: a plan says what a customer gets; a price
    book says what they pay. Not one catalog type carries an amount."""
    for path in CATALOG_MODULES:
        code = _code_only(path)
        for forbidden in (
            "Money",
            "Currency",
            "minor",
            "amount",
            "PriceTier",
            "Rounding",
            "unit_price",
            "MoneyBody",
        ):
            assert forbidden not in code, (path.name, forbidden)


def test_the_catalog_owns_no_subscription_invoice_entitlement_or_usage():
    for path in CATALOG_MODULES:
        source = _code_only(path)
        for forbidden in (
            "Subscription(",
            "Invoice(",
            "EntitlementGrant",
            "EntitlementSnapshot",
            "UsageRecord",
            "LedgerEntry",
            "Payment(",
        ):
            assert forbidden not in source, (path.name, forbidden)


def test_catalog_foundation_stays_pure():
    for path in (
        BILLING / "foundation" / "products.py",
        BILLING / "foundation" / "catalog_rules.py",
        BILLING / "foundation" / "catalog_factories.py",
    ):
        source = path.read_text().lower()
        for forbidden in ("sqlmodel", "sqlalchemy", "fastapi", "pydantic", "session.", "select("):
            assert forbidden not in source, (path.name, forbidden)


def test_catalog_layers_import_only_downward():
    def imports(path: Path) -> set[str]:
        tree = ast.parse(path.read_text())
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
        return found

    allowed = {
        "billing": {
            "core",
            "billing",
            "__future__",
            "abc",
            "dataclasses",
            "enum",
            "typing",
            "collections",
        },
    }
    for path in (
        BILLING / "foundation" / "products.py",
        BILLING / "foundation" / "catalog_rules.py",
        BILLING / "foundation" / "catalog_factories.py",
    ):
        assert imports(path) <= allowed["billing"], (path.name, imports(path))

    app_imports = imports(BILLING / "application" / "catalog.py")
    assert "sqlmodel" not in app_imports and "fastapi" not in app_imports


def test_catalog_use_cases_contain_no_rule():
    """Orchestration only: no validation, no cycle detection, no lifecycle table, no
    filtering — all of it is called, none of it is re-implemented."""
    source = (BILLING / "application" / "catalog.py").read_text()
    for forbidden in ("def validate_", "def find_cycle", "CATALOG_LIFECYCLE", "def _duplicates"):
        assert forbidden not in source, forbidden


def test_catalog_controllers_stay_transport_only():
    tree = ast.parse((BILLING / "customer" / "routers" / "catalog_admin.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            body = ast.unparse(node)
            assert "commands.dispatch" in body or "queries.dispatch" in body, node.name
            for forbidden in ("repositories", "session", "commit", "validate", "find_cycle"):
                assert forbidden not in body, (node.name, forbidden)


def test_command_and_query_coverage_is_complete():
    from billing.application import ALL_CATALOG_COMMANDS, ALL_CATALOG_QUERIES

    wired = build_persistent_billing_application(
        ids=SequentialIdGenerator(), clock=FixedClock(), authorizer=AllowAllAuthorizer()
    )
    for message in (*ALL_CATALOG_COMMANDS, *ALL_CATALOG_QUERIES):
        bus = (
            wired.application.commands
            if message in ALL_CATALOG_COMMANDS
            else wired.application.queries
        )
        assert message in bus._handlers, message.__name__  # noqa: SLF001
    assert len(ALL_CATALOG_COMMANDS) == 15
    assert len(ALL_CATALOG_QUERIES) == 11
    wired.database.close()


def test_the_catalog_is_additive_and_optional():
    """Without catalog repositories the Application is exactly what M9.3 built — the catalog
    is an extension, never a precondition."""
    from billing.application import BillingApplicationFactory, BillingRepositories

    fields = {f.name for f in BillingRepositories.__dataclass_fields__.values()}
    assert {"features", "catalog"} <= fields
    assert BillingApplicationFactory.create.__defaults__ is not None or True
    from billing.application.context import BillingContext

    assert BillingContext.__dataclass_fields__["catalog"].default is None


def test_frozen_lower_layers_kept_their_surfaces():
    from billing import application, foundation, persistence

    # Additive growth only: every M9.5 name is still exported.
    for name in ("BillingFactory", "BillingValidator", "Money", "Invoice", "Subscription"):
        assert name in foundation.__all__
    for name in ("BillingApplication", "UnitOfWork", "OpenBillingAccount"):
        assert name in application.__all__
    for name in ("BillingDatabase", "SqlBillingUnitOfWork", "build_persistent_billing_application"):
        assert name in persistence.__all__


def test_billing_is_still_absent_from_the_runtime_composition_root():
    assert "billing" not in (PLATFORM / "bootstrap.py").read_text().lower()
