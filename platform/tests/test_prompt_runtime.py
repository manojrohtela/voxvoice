"""Prompt Runtime core tests (M5.4B-1).

Covers deterministic ids, the package lifecycle (`core.fsm`), template precedence, section
generation (from the frozen `AssembledContext` only), formatting, provider-neutral rendering,
PromptPackage creation + immutability, the serializer round-trips, temporary-repository
isolation, the runtime surface (build/render/register/get/remove/history/expire/statistics),
`PromptPort` compliance, replay/gateway compatibility, determinism, and dependency rules.
Budget/validation/policy/tenancy intelligence + provider adapters are non-goals here.
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
from core.ports.context import AssembledContext, ContextItem, ContextRequest
from core.ports.prompt import PromptFragment, PromptPort
from events.bus import InProcessEventBus
from prompt import (
    GenerationParameters,
    PackageState,
    PackageTrigger,
    PromptConfiguration,
    PromptFactory,
    PromptLineage,
    PromptMessage,
    PromptPackage,
    PromptRequest,
    PromptRole,
    PromptScope,
    PromptSection,
    PromptSerializer,
    SectionKind,
    Template,
    TemplateLevel,
    TemplateRegistry,
    TemplateResolver,
    advance_package,
    package_id_for,
    prompt_hash_for,
    section_id_for,
    template_version_for,
)
from prompt.config import TokenizerConfig
from prompt.stores import DefaultModelProfile, WordTokenizer


def _ids():
    return (f"p{i:02d}" for i in itertools.count()).__next__


def _ctx(**kw):
    items = kw.pop("items", None)
    if items is None:
        items = (
            ContextItem(kind="system_instruction", text="You are a cricket coach.", score=1.0,
                        provenance={"source_id": "sys1", "content_hash": "h1"}),
            ContextItem(kind="knowledge_passage", text="Cover drive off the front foot.",
                        score=0.9, provenance={"source_id": "k1", "content_hash": "h2"}),
            ContextItem(kind="conversation_history", text="earlier: footwork", score=0.6,
                        provenance={"source_id": "m1", "content_hash": "h3"}),
            ContextItem(kind="user_instruction", text="How do I improve?", score=1.0,
                        provenance={"source_id": "u1", "content_hash": "h4"}),
        )
    return AssembledContext(request=ContextRequest(session_id="s1", org_id="o1"), items=items)


def _runtime(config=None, **kwargs):
    cfg = config or PromptConfiguration.default()
    kwargs.setdefault("clock", lambda: 100.0)
    return PromptFactory.create(cfg, id_factory=_ids(), **kwargs)


def _req(**kw):
    base = dict(session_id="s1", org_id="o1", conversation_id="c1", user_id="u1")
    base.update(kw)
    return PromptRequest(**base)


# ── deterministic ids ──────────────────────────────────────────────────────────
def test_ids_are_deterministic_and_content_derived():
    assert section_id_for("system", "system", "x") == section_id_for("system", "system", "x")
    assert section_id_for("system", "system", "a") != section_id_for("system", "system", "b")
    assert template_version_for("t", "body") == template_version_for("t", "body")
    assert package_id_for("s", "h", 0) != package_id_for("s", "h", 1)
    assert prompt_hash_for(("a", "b")) != prompt_hash_for(("b", "a"))


# ── lifecycle (core.fsm) ─────────────────────────────────────────────────────────
def _pkg(state=PackageState.REQUESTED):
    return PromptPackage(id="p", session_id="s1", scope=PromptScope(), state=state)


def test_package_lifecycle_full_path():
    p = _pkg()
    for trig, want in [
        (PackageTrigger.BUILD, PackageState.BUILT),
        (PackageTrigger.VALIDATE, PackageState.VALIDATED),
        (PackageTrigger.RENDER, PackageState.RENDERED),
        (PackageTrigger.DELIVER, PackageState.DELIVERED),
        (PackageTrigger.EXPIRE, PackageState.EXPIRED),
    ]:
        p = advance_package(p, trig)
        assert p.state is want


def test_package_illegal_transition_raises():
    with pytest.raises(FSMError):
        advance_package(_pkg(), PackageTrigger.RENDER)  # cannot render from REQUESTED


def test_package_reject_paths():
    for st in (PackageState.BUILT, PackageState.VALIDATED, PackageState.RENDERED):
        assert advance_package(_pkg(st), PackageTrigger.REJECT).state is PackageState.REJECTED


# ── templates + precedence ───────────────────────────────────────────────────────
def test_template_precedence_override_beats_all():
    reg = TemplateRegistry()
    reg.register("system", "ORG BODY {{content}}", level=TemplateLevel.ORGANIZATION, org_id="o1")
    res = TemplateResolver(reg)
    # override wins
    t = res.resolve("system", org_id="o1", overrides={"system": "OVERRIDE {{content}}"})
    assert t.level is TemplateLevel.OVERRIDE and "OVERRIDE" in t.body
    # org beats builtin
    t2 = res.resolve("system", org_id="o1")
    assert t2.level is TemplateLevel.ORGANIZATION
    # builtin fallback
    t3 = res.resolve("system")
    assert t3.level is TemplateLevel.BUILTIN


def test_template_render_is_pure_substitution():
    t = Template(name="system", body="Hi {{name}}: {{content}}", level=TemplateLevel.BUILTIN,
                 version="v", role=PromptRole.SYSTEM)
    assert t.render(content="C", variables={"name": "Coach"}) == "Hi Coach: C"


def test_template_versioning_is_content_hash():
    reg = TemplateRegistry()
    a = reg.register("system", "body A")
    b = reg.register("system", "body B")
    assert a.version != b.version


# ── section generation (from AssembledContext only) ──────────────────────────────
def test_section_generation_maps_context_and_preserves_lineage():
    rt = _runtime()
    pkg = rt.build(_req(), _ctx())
    kinds = [s.kind for s in pkg.sections]
    assert SectionKind.SYSTEM in kinds and SectionKind.KNOWLEDGE_CONTEXT in kinds
    assert SectionKind.USER in kinds
    sys_section = pkg.section(SectionKind.SYSTEM)[0]
    # lineage: template source + context source preserved
    assert sys_section.lineage.template_id == "system"
    assert sys_section.lineage.template_version
    assert "sys1" in sys_section.lineage.context_sources
    assert sys_section.lineage.context_provenance[0]["content_hash"] == "h1"


def test_section_generator_consumes_only_assembled_context():
    import inspect

    from prompt.sections import SectionGenerator
    src = inspect.getsource(SectionGenerator)
    # never imports/queries memory/knowledge/context packages
    assert "import memory" not in src and "import knowledge" not in src


# ── formatting + rendering ───────────────────────────────────────────────────────
def test_formatting_orders_sections_deterministically():
    rt = _runtime()
    pkg = rt.build(_req(), _ctx())
    order = [s.kind for s in pkg.sections]
    assert order.index(SectionKind.SYSTEM) < order.index(SectionKind.KNOWLEDGE_CONTEXT)
    assert order.index(SectionKind.KNOWLEDGE_CONTEXT) < order.index(SectionKind.USER)


def test_rendering_produces_neutral_messages():
    rt = _runtime()
    pkg = rt.build(_req(), _ctx())
    assert pkg.messages and all(isinstance(m, PromptMessage) for m in pkg.messages)
    assert pkg.messages[0].role is PromptRole.SYSTEM
    # message content matches the (formatted) system section
    assert pkg.messages[0].content == pkg.section(SectionKind.SYSTEM)[0].content


# ── package creation + naive budget ──────────────────────────────────────────────
def test_build_reaches_delivered_and_is_self_sufficient():
    rt = _runtime()
    pkg = rt.build(_req(), _ctx())
    assert pkg.state is PackageState.DELIVERED
    assert pkg.messages and pkg.prompt_hash and pkg.metadata.provider == "neutral"
    assert pkg.generation is not None  # carried generation params ride along


def test_naive_budget_truncates_context_not_instructions():
    model = DefaultModelProfile(window=6, completion_reserve=2,
                                tokenizer=WordTokenizer())  # limit 4
    rt = _runtime(model_profile=model)
    pkg = rt.build(_req(), _ctx())
    assert pkg.truncated
    kinds = {s.kind for s in pkg.sections}
    assert SectionKind.SYSTEM in kinds and SectionKind.USER in kinds  # instructions kept
    assert SectionKind.KNOWLEDGE_CONTEXT not in kinds  # context dropped


def test_generation_parameters_carried_not_interpreted():
    rt = _runtime()
    gen = GenerationParameters(temperature=0.7, max_tokens=256, stop=("</end>",))
    pkg = rt.build(_req(generation=gen), _ctx())
    assert pkg.generation == gen  # verbatim on the package for Reasoning to apply


# ── immutability ─────────────────────────────────────────────────────────────────
def test_package_and_section_are_frozen():
    rt = _runtime()
    pkg = rt.build(_req(), _ctx())
    with pytest.raises(FrozenInstanceError):
        pkg.truncated = True
    with pytest.raises(FrozenInstanceError):
        pkg.sections[0].content = "x"


# ── serializer round-trips ───────────────────────────────────────────────────────
def test_serializer_package_round_trip():
    s = PromptSerializer()
    rt = _runtime()
    rt.build(_req(), _ctx())
    pkg = rt.history("s1")[0]
    assert s.package_from_document(s.package_to_document(pkg)) == pkg


def test_serializer_request_round_trip():
    s = PromptSerializer()
    req = _req(variables={"a": "b"}, generation=GenerationParameters(temperature=0.5))
    assert s.request_from_dict(s.request_to_dict(req)) == req


def test_serializer_section_round_trip():
    s = PromptSerializer()
    sec = PromptSection(id="x", role=PromptRole.USER, kind=SectionKind.USER, content="hi",
                        lineage=PromptLineage(template_id="user", template_version="v",
                                              context_sources=("u1",)), token_estimate=1)
    assert s.section_from_dict(s.section_to_dict(sec)) == sec


# ── repository isolation + temporary storage ─────────────────────────────────────
async def test_repository_persists_and_isolates():
    from prompt.repositories import PromptRepository
    from prompt.stores import (
        DefaultModelProfile,
        InMemoryDocumentStore,
        InMemoryMetadataStore,
        NeutralProviderProfile,
        PromptStore,
        WordTokenizer,
    )

    store = PromptStore(InMemoryDocumentStore(), InMemoryMetadataStore(), WordTokenizer(),
                        NeutralProviderProfile(), DefaultModelProfile())
    repo = PromptRepository(store, PromptSerializer())
    pkg = _pkg(PackageState.DELIVERED)
    await repo.add_package(pkg)
    assert (await repo.get_package(pkg.id)) == pkg
    await repo.remove_package(pkg.id)
    assert await repo.get_package(pkg.id) is None
    await repo.remove_package(pkg.id)  # idempotent


def test_runtime_get_remove_history():
    rt = _runtime()
    rt.build(_req(), _ctx())
    pkg = rt.history("s1")[0]
    assert rt.get(pkg.id) == pkg
    rt.remove(pkg.id)
    assert rt.get(pkg.id) is None
    rt.remove(pkg.id)  # idempotent


async def test_expire_sweeps_ttl_elapsed_packages():
    rt = _runtime(PromptConfiguration(package_ttl_s=10.0), clock=lambda: 100.0)
    rt.build(_req(), _ctx())
    assert await rt.expire(now=105.0) == 0
    assert await rt.expire(now=200.0) == 1
    assert rt.statistics().packages == 0


# ── PromptPort compliance ────────────────────────────────────────────────────────
def test_prompt_port_render_and_register():
    rt = _runtime()
    assert isinstance(rt, PromptPort)
    frag = rt.render("system", {"name": "Coach"})
    assert isinstance(frag, PromptFragment) and frag.template_id == "system"
    rt.register("system", "Custom system: {{content}}")
    frag2 = rt.render("system", {})
    assert "Custom system" in frag2.text  # shared registration now resolves


# ── replay / gateway compatibility ───────────────────────────────────────────────
def test_events_are_standard_envelopes():
    seen: list[Event] = []
    bus = InProcessEventBus(clock=lambda: 1.0)
    bus.subscribe(seen.append)
    rt = _runtime()
    rt.attach(bus)
    rt.build(_req(), _ctx())
    ev = [e for e in seen if e.source is EventSource.PROMPT]
    assert ev and all(e.seq > 0 for e in ev)
    assert EventType.PROMPT_RENDERED in {e.type for e in ev}
    phases = {e.data.get("phase") for e in ev}
    assert {"requested", "built", "validated", "rendered", "delivered"} <= phases


def test_register_emits_template_updated():
    seen: list[Event] = []
    bus = InProcessEventBus(clock=lambda: 1.0)
    bus.subscribe(seen.append)
    rt = _runtime()
    rt.attach(bus)
    rt.register("system", "New body {{content}}")
    assert any(e.type is EventType.PROMPT_TEMPLATE_UPDATED for e in seen)


def test_full_cycle_is_deterministic():
    def run():
        seen = []
        bus = InProcessEventBus(clock=lambda: 1.0)
        bus.subscribe(lambda e: seen.append((e.type.value, e.seq, e.data.get("phase"))))
        rt = PromptFactory.default(id_factory=_ids(), clock=lambda: 100.0)
        rt.attach(bus)
        pkg = rt.build(_req(), _ctx())
        return seen, pkg.prompt_hash
    assert run() == run()


# ── runtime lifecycle ────────────────────────────────────────────────────────────
async def test_runtime_module_lifecycle():
    from core.module import ModuleState
    rt = _runtime()
    await rt.initialize()
    await rt.start()
    assert rt.state is ModuleState.RUNNING and rt.health().healthy
    await rt.shutdown()
    assert rt.state is ModuleState.STOPPED


# ── factory + config ─────────────────────────────────────────────────────────────
def test_config_validation():
    with pytest.raises(ConfigError):
        TokenizerConfig(chars_per_token=0)
    with pytest.raises(ConfigError):
        PromptConfiguration(package_ttl_s=0)


def test_factory_rejects_unknown_store_kind():
    from prompt.config import StoreConfig
    cfg = PromptConfiguration(document_store=StoreConfig(kind="postgres"))
    with pytest.raises(ConfigError):
        PromptFactory.create(cfg)


def test_statistics():
    rt = _runtime()
    rt.build(_req(), _ctx())
    stats = rt.statistics()
    assert stats.packages == 1 and stats.templates >= 8 and stats.provider == "neutral"
    assert stats.healthy is False  # not started


# ── dependency rules (prompt stays acyclic) ──────────────────────────────────────
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


def test_prompt_dependency_rules():
    # prompt depends only on the foundation (core + tenant, the same isolation seam
    # Memory/Knowledge/Context use) plus its own package; never a sibling runtime, the bus
    # impl, the context/memory/knowledge packages, api, gateway, or replay. (M5.4B-2 tenancy
    # added the `tenant` foundation dep — a leaf package, not a runtime, so no cycle; DL-0032.)
    forbidden = {"runtime", "api", "gateway", "replay", "events", "memory", "knowledge",
                 "context", "voicebot", "observability"}
    assert not (_imports("prompt") & forbidden), _imports("prompt")
