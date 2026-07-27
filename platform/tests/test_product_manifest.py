"""Product Definition & Manifest Core (M7.0B-1) tests.

Verifies immutable models, manifest parsing/round-trip, structural validation
(schema/interface_version, required fields, duplicate ids, SemVer format, metadata,
secret integrity), canonical ordering (authoring-order independence), deterministic
hashing + content identity, the factory (load→parse→validate→canonicalize→
construct, fail-fast), deterministic diagnostics, reference placeholders staying
unresolved, and the acyclic `product → core` dependency rule (in particular: zero
import of any sibling runtime or the frozen Epic 6/5 packages, per ADR-0018).
Compilation, bindings, packaging, deployment, and registry are non-goals here.

Regression anchors are marked `# regression:` — each pins a specific guarantee that
must fail on a naive reimplementation.
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import FrozenInstanceError

import pytest

from core.errors import ConfigError
from product import (
    AgentRef,
    DeploymentMetadata,
    DeploymentStrategy,
    Diagnostic,
    EnvironmentProfile,
    FeatureFlag,
    KnowledgeRef,
    MCPServerRef,
    ProductDefinition,
    ProductDiagnosticCode,
    ProductFactory,
    ProductManifest,
    ProductMetadata,
    ProductSerializer,
    ProductValidationError,
    ProviderRef,
    SecretReference,
    Severity,
    SkillRef,
    TenantOverride,
    ToolRef,
    VersionMetadata,
    WorkflowRef,
    canonical_json,
    definition_id_for,
    is_valid_semver,
    manifest_hash,
    validate_manifest,
)


def _metadata(**kw) -> ProductMetadata:
    base = dict(id="support-bot", name="Support Bot", version="1.2.0", owner="team-cx")
    base.update(kw)
    return ProductMetadata(**base)


def _manifest(**kw) -> ProductManifest:
    base: dict = dict(metadata=_metadata())
    base.update(kw)
    return ProductManifest(**base)


# ── immutability ─────────────────────────────────────────────────────────────────
def test_manifest_and_definition_are_immutable():
    m = _manifest()
    with pytest.raises(FrozenInstanceError):
        m.namespace = "org.acme"  # type: ignore[misc]
    definition = ProductFactory.from_manifest(m)
    with pytest.raises(FrozenInstanceError):
        definition.content_hash = "tampered"  # type: ignore[misc]


def test_reference_placeholders_are_immutable():
    ref = AgentRef(id="a")
    with pytest.raises(FrozenInstanceError):
        ref.id = "b"  # type: ignore[misc]


# ── parsing / round-trip ─────────────────────────────────────────────────────────
def test_manifest_round_trips_through_serializer():
    m = _manifest(
        agents=(AgentRef(id="greeter", version="1.0.0", workflow_refs=("wf1",)),),
        workflows=(WorkflowRef(id="wf1"),),
        skills=(SkillRef(id="sk1"),),
        tools=(ToolRef(id="calendar"),),
        providers=(ProviderRef(id="anthropic"),),
        mcp_servers=(MCPServerRef(id="github"),),
        knowledge=(KnowledgeRef(id="faq"),),
        environments=(EnvironmentProfile(name="prod", overrides={"log": "info"}),),
        tenants=(TenantOverride(tenant_id="org_1", overrides={"log": "debug"}),),
        feature_flags=(FeatureFlag(key="beta", default=True),),
        secrets=(SecretReference(ref="llm_key", provider="vault", path="kv/llm"),),
        deployment=DeploymentMetadata(
            strategy=DeploymentStrategy.CANARY, target_environments=("prod",)
        ),
        version_metadata=VersionMetadata(requires_execution="1.0.0"),
    )
    ser = ProductSerializer()
    again = ser.manifest_from_dict(ser.manifest_to_dict(m))
    assert again == m  # regression: full structural round-trip, no field dropped


def test_parse_tolerates_omitted_optional_fields():
    m = ProductFactory.parse({"metadata": {"id": "p", "name": "P", "version": "1.0.0"}})
    assert m.agents == () and m.namespace == "internal" and m.interface_version == "1.0"


# ── canonical ordering (authoring-order independence) ─────────────────────────────
def test_canonical_form_is_authoring_order_independent():
    a = _manifest(tools=(ToolRef(id="a"), ToolRef(id="b")))
    b = _manifest(tools=(ToolRef(id="b"), ToolRef(id="a")))
    # regression: reordering references must NOT change canonical json or hash
    assert canonical_json(a) == canonical_json(b)
    assert manifest_hash(a) == manifest_hash(b)


def test_canonical_form_distinguishes_real_differences():
    a = _manifest(tools=(ToolRef(id="a"),))
    b = _manifest(tools=(ToolRef(id="b"),))
    assert manifest_hash(a) != manifest_hash(b)


# ── deterministic hashing / identity ─────────────────────────────────────────────
def test_hash_and_id_are_deterministic():
    assert manifest_hash(_manifest()) == manifest_hash(_manifest())
    assert definition_id_for(_manifest()) == definition_id_for(_manifest())
    assert definition_id_for(_manifest()).startswith("pd_")


def test_definition_identity_is_derived_from_manifest():
    m = _manifest()
    d = ProductFactory.from_manifest(m)
    assert d.content_hash == manifest_hash(m)
    assert d.content_id == definition_id_for(m)
    assert d.version == "1.2.0" and d.qualified_name == "internal.support-bot"


def test_different_version_changes_identity():
    d1 = ProductFactory.from_manifest(_manifest(metadata=_metadata(version="1.0.0")))
    d2 = ProductFactory.from_manifest(_manifest(metadata=_metadata(version="2.0.0")))
    assert d1.content_hash != d2.content_hash


# ── factory: fail-fast on invalid, construct on valid ────────────────────────────
def test_factory_constructs_definition_for_valid_manifest():
    d = ProductFactory.from_mapping(
        {"metadata": {"id": "p", "name": "P", "version": "1.0.0"}}
    )
    assert isinstance(d, ProductDefinition)


def test_factory_raises_on_validation_errors():
    with pytest.raises(ProductValidationError) as ei:
        ProductFactory.from_manifest(_manifest(metadata=_metadata(id="")))
    assert ei.value.diagnostics.has_errors
    # regression: ProductValidationError is a ConfigError (fail fast, typed)
    assert isinstance(ei.value, ConfigError)


def test_factory_diagnose_does_not_raise():
    diags = ProductFactory.diagnose(_manifest(metadata=_metadata(id="")))
    assert diags.has_errors and not diags.is_valid


# ── validation: required fields / metadata ───────────────────────────────────────
def test_missing_id_is_error():
    diags = validate_manifest(_manifest(metadata=_metadata(id="")))
    assert any(d.code is ProductDiagnosticCode.EMPTY_IDENTITY for d in diags.errors)


def test_missing_name_is_warning_not_error():
    diags = validate_manifest(_manifest(metadata=_metadata(name="")))
    assert diags.is_valid  # regression: empty name warns, never blocks
    assert any(d.severity is Severity.WARNING for d in diags.warnings)


def test_unsupported_interface_version_is_error():
    diags = validate_manifest(_manifest(interface_version="9.9"))
    assert any(
        d.code is ProductDiagnosticCode.UNSUPPORTED_INTERFACE_VERSION for d in diags.errors
    )


# ── validation: version format ───────────────────────────────────────────────────
@pytest.mark.parametrize("good", ["1.0.0", "0.1.0", "10.20.30", "1.0.0-rc.1", "1.0.0+build.5"])
def test_semver_accepts_valid(good):
    assert is_valid_semver(good)


@pytest.mark.parametrize("bad", ["1.0", "v1.0.0", "1", "1.0.0.0", "abc", ""])
def test_semver_rejects_invalid(bad):
    assert not is_valid_semver(bad)


def test_invalid_product_version_is_error():
    diags = validate_manifest(_manifest(metadata=_metadata(version="1.0")))
    assert any(d.code is ProductDiagnosticCode.INVALID_VERSION_FORMAT for d in diags.errors)


def test_invalid_pinned_reference_version_is_error():
    diags = validate_manifest(_manifest(tools=(ToolRef(id="t", version="not-semver"),)))
    assert any(
        d.code is ProductDiagnosticCode.INVALID_VERSION_FORMAT and d.path == "tools[0].version"
        for d in diags.errors
    )


def test_unpinned_reference_version_is_allowed():
    diags = validate_manifest(_manifest(tools=(ToolRef(id="t", version=None),)))
    assert diags.is_valid


# ── validation: duplicate ids ────────────────────────────────────────────────────
def test_duplicate_reference_id_is_error():
    diags = validate_manifest(
        _manifest(skills=(SkillRef(id="dup", version="1.0.0"), SkillRef(id="dup", version="1.0.0")))
    )
    assert any(d.code is ProductDiagnosticCode.DUPLICATE_ID for d in diags.errors)


def test_same_id_different_version_is_not_duplicate():
    diags = validate_manifest(
        _manifest(skills=(SkillRef(id="s", version="1.0.0"), SkillRef(id="s", version="2.0.0")))
    )
    assert diags.is_valid  # regression: (namespace,id,version) is the identity, not id alone


def test_duplicate_feature_flag_key_is_error():
    diags = validate_manifest(
        _manifest(feature_flags=(FeatureFlag(key="f"), FeatureFlag(key="f")))
    )
    assert any(
        d.code is ProductDiagnosticCode.DUPLICATE_ID and d.path.startswith("feature_flags")
        for d in diags.errors
    )


def test_duplicate_environment_and_tenant_are_errors():
    diags = validate_manifest(
        _manifest(
            environments=(EnvironmentProfile(name="prod"), EnvironmentProfile(name="prod")),
            tenants=(TenantOverride(tenant_id="t"), TenantOverride(tenant_id="t")),
        )
    )
    paths = {d.path.split("[")[0] for d in diags.errors}
    assert {"environments", "tenants"} <= paths


# ── validation: reference / secret integrity ─────────────────────────────────────
def test_empty_reference_id_is_malformed():
    diags = validate_manifest(_manifest(providers=(ProviderRef(id=""),)))
    assert any(d.code is ProductDiagnosticCode.MALFORMED_REFERENCE for d in diags.errors)


def test_secret_reference_requires_pointer_fields():
    diags = validate_manifest(_manifest(secrets=(SecretReference(ref="", provider="", path=""),)))
    assert any(d.code is ProductDiagnosticCode.MALFORMED_REFERENCE for d in diags.errors)


def test_secret_reference_rejects_inlined_value():
    # regression: a secret is a pointer, never a value (ADR-0018 §Security)
    diags = validate_manifest(
        _manifest(secrets=(SecretReference(ref="k", provider="vault", path="token=abc123"),))
    )
    assert any(d.code is ProductDiagnosticCode.SECRET_VALUE_PRESENT for d in diags.errors)


def test_empty_environment_name_and_tenant_id_are_errors():
    diags = validate_manifest(
        _manifest(
            environments=(EnvironmentProfile(name=""),),
            tenants=(TenantOverride(tenant_id=""),),
        )
    )
    codes = {d.code for d in diags.errors}
    assert ProductDiagnosticCode.EMPTY_ENVIRONMENT_NAME in codes
    assert ProductDiagnosticCode.EMPTY_TENANT_ID in codes


# ── diagnostics determinism ──────────────────────────────────────────────────────
def test_diagnostics_ordering_is_deterministic():
    bad = _manifest(
        metadata=_metadata(id="", version="1.0"),
        tools=(ToolRef(id=""),),
        interface_version="9.9",
    )
    first = validate_manifest(bad).findings
    second = validate_manifest(bad).findings
    assert first == second  # regression: identical input -> identical ordered findings
    assert list(first) == sorted(first)  # canonical order


def test_diagnostic_ordering_ignores_message_wording():
    # Two findings identical in (path, code, severity) but different wording compare equal
    a = Diagnostic("p", ProductDiagnosticCode.DUPLICATE_ID, Severity.ERROR, "msg a")
    b = Diagnostic("p", ProductDiagnosticCode.DUPLICATE_ID, Severity.ERROR, "msg b", "fix")
    assert not (a < b) and not (b < a)


# ── references stay unresolved (no bindings in this bundle) ───────────────────────
def test_references_are_inert_placeholders():
    d = ProductFactory.from_manifest(_manifest(tools=(ToolRef(id="calendar"),)))
    tool = d.manifest.tools[0]
    # A placeholder carries declared data only — no resolve/bind/execute surface.
    surface = {n for n in dir(tool) if not n.startswith("_")}
    assert not ({"resolve", "bind", "execute", "invoke", "load"} & surface)


def test_definition_carries_no_runtime_or_execution_state():
    d = ProductFactory.from_manifest(_manifest())
    surface = {n for n in dir(d) if not n.startswith("_")}
    forbidden = {"run", "execute", "compile", "deploy", "register", "resolve", "package"}
    assert not (forbidden & surface)


# ── dependency rules (product stays acyclic; no ownership leakage) ────────────────
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


def test_product_dependency_rules():
    # M7.0B-1: product imports only the foundation (core) plus its own package —
    # never any sibling runtime, never the frozen Epic 6/5 execution packages,
    # never the composition roots or the bus (ADR-0018; AR-15 acyclic).
    forbidden = {
        "runtime", "api", "gateway", "replay", "events", "memory", "knowledge",
        "context", "prompt", "reasoning", "provider", "bridge", "pipeline",
        "voicebot", "observability", "tenant", "tool", "skill", "workflow",
        "agent", "loop", "mcp", "hooks", "integration",
    }
    assert not (_imports("product") & forbidden), _imports("product")


def test_no_epic6_or_epic5_package_imports_product():
    # regression: the freeze is preserved by construction — nothing imports product
    for pkg in ("tool", "skill", "workflow", "agent", "loop", "mcp", "hooks",
                "integration", "reasoning", "provider", "pipeline", "bridge"):
        if pathlib.Path(pkg).exists():
            assert "product" not in _imports(pkg), pkg
