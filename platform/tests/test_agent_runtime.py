"""Agent Runtime Core (M6.5B-1) tests.

Verifies manifest/descriptor validation, the registry (registration/
discovery/namespacing/version-resolution/lifecycle — no execution), the
goal-scoped execution lifecycle's *structure* (never wired to a live
record), serialization round-trips, the temporary registration repository,
diagnostics/statistics, determinism, and the `agent → core` dependency rule
(zero import of Tool Runtime, Skill Runtime, Hook Framework, or Workflow
Runtime, per this bundle's explicit constraint). Planning, reasoning,
workflow delegation, execution, replay, and observability are non-goals
here (later bundles).
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import FrozenInstanceError

import pytest

from agent import (
    AgentCapability,
    AgentConfiguration,
    AgentDescriptor,
    AgentFactory,
    AgentGoalState,
    AgentGoalTrigger,
    AgentLifecycleState,
    AgentManifest,
    AgentRegistry,
    AgentRepository,
    AgentRuntime,
    AgentSerializer,
    AgentTrigger,
    advance_agent,
    advance_goal_state,
    content_hash,
    descriptor_id_for,
    validate_manifest,
)
from agent.stores import InMemoryDocumentStore
from core.errors import ConfigError
from core.fsm import FSMError, TerminalStateError


def _manifest(id_: str = "ag", **kw) -> AgentManifest:
    base = dict(
        id=id_, namespace="internal", version="1.0.0", display_name="Test Agent",
        capabilities=(AgentCapability(kind="workflow", tag="onboarding"),),
    )
    base.update(kw)
    return AgentManifest(**base)


def _runtime(**kwargs) -> AgentRuntime:
    kwargs.setdefault("clock", lambda: 100.0)
    return AgentFactory.default(**kwargs)


def _registered_active(rt: AgentRuntime, manifest: AgentManifest) -> AgentDescriptor:
    d = rt.register(manifest)
    rt.activate(manifest.namespace, manifest.id, manifest.version)
    return d


# ── AgentManifest / AgentDescriptor ────────────────────────────────────────────
def test_manifest_is_immutable():
    m = _manifest()
    with pytest.raises(FrozenInstanceError):
        m.id = "other"  # type: ignore[misc]


def test_descriptor_is_immutable():
    d = AgentDescriptor(manifest=_manifest())
    with pytest.raises(FrozenInstanceError):
        d.state = AgentLifecycleState.ACTIVE  # type: ignore[misc]


def test_manifest_qualified_name():
    assert _manifest().qualified_name == "internal.ag"


def test_capability_carries_kind_tag_required():
    cap = AgentCapability(kind="tool", tag="send_email", required=False)
    assert cap.kind == "tool"
    assert cap.tag == "send_email"
    assert cap.required is False


# ── Validation ──────────────────────────────────────────────────────────────
def test_validate_manifest_accepts_well_formed():
    ok, errors = validate_manifest(_manifest())
    assert ok, errors


def test_validate_manifest_rejects_unsupported_interface_version():
    ok, errors = validate_manifest(_manifest(interface_version="9.9"))
    assert not ok
    assert any("interface_version" in e for e in errors)


def test_validate_manifest_rejects_empty_id():
    ok, errors = validate_manifest(_manifest(id=""))
    assert not ok
    assert any("id" in e for e in errors)


def test_validate_manifest_rejects_capability_with_empty_kind():
    ok, errors = validate_manifest(_manifest(capabilities=(AgentCapability(kind="", tag="x"),)))
    assert not ok
    assert any("kind" in e for e in errors)


def test_validate_manifest_rejects_capability_with_empty_tag():
    ok, errors = validate_manifest(_manifest(capabilities=(AgentCapability(kind="tool", tag=""),)))
    assert not ok
    assert any("tag" in e for e in errors)


# ── ids ───────────────────────────────────────────────────────────────────────
def test_ids_are_deterministic_and_content_derived():
    assert descriptor_id_for("internal", "ag", "1.0.0") == descriptor_id_for(
        "internal", "ag", "1.0.0"
    )
    assert descriptor_id_for("internal", "ag", "1.0.0") != descriptor_id_for(
        "internal", "ag", "2.0.0"
    )
    assert content_hash("a", "b") != content_hash("a", "c")


# ── Descriptor lifecycle (registration only) ───────────────────────────────────
def test_lifecycle_registers_then_activates():
    d = AgentDescriptor(manifest=_manifest())
    d = advance_agent(d, AgentTrigger.REGISTER, clock=lambda: 1.0)
    assert d.state is AgentLifecycleState.REGISTERED
    d = advance_agent(d, AgentTrigger.ACTIVATE, clock=lambda: 2.0)
    assert d.state is AgentLifecycleState.ACTIVE


def test_lifecycle_direct_retire_from_registered_and_active():
    d = AgentDescriptor(manifest=_manifest())
    d = advance_agent(d, AgentTrigger.REGISTER, clock=lambda: 1.0)
    d = advance_agent(d, AgentTrigger.RETIRE, clock=lambda: 2.0)
    assert d.state is AgentLifecycleState.RETIRED

    d2 = AgentDescriptor(manifest=_manifest())
    d2 = advance_agent(d2, AgentTrigger.REGISTER, clock=lambda: 1.0)
    d2 = advance_agent(d2, AgentTrigger.ACTIVATE, clock=lambda: 2.0)
    d2 = advance_agent(d2, AgentTrigger.RETIRE, clock=lambda: 3.0)
    assert d2.state is AgentLifecycleState.RETIRED


def test_lifecycle_rejects_illegal_transition():
    d = AgentDescriptor(manifest=_manifest())
    with pytest.raises(FSMError):
        advance_agent(d, AgentTrigger.ACTIVATE)  # DRAFT -> ACTIVATE is illegal


# ── Goal execution lifecycle (structure only — no live goal record) ──────────
def test_goal_lifecycle_linear_path():
    s = advance_goal_state(AgentGoalState.RECEIVED, AgentGoalTrigger.DECOMPOSE)
    assert s is AgentGoalState.DECOMPOSING
    s = advance_goal_state(s, AgentGoalTrigger.DELEGATE)
    assert s is AgentGoalState.DELEGATING
    s = advance_goal_state(s, AgentGoalTrigger.AWAIT)
    assert s is AgentGoalState.AWAITING_WORKFLOW
    s = advance_goal_state(s, AgentGoalTrigger.COMPLETE)
    assert s is AgentGoalState.COMPLETED


def test_goal_lifecycle_revision_loops_back_to_delegating():
    s = AgentGoalState.AWAITING_WORKFLOW
    s = advance_goal_state(s, AgentGoalTrigger.REVISE)
    assert s is AgentGoalState.REVISING
    s = advance_goal_state(s, AgentGoalTrigger.DELEGATE)
    assert s is AgentGoalState.DELEGATING


def test_goal_lifecycle_fails_from_awaiting_workflow():
    s = advance_goal_state(AgentGoalState.AWAITING_WORKFLOW, AgentGoalTrigger.FAIL)
    assert s is AgentGoalState.FAILED


@pytest.mark.parametrize(
    "state",
    [
        AgentGoalState.RECEIVED, AgentGoalState.DECOMPOSING, AgentGoalState.DELEGATING,
        AgentGoalState.AWAITING_WORKFLOW, AgentGoalState.REVISING,
    ],
)
def test_goal_lifecycle_cancel_reaches_abandoned_from_any_non_terminal_state(state):
    assert advance_goal_state(state, AgentGoalTrigger.CANCEL) is AgentGoalState.ABANDONED


def test_goal_lifecycle_rejects_illegal_transition():
    with pytest.raises(FSMError):
        advance_goal_state(AgentGoalState.RECEIVED, AgentGoalTrigger.COMPLETE)


def test_goal_lifecycle_terminal_states_refuse_any_trigger():
    with pytest.raises(TerminalStateError):
        advance_goal_state(AgentGoalState.COMPLETED, AgentGoalTrigger.CANCEL)
    with pytest.raises(TerminalStateError):
        advance_goal_state(AgentGoalState.ABANDONED, AgentGoalTrigger.DECOMPOSE)


def test_agent_goal_carries_no_state_field_in_this_bundle():
    """Structure only (§7.1 of the M6.5A design): `AgentGoal` itself has no
    `state` field in this bundle -- the goal lifecycle FSM is directly
    testable but nothing wires it to a live, stateful goal record yet."""
    from dataclasses import fields

    from agent.domain import AgentGoal

    names = {f.name for f in fields(AgentGoal)}
    assert "state" not in names


def test_execution_engine_module_did_not_exist_as_of_bundle_1():
    """Historical record: at Bundle 1 time, neither `agent.planning` nor
    `agent.execution` existed yet. Both are now legitimate, later bundles'
    own scope (Bundle 2 and Bundle 3 respectively) and exist from those
    bundles onward — asserting their absence here would go stale the moment
    each bundle landed, so this test now only documents the boundary (see
    `test_agent_planning.py` and `test_agent_execution.py` for those
    bundles' own suites)."""
    import importlib

    assert importlib.import_module("agent.planning") is not None
    assert importlib.import_module("agent.execution") is not None


# ── Registry ────────────────────────────────────────────────────────────────
def test_registry_register_then_lookup():
    reg = AgentRegistry(clock=lambda: 100.0)
    reg.register(_manifest())
    d = reg.get_descriptor("internal", "ag", "1.0.0")
    assert d.state is AgentLifecycleState.REGISTERED


def test_registry_rejects_duplicate_registration():
    reg = AgentRegistry(clock=lambda: 100.0)
    reg.register(_manifest())
    with pytest.raises(ConfigError):
        reg.register(_manifest())


def test_registry_rejects_invalid_manifest():
    reg = AgentRegistry(clock=lambda: 100.0)
    with pytest.raises(ConfigError):
        reg.register(_manifest(id=""))


def test_registry_namespace_isolation():
    reg = AgentRegistry(clock=lambda: 100.0)
    reg.register(_manifest(namespace="internal"))
    reg.activate("internal", "ag", "1.0.0")
    reg.register(_manifest(namespace="org.acme"))
    reg.activate("org.acme", "ag", "1.0.0")
    assert reg.names() == ("internal.ag", "org.acme.ag")
    assert reg.list_scoped(namespace="org.acme")[0].manifest.namespace == "org.acme"


def test_registry_version_resolution_prefers_active():
    reg = AgentRegistry(clock=lambda: 100.0)
    reg.register(_manifest(version="1.0.0"))
    reg.activate("internal", "ag", "1.0.0")
    reg.register(_manifest(version="2.0.0"))  # left REGISTERED, not ACTIVE
    chosen = reg.get_descriptor("internal", "ag")
    assert chosen.version == "1.0.0"


def test_registry_version_resolution_falls_back_to_latest_when_none_active():
    reg = AgentRegistry(clock=lambda: 100.0)
    reg.register(_manifest(version="1.0.0"))
    reg.register(_manifest(version="2.0.0"))
    chosen = reg.get_descriptor("internal", "ag")
    assert chosen.version == "2.0.0"


def test_registry_deregister_removes_entry():
    reg = AgentRegistry(clock=lambda: 100.0)
    reg.register(_manifest())
    reg.deregister("internal", "ag", "1.0.0")
    with pytest.raises(ConfigError):
        reg.get_descriptor("internal", "ag", "1.0.0")


def test_registry_has_no_execution_surface():
    forbidden = {"execute", "invoke", "run", "decompose", "resolve_capability", "delegate"}
    assert not (forbidden & set(dir(AgentRegistry)))


def test_list_scoped_filters_by_namespace():
    reg = AgentRegistry(clock=lambda: 100.0)
    reg.register(_manifest(namespace="internal"))
    reg.activate("internal", "ag", "1.0.0")
    reg.register(_manifest(namespace="org.acme"))
    reg.activate("org.acme", "ag", "1.0.0")
    assert len(reg.list_scoped(namespace="internal")) == 1


# ── Runtime facade ─────────────────────────────────────────────────────────────
def test_runtime_register_and_get_descriptor():
    rt = _runtime()
    _registered_active(rt, _manifest())
    d = rt.get_descriptor("internal", "ag")
    assert d.state is AgentLifecycleState.ACTIVE


def test_runtime_registrations_returns_registration_log():
    rt = _runtime()
    _registered_active(rt, _manifest())
    regs = rt.registrations()
    assert len(regs) == 1


def test_runtime_diagnostics_and_statistics():
    rt = _runtime()
    _registered_active(rt, _manifest())
    diag = rt.diagnostics()
    assert diag.health_summary == "healthy"
    assert diag.registry_stats["count"] == 1

    stats = rt.statistics()
    assert stats.agent_count == 1
    assert stats.per_capability_kind == {"workflow": 1}

    rstats = rt.runtime_statistics()
    assert rstats.agents == ("internal.ag",)
    assert rstats.registrations == 1


def test_runtime_has_no_execution_surface():
    forbidden = {
        "decompose", "resolve_capability", "select_workflow", "delegate", "run", "execute",
        "advance_goal",
    }
    assert not (forbidden & set(dir(AgentRuntime)))


def test_factory_rejects_unknown_store_kind():
    from dataclasses import replace

    cfg = AgentConfiguration.default()
    cfg = replace(cfg, document_store=replace(cfg.document_store, kind="s3"))
    with pytest.raises(ConfigError):
        AgentFactory.create(cfg)


# ── Serialization round-trips ───────────────────────────────────────────────────
def test_manifest_serialization_round_trips():
    ser = AgentSerializer()
    m = _manifest(
        display_name="Concierge", persona_prompt_id="persona.v1", owner="team-x",
        tags=frozenset({"support"}), metadata={"env": "prod"},
    )
    d = ser.manifest_to_dict(m)
    back = ser.manifest_from_dict(d)
    assert back == m


def test_manifest_with_multiple_capabilities_round_trips():
    ser = AgentSerializer()
    m = _manifest(
        capabilities=(
            AgentCapability(kind="workflow", tag="onboarding"),
            AgentCapability(kind="tool", tag="send_email", required=False),
        )
    )
    back = ser.manifest_from_dict(ser.manifest_to_dict(m))
    assert back.capabilities == m.capabilities


def test_descriptor_serialization_round_trips():
    ser = AgentSerializer()
    d = AgentDescriptor(
        manifest=_manifest(), state=AgentLifecycleState.ACTIVE, registered_at=123.0,
    )
    back = ser.descriptor_from_document(ser.descriptor_to_document(d))
    assert back == d


# ── Configuration ────────────────────────────────────────────────────────────
def test_configuration_rejects_non_positive_ttl():
    from dataclasses import replace

    with pytest.raises(ConfigError):
        replace(AgentConfiguration.default(), registration_ttl_s=0)


def test_configuration_fingerprint_is_deterministic():
    a = AgentConfiguration.default()
    b = AgentConfiguration.default()
    assert a.fingerprint() == b.fingerprint()


# ── Determinism ──────────────────────────────────────────────────────────────
def test_registration_is_deterministic_for_identical_input():
    reg1 = AgentRegistry(clock=lambda: 100.0)
    reg2 = AgentRegistry(clock=lambda: 100.0)
    d1 = reg1.register(_manifest())
    d2 = reg2.register(_manifest())
    assert d1 == d2


# ── Repository ────────────────────────────────────────────────────────────────
def test_repository_round_trips_a_registration():
    import asyncio

    async def run() -> None:
        store = InMemoryDocumentStore()
        ser = AgentSerializer()
        repo = AgentRepository(store, ser)
        d = AgentDescriptor(manifest=_manifest(), registered_at=1.0)
        reg_id = await repo.add_registration(d)
        back = await repo.get_registration(reg_id)
        assert back == d
        assert await repo.registration_count() == 1

    asyncio.run(run())


# ── Dependency rules (agent stays acyclic; no ownership leakage) ─────────────
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


def test_agent_dependency_rules():
    forbidden = {
        "runtime", "api", "gateway", "replay", "events", "memory", "knowledge",
        "context", "prompt", "reasoning", "provider", "bridge", "pipeline",
        "voicebot", "observability", "tenant", "tool", "skill", "hooks", "workflow",
    }
    assert not (_imports("agent") & forbidden), _imports("agent")


def test_agent_imports_only_core_and_own_package():
    allowed_extra = {
        "core", "__future__", "abc", "collections", "dataclasses", "enum", "typing",
        "hashlib", "copy", "time",
    }
    non_stdlib_like = {t for t in _imports("agent") if t not in allowed_extra}
    assert not non_stdlib_like, non_stdlib_like


def test_agent_extension_seams_remain_unimplemented():
    from abc import ABC

    from agent.extensions import (
        AgentCapabilityResolver,
        AgentHookAdapter,
        AgentObservabilitySink,
        AgentPolicyEngine,
        AgentReplaySink,
        AgentWorkflowPort,
    )

    for seam in (
        AgentCapabilityResolver, AgentWorkflowPort, AgentHookAdapter,
        AgentPolicyEngine, AgentObservabilitySink, AgentReplaySink,
    ):
        assert issubclass(seam, ABC)
        with pytest.raises(TypeError):
            seam()  # type: ignore[abstract]
