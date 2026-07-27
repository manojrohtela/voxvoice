"""Product Package, Registry & Versioning (M7.0B-3) tests.

Verifies package creation, immutability, the deterministic identity chain
(Manifest→Definition→Compilation→Package), byte-identical serialization + reproducible
identity, integrity verification (and tamper detection), package validation, the
immutable read-only Product Registry (latest/exact/compatible/rollback/history/
certification/provenance queries), content-addressed registry snapshots, version
management (transition classification + rollback rules) and the compatibility matrix.
Also asserts the package/registry layer imports no Epic 5/6 sibling runtime and holds
no execution/deployment/network state.

Regression anchors are marked `# regression:`.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib
from dataclasses import FrozenInstanceError

import pytest

from product import ProductFactory
from product.compiler import (
    BindingKind,
    BindingSnapshot,
    BindingSnapshotEntry,
    CompilationContext,
    ProductCompiler,
)
from product.domain import (
    AgentRef,
    ProductManifest,
    ProductMetadata,
    ToolRef,
    VersionMetadata,
    WorkflowRef,
)
from product.errors import ProductError
from product.registry import (
    IntegrityValidator,
    PackageSerializer,
    PackageValidator,
    ProductPackage,
    ProductPackager,
    ProductRegistry,
    RegistryQuery,
    RegistryQueryKind,
    RegistrySnapshot,
    TransitionKind,
    VersionManager,
)
from product.registry.registry import InMemoryPackageStore


def _snapshot() -> BindingSnapshot:
    return BindingSnapshot.of(
        [
            BindingSnapshotEntry(BindingKind.AGENT, "internal", "a", ("1.0.0",)),
            BindingSnapshotEntry(BindingKind.WORKFLOW, "internal", "wf", ("1.0.0",)),
            BindingSnapshotEntry(BindingKind.TOOL, "internal", "t", ("1.0.0",)),
        ]
    )


def _manifest(product_version: str = "1.0.0") -> ProductManifest:
    return ProductManifest(
        metadata=ProductMetadata(id="bot", name="Bot", version=product_version),
        agents=(AgentRef(id="a", workflow_refs=("wf",)),),
        workflows=(WorkflowRef(id="wf"),),
        tools=(ToolRef(id="t", version="1.0.0"),),
        version_metadata=VersionMetadata(requires_execution="1.0.0"),
    )


def _build_package(
    *,
    package_version: str = "1.0.0",
    product_version: str = "1.0.0",
    creator: str = "team-cx",
    created_at: str = "2026-07-27T00:00:00Z",
    certified: bool = False,
) -> ProductPackage:
    definition = ProductFactory.from_manifest(_manifest(product_version))
    result = ProductCompiler().compile(
        definition, CompilationContext(snapshot=_snapshot(), execution_platform_version="1.2.0")
    )
    return ProductPackager().package(
        definition,
        result,
        package_version=package_version,
        creator=creator,
        created_at=created_at,
        certified=certified,
    )


# ── package creation + immutability ──────────────────────────────────────────────
def test_package_creation_produces_immutable_artifact():
    p = _build_package()
    assert isinstance(p, ProductPackage)
    assert p.package_id.startswith("pk_")
    with pytest.raises(FrozenInstanceError):
        p.content_hash = "x"  # type: ignore[misc]


def test_package_contains_only_inert_data_no_execution_surface():
    p = _build_package()
    surface = {n for n in dir(p) if not n.startswith("_")}
    forbidden = {"execute", "run", "deploy", "connect", "session", "client", "socket", "invoke"}
    assert not (forbidden & surface)


def test_packaging_a_failed_compilation_raises():
    # A manifest whose tool is absent from the snapshot fails compilation.
    definition = ProductFactory.from_manifest(
        ProductManifest(
            metadata=ProductMetadata(id="bot", name="Bot", version="1.0.0"),
            tools=(ToolRef(id="ghost"),),
        )
    )
    result = ProductCompiler().compile(definition, CompilationContext(snapshot=BindingSnapshot()))
    assert not result.success
    with pytest.raises(ProductError):
        ProductPackager().package(definition, result)


# ── identity chain ───────────────────────────────────────────────────────────────
def test_identity_chain_is_wired_manifest_definition_compilation_package():
    p = _build_package()
    ident = p.package_manifest.identity
    assert ident.manifest_id.startswith("mf_")
    assert ident.definition_id == p.compiled_product.definition_id
    assert ident.compilation_id == p.compiled_product.content_id
    assert ident.package_id == p.package_id == p.content_id


def test_package_identity_changes_when_contents_change():
    p1 = _build_package(product_version="1.0.0")
    p2 = _build_package(product_version="2.0.0")
    assert p1.package_id != p2.package_id  # regression: content change -> id change


def test_package_identity_changes_when_package_version_changes():
    assert _build_package(package_version="1.0.0").package_id != _build_package(
        package_version="1.1.0"
    ).package_id


def test_package_identity_is_reproducible_and_excludes_timestamp():
    # regression: identity is content-derived; differing creator/timestamp must NOT
    # change the package id (only descriptive provenance differs).
    a = _build_package(creator="x", created_at="2026-01-01T00:00:00Z")
    b = _build_package(creator="y", created_at="2026-12-31T00:00:00Z")
    assert a.package_id == b.package_id


# ── deterministic serialization ──────────────────────────────────────────────────
def test_serialization_is_byte_identical_for_identical_packages():
    ser = PackageSerializer()
    a = _build_package(creator="c", created_at="2026-07-27T00:00:00Z")
    b = _build_package(creator="c", created_at="2026-07-27T00:00:00Z")
    assert ser.package_to_dict(a) == ser.package_to_dict(b)  # regression: byte-identical


def test_serialization_reflects_provenance_differences():
    ser = PackageSerializer()
    a = _build_package(creator="x", created_at="2026-01-01T00:00:00Z")
    b = _build_package(creator="y", created_at="2026-01-01T00:00:00Z")
    # Same package id, but serialized provenance differs (creator).
    assert a.package_id == b.package_id
    assert ser.package_to_dict(a) != ser.package_to_dict(b)


# ── integrity verification ───────────────────────────────────────────────────────
def test_integrity_verifies_for_a_freshly_built_package():
    verification = IntegrityValidator().verify(_build_package())
    assert verification.verified
    assert not verification.findings


def test_integrity_detects_tampered_integrity_metadata():
    p = _build_package()
    tampered_integrity = dataclasses.replace(p.package_manifest.integrity, dependency_hash="dead")
    tampered_manifest = dataclasses.replace(p.package_manifest, integrity=tampered_integrity)
    tampered = dataclasses.replace(p, package_manifest=tampered_manifest)
    verification = IntegrityValidator().verify(tampered)
    assert not verification.verified and not verification.dependency_hash_ok


def test_all_four_hashes_are_present_and_distinct_roles():
    integ = _build_package().package_manifest.integrity
    assert integ.package_hash and integ.manifest_hash
    assert integ.compilation_hash and integ.dependency_hash


# ── package validation ───────────────────────────────────────────────────────────
def test_valid_package_passes_validation():
    assert PackageValidator().validate(_build_package()).is_valid


def test_validation_flags_identity_tampering():
    from product.diagnostics import ProductDiagnosticCode

    p = _build_package()
    broken_manifest = dataclasses.replace(p, content_id="pk_tampered")
    diags = PackageValidator().validate(broken_manifest)
    assert any(
        d.code is ProductDiagnosticCode.PACKAGE_IDENTITY_MISMATCH for d in diags.errors
    )


def test_validation_flags_bad_package_version():
    from product.diagnostics import ProductDiagnosticCode

    p = _build_package()
    bad_manifest = dataclasses.replace(p.package_manifest, package_version="1.0")
    broken = dataclasses.replace(p, package_manifest=bad_manifest)
    diags = PackageValidator().validate(broken)
    assert any(d.code is ProductDiagnosticCode.INVALID_PACKAGE_VERSION for d in diags.errors)


# ── version manager ──────────────────────────────────────────────────────────────
def test_transition_classification():
    vm = VersionManager()
    assert vm.classify_transition("1.0.0", "1.1.0") is TransitionKind.UPGRADE
    assert vm.classify_transition("2.0.0", "1.0.0") is TransitionKind.DOWNGRADE
    assert vm.classify_transition("1.0.0", "1.0.0") is TransitionKind.SAME
    assert vm.classify_transition("bad", "1.0.0") is TransitionKind.INVALID


def test_rollback_allowed_only_within_same_major():
    vm = VersionManager()
    assert vm.is_rollback_allowed("1.2.0", "1.1.0")  # same-major downgrade
    assert not vm.is_rollback_allowed("2.0.0", "1.9.0")  # cross-major
    assert not vm.is_rollback_allowed("1.0.0", "1.1.0")  # an upgrade is not a rollback


def test_version_report_is_deterministic():
    vm = VersionManager()
    assert vm.report("1.2.0", "1.1.0") == vm.report("1.2.0", "1.1.0")


# ── compatibility matrix ─────────────────────────────────────────────────────────
def test_compatibility_matrix_evaluates_statically():
    cm = _build_package().package_manifest.compatibility_matrix
    ok = cm.evaluate(execution_platform_version="1.5.0", runtime_platform_version="")
    assert ok.compatible and ok.execution_compatible
    bad = cm.evaluate(execution_platform_version="0.9.0", runtime_platform_version="")
    assert not bad.compatible


# ── product registry ─────────────────────────────────────────────────────────────
def _registry_with(*versions: str, certified: set[str] | None = None) -> ProductRegistry:
    certified = certified or set()
    reg = ProductRegistry()
    for v in versions:
        reg.register(_build_package(package_version=v, certified=v in certified))
    return reg


def test_registry_latest_and_exact():
    reg = _registry_with("1.0.0", "1.1.0", "1.2.0")
    assert reg.latest("internal.bot").package_version == "1.2.0"
    assert reg.exact("internal.bot", "1.1.0").package_version == "1.1.0"
    assert reg.exact("internal.bot", "9.9.9") is None


def test_registry_history_is_version_sorted():
    reg = _registry_with("2.0.0", "1.0.0", "1.1.0")
    assert [e.package_version for e in reg.history("internal.bot")] == ["1.0.0", "1.1.0", "2.0.0"]


def test_registry_rollback_target_is_highest_same_major_downgrade():
    reg = _registry_with("1.0.0", "1.1.0", "1.2.0")
    target = reg.rollback_target("internal.bot", "1.2.0")
    assert target.package_version == "1.1.0"  # regression: highest valid rollback
    assert reg.rollback_target("internal.bot", "1.0.0") is None  # nothing below


def test_registry_rejects_cross_major_rollback():
    reg = _registry_with("1.9.0", "2.0.0")
    assert reg.rollback_target("internal.bot", "2.0.0") is None


def test_registry_compatible_lookup():
    reg = _registry_with("1.0.0", "1.1.0")
    compatible = reg.compatible(
        "internal.bot", execution_platform_version="1.5.0", runtime_platform_version=""
    )
    assert len(compatible) == 2
    none = reg.compatible(
        "internal.bot", execution_platform_version="0.1.0", runtime_platform_version=""
    )
    assert none == ()


def test_registry_certification_history():
    reg = _registry_with("1.0.0", "1.1.0", "1.2.0", certified={"1.1.0"})
    certified = reg.certification_history("internal.bot")
    assert [e.package_version for e in certified] == ["1.1.0"]


def test_registry_provenance_lookup():
    reg = ProductRegistry()
    p = _build_package(creator="team-cx")
    reg.register(p)
    prov = reg.provenance(p.package_id)
    assert prov.creator == "team-cx" and prov.package_id == p.package_id


def test_registry_rejects_duplicate_registration():
    reg = ProductRegistry()
    p = _build_package()
    reg.register(p)
    with pytest.raises(ProductError):
        reg.register(_build_package())  # identical content -> same package_id


def test_registry_never_deploys_or_executes():
    # `execute` here is the query surface (runs a RegistryQuery), never a package —
    # the registry must expose no verb that runs/deploys the packaged product.
    surface = {n for n in dir(ProductRegistry()) if not n.startswith("_")}
    forbidden = {"deploy", "launch", "install", "provision", "run_package", "start_package"}
    assert not (forbidden & surface)


def test_registry_get_package_via_store():
    reg = ProductRegistry(InMemoryPackageStore())
    p = _build_package()
    reg.register(p)
    assert reg.get_package(p.package_id).package_id == p.package_id
    assert reg.get_package("pk_missing") is None


# ── unified query surface ────────────────────────────────────────────────────────
def test_registry_query_execution():
    reg = _registry_with("1.0.0", "1.1.0")
    latest = reg.execute(
        RegistryQuery(kind=RegistryQueryKind.LATEST, qualified_name="internal.bot")
    )
    assert latest.match.package_version == "1.1.0" and latest.count == 1
    rollback = reg.execute(
        RegistryQuery(
            kind=RegistryQueryKind.ROLLBACK, qualified_name="internal.bot", current_version="1.1.0"
        )
    )
    assert rollback.match.package_version == "1.0.0"


# ── registry snapshot (content-addressed, immutable) ─────────────────────────────
def test_registry_snapshot_is_content_addressed_and_stable():
    reg = _registry_with("1.0.0", "1.1.0")
    s1 = reg.snapshot()
    s2 = reg.snapshot()
    assert isinstance(s1, RegistrySnapshot)
    assert s1.snapshot_id == s2.snapshot_id and s1.snapshot_id.startswith("rgs_")


def test_registry_snapshot_changes_with_contents():
    reg = _registry_with("1.0.0")
    before = reg.snapshot().snapshot_id
    reg.register(_build_package(package_version="1.1.0"))
    assert reg.snapshot().snapshot_id != before


def test_snapshot_is_immutable():
    snap = _registry_with("1.0.0").snapshot()
    with pytest.raises(FrozenInstanceError):
        snap.snapshot_id = "x"  # type: ignore[misc]


# ── dependency rules (registry stays acyclic; ADR-0018/0019/0020) ────────────────
def _imports(pkg: str) -> set[str]:
    tops: set[str] = set()
    for p in pathlib.Path(pkg).rglob("*.py"):
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.ImportFrom) and n.module and not n.level:
                tops.add(n.module.split(".")[0])
            elif isinstance(n, ast.Import):
                for a in n.names:
                    tops.add(a.name.split(".")[0])
    return tops


def test_registry_imports_no_sibling_runtime():
    forbidden = {
        "runtime", "api", "gateway", "replay", "events", "memory", "knowledge",
        "context", "prompt", "reasoning", "provider", "bridge", "pipeline",
        "voicebot", "observability", "tenant", "tool", "skill", "workflow",
        "agent", "loop", "mcp", "hooks", "integration",
    }
    assert not (_imports("product") & forbidden), _imports("product")
