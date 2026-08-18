"""Commercial Catalog rules (M9.6) — validation, lifecycle, search, assembly.

Every catalog *decision* lives here: what makes a catalog consistent, which lifecycle moves
are legal, how a bundle expands, and how a snapshot is assembled. Layers above coordinate
these; they never re-decide.

Validation follows the Foundation discipline exactly: it **returns diagnostics and never
raises**, reports *every* finding rather than the first, and orders them deterministically —
a publish wizard should be able to show a complete checklist, not a whack-a-mole sequence.

Pure: no I/O, no clock read, no persistence, no money. `catalog_rules` is the module a
reviewer should read to answer "what does this system consider a valid catalog?".
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace

from core.fsm import StateMachineDefinition

from .catalog import Feature, MeterDefinition, Plan
from .diagnostics import BillingDiagnosticCode as C
from .diagnostics import BillingDiagnostics, Diagnostic, Severity
from .errors import BillingConflict, InvalidBillingTransition
from .products import (
    CATALOG_LIFECYCLE,
    Bundle,
    CatalogSearchQuery,
    CatalogSearchResult,
    CatalogState,
    CatalogTrigger,
    CatalogVersion,
    Edition,
    FeatureGroup,
    Product,
    Visibility,
)


def _err(path: str, code: C, message: str, remediation: str = "") -> Diagnostic:
    return Diagnostic(path, code, Severity.ERROR, message, remediation)


def _warn(path: str, code: C, message: str, remediation: str = "") -> Diagnostic:
    return Diagnostic(path, code, Severity.WARNING, message, remediation)


def _duplicates(codes: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for code in codes:
        if code in seen:
            dupes.add(code)
        seen.add(code)
    return sorted(dupes)


_CODE_ALLOWED = set("abcdefghijklmnopqrstuvwxyz0123456789_-.")


def _check_code(out: list[Diagnostic], path: str, code: str, kind: str) -> None:
    if not code:
        out.append(_err(path, C.EMPTY_IDENTITY, f"{kind} code is required"))
        return
    if set(code) - _CODE_ALLOWED:
        out.append(
            _err(
                path,
                C.INVALID_CODE,
                f"{kind} code {code!r} may contain only lowercase letters, digits, '_', '-', '.'",
                "codes appear in URLs, invoices, and contracts — keep them stable and typeable",
            )
        )


# ── validation ──────────────────────────────────────────────────────────────────


class CatalogValidator:
    """Structural and referential validation for the commercial catalog. Never raises."""

    def validate_product(self, product: Product) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        _check_code(out, "product.code", product.code, "product")
        if not product.org_id:
            out.append(_err("product.org_id", C.EMPTY_IDENTITY, "org_id is required"))
        if not product.display_name:
            out.append(_err("product.display_name", C.INVALID_NAME, "a display name is required"))
        for dupe in _duplicates(product.edition_codes):
            out.append(
                _err(
                    "product.edition_codes",
                    C.DUPLICATE_EDITION_CODE,
                    f"edition {dupe!r} is listed more than once",
                )
            )
        for dupe in _duplicates(product.feature_codes):
            out.append(
                _err(
                    "product.feature_codes",
                    C.DUPLICATE_FEATURE_CODE,
                    f"feature {dupe!r} is listed more than once",
                )
            )
        if product.state is CatalogState.DEPRECATED and not product.replaced_by_code:
            out.append(
                _warn(
                    "product.replaced_by_code",
                    C.MISSING_REQUIRED_FIELD,
                    "a deprecated product should name its replacement",
                    "customers on this product need somewhere to go",
                )
            )
        return BillingDiagnostics.of(out)

    def validate_edition(
        self, edition: Edition, *, known_products: frozenset[str] | None = None
    ) -> BillingDiagnostics:
        """`known_products=None` means "not checked here"; an **empty set** means "this
        catalog has no products", which must reject the reference rather than skip the
        check — the distinction an empty-collection falsiness test silently loses."""
        out: list[Diagnostic] = []
        _check_code(out, "edition.code", edition.code, "edition")
        if not edition.org_id:
            out.append(_err("edition.org_id", C.EMPTY_IDENTITY, "org_id is required"))
        if not edition.display_name:
            out.append(_err("edition.display_name", C.INVALID_NAME, "a display name is required"))
        if not edition.product_code:
            out.append(
                _err("edition.product_code", C.EMPTY_IDENTITY, "an edition belongs to a product")
            )
        elif known_products is not None and edition.product_code not in known_products:
            out.append(
                _err(
                    "edition.product_code",
                    C.UNKNOWN_EDITION_PRODUCT,
                    f"no product {edition.product_code!r} in this catalog",
                )
            )
        if edition.rank < 0:
            out.append(
                _err("edition.rank", C.INVALID_CODE, "rank must be non-negative", "0 is the lowest")
            )
        return BillingDiagnostics.of(out)

    def validate_feature_group(self, group: FeatureGroup) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        _check_code(out, "feature_group.code", group.code, "feature group")
        if not group.org_id:
            out.append(_err("feature_group.org_id", C.EMPTY_IDENTITY, "org_id is required"))
        if not group.display_name:
            out.append(
                _err("feature_group.display_name", C.INVALID_NAME, "a display name is required")
            )
        for dupe in _duplicates(group.feature_codes):
            out.append(
                _err(
                    "feature_group.feature_codes",
                    C.DUPLICATE_FEATURE_CODE,
                    f"feature {dupe!r} is listed more than once",
                )
            )
        return BillingDiagnostics.of(out)

    def validate_bundle(
        self, bundle: Bundle, *, known_bundles: Sequence[Bundle] = ()
    ) -> BillingDiagnostics:
        out: list[Diagnostic] = []
        _check_code(out, "bundle.code", bundle.code, "bundle")
        if not bundle.org_id:
            out.append(_err("bundle.org_id", C.EMPTY_IDENTITY, "org_id is required"))
        if not bundle.display_name:
            out.append(_err("bundle.display_name", C.INVALID_NAME, "a display name is required"))
        if not bundle.member_codes:
            out.append(
                _err(
                    "bundle.product_codes",
                    C.EMPTY_CATALOG,
                    "a bundle must contain at least one product or bundle",
                )
            )
        if bundle.code in bundle.bundle_codes:
            out.append(
                _err(
                    "bundle.bundle_codes",
                    C.BUNDLE_CYCLE,
                    f"bundle {bundle.code!r} cannot contain itself",
                )
            )
        for dupe in _duplicates(bundle.member_codes):
            out.append(
                _err(
                    "bundle.product_codes",
                    C.DUPLICATE_CODE,
                    f"member {dupe!r} is listed more than once",
                )
            )
        cycle = BundleResolver().find_cycle(bundle, known_bundles)
        if cycle:
            out.append(
                _err(
                    "bundle.bundle_codes",
                    C.BUNDLE_CYCLE,
                    f"bundle nesting forms a cycle: {' → '.join(cycle)}",
                    "a bundle may not contain itself, directly or transitively",
                )
            )
        return BillingDiagnostics.of(out)

    def validate_catalog(
        self,
        *,
        products: Sequence[Product],
        editions: Sequence[Edition],
        bundles: Sequence[Bundle],
        feature_groups: Sequence[FeatureGroup] = (),
        plans: Sequence[Plan] = (),
        features: Sequence[Feature] = (),
        meters: Sequence[MeterDefinition] = (),
    ) -> BillingDiagnostics:
        """Whole-catalog consistency — the check that runs before a snapshot is published.

        A publish is the one moment the catalog is examined as a whole, so this is where
        duplicate codes, dangling references, bundle cycles, and edition conflicts must be
        caught. After publication the snapshot is immutable, and an inconsistency inside one
        is permanent."""
        out: list[Diagnostic] = []

        # Per-entity structural validation first: a catalog cannot be consistent if the
        # things in it are individually invalid, and reporting only the cross-entity
        # findings would hide the simpler cause underneath them.
        for product in products:
            out.extend(self.validate_product(product).findings)
        for edition in editions:
            out.extend(
                self.validate_edition(
                    edition, known_products=frozenset(p.code for p in products)
                ).findings
            )
        for group in feature_groups:
            out.extend(self.validate_feature_group(group).findings)

        for dupe in _duplicates(p.code for p in products):
            out.append(
                _err("catalog.products", C.DUPLICATE_PRODUCT_CODE, f"duplicate product {dupe!r}")
            )
        for dupe in _duplicates(b.code for b in bundles):
            out.append(
                _err("catalog.bundles", C.DUPLICATE_BUNDLE_CODE, f"duplicate bundle {dupe!r}")
            )
        for dupe in _duplicates(f.code for f in features):
            out.append(
                _err("catalog.features", C.DUPLICATE_FEATURE_CODE, f"duplicate feature {dupe!r}")
            )
        for dupe in _duplicates(g.code for g in feature_groups):
            out.append(
                _err(
                    "catalog.feature_groups",
                    C.DUPLICATE_CODE,
                    f"duplicate feature group {dupe!r}",
                )
            )
        for dupe in _duplicates(f"{p.code}@{p.version}" for p in plans):
            out.append(
                _err("catalog.plans", C.DUPLICATE_PLAN_CODE, f"duplicate plan version {dupe!r}")
            )
        for dupe in _duplicates(m.code for m in meters):
            out.append(_err("catalog.meters", C.DUPLICATE_METER, f"duplicate meter {dupe!r}"))

        # Edition codes are unique *within* a product, not globally — two products may both
        # have a "pro" edition, and forcing global uniqueness would be a naming tax.
        for product in products:
            product_editions = [e for e in editions if e.product_code == product.code]
            for dupe in _duplicates(e.code for e in product_editions):
                out.append(
                    _err(
                        f"catalog.editions[{product.code}]",
                        C.DUPLICATE_EDITION_CODE,
                        f"product {product.code!r} has two editions coded {dupe!r}",
                    )
                )
            ranks = [e.rank for e in product_editions]
            for dupe in _duplicates(str(r) for r in ranks):
                out.append(
                    _err(
                        f"catalog.editions[{product.code}]",
                        C.EDITION_CONFLICT,
                        f"product {product.code!r} has two editions at rank {dupe}",
                        "ranks order upgrades and downgrades; they must be unambiguous",
                    )
                )

        product_codes = frozenset(p.code for p in products)
        bundle_codes = frozenset(b.code for b in bundles)
        feature_codes = frozenset(f.code for f in features)
        meter_codes = frozenset(m.code for m in meters)
        plan_codes = frozenset(p.code for p in plans)

        for edition in editions:
            if edition.product_code not in product_codes:
                out.append(
                    _err(
                        f"catalog.editions[{edition.code}].product_code",
                        C.UNKNOWN_EDITION_PRODUCT,
                        f"edition {edition.code!r} references unknown product "
                        f"{edition.product_code!r}",
                    )
                )
            for plan_code in edition.plan_codes:
                if plan_codes and plan_code not in plan_codes:
                    out.append(
                        _err(
                            f"catalog.editions[{edition.code}].plan_codes",
                            C.UNKNOWN_PLAN,
                            f"unknown plan {plan_code!r}",
                        )
                    )
            out.extend(
                self._feature_refs(
                    f"catalog.editions[{edition.code}]", edition.feature_codes, feature_codes
                )
            )

        for product in products:
            out.extend(
                self._feature_refs(
                    f"catalog.products[{product.code}]", product.feature_codes, feature_codes
                )
            )
            for edition_code in product.edition_codes:
                if not any(
                    e.code == edition_code and e.product_code == product.code for e in editions
                ):
                    out.append(
                        _err(
                            f"catalog.products[{product.code}].edition_codes",
                            C.UNKNOWN_EDITION_PRODUCT,
                            f"product lists edition {edition_code!r}, which is not in the catalog",
                        )
                    )

        for group in feature_groups:
            out.extend(
                self._feature_refs(
                    f"catalog.feature_groups[{group.code}]", group.feature_codes, feature_codes
                )
            )

        resolver = BundleResolver()
        for bundle in bundles:
            for member in bundle.product_codes:
                if member not in product_codes:
                    out.append(
                        _err(
                            f"catalog.bundles[{bundle.code}].product_codes",
                            C.UNKNOWN_BUNDLE_MEMBER,
                            f"unknown product {member!r}",
                        )
                    )
            for member in bundle.bundle_codes:
                if member not in bundle_codes:
                    out.append(
                        _err(
                            f"catalog.bundles[{bundle.code}].bundle_codes",
                            C.UNKNOWN_BUNDLE_MEMBER,
                            f"unknown bundle {member!r}",
                        )
                    )
            cycle = resolver.find_cycle(bundle, bundles)
            if cycle:
                out.append(
                    _err(
                        f"catalog.bundles[{bundle.code}]",
                        C.BUNDLE_CYCLE,
                        f"bundle nesting forms a cycle: {' → '.join(cycle)}",
                    )
                )

        # Meter references: a plan that meters something the catalog does not define would
        # produce usage nobody can rate.
        for plan in plans:
            for meter_code in plan.metered_codes:
                if meter_codes and meter_code not in meter_codes:
                    out.append(
                        _err(
                            f"catalog.plans[{plan.code}].metered_codes",
                            C.UNKNOWN_METER,
                            f"plan meters {meter_code!r}, which this catalog does not define",
                        )
                    )
            out.extend(
                self._feature_refs(
                    f"catalog.plans[{plan.code}]",
                    tuple(f.feature_code for f in plan.features),
                    feature_codes,
                )
            )

        if not products and not plans and not bundles:
            out.append(
                _err(
                    "catalog",
                    C.EMPTY_CATALOG,
                    "a catalog version must contain at least one product, plan, or bundle",
                )
            )

        for entity, path in (
            *((p, f"catalog.products[{p.code}]") for p in products),
            *((b, f"catalog.bundles[{b.code}]") for b in bundles),
        ):
            if entity.state is CatalogState.DRAFT:
                out.append(
                    _warn(
                        path,
                        C.UNPUBLISHABLE_LIFECYCLE_STATE,
                        f"{entity.code!r} is a draft and will be snapshotted as one",
                        "publish it first if customers should be able to buy it",
                    )
                )
        return BillingDiagnostics.of(out)

    def validate_snapshot(self, snapshot: CatalogVersion) -> BillingDiagnostics:
        """Snapshot integrity — a published version must be internally complete, because
        nothing may ever be added to it."""
        out: list[Diagnostic] = []
        if snapshot.version < 1:
            out.append(
                _err("snapshot.version", C.SNAPSHOT_INTEGRITY, "versions start at 1 and increase")
            )
        if not snapshot.org_id:
            out.append(_err("snapshot.org_id", C.EMPTY_IDENTITY, "org_id is required"))
        if snapshot.entity_count == 0:
            out.append(_err("snapshot", C.EMPTY_CATALOG, "an empty catalog version is not useful"))
        if snapshot.rolled_back_from is not None and snapshot.rolled_back_from >= snapshot.version:
            out.append(
                _err(
                    "snapshot.rolled_back_from",
                    C.SNAPSHOT_INTEGRITY,
                    "a rollback source must be an earlier version",
                    "rollback moves forward by copying backward — history is never rewritten",
                )
            )
        inner = self.validate_catalog(
            products=snapshot.products,
            editions=snapshot.editions,
            bundles=snapshot.bundles,
            feature_groups=snapshot.feature_groups,
            plans=snapshot.plans,
            features=snapshot.features,
            meters=snapshot.meters,
        )
        return BillingDiagnostics.of(out).merged(inner)

    @staticmethod
    def _feature_refs(
        path: str, referenced: tuple[str, ...], known: frozenset[str]
    ) -> list[Diagnostic]:
        if not known:
            return []
        return [
            _err(
                f"{path}.feature_codes",
                C.UNKNOWN_FEATURE_REFERENCE,
                f"unknown feature {code!r}",
            )
            for code in referenced
            if code not in known
        ]


# ── bundles ─────────────────────────────────────────────────────────────────────


class BundleResolver:
    """Expands nested bundles and detects cycles. Stateless."""

    def find_cycle(self, bundle: Bundle, known: Sequence[Bundle]) -> tuple[str, ...]:
        """The cycle through `bundle`, as a path, or `()` if there is none.

        Returning the *path* rather than a boolean is deliberate: "bundle nesting forms a
        cycle" is unactionable; "a → b → c → a" tells the author which edge to cut."""
        by_code = {b.code: b for b in known}
        path: list[str] = []
        visiting: set[str] = set()

        def walk(code: str) -> tuple[str, ...]:
            if code in visiting:
                start = path.index(code)
                return tuple(path[start:]) + (code,)
            current = by_code.get(code)
            if current is None:
                return ()
            visiting.add(code)
            path.append(code)
            for nested in current.bundle_codes:
                found = walk(nested)
                if found:
                    return found
            path.pop()
            visiting.discard(code)
            return ()

        if bundle.code in bundle.bundle_codes:
            return (bundle.code, bundle.code)
        by_code.setdefault(bundle.code, bundle)
        return walk(bundle.code)

    def expand(self, bundle: Bundle, known: Sequence[Bundle]) -> tuple[str, ...]:
        """Every product reachable through the bundle, deduplicated, in stable order.

        Raises `BillingConflict` on a cycle rather than looping: expansion is called after
        validation, so reaching a cycle here means the catalog was published inconsistent —
        a condition that must be loud."""
        if self.find_cycle(bundle, known):
            raise BillingConflict(f"bundle {bundle.code!r} nesting forms a cycle")
        by_code = {b.code: b for b in known}
        by_code.setdefault(bundle.code, bundle)
        seen: list[str] = []

        def walk(current: Bundle) -> None:
            for product in current.product_codes:
                if product not in seen:
                    seen.append(product)
            for nested_code in current.bundle_codes:
                nested = by_code.get(nested_code)
                if nested is not None:
                    walk(nested)

        walk(bundle)
        return tuple(seen)


# ── lifecycle ───────────────────────────────────────────────────────────────────


class CatalogLifecycleService:
    """Applies catalog lifecycle triggers through the shared FSM. Illegal edges are
    rejected with a typed error, never coerced into an adjacent state."""

    def __init__(self, definition: StateMachineDefinition = CATALOG_LIFECYCLE) -> None:
        self._definition = definition

    def can(self, current: CatalogState, trigger: CatalogTrigger) -> bool:
        return self._definition.get(current, trigger) is not None

    def next_state(self, current: CatalogState, trigger: CatalogTrigger) -> CatalogState:
        edge = self._definition.get(current, trigger)
        if edge is None:
            legal = sorted(t.value for t in self._definition.triggers_from(current))
            raise InvalidBillingTransition(
                f"catalog entity cannot {trigger.value} from {current.value}; "
                f"legal triggers: {legal}"
            )
        return edge.target

    def allowed(self, current: CatalogState) -> tuple[CatalogTrigger, ...]:
        """Which moves are legal from here — what a UI needs to render buttons that cannot
        produce a 409, and what makes an "Archive" button absent rather than broken."""
        return tuple(t for t in CatalogTrigger if self.can(current, t))

    def apply_product(
        self, product: Product, trigger: CatalogTrigger, *, at: float, replaced_by: str = ""
    ) -> Product:
        state = self.next_state(product.state, trigger)
        return replace(
            product,
            state=state,
            updated_at=at,
            replaced_by_code=replaced_by or product.replaced_by_code,
        )

    def apply_bundle(self, bundle: Bundle, trigger: CatalogTrigger) -> Bundle:
        return replace(bundle, state=self.next_state(bundle.state, trigger))

    def apply_edition(self, edition: Edition, trigger: CatalogTrigger) -> Edition:
        return replace(edition, state=self.next_state(edition.state, trigger))


# ── search ──────────────────────────────────────────────────────────────────────


class CatalogSearchService:
    """Deterministic catalog filtering. No scoring, no ranking, no index — the same catalog
    and the same query always produce the same rows in the same order.

    Ordering is by code, always. It is arbitrary but *stable*, which is the property a paged
    admin screen and a reproducible test both actually need."""

    def search_products(
        self, products: Sequence[Product], query: CatalogSearchQuery
    ) -> tuple[tuple[Product, ...], int]:
        matched = [p for p in products if self._matches_product(p, query)]
        matched.sort(key=lambda p: p.code)
        return self._page(matched, query), len(matched)

    def search_editions(
        self, editions: Sequence[Edition], query: CatalogSearchQuery
    ) -> tuple[tuple[Edition, ...], int]:
        matched = [
            e
            for e in editions
            if self._text_matches(query.text, e.code, e.display_name, e.metadata.description)
            and (not query.states or e.state in query.states)
            and (not query.visibilities or e.visibility in query.visibilities)
            and (not query.product_code or e.product_code == query.product_code)
            and (not query.feature_code or query.feature_code in e.feature_codes)
        ]
        matched.sort(key=lambda e: (e.product_code, e.rank, e.code))
        return self._page(matched, query), len(matched)

    def search_bundles(
        self, bundles: Sequence[Bundle], query: CatalogSearchQuery
    ) -> tuple[tuple[Bundle, ...], int]:
        matched = [
            b
            for b in bundles
            if self._text_matches(
                query.text, b.code, b.display_name, b.metadata.description, *b.metadata.tags
            )
            and (not query.states or b.state in query.states)
            and (not query.visibilities or b.visibility in query.visibilities)
            and (not query.audiences or b.audience in query.audiences)
            and (not query.tags or set(query.tags) <= set(b.metadata.tags))
            and (not query.product_code or query.product_code in b.product_codes)
            and (not query.feature_code or query.feature_code in b.feature_codes)
        ]
        matched.sort(key=lambda b: b.code)
        return self._page(matched, query), len(matched)

    def search_plans(
        self, plans: Sequence[Plan], query: CatalogSearchQuery
    ) -> tuple[tuple[Plan, ...], int]:
        matched = [
            p
            for p in plans
            if self._text_matches(query.text, p.code, p.display_name, p.description)
            and (
                not query.feature_code
                or any(f.feature_code == query.feature_code for f in p.features)
            )
        ]
        matched.sort(key=lambda p: (p.code, p.version))
        return self._page(matched, query), len(matched)

    def search_features(
        self, features: Sequence[Feature], query: CatalogSearchQuery
    ) -> tuple[tuple[Feature, ...], int]:
        matched = [
            f
            for f in features
            if self._text_matches(query.text, f.code, f.display_name, f.description)
        ]
        matched.sort(key=lambda f: f.code)
        return self._page(matched, query), len(matched)

    def search_feature_groups(
        self, groups: Sequence[FeatureGroup], query: CatalogSearchQuery
    ) -> tuple[tuple[FeatureGroup, ...], int]:
        matched = [
            g
            for g in groups
            if self._text_matches(query.text, g.code, g.display_name, g.description)
            and (not query.feature_code or query.feature_code in g.feature_codes)
        ]
        matched.sort(key=lambda g: (g.rank, g.code))
        return self._page(matched, query), len(matched)

    def search(
        self,
        query: CatalogSearchQuery,
        *,
        products: Sequence[Product] = (),
        editions: Sequence[Edition] = (),
        bundles: Sequence[Bundle] = (),
        plans: Sequence[Plan] = (),
        features: Sequence[Feature] = (),
        feature_groups: Sequence[FeatureGroup] = (),
    ) -> CatalogSearchResult:
        found_products, n_products = self.search_products(products, query)
        found_editions, n_editions = self.search_editions(editions, query)
        found_bundles, n_bundles = self.search_bundles(bundles, query)
        found_plans, n_plans = self.search_plans(plans, query)
        found_features, n_features = self.search_features(features, query)
        found_groups, n_groups = self.search_feature_groups(feature_groups, query)
        return CatalogSearchResult(
            products=found_products,
            editions=found_editions,
            bundles=found_bundles,
            plans=found_plans,
            features=found_features,
            feature_groups=found_groups,
            total=n_products + n_editions + n_bundles + n_plans + n_features + n_groups,
        )

    def _matches_product(self, product: Product, query: CatalogSearchQuery) -> bool:
        return (
            self._text_matches(
                query.text,
                product.code,
                product.display_name,
                product.metadata.headline,
                product.metadata.description,
                *product.metadata.tags,
            )
            and (not query.kinds or product.kind in query.kinds)
            and (not query.states or product.state in query.states)
            and (not query.visibilities or product.visibility in query.visibilities)
            and (not query.audiences or product.audience in query.audiences)
            and (not query.tags or set(query.tags) <= set(product.metadata.tags))
            and (not query.feature_code or query.feature_code in product.feature_codes)
        )

    @staticmethod
    def _text_matches(text: str, *fields: str) -> bool:
        if not text:
            return True
        needle = text.casefold()
        return any(needle in (field or "").casefold() for field in fields)

    @staticmethod
    def _page(matched: list, query: CatalogSearchQuery) -> tuple:
        start = max(query.offset, 0)
        return tuple(matched[start : start + max(query.limit, 0)])


# ── snapshot assembly ───────────────────────────────────────────────────────────


class CatalogAssembler:
    """Composes a `CatalogVersion` from the current catalog. Pure: given the same entities
    and the same version number, it produces an identical snapshot.

    Entities are sorted on the way in, so two publishes of the same catalog are byte-identical
    — which is what makes "did anything actually change between v7 and v8?" a diff rather than
    an investigation."""

    def assemble(
        self,
        *,
        snapshot_id: str,
        org_id: str,
        version: int,
        published_at: float,
        published_by: str = "",
        notes: str = "",
        rolled_back_from: int | None = None,
        products: Sequence[Product] = (),
        editions: Sequence[Edition] = (),
        bundles: Sequence[Bundle] = (),
        feature_groups: Sequence[FeatureGroup] = (),
        plans: Sequence[Plan] = (),
        features: Sequence[Feature] = (),
        meters: Sequence[MeterDefinition] = (),
    ) -> CatalogVersion:
        return CatalogVersion(
            id=snapshot_id,
            org_id=org_id,
            version=version,
            published_at=published_at,
            published_by=published_by,
            notes=notes,
            rolled_back_from=rolled_back_from,
            products=tuple(sorted(products, key=lambda p: p.code)),
            editions=tuple(sorted(editions, key=lambda e: (e.product_code, e.rank, e.code))),
            bundles=tuple(sorted(bundles, key=lambda b: b.code)),
            feature_groups=tuple(sorted(feature_groups, key=lambda g: (g.rank, g.code))),
            plans=tuple(sorted(plans, key=lambda p: (p.code, p.version))),
            features=tuple(sorted(features, key=lambda f: f.code)),
            meters=tuple(sorted(meters, key=lambda m: m.code)),
        )

    def rollback(
        self,
        source: CatalogVersion,
        *,
        snapshot_id: str,
        version: int,
        published_at: float,
        published_by: str = "",
        notes: str = "",
    ) -> CatalogVersion:
        """A rollback is a **new version** whose content is copied from an older one.

        Nothing is rewritten and nothing is deleted: the versions in between remain readable
        forever, and the new version records where its content came from. That is the whole
        difference between an auditable catalog and a mutable one (§A5)."""
        if version <= source.version:
            raise BillingConflict(
                f"rollback must publish a *new* version; {version} does not follow {source.version}"
            )
        return replace(
            source,
            id=snapshot_id,
            version=version,
            published_at=published_at,
            published_by=published_by,
            notes=notes or f"rollback to v{source.version}",
            rolled_back_from=source.version,
        )


# ── visibility ──────────────────────────────────────────────────────────────────


class CatalogVisibilityService:
    """Filters a catalog for an audience. Presentation, never permission — Billing decides
    nothing about who a caller *is* (ADR-0023 §5.3); it states what is publicly listed."""

    def public_products(self, products: Sequence[Product]) -> tuple[Product, ...]:
        return tuple(p for p in products if p.is_visible_to_customers)

    def sellable_products(self, products: Sequence[Product]) -> tuple[Product, ...]:
        return tuple(p for p in products if p.is_sellable)

    def for_visibility(
        self, products: Sequence[Product], allowed: tuple[Visibility, ...]
    ) -> tuple[Product, ...]:
        return tuple(p for p in products if p.visibility in allowed)
