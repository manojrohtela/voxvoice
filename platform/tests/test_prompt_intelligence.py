"""Prompt Runtime intelligence tests (M5.4B-2).

Covers budget allocation (completion reserve, adaptive, overflow), provider + model
profiles, the tokenizer abstraction, the prompt fingerprint (provider-independent) + prompt
lineage, validation (verdicts), the policy engine, tenant isolation + provider allow-list,
extension interfaces, replay/gateway compatibility, determinism, and architecture
boundaries. The M5.4B-1 core tests remain in `test_prompt_runtime.py`.
"""

from __future__ import annotations

import ast
import inspect
import itertools
import pathlib

import pytest

from core.events import Event, EventSource
from core.ports.context import AssembledContext, ContextItem, ContextRequest
from events.bus import InProcessEventBus
from prompt import (
    AnthropicProviderProfile,
    DefaultModelProfile,
    EstimatedTokenizer,
    GeminiProviderProfile,
    GenericProviderProfile,
    LocalModelProviderProfile,
    MultiModalRenderer,
    OpenAIProviderProfile,
    PromptAnalyzer,
    PromptBudgetManager,
    PromptBudgetPolicy,
    PromptCitationPolicy,
    PromptCompressor,
    PromptConfiguration,
    PromptConsumer,
    PromptEnhancer,
    PromptFactory,
    PromptOptimizer,
    PromptPolicies,
    PromptPolicyEngine,
    PromptProviderPolicy,
    PromptRequest,
    PromptRewriter,
    PromptValidationEngine,
    SectionKind,
    Verdict,
    WordTokenizer,
    model_profiles,
)
from prompt.budget import _truncate_section
from prompt.domain import (
    PromptLineage,
    PromptPackage,
    PromptRole,
    PromptScope,
    PromptSection,
)
from prompt.policies import OverflowStrategy, PromptValidationPolicy
from prompt.tenancy import PromptAccessDenied, PromptTenancy
from tenant import OrganizationContext, TenantContext


def _ids():
    return (f"p{i:02d}" for i in itertools.count()).__next__


def _ctx(long_knowledge=False):
    kn = ("The cover drive is played off the front foot with a high elbow and a full "
          "follow through toward the target " * (8 if long_knowledge else 1)).strip()
    return AssembledContext(
        request=ContextRequest(session_id="s1", org_id="o1"),
        items=(
            ContextItem(kind="system_instruction", text="You are a cricket coach.", score=1.0,
                        provenance={"source_id": "sys1", "content_hash": "h1"}),
            ContextItem(kind="knowledge_passage", text=kn, score=0.9,
                        provenance={"source_id": "k1", "content_hash": "h2"}),
            ContextItem(kind="user_instruction", text="How do I improve my cover drive?",
                        score=1.0, provenance={"source_id": "u1", "content_hash": "h4"}),
        ),
    )


def _rt(config=None, *, intelligence=True, **kwargs):
    cfg = config or PromptConfiguration.default()
    kwargs.setdefault("clock", lambda: 100.0)
    return PromptFactory.create(cfg, intelligence=intelligence, id_factory=_ids(), **kwargs)


def _req(**kw):
    base = dict(session_id="s1", org_id="o1", conversation_id="c1", user_id="u1")
    base.update(kw)
    return PromptRequest(**base)


def _section(kind, role, content, sid, tokens):
    return PromptSection(id=sid, role=role, kind=kind, content=content,
                         lineage=PromptLineage(template_id=kind.value, template_version="v"),
                         token_estimate=tokens)


# ── budget allocation ────────────────────────────────────────────────────────
def test_budget_reserves_instructions_and_completion():
    mgr = PromptBudgetManager()
    model = DefaultModelProfile(window=10, completion_reserve=4)  # prompt ceiling 6
    sections = [
        _section(SectionKind.SYSTEM, PromptRole.SYSTEM, "a b", "s1", 2),
        _section(SectionKind.USER, PromptRole.USER, "q", "u1", 1),
        _section(SectionKind.KNOWLEDGE_CONTEXT, PromptRole.SYSTEM, "k k k k k k", "k1", 6),
    ]
    res = mgr.allocate(sections, limit=0, reserve=4, model=model)
    kinds = {s.kind for s in res.sections}
    assert SectionKind.SYSTEM in kinds and SectionKind.USER in kinds  # reserved kept
    assert SectionKind.KNOWLEDGE_CONTEXT not in kinds and res.truncated  # dropped over ceiling
    assert res.usage.completion_reserve == 4


def test_budget_adaptive_vs_strict_per_section():
    model = DefaultModelProfile(window=1000, completion_reserve=0)
    sections = [_section(SectionKind.KNOWLEDGE_CONTEXT, PromptRole.SYSTEM, "w " * 10, f"k{i}", 10)
                for i in range(3)]
    strict = PromptBudgetManager(policy=PromptBudgetPolicy(
        per_section_fraction={"knowledge_context": 0.01}, adaptive=False))
    adaptive = PromptBudgetManager(policy=PromptBudgetPolicy(
        per_section_fraction={"knowledge_context": 0.01}, adaptive=True))
    a = adaptive.allocate(sections, limit=1000, reserve=0, model=model)
    s = strict.allocate(sections, limit=1000, reserve=0, model=model)
    assert len(a.sections) >= len(s.sections)


def test_budget_truncate_lowest_strategy():
    model = DefaultModelProfile(window=100, completion_reserve=0)
    mgr = PromptBudgetManager(policy=PromptBudgetPolicy(overflow=OverflowStrategy.TRUNCATE_LOWEST))
    big = _section(SectionKind.KNOWLEDGE_CONTEXT, PromptRole.SYSTEM, "word " * 50, "k1", 50)
    sysx = _section(SectionKind.SYSTEM, PromptRole.SYSTEM, "sys", "s1", 60)
    res = mgr.allocate([sysx, big], limit=80, reserve=0, model=model)
    kn = [s for s in res.sections if s.kind is SectionKind.KNOWLEDGE_CONTEXT]
    assert kn and kn[0].content.endswith("…")  # trimmed variant kept + marked
    assert res.stats.truncated_sections == 1


def test_truncate_section_helper_is_deterministic():
    model = DefaultModelProfile(window=100, completion_reserve=0)
    sec = _section(SectionKind.KNOWLEDGE_CONTEXT, PromptRole.SYSTEM, "w " * 40, "k1", 40)
    a = _truncate_section(sec, 5, model)
    b = _truncate_section(sec, 5, model)
    assert a.content == b.content and a.token_estimate <= 6


# ── provider + model profiles ────────────────────────────────────────────────
def test_provider_profiles_map_roles_distinctly():
    secs = [
        _section(SectionKind.SYSTEM, PromptRole.SYSTEM, "sys", "s1", 1),
        _section(SectionKind.KNOWLEDGE_CONTEXT, PromptRole.SYSTEM, "kn", "k1", 1),
        _section(SectionKind.USER, PromptRole.USER, "q", "u1", 1),
    ]
    gemini = GeminiProviderProfile().render(secs)   # folds system → user only
    local = LocalModelProviderProfile().render(secs)  # one system + one user
    anthropic = AnthropicProviderProfile().render(secs)
    assert all(m.role is PromptRole.USER for m in gemini)
    assert len(local) == 2 and local[0].role is PromptRole.SYSTEM
    assert anthropic[0].role is PromptRole.SYSTEM


def test_provider_profile_capabilities_are_descriptors():
    assert OpenAIProviderProfile().capabilities["system_message"] is True
    assert GeminiProviderProfile().capabilities["system_message"] is False
    assert GenericProviderProfile().name == "generic"


def test_model_profiles_registry_and_metadata():
    models = model_profiles()
    assert set(models) >= {"default", "small", "large"}
    assert models["small"].context_window() == 4096
    assert models["large"].completion_reserve() == 4096
    assert isinstance(models["default"].capabilities, dict)


# ── tokenizer abstraction ────────────────────────────────────────────────────
def test_tokenizer_abstraction():
    # word-dominant estimate is bounded below by chars/cpt; "one two three" = 13 chars → 4
    assert WordTokenizer().count("one two three") == 4
    assert WordTokenizer().count("a b c d") == 4  # 4 short words dominate the char estimate
    assert EstimatedTokenizer(chars_per_token=4).count("abcdefgh") == 2  # pure char estimate
    assert WordTokenizer().count("") == 0


# ── prompt fingerprint (provider-independent) ────────────────────────────────
def test_fingerprint_stable_across_providers_but_hash_differs():
    ctx = _ctx()

    def build(provider):
        return _rt().build(_req(provider=provider), ctx)

    a, o, g = build("anthropic"), build("openai"), build("gemini")
    assert a.prompt_fingerprint == o.prompt_fingerprint == g.prompt_fingerprint  # semantic id
    assert len({a.prompt_hash, o.prompt_hash, g.prompt_hash}) == 3  # provider-sensitive hash


# ── prompt lineage ───────────────────────────────────────────────────────────
def test_lineage_records_template_context_provider_model():
    pkg = _rt().build(_req(provider="anthropic"), _ctx())
    sec = pkg.section(SectionKind.KNOWLEDGE_CONTEXT)[0]
    ln = sec.lineage
    assert ln.template_id == "knowledge_context" and ln.template_version
    assert "k1" in ln.context_sources and ln.context_provenance[0]["content_hash"] == "h2"
    assert ln.provider == "anthropic" and ln.model
    assert pkg.prompt_fingerprint and pkg.prompt_hash and pkg.metadata.provider == "anthropic"


# ── validation (verdicts only) ────────────────────────────────────────────────
def test_validation_flags_unresolved_placeholder():
    eng = PromptValidationEngine()
    sec = PromptSection(id="x", role=PromptRole.SYSTEM, kind=SectionKind.SYSTEM,
                        content="Hello {{missing}}",
                        lineage=PromptLineage(template_id="system", template_version="v"))
    pkg = PromptPackage(id="p", session_id="s", scope=PromptScope(),
                        state=__import__("prompt").PackageState.BUILT, sections=(sec,))
    report = eng.validate(pkg)
    assert not report.ok and any("placeholder" in r for r in report.reasons)


def test_validation_citation_verdict():
    pol = PromptPolicies(citation=PromptCitationPolicy(require_source_for_cited_kinds=True))
    eng = PromptValidationEngine(PromptPolicyEngine(pol))
    sec = PromptSection(
        id="k", role=PromptRole.SYSTEM, kind=SectionKind.KNOWLEDGE_CONTEXT, content="knowledge",
        lineage=PromptLineage(template_id="knowledge_context", template_version="v"))
    pkg = PromptPackage(id="p", session_id="s", scope=PromptScope(),
                        state=__import__("prompt").PackageState.BUILT, sections=(sec,))
    assert eng.validate(pkg).ok is False


def test_validation_rejection_in_runtime():
    pol = PromptPolicies(validation=PromptValidationPolicy(require_non_empty=True))
    rt = _rt(PromptConfiguration(policies=pol))
    # empty context → no sections → validation rejects
    empty = AssembledContext(request=ContextRequest(session_id="s1"))
    resp = __import__("prompt")._run.run_coro_sync(rt.abuild(_req(), empty))
    assert resp.delivered is False


# ── policy engine (verdicts, no I/O) ─────────────────────────────────────────
def test_policy_engine_has_no_io():
    src = inspect.getsource(PromptPolicyEngine)
    assert "await" not in src and "open(" not in src
    eng = PromptPolicyEngine()
    assert isinstance(eng.budget_ok(1, 10), Verdict)


def test_provider_policy_verdict():
    eng = PromptPolicyEngine(PromptPolicies(provider=PromptProviderPolicy(
        allowed_providers=frozenset({"anthropic"}))))
    assert eng.admit_provider("anthropic").ok
    assert not eng.admit_provider("openai").ok


# ── tenant isolation + provider allow-list ───────────────────────────────────
def _tenant(org="o1", user=None):
    return TenantContext(org=OrganizationContext(org_id=org), user_id=user)


def test_tenancy_blocks_cross_org():
    rt = _rt()
    with pytest.raises(PromptAccessDenied):
        rt.build_scoped(_req(org_id="o1"), _ctx(), _tenant(org="o2"))


def test_tenancy_provider_allow_list():
    t = PromptTenancy(allowed_providers=frozenset({"anthropic"}), clock=lambda: 1.0)
    with pytest.raises(PromptAccessDenied):
        t.guard(PromptScope(org_id="o1"), _tenant(org="o1"), provider="openai")
    ok = t.guard(PromptScope(org_id="o1"), _tenant(org="o1"), provider="anthropic")
    assert ok.scope.org_id == "o1"


def test_tenancy_runs_before_template_resolution():
    src = inspect.getsource(type(_rt()).abuild)
    assert src.index("_tenancy") < src.index("_builder.build")


# ── replay / gateway + determinism ───────────────────────────────────────────
def test_full_pipeline_is_deterministic():
    ctx = _ctx()

    def run():
        seen = []
        bus = InProcessEventBus(clock=lambda: 1.0)
        bus.subscribe(lambda e: seen.append((e.type.value, e.seq, e.data.get("phase"))))
        rt = PromptFactory.default(intelligence=True, id_factory=_ids(), clock=lambda: 100.0)
        rt.attach(bus)
        pkg = rt.build(_req(provider="openai"), ctx)
        return seen, pkg.prompt_hash, pkg.prompt_fingerprint

    assert run() == run()


def test_pipeline_emits_standard_envelopes():
    seen: list[Event] = []
    bus = InProcessEventBus(clock=lambda: 1.0)
    bus.subscribe(seen.append)
    rt = _rt()
    rt.attach(bus)
    rt.build(_req(provider="anthropic"), _ctx())
    ev = [e for e in seen if e.source is EventSource.PROMPT]
    phases = {e.data.get("phase") for e in ev}
    assert {"requested", "built", "validated", "rendered", "delivered"} <= phases
    assert all(e.seq > 0 for e in ev)


# ── extension interfaces (interfaces only) ───────────────────────────────────
def test_extension_interfaces_are_abstract():
    from abc import ABC
    for cls in (PromptOptimizer, PromptRewriter, PromptCompressor, PromptEnhancer,
                PromptAnalyzer, MultiModalRenderer, PromptConsumer):
        assert issubclass(cls, ABC)
        with pytest.raises(TypeError):
            cls()


def test_prompt_consumer_receives_package():
    received = []

    class Sink(PromptConsumer):
        def consume(self, package): received.append(package)

    rt = _rt()
    rt.build(_req(), _ctx())
    Sink().consume(rt.history("s1")[0])
    assert received and received[0].messages


# ── architecture boundaries ──────────────────────────────────────────────────
def test_intelligence_modules_import_only_core_tenant_own_package():
    tops: set[str] = set()
    for name in ("budget", "validation", "policies", "tenancy", "extensions"):
        p = pathlib.Path("prompt") / f"{name}.py"
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.ImportFrom) and n.module and not n.level:
                tops.add(n.module.split(".")[0])
            elif isinstance(n, ast.Import):
                for a in n.names:
                    tops.add(a.name.split(".")[0])
    forbidden = {"runtime", "api", "gateway", "replay", "events", "memory", "knowledge",
                 "context", "voicebot"}
    assert not (tops & forbidden), tops


def test_core_path_unchanged_without_intelligence():
    rt = _rt(intelligence=False)
    pkg = rt.build(_req(), _ctx())
    assert pkg.metadata.provider == "neutral"  # default single provider, no registry selection
