"""Tool Runtime Core (M6.1B-1) tests.

Verifies deterministic execution, the registry (registration/discovery/lifecycle —
no execution logic), the capability catalog (no intelligence), manifest + argument
validation, classification metadata, both FSMs (descriptor + execution),
serialization round-trips, the temporary repository, replay/gateway-compatible
standard envelopes, and the acyclic `tool → core` dependency rule. Policy, approval,
secrets, an observability engine, streaming, and workflow/MCP/A2A integration are
non-goals here (M6.1B-2+).
"""

from __future__ import annotations

import ast
import itertools
import pathlib
from dataclasses import FrozenInstanceError

import pytest

from core.errors import ConfigError
from core.events import Event, EventSource, EventType
from core.fsm import FSMError
from events.bus import InProcessEventBus
from tool import (
    Classification,
    DescriptorState,
    DescriptorTrigger,
    EchoToolAdapter,
    ExecutionState,
    ExecutionTrigger,
    ToolAdapter,
    ToolCapabilityCatalog,
    ToolConfiguration,
    ToolDescriptor,
    ToolExecutionOutcome,
    ToolFactory,
    ToolManifest,
    ToolRegistry,
    ToolRequest,
    ToolResult,
    ToolSerializer,
    advance_descriptor,
    advance_execution,
    content_hash,
    descriptor_id_for,
    request_hash_for,
    result_hash_for,
    validate_arguments,
    validate_manifest,
)


def _ids():
    return (f"t{i:02d}" for i in itertools.count()).__next__


def _manifest(**kw) -> ToolManifest:
    base = dict(name="greet", namespace="internal", version="1.0.0",
                description="Greets a person.", capabilities=frozenset({"greet"}),
                inputs={"type": "object", "properties": {"name": {"type": "string"}},
                        "required": ["name"]})
    base.update(kw)
    return ToolManifest(**base)


class _GreetAdapter(ToolAdapter):
    name = "greet"
    capabilities = frozenset({"greet"})

    def execute(self, request: ToolRequest) -> ToolExecutionOutcome:
        return ToolExecutionOutcome(output={"hello": request.arguments["name"]})


class _BoomAdapter(ToolAdapter):
    name = "boom"
    capabilities = frozenset({"boom"})

    def execute(self, request: ToolRequest) -> ToolExecutionOutcome:
        raise RuntimeError("adapter exploded")


def _req(**kw) -> ToolRequest:
    base = dict(request_id="r1", tool="greet", namespace="internal",
                arguments={"name": "Ada"}, session_id="s1")
    base.update(kw)
    return ToolRequest(**base)


def _runtime(config=None, *, tools=(), **kwargs):
    cfg = config or ToolConfiguration.default()
    kwargs.setdefault("clock", lambda: 100.0)
    return ToolFactory.create(cfg, tools=tools, id_factory=_ids(), **kwargs)


def _greet_runtime(**kwargs):
    return _runtime(tools=[(_manifest(), _GreetAdapter())], **kwargs)


# ── deterministic ids ──────────────────────────────────────────────────────────
def test_ids_are_deterministic_and_content_derived():
    assert content_hash("a", "b") == content_hash("a", "b")
    assert content_hash("a", "b") != content_hash("b", "a")
    assert descriptor_id_for("internal", "greet", "1.0.0") == descriptor_id_for(
        "internal", "greet", "1.0.0"
    )
    assert request_hash_for("internal", "greet", "1.0.0", {"name": "Ada"}) == request_hash_for(
        "internal", "greet", "1.0.0", {"name": "Ada"}
    )
    assert request_hash_for("internal", "greet", "1.0.0", {"a": 1}) != request_hash_for(
        "internal", "greet", "1.0.0", {"a": 2}
    )
    assert result_hash_for("r", {"x": 1}) != result_hash_for("r", {"x": 2})


# ── ToolManifest / ToolDescriptor ─────────────────────────────────────────────────
def test_manifest_is_immutable():
    m = _manifest()
    with pytest.raises(FrozenInstanceError):
        m.name = "other"  # type: ignore[misc]


def test_manifest_qualified_name():
    assert _manifest(namespace="mcp.figma").qualified_name == "mcp.figma.greet"


def test_manifest_validation_rejects_empty_name():
    with pytest.raises(ConfigError):
        validate_manifest(_manifest(name=""))


def test_manifest_validation_rejects_empty_namespace():
    with pytest.raises(ConfigError):
        validate_manifest(_manifest(namespace=""))


def test_manifest_validation_rejects_bad_inputs_shape():
    with pytest.raises(ConfigError):
        validate_manifest(_manifest(inputs="not-a-mapping"))  # type: ignore[arg-type]


def test_manifest_validation_accepts_well_formed_manifest():
    validate_manifest(_manifest())  # does not raise


def test_descriptor_separate_from_manifest():
    manifest = _manifest()
    descriptor = ToolDescriptor(manifest=manifest)
    assert descriptor.manifest is manifest
    assert descriptor.qualified_name == manifest.qualified_name
    assert descriptor.version == manifest.version
    assert descriptor.state is DescriptorState.DRAFT
    advanced = descriptor.with_state(DescriptorState.REGISTERED)
    assert descriptor.state is DescriptorState.DRAFT  # original untouched
    assert advanced.state is DescriptorState.REGISTERED


# ── classification ────────────────────────────────────────────────────────────────
def test_classification_is_metadata_only():
    m = _manifest(classification=Classification.DESTRUCTIVE)
    assert m.classification is Classification.DESTRUCTIVE
    rt = _runtime(tools=[(m, _GreetAdapter())])
    result = rt.execute(_req())
    assert result.classification is Classification.DESTRUCTIVE  # carried, not enforced


def test_all_reserved_classifications_are_valid_members():
    for c in ("read_only", "read_write", "destructive", "internal", "external",
              "streaming", "async"):
        assert Classification(c) is not None


# ── argument validation ───────────────────────────────────────────────────────────
def test_validate_arguments_flags_missing_required_field():
    ok, errors = validate_arguments(_manifest(), {})
    assert not ok
    assert any("name" in e for e in errors)


def test_validate_arguments_flags_wrong_type():
    m = _manifest(inputs={"type": "object",
                          "properties": {"count": {"type": "integer"}},
                          "required": ["count"]})
    ok, errors = validate_arguments(m, {"count": "not-a-number"})
    assert not ok
    assert errors


def test_validate_arguments_accepts_well_formed_arguments():
    ok, errors = validate_arguments(_manifest(), {"name": "Ada"})
    assert ok
    assert errors == ()


def test_validate_arguments_ignores_unknown_extra_arguments():
    ok, _ = validate_arguments(_manifest(), {"name": "Ada", "extra": "ignored"})
    assert ok


# ── descriptor lifecycle (core.fsm) ───────────────────────────────────────────────
def _descriptor(state=DescriptorState.DRAFT):
    return ToolDescriptor(manifest=_manifest(), state=state)


def test_descriptor_lifecycle_happy_path():
    d = advance_descriptor(_descriptor(), DescriptorTrigger.REGISTER)
    assert d.state is DescriptorState.REGISTERED
    d = advance_descriptor(d, DescriptorTrigger.ACTIVATE)
    assert d.state is DescriptorState.ACTIVE
    d = advance_descriptor(d, DescriptorTrigger.DEPRECATE)
    assert d.state is DescriptorState.DEPRECATED
    d = advance_descriptor(d, DescriptorTrigger.RETIRE)
    assert d.state is DescriptorState.RETIRED


def test_descriptor_can_retire_directly_from_registered_or_active():
    registered = advance_descriptor(_descriptor(), DescriptorTrigger.REGISTER)
    retired = advance_descriptor(registered, DescriptorTrigger.RETIRE)
    assert retired.state is DescriptorState.RETIRED

    active = advance_descriptor(registered, DescriptorTrigger.ACTIVATE)
    retired2 = advance_descriptor(active, DescriptorTrigger.RETIRE)
    assert retired2.state is DescriptorState.RETIRED


def test_descriptor_illegal_transition_raises_fsm_error():
    with pytest.raises(FSMError):
        advance_descriptor(_descriptor(DescriptorState.RETIRED), DescriptorTrigger.ACTIVATE)
    with pytest.raises(FSMError):
        advance_descriptor(_descriptor(DescriptorState.DRAFT), DescriptorTrigger.ACTIVATE)


# ── execution lifecycle (core.fsm) ────────────────────────────────────────────────
def _result(state=ExecutionState.RECEIVED):
    return ToolResult(request_id="r1", state=state)


def test_execution_lifecycle_happy_path():
    r = advance_execution(_result(), ExecutionTrigger.VALIDATE)
    assert r.state is ExecutionState.VALIDATING
    r = advance_execution(r, ExecutionTrigger.EXECUTE)
    assert r.state is ExecutionState.EXECUTING
    r = advance_execution(r, ExecutionTrigger.COMPLETE)
    assert r.state is ExecutionState.COMPLETED


def test_execution_lifecycle_fail_from_any_active_state():
    for state in (ExecutionState.RECEIVED, ExecutionState.VALIDATING, ExecutionState.EXECUTING):
        r = advance_execution(_result(state), ExecutionTrigger.FAIL)
        assert r.state is ExecutionState.FAILED


def test_execution_lifecycle_cancel_from_any_pre_terminal_state():
    for state in (ExecutionState.RECEIVED, ExecutionState.VALIDATING, ExecutionState.EXECUTING):
        r = advance_execution(_result(state), ExecutionTrigger.CANCEL)
        assert r.state is ExecutionState.CANCELLED


def test_execution_lifecycle_no_authorizing_state_in_this_milestone():
    assert not hasattr(ExecutionState, "AUTHORIZING")


def test_execution_lifecycle_terminal_states_reject_further_triggers():
    with pytest.raises(FSMError):
        advance_execution(_result(ExecutionState.COMPLETED), ExecutionTrigger.FAIL)
    with pytest.raises(FSMError):
        advance_execution(_result(ExecutionState.FAILED), ExecutionTrigger.COMPLETE)


# ── ToolRegistry (registration / discovery / version resolution / namespacing) ────
def test_registry_register_returns_registered_descriptor():
    reg = ToolRegistry(clock=lambda: 1.0)
    d = reg.register(_manifest(), _GreetAdapter())
    assert d.state is DescriptorState.REGISTERED
    assert d.registered_at == 1.0


def test_registry_duplicate_registration_raises():
    reg = ToolRegistry()
    reg.register(_manifest(), _GreetAdapter())
    with pytest.raises(ConfigError):
        reg.register(_manifest(), _GreetAdapter())


def test_registry_unknown_lookup_raises_config_error():
    reg = ToolRegistry()
    with pytest.raises(ConfigError):
        reg.get_adapter("internal", "nope", "1.0.0")
    with pytest.raises(ConfigError):
        reg.get_descriptor("internal", "nope")


def test_registry_version_resolution_picks_highest_active():
    reg = ToolRegistry()
    reg.register(_manifest(version="1.0.0"), _GreetAdapter())
    reg.register(_manifest(version="2.0.0"), _GreetAdapter())
    reg.activate("internal", "greet", "1.0.0")
    reg.activate("internal", "greet", "2.0.0")
    resolved = reg.get_descriptor("internal", "greet")
    assert resolved.version == "2.0.0"


def test_registry_version_resolution_skips_non_active_when_others_active():
    reg = ToolRegistry()
    reg.register(_manifest(version="1.0.0"), _GreetAdapter())
    reg.register(_manifest(version="2.0.0"), _GreetAdapter())
    reg.activate("internal", "greet", "1.0.0")  # 2.0.0 left REGISTERED, not ACTIVE
    resolved = reg.get_descriptor("internal", "greet")
    assert resolved.version == "1.0.0"


def test_registry_explicit_version_pin_bypasses_resolution():
    reg = ToolRegistry()
    reg.register(_manifest(version="1.0.0"), _GreetAdapter())
    reg.register(_manifest(version="2.0.0"), _GreetAdapter())
    resolved = reg.get_descriptor("internal", "greet", "1.0.0")
    assert resolved.version == "1.0.0"


def test_registry_namespacing_isolates_identical_names():
    reg = ToolRegistry()
    reg.register(_manifest(namespace="internal"), _GreetAdapter())
    reg.register(_manifest(namespace="org.acme"), _GreetAdapter())
    reg.activate("internal", "greet", "1.0.0")
    reg.activate("org.acme", "greet", "1.0.0")
    assert reg.names() == ("internal.greet", "org.acme.greet")


def test_registry_list_excludes_draft_registered_and_retired():
    reg = ToolRegistry()
    reg.register(_manifest(), _GreetAdapter())
    assert reg.list() == ()  # REGISTERED, not yet ACTIVE
    reg.activate("internal", "greet", "1.0.0")
    assert len(reg.list()) == 1
    reg.deprecate("internal", "greet", "1.0.0")
    assert reg.list() == ()
    assert len(reg.list(include_deprecated=True)) == 1
    reg.retire("internal", "greet", "1.0.0")
    assert reg.list(include_deprecated=True) == ()


def test_registry_find_by_capability_sorted_deterministic():
    reg = ToolRegistry()
    reg.register(_manifest(name="zeta", capabilities=frozenset({"greet"})), _GreetAdapter())
    reg.register(_manifest(name="alpha", capabilities=frozenset({"greet"})), _GreetAdapter())
    reg.activate("internal", "zeta", "1.0.0")
    reg.activate("internal", "alpha", "1.0.0")
    found = reg.find("greet")
    assert [d.manifest.name for d in found] == ["alpha", "zeta"]


def test_registry_list_scoped_filters_by_namespace_and_capability():
    reg = ToolRegistry()
    reg.register(_manifest(namespace="internal", capabilities=frozenset({"greet"})),
                 _GreetAdapter())
    reg.register(_manifest(namespace="mcp.figma", capabilities=frozenset({"design"})),
                 _GreetAdapter())
    reg.activate("internal", "greet", "1.0.0")
    reg.activate("mcp.figma", "greet", "1.0.0")
    assert len(reg.list_scoped(namespace="internal")) == 1
    assert len(reg.list_scoped(capability="design")) == 1
    assert len(reg.list_scoped(namespace="mcp.figma", capability="greet")) == 0


def test_registry_owns_no_execution_logic():
    """The registry surface has no `execute`/`invoke`-shaped method — execution is
    `ToolRuntime`'s job only."""
    surface = {name for name in dir(ToolRegistry) if not name.startswith("_")}
    assert not ({"execute", "invoke", "run"} & surface)


# ── ToolCapabilityCatalog (separate from registry, no intelligence) ───────────────
def test_catalog_is_separate_object_from_registry():
    reg = ToolRegistry()
    catalog = ToolCapabilityCatalog(reg)
    assert catalog is not reg
    assert not isinstance(catalog, ToolRegistry)


def test_catalog_capabilities_for_and_supports():
    reg = ToolRegistry()
    reg.register(_manifest(), _GreetAdapter())
    reg.activate("internal", "greet", "1.0.0")
    catalog = ToolCapabilityCatalog(reg)
    assert catalog.capabilities_for("internal", "greet") == frozenset({"greet"})
    assert catalog.supports("internal", "greet", "greet")
    assert not catalog.supports("internal", "greet", "fly")


def test_catalog_discover_and_full_catalog():
    reg = ToolRegistry()
    reg.register(_manifest(), _GreetAdapter())
    reg.activate("internal", "greet", "1.0.0")
    catalog = ToolCapabilityCatalog(reg)
    assert catalog.discover("greet") == ("internal.greet",)
    assert catalog.catalog() == {"internal.greet": frozenset({"greet"})}


# ── EchoToolAdapter (deterministic offline default) ───────────────────────────────
def test_echo_adapter_is_deterministic():
    adapter = EchoToolAdapter()
    req = _req(tool="echo", arguments={"a": 1})
    out1 = adapter.execute(req)
    out2 = adapter.execute(req)
    assert out1.output == out2.output == {"echo": {"a": 1}}


def test_default_runtime_registers_echo_tool_active():
    rt = _runtime()
    names = [d.qualified_name for d in rt.list()]
    assert "internal.echo" in names


# ── execution (happy path + failures) ─────────────────────────────────────────────
def test_execute_happy_path_completes():
    rt = _greet_runtime()
    result = rt.execute(_req())
    assert result.state is ExecutionState.COMPLETED
    assert result.success is True
    assert result.output == {"hello": "Ada"}
    assert result.result_hash


def test_execute_unknown_tool_fails_without_dispatch():
    rt = _greet_runtime()
    result = rt.execute(_req(tool="nope"))
    assert result.state is ExecutionState.FAILED
    assert result.success is False
    assert "unknown_tool" in result.error


def test_execute_retired_tool_fails_without_dispatch():
    rt = _greet_runtime()
    rt.registry.deprecate("internal", "greet", "1.0.0")
    rt.registry.retire("internal", "greet", "1.0.0")
    result = rt.execute(_req())
    assert result.state is ExecutionState.FAILED
    assert result.error == "tool_retired"


def test_execute_deprecated_tool_still_invocable():
    rt = _greet_runtime()
    rt.registry.deprecate("internal", "greet", "1.0.0")
    result = rt.execute(_req(version="1.0.0"))
    assert result.state is ExecutionState.COMPLETED


def test_execute_validation_error_fails_before_dispatch():
    rt = _greet_runtime()
    result = rt.execute(_req(arguments={}))
    assert result.state is ExecutionState.FAILED
    assert "validation_error" in result.error
    assert "name" in result.error


def test_execute_adapter_exception_becomes_execution_error_result():
    manifest = _manifest(name="boom", capabilities=frozenset({"boom"}), inputs={})
    rt = _runtime(tools=[(manifest, _BoomAdapter())])
    result = rt.execute(_req(tool="boom", arguments={}))
    assert result.state is ExecutionState.FAILED
    assert "execution_error" in result.error


def test_execute_is_deterministic_for_identical_requests():
    def run():
        rt = _greet_runtime(clock=lambda: 100.0)
        return rt.execute(_req()).result_hash
    assert run() == run()


def test_execute_result_hash_differs_for_different_arguments():
    rt = _greet_runtime()
    r1 = rt.execute(_req(request_id="a", arguments={"name": "Ada"}))
    r2 = rt.execute(_req(request_id="b", arguments={"name": "Grace"}))
    assert r1.result_hash != r2.result_hash


# ── temporary repository (get/remove/history/expire) ──────────────────────────────
def test_get_remove_round_trip():
    rt = _greet_runtime()
    result = rt.execute(_req())
    assert rt.get(result.request_id) == result
    rt.remove(result.request_id)
    assert rt.get(result.request_id) is None
    rt.remove(result.request_id)  # idempotent


def test_history_returns_session_results_sorted():
    rt = _greet_runtime()
    rt.execute(_req(request_id="a", session_id="s1"))
    rt.execute(_req(request_id="b", session_id="s1"))
    rt.execute(_req(request_id="c", session_id="s2"))
    hist = rt.history("s1")
    assert [r.request_id for r in hist] == ["a", "b"]


async def test_expire_sweeps_ttl_elapsed_results():
    rt = _greet_runtime(config=ToolConfiguration(result_ttl_s=10.0), clock=lambda: 100.0)
    rt.execute(_req())
    assert await rt.expire(now=105.0) == 0
    assert await rt.expire(now=200.0) == 1
    assert rt.statistics().results == 0


# ── serialization round-trips ──────────────────────────────────────────────────────
def test_manifest_serialization_round_trip():
    s = ToolSerializer()
    m = _manifest(tags=frozenset({"comms"}), permissions=frozenset({"greet.send"}))
    assert s.manifest_from_dict(s.manifest_to_dict(m)) == m


def test_request_serialization_round_trip():
    s = ToolSerializer()
    r = _req()
    assert s.request_from_dict(s.request_to_dict(r)) == r


def test_result_serialization_round_trip():
    rt = _greet_runtime()
    result = rt.execute(_req())
    s = ToolSerializer()
    assert s.result_from_document(s.result_to_document(result)) == result


# ── configuration ──────────────────────────────────────────────────────────────────
def test_configuration_rejects_non_positive_ttl():
    with pytest.raises(ConfigError):
        ToolConfiguration(result_ttl_s=0)


def test_configuration_rejects_empty_namespace():
    with pytest.raises(ConfigError):
        ToolConfiguration(default_namespace="")


def test_configuration_default_is_all_in_memory():
    cfg = ToolConfiguration.default()
    assert cfg.document_store.kind == "in_memory"


# ── replay / gateway compatibility ────────────────────────────────────────────────
def test_events_are_standard_envelopes():
    seen: list[Event] = []
    bus = InProcessEventBus(clock=lambda: 1.0)
    bus.subscribe(seen.append)
    rt = _greet_runtime()
    rt.attach(bus)
    rt.execute(_req())
    ev = [e for e in seen if e.source is EventSource.TOOL]
    assert ev and all(e.seq > 0 for e in ev)
    types = {e.type for e in ev}
    assert EventType.TOOL_REQUESTED in types
    assert EventType.TOOL_COMPLETED in types
    phases = {e.data.get("phase") for e in ev}
    assert {"received", "validating", "executing", "completed"} <= phases


def test_failed_execution_emits_tool_failed():
    seen: list[Event] = []
    bus = InProcessEventBus(clock=lambda: 1.0)
    bus.subscribe(seen.append)
    rt = _greet_runtime()
    rt.attach(bus)
    rt.execute(_req(arguments={}))
    types = {e.type for e in seen if e.source is EventSource.TOOL}
    assert EventType.TOOL_FAILED in types


def test_full_cycle_is_deterministic():
    def run():
        seen = []
        bus = InProcessEventBus(clock=lambda: 1.0)
        bus.subscribe(lambda e: seen.append((e.type.value, e.seq, e.data.get("phase"))))
        rt = _greet_runtime(clock=lambda: 100.0)
        rt.attach(bus)
        result = rt.execute(_req())
        return seen, result.result_hash, result.output
    assert run() == run()


# ── runtime lifecycle ──────────────────────────────────────────────────────────────
async def test_runtime_module_lifecycle():
    from core.module import ModuleState
    rt = _greet_runtime()
    await rt.initialize()
    await rt.start()
    assert rt.state is ModuleState.RUNNING
    await rt.stop()
    assert rt.state is ModuleState.STOPPED


def test_runtime_does_not_ingest_content_events():
    rt = _greet_runtime()
    assert rt.should_handle(object()) is False  # type: ignore[arg-type]


# ── dependency rules (tool stays acyclic; no ownership leakage) ──────────────────
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


def test_tool_dependency_rules():
    # tool depends only on the foundation (core) plus its own package; never a
    # sibling runtime, the bus impl, pipeline, api, gateway, or replay. Reaching
    # into `pipeline` (or any sibling runtime) would create a cycle since the
    # pipeline is the downstream composition root that will import `tool` later,
    # never the reverse (DL-0044).
    forbidden = {"runtime", "api", "gateway", "replay", "events", "memory", "knowledge",
                 "context", "prompt", "reasoning", "provider", "bridge", "pipeline",
                 "voicebot", "observability", "tenant"}
    assert not (_imports("tool") & forbidden), _imports("tool")


def test_tool_runtime_owns_no_planning_reasoning_workflow_or_prompt_logic():
    """The public runtime surface never exposes planning/reasoning/workflow/prompt-
    shaped methods — those are Agent/Reasoning/Workflow/Prompt Runtime's job, never
    Tool Runtime's (§3 of the M6.1A design's ownership boundary)."""
    surface = {name for name in dir(ToolFactory.default()) if not name.startswith("_")}
    forbidden_methods = {"plan", "reason", "infer", "orchestrate_workflow", "render_prompt",
                         "route_provider", "assemble_context"}
    assert not (forbidden_methods & surface)
