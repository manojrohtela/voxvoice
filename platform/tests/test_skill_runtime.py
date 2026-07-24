"""Skill Runtime Core (M6.2B-1) tests.

Verifies manifest validation (including `interface_version`), descriptor
validation, the registry (registration/discovery/namespacing/version-
resolution/dependency-graph-validation — no execution, no capability
resolution), the capability catalog (no intelligence), the lifecycle FSM,
serialization round-trips, the temporary registration repository,
determinism, and the acyclic `skill → core` dependency rule (in particular:
zero import of Tool Runtime, per this bundle's explicit constraint). Planning,
execution context, policies, observability, and replay are non-goals here
(Bundle 2).
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import FrozenInstanceError, replace

import pytest

from core.errors import ConfigError
from core.fsm import FSMError
from skill import (
    CapabilityKind,
    SkillCapabilityCatalog,
    SkillCapabilityRequirement,
    SkillConfiguration,
    SkillDependency,
    SkillDescriptor,
    SkillFactory,
    SkillLifecycleState,
    SkillManifest,
    SkillRegistry,
    SkillRepository,
    SkillResource,
    SkillRuntime,
    SkillSerializer,
    SkillTrigger,
    SkillVisibility,
    advance_skill,
    content_hash,
    descriptor_id_for,
    validate_manifest,
)
from skill.stores import InMemoryDocumentStore


def _manifest(**kw) -> SkillManifest:
    base = dict(id="greet", namespace="internal", version="1.0.0",
                description="Greets a person.", instructions="Say hello politely.")
    base.update(kw)
    return SkillManifest(**base)


def _runtime(config=None, *, skills=(), **kwargs):
    cfg = config or SkillConfiguration.default()
    kwargs.setdefault("clock", lambda: 100.0)
    return SkillFactory.create(cfg, skills=skills, **kwargs)


# ── deterministic ids ──────────────────────────────────────────────────────────
def test_ids_are_deterministic_and_content_derived():
    assert content_hash("a", "b") == content_hash("a", "b")
    assert content_hash("a", "b") != content_hash("b", "a")
    assert descriptor_id_for("internal", "greet", "1.0.0") == descriptor_id_for(
        "internal", "greet", "1.0.0"
    )
    assert descriptor_id_for("internal", "greet", "1.0.0") != descriptor_id_for(
        "internal", "greet", "2.0.0"
    )


# ── SkillManifest / SkillDescriptor ────────────────────────────────────────────
def test_manifest_is_immutable():
    m = _manifest()
    with pytest.raises(FrozenInstanceError):
        m.id = "other"  # type: ignore[misc]


def test_manifest_qualified_name():
    m = _manifest(namespace="org.acme", id="foo")
    assert m.qualified_name == "org.acme.foo"


def test_manifest_defaults():
    m = _manifest()
    assert m.interface_version == "1.0"
    assert m.visibility is SkillVisibility.PUBLIC
    assert m.required_capabilities == frozenset()
    assert m.dependencies == ()


def test_descriptor_is_distinct_object_from_manifest():
    m = _manifest()
    d = SkillDescriptor(manifest=m)
    d2 = d.with_state(SkillLifecycleState.REGISTERED)
    assert d.state is SkillLifecycleState.DRAFT
    assert d2.state is SkillLifecycleState.REGISTERED
    assert d2.manifest is m  # never mutated, never re-derived


# ── Manifest validation ────────────────────────────────────────────────────────
def test_validate_manifest_accepts_well_formed():
    ok, errors = validate_manifest(_manifest())
    assert ok
    assert errors == []


def test_validate_manifest_rejects_empty_id():
    ok, errors = validate_manifest(_manifest(id=""))
    assert not ok
    assert any("id" in e for e in errors)


def test_validate_manifest_rejects_unsupported_interface_version():
    ok, errors = validate_manifest(_manifest(interface_version="99.0"))
    assert not ok
    assert any("interface_version" in e for e in errors)


def test_validate_manifest_rejects_empty_capability_tag():
    req = SkillCapabilityRequirement(kind=CapabilityKind.TOOL, tag="")
    ok, errors = validate_manifest(_manifest(required_capabilities=frozenset({req})))
    assert not ok
    assert any("tag" in e for e in errors)


def test_validate_manifest_rejects_malformed_dependency():
    ok, errors = validate_manifest(
        _manifest(dependencies=(SkillDependency(namespace="", id=""),))
    )
    assert not ok


def test_validate_manifest_rejects_resource_without_uri():
    ok, errors = validate_manifest(_manifest(resources=(SkillResource(uri=""),)))
    assert not ok


# ── Lifecycle FSM ──────────────────────────────────────────────────────────────
def test_lifecycle_full_happy_path():
    d = SkillDescriptor(manifest=_manifest())
    d = advance_skill(d, SkillTrigger.REGISTER)
    assert d.state is SkillLifecycleState.REGISTERED
    d = advance_skill(d, SkillTrigger.ACTIVATE)
    assert d.state is SkillLifecycleState.ACTIVE
    d = advance_skill(d, SkillTrigger.DEPRECATE)
    assert d.state is SkillLifecycleState.DEPRECATED
    d = advance_skill(d, SkillTrigger.RETIRE)
    assert d.state is SkillLifecycleState.RETIRED


def test_lifecycle_direct_retire_from_registered():
    d = SkillDescriptor(manifest=_manifest())
    d = advance_skill(d, SkillTrigger.REGISTER)
    d = advance_skill(d, SkillTrigger.RETIRE)
    assert d.state is SkillLifecycleState.RETIRED


def test_lifecycle_direct_retire_from_active():
    d = SkillDescriptor(manifest=_manifest())
    d = advance_skill(d, SkillTrigger.REGISTER)
    d = advance_skill(d, SkillTrigger.ACTIVATE)
    d = advance_skill(d, SkillTrigger.RETIRE)
    assert d.state is SkillLifecycleState.RETIRED


def test_lifecycle_rejects_illegal_transition():
    d = SkillDescriptor(manifest=_manifest())
    with pytest.raises(FSMError):
        advance_skill(d, SkillTrigger.ACTIVATE)  # DRAFT has no ACTIVATE edge


def test_lifecycle_rejects_transition_from_terminal_state():
    d = SkillDescriptor(manifest=_manifest())
    d = advance_skill(d, SkillTrigger.REGISTER)
    d = advance_skill(d, SkillTrigger.RETIRE)
    with pytest.raises(FSMError):
        advance_skill(d, SkillTrigger.ACTIVATE)


def test_no_second_execution_fsm_exists():
    """A deliberate, documented asymmetry from Tool Runtime: exactly one
    lifecycle FSM, no execution-lifecycle state machine anywhere."""
    import skill

    assert not hasattr(skill, "ExecutionState")
    assert not hasattr(skill, "advance_execution")


# ── Registry: registration, namespacing, version resolution ───────────────────
def test_registry_register_and_get_descriptor():
    reg = SkillRegistry(clock=lambda: 10.0)
    d = reg.register(_manifest())
    assert d.state is SkillLifecycleState.REGISTERED
    fetched = reg.get_descriptor("internal", "greet", "1.0.0")
    assert fetched == d


def test_registry_rejects_invalid_manifest():
    reg = SkillRegistry()
    with pytest.raises(ConfigError):
        reg.register(_manifest(id=""))


def test_registry_rejects_duplicate_registration():
    reg = SkillRegistry()
    reg.register(_manifest())
    with pytest.raises(ConfigError):
        reg.register(_manifest())


def test_registry_unpinned_resolution_picks_highest_active():
    reg = SkillRegistry(clock=lambda: 10.0)
    reg.register(_manifest(version="1.0.0"))
    reg.activate("internal", "greet", "1.0.0")
    reg.register(_manifest(version="2.0.0"))
    reg.activate("internal", "greet", "2.0.0")
    resolved = reg.get_descriptor("internal", "greet")
    assert resolved.version == "2.0.0"


def test_registry_unpinned_resolution_falls_back_when_none_active():
    reg = SkillRegistry(clock=lambda: 10.0)
    reg.register(_manifest(version="1.0.0"))
    reg.register(_manifest(version="2.0.0"))  # neither activated
    resolved = reg.get_descriptor("internal", "greet")
    assert resolved.version == "2.0.0"  # highest registered, any state


def test_registry_namespace_isolation():
    reg = SkillRegistry()
    reg.register(_manifest(namespace="internal"))
    reg.register(_manifest(namespace="org.acme"))
    assert set(reg.names()) == {"internal.greet", "org.acme.greet"}


def test_registry_unknown_skill_raises():
    reg = SkillRegistry()
    with pytest.raises(ConfigError):
        reg.get_descriptor("internal", "nope")


def test_registry_versions_for_sorted_numerically():
    reg = SkillRegistry()
    reg.register(_manifest(version="2.0.0"))
    reg.register(_manifest(version="10.0.0"))
    reg.register(_manifest(version="1.0.0"))
    assert reg.versions_for("internal", "greet") == ("1.0.0", "2.0.0", "10.0.0")


def test_registry_list_excludes_non_active_by_default():
    reg = SkillRegistry()
    reg.register(_manifest())
    assert reg.list() == ()
    reg.activate("internal", "greet", "1.0.0")
    assert len(reg.list()) == 1


def test_registry_list_scoped_filters_by_namespace_and_capability():
    reg = SkillRegistry()
    req = SkillCapabilityRequirement(kind=CapabilityKind.TOOL, tag="calendar.create_event")
    reg.register(_manifest(id="a", namespace="internal", required_capabilities=frozenset({req})))
    reg.register(_manifest(id="b", namespace="org.acme"))
    reg.activate("internal", "a", "1.0.0")
    reg.activate("org.acme", "b", "1.0.0")
    scoped = reg.list_scoped(namespace="internal")
    assert {d.manifest.id for d in scoped} == {"a"}
    by_cap = reg.list_scoped(capability="calendar.create_event")
    assert {d.manifest.id for d in by_cap} == {"a"}


def test_registry_deregister_removes_entry():
    reg = SkillRegistry()
    reg.register(_manifest())
    reg.deregister("internal", "greet", "1.0.0")
    with pytest.raises(ConfigError):
        reg.get_descriptor("internal", "greet", "1.0.0")


def test_registry_owns_no_execution_logic():
    surface = {name for name in dir(SkillRegistry) if not name.startswith("_")}
    assert not ({"execute", "invoke", "run"} & surface)


# ── Dependency graph validation ────────────────────────────────────────────────
def test_dependency_must_already_be_registered():
    reg = SkillRegistry()
    with pytest.raises(ConfigError):
        reg.register(_manifest(id="composed",
                               dependencies=(SkillDependency(namespace="internal", id="base"),)))


def test_dependency_resolves_and_populates_closure():
    reg = SkillRegistry(clock=lambda: 10.0)
    reg.register(_manifest(id="base"))
    reg.activate("internal", "base", "1.0.0")
    composed = reg.register(_manifest(
        id="composed", dependencies=(SkillDependency(namespace="internal", id="base"),)
    ))
    assert composed.dependency_closure == (("internal", "base", "1.0.0"),)


def test_dependency_closure_is_transitive():
    reg = SkillRegistry(clock=lambda: 10.0)
    reg.register(_manifest(id="a"))
    reg.activate("internal", "a", "1.0.0")
    reg.register(_manifest(id="b", dependencies=(SkillDependency("internal", "a"),)))
    reg.activate("internal", "b", "1.0.0")
    c = reg.register(_manifest(id="c", dependencies=(SkillDependency("internal", "b"),)))
    assert ("internal", "a", "1.0.0") in c.dependency_closure
    assert ("internal", "b", "1.0.0") in c.dependency_closure


def test_dependency_graph_is_structurally_acyclic_by_registration_order():
    """A dependency must already be registered, so a true cycle in the
    concrete (namespace, id, version) graph cannot be constructed through the
    public API — a different version of the same id is a distinct, valid DAG
    node, not a cycle. Documents the design's own stated property."""
    reg = SkillRegistry(clock=lambda: 10.0)
    reg.register(_manifest(id="a", version="1.0.0"))
    reg.activate("internal", "a", "1.0.0")
    reg.register(_manifest(id="b", version="1.0.0",
                           dependencies=(SkillDependency("internal", "a", "1.0.0"),)))
    reg.activate("internal", "b", "1.0.0")
    # a new version of "a" depending on "b" is valid: a@2.0.0 -> b@1.0.0 -> a@1.0.0
    # is a DAG (a@2.0.0 and a@1.0.0 are distinct nodes), not a cycle.
    a2 = reg.register(_manifest(id="a", version="2.0.0",
                                dependencies=(SkillDependency("internal", "b", "1.0.0"),)))
    assert ("internal", "b", "1.0.0") in a2.dependency_closure


def test_dependency_cycle_guard_rejects_self_referential_closure():
    """White-box test of the defensive cycle guard: constructs an artificial
    scenario (a doctored dependency_closure already containing the new
    manifest's own identity) to exercise a branch the public API cannot
    otherwise reach, proving the guard is live code, not decoration."""
    reg = SkillRegistry(clock=lambda: 10.0)
    base = reg.register(_manifest(id="base"))
    reg.activate("internal", "base", "1.0.0")
    poisoned = replace(base, dependency_closure=(("internal", "new_skill", "1.0.0"),))
    reg._entries[("internal", "base", "1.0.0")] = poisoned  # noqa: SLF001 — deliberate white-box test

    with pytest.raises(ConfigError, match="cycle"):
        reg.register(_manifest(
            id="new_skill", dependencies=(SkillDependency(namespace="internal", id="base"),)
        ))


# ── Capability declaration model ──────────────────────────────────────────────
def test_capability_requirement_kinds():
    for kind in CapabilityKind:
        req = SkillCapabilityRequirement(kind=kind, tag="x")
        assert req.kind is kind


def test_capability_requirement_is_declared_never_resolved():
    """Skill Runtime never checks satisfiability itself — no method on the
    catalog or registry ever returns a satisfiability verdict."""
    surface = {name for name in dir(SkillCapabilityCatalog) if not name.startswith("_")}
    assert not ({"resolve", "satisfy", "can_satisfy"} & surface)


# ── Capability Catalog ─────────────────────────────────────────────────────────
def test_catalog_capabilities_for_and_discover():
    reg = SkillRegistry(clock=lambda: 10.0)
    req = SkillCapabilityRequirement(kind=CapabilityKind.MEMORY, tag="memory.read")
    reg.register(_manifest(required_capabilities=frozenset({req})))
    reg.activate("internal", "greet", "1.0.0")
    catalog = SkillCapabilityCatalog(reg)
    assert catalog.capabilities_for("internal", "greet") == frozenset({"memory.read"})
    assert catalog.discover("memory.read") == ("internal.greet",)
    assert catalog.requires("internal", "greet", "memory.read", kind=CapabilityKind.MEMORY)


def test_catalog_is_separate_object_from_registry():
    reg = SkillRegistry()
    catalog = SkillCapabilityCatalog(reg)
    assert catalog is not reg
    assert not hasattr(catalog, "register")


# ── Serialization round-trips ──────────────────────────────────────────────────
def test_serializer_manifest_round_trip():
    s = SkillSerializer()
    req = SkillCapabilityRequirement(kind=CapabilityKind.PROVIDER, tag="provider.reason",
                                     optional=True)
    dep = SkillDependency(namespace="internal", id="base", version="1.0.0")
    res = SkillResource(uri="file://x", content_type="text/markdown", content_hash="abc")
    m = _manifest(required_capabilities=frozenset({req}), dependencies=(dep,), resources=(res,),
                 tags=frozenset({"greeting"}), owner="team-x", metadata={"k": "v"})
    doc = s.manifest_to_dict(m)
    assert s.manifest_from_dict(doc) == m


def test_serializer_descriptor_round_trip():
    reg = SkillRegistry(clock=lambda: 10.0)
    reg.register(_manifest(id="base"))
    reg.activate("internal", "base", "1.0.0")
    d = reg.register(_manifest(id="composed",
                               dependencies=(SkillDependency("internal", "base"),)))
    s = SkillSerializer()
    doc = s.descriptor_to_document(d)
    assert s.descriptor_from_document(doc) == d


# ── Repository (temporary) ────────────────────────────────────────────────────
async def test_repository_registration_round_trip():
    from skill.config import CollectionsConfig

    store = InMemoryDocumentStore()
    repo = SkillRepository(store, SkillSerializer(), collections=CollectionsConfig())
    reg = SkillRegistry(clock=lambda: 10.0)
    descriptor = reg.register(_manifest())

    reg_id = await repo.add_registration(descriptor)
    fetched = await repo.get_registration(reg_id)
    assert fetched == descriptor
    assert await repo.registration_count() == 1

    all_regs = await repo.all_registrations()
    assert descriptor in all_regs


# ── SkillRuntime facade + SkillFactory ─────────────────────────────────────────
def test_runtime_register_and_activate_via_factory():
    rt = _runtime(skills=[_manifest()])
    d = rt.get_descriptor("internal", "greet")
    assert d.state is SkillLifecycleState.ACTIVE  # auto_activate default True


def test_runtime_register_records_registration_history():
    rt = _runtime()
    rt.register(_manifest())
    assert rt.statistics().registrations == 1
    assert len(rt.registrations()) == 1


def test_runtime_statistics_reports_registered_skills():
    rt = _runtime(skills=[_manifest(id="a"), _manifest(id="b")])
    stats = rt.statistics()
    assert set(stats.skills) == {"internal.a", "internal.b"}
    assert stats.healthy


def test_runtime_owns_no_execution_or_planning_logic():
    surface = {name for name in dir(SkillRuntime) if not name.startswith("_")}
    forbidden = {"execute", "invoke", "run", "plan", "resolve_capability"}
    assert not (forbidden & surface)


def test_factory_default_builds_empty_runtime():
    rt = SkillFactory.default()
    assert rt.registry.names() == ()


def test_factory_unknown_store_kind_fails_fast():
    from skill.config import StoreConfig

    cfg = SkillConfiguration(document_store=StoreConfig(kind="postgres"))
    with pytest.raises(ConfigError):
        SkillFactory.create(cfg)


# ── Configuration ──────────────────────────────────────────────────────────────
def test_configuration_rejects_non_positive_ttl():
    with pytest.raises(ConfigError):
        SkillConfiguration(registration_ttl_s=0.0)


def test_configuration_rejects_empty_namespace():
    with pytest.raises(ConfigError):
        SkillConfiguration(default_namespace="")


def test_configuration_default_is_valid():
    assert SkillConfiguration.default().default_namespace == "internal"


# ── Determinism ────────────────────────────────────────────────────────────────
def test_registration_is_deterministic_for_identical_input():
    reg1 = SkillRegistry(clock=lambda: 100.0)
    reg2 = SkillRegistry(clock=lambda: 100.0)
    d1 = reg1.register(_manifest())
    d2 = reg2.register(_manifest())
    assert d1 == d2


def test_manifest_hash_ids_never_use_random_or_wall_clock():
    assert descriptor_id_for("internal", "greet", "1.0.0") == descriptor_id_for(
        "internal", "greet", "1.0.0"
    )


# ── Dependency rules (skill stays acyclic; no ownership leakage) ─────────────
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


def test_skill_dependency_rules():
    # skill depends only on the foundation (core) plus its own package; never
    # Tool Runtime (an explicit M6.2B-1 constraint), never Memory/Knowledge/
    # Provider, never any sibling runtime, never the bus implementation.
    forbidden = {"runtime", "api", "gateway", "replay", "events", "memory", "knowledge",
                 "context", "prompt", "reasoning", "provider", "bridge", "pipeline",
                 "voicebot", "observability", "tenant", "tool"}
    assert not (_imports("skill") & forbidden), _imports("skill")


def test_skill_runtime_owns_no_planning_reasoning_workflow_or_prompt_logic():
    surface = {name for name in dir(SkillFactory.default()) if not name.startswith("_")}
    forbidden_methods = {"plan", "reason", "infer", "orchestrate_workflow", "render_prompt",
                         "route_provider", "assemble_context", "resolve_capability"}
    assert not (forbidden_methods & surface)
