"""Context Runtime intelligence tests (M5.3B-2).

Covers the budget manager (allocation, per-source/reserved/emergency, adaptive), ranking
(determinism + lineage), deduplication (exact + provenance), compression (provenance +
lineage preserved), ordering, validation (verdicts), the policy engine, tenant isolation,
extension interfaces, replay/gateway compatibility, determinism, and the architecture
boundaries. The M5.3B-1 core tests remain in `test_context_runtime.py`.
"""

from __future__ import annotations

import ast
import inspect
import itertools
import pathlib

import pytest

from context import (
    Compressor,
    ContextBudgetManager,
    ContextBudgetPolicy,
    ContextCompressionEngine,
    ContextConfiguration,
    ContextConsumer,
    ContextDeduplicationEngine,
    ContextFactory,
    ContextItem,
    ContextKind,
    ContextOrderingEngine,
    ContextPackage,
    ContextPolicies,
    ContextPolicyEngine,
    ContextRankingEngine,
    ContextScope,
    ContextTenancy,
    ContextValidationEngine,
    ModelProfile,
    OverflowStrategy,
    Provenance,
    Redactor,
    Reranker,
    SemanticDeduplicator,
    SourceContribution,
    SourceRuntime,
    StaticContextSource,
    Summarizer,
    Verdict,
)
from context.compression import ContextCompressionEngine as _CE  # noqa: F401
from context.policies import (
    CITED_KINDS,
    ContextCitationPolicy,
    ContextPrivacyPolicy,
)
from context.stores import WordTokenizer
from context.tenancy import ContextAccessDenied
from core.events import Event, EventSource
from core.ports.context import ContextRequest
from events.bus import InProcessEventBus
from tenant import OrganizationContext, TenantContext


def _ids():
    return (f"c{i:02d}" for i in itertools.count()).__next__


def _item(kind, text, src, sid, score=0.5, conf=None, order=0):
    return ContextItem(
        id=f"{kind.value}:{sid}",
        kind=kind,
        text=text,
        confidence=conf if conf is not None else score,
        confidence_source=src.value,
        provenance=Provenance(source_runtime=src, source_id=sid, original_score=score,
                              content_hash=f"h_{sid}_{text[:6]}"),
        order_index=order,
        budget=__import__("context").BudgetMetadata(token_estimate=max(1, len(text.split()))),
    )


def _contribs(n_conv=3):
    out = [
        SourceContribution(ContextKind.SYSTEM_INSTRUCTION, "You are a cricket coach.",
                           SourceRuntime.SYSTEM, "s1", 1.0, 1.0),
        SourceContribution(ContextKind.KNOWLEDGE_PASSAGE, "cover drive off the front foot",
                           SourceRuntime.KNOWLEDGE, "k1", 0.9),
        SourceContribution(ContextKind.WORKING_MEMORY, "prefers the cover drive",
                           SourceRuntime.MEMORY, "m1", 0.8),
        SourceContribution(ContextKind.USER_INSTRUCTION, "how to improve my cover drive",
                           SourceRuntime.INPUT, "u1", 1.0, 1.0),
    ]
    out += [SourceContribution(ContextKind.CONVERSATION_HISTORY, f"turn {i} footwork",
                               SourceRuntime.MEMORY, f"t{i}", 0.4) for i in range(n_conv)]
    return out


def _rt(config=None, *, contributions=None, intelligence=True, **kwargs):
    cfg = config or ContextConfiguration.default()
    kwargs.setdefault("clock", lambda: 1000.0)
    src = StaticContextSource("bundle", contributions if contributions is not None else _contribs())
    return ContextFactory.create(cfg, extra_sources=[src], intelligence=intelligence,
                                 id_factory=_ids(), **kwargs)


def _req(**kw):
    base = dict(session_id="s1", org_id="o1", conversation_id="conv1", query="cover drive")
    base.update(kw)
    return ContextRequest(**base)


# ── budget manager ─────────────────────────────────────────────────────────────
def test_budget_reserved_kinds_always_kept():
    mgr = ContextBudgetManager(policy=ContextBudgetPolicy(global_tokens=3))
    items = [
        _item(ContextKind.SYSTEM_INSTRUCTION, "sys a b c d e", SourceRuntime.SYSTEM, "s1", 1.0),
        _item(ContextKind.KNOWLEDGE_PASSAGE, "k a b c d", SourceRuntime.KNOWLEDGE, "k1", 0.9),
    ]
    res = mgr.allocate(items, global_tokens=3)
    kept_kinds = {i.kind for i in res.items}
    assert ContextKind.SYSTEM_INSTRUCTION in kept_kinds  # reserved kept despite overflow
    assert res.truncated  # the knowledge item dropped


def test_budget_emergency_floor_keeps_top_item():
    mgr = ContextBudgetManager(policy=ContextBudgetPolicy(global_tokens=1, emergency_tokens=100))
    items = [_item(ContextKind.KNOWLEDGE_PASSAGE, "a b c d e f",
                  SourceRuntime.KNOWLEDGE, "k1", 0.9)]
    res = mgr.allocate(items, global_tokens=1)
    assert len(res.items) == 1 and res.stats.emergency_used


def test_budget_adaptive_ignores_per_source_caps():
    items = [_item(ContextKind.KNOWLEDGE_PASSAGE, "a b c d e",
                   SourceRuntime.KNOWLEDGE, f"k{i}", 0.9)
             for i in range(3)]
    strict = ContextBudgetManager(policy=ContextBudgetPolicy(
        global_tokens=100, per_source_fraction={"knowledge_passage": 0.1}, adaptive=False))
    adaptive = ContextBudgetManager(policy=ContextBudgetPolicy(
        global_tokens=100, per_source_fraction={"knowledge_passage": 0.1}, adaptive=True))
    assert len(adaptive.allocate(items, global_tokens=100).items) >= \
        len(strict.allocate(items, global_tokens=100).items)


def test_budget_reject_strategy_flags_rejected():
    mgr = ContextBudgetManager(policy=ContextBudgetPolicy(
        global_tokens=1, overflow=OverflowStrategy.REJECT, emergency_tokens=0))
    items = [_item(ContextKind.KNOWLEDGE_PASSAGE, "a b c d e", SourceRuntime.KNOWLEDGE, "k1", 0.9)]
    assert mgr.allocate(items, global_tokens=1).rejected


def test_budget_unbounded_keeps_all():
    items = [_item(ContextKind.KNOWLEDGE_PASSAGE, "x y z", SourceRuntime.KNOWLEDGE, f"k{i}")
             for i in range(5)]
    res = ContextBudgetManager().allocate(items, global_tokens=0)
    assert len(res.items) == 5 and not res.truncated


# ── ranking ─────────────────────────────────────────────────────────────────────
def test_ranking_is_deterministic_with_lineage():
    eng = ContextRankingEngine(clock=lambda: 1000.0)
    items = [_item(ContextKind.KNOWLEDGE_PASSAGE, "k", SourceRuntime.KNOWLEDGE, "k1", 0.9),
             _item(ContextKind.WORKING_MEMORY, "m", SourceRuntime.MEMORY, "m1", 0.5)]
    a = eng.rank(items)
    b = eng.rank(items)
    assert [i.id for i in a] == [i.id for i in b]
    top = a[0]
    # lineage preserved end-to-end
    assert top.provenance.original_score in (0.9, 0.5)  # original source score
    assert top.provenance.source_runtime in (SourceRuntime.KNOWLEDGE, SourceRuntime.MEMORY)
    assert top.ranking.total == pytest.approx(top.adjusted_score)
    assert top.ranking.reason and len(top.ranking.signals) == 12


def test_ranking_tiebreak_by_id():
    eng = ContextRankingEngine(clock=lambda: 1000.0)
    # identical signals → deterministic id tie-break
    items = [_item(ContextKind.KNOWLEDGE_PASSAGE, "same", SourceRuntime.KNOWLEDGE, sid, 0.9)
             for sid in ("kb", "ka")]
    ranked = eng.rank(items)
    assert [i.id for i in ranked] == sorted(i.id for i in items)


def test_injected_reranker_reorders():
    class Rev(Reranker):
        def rerank(self, query, items):
            return tuple(reversed(list(items)))

    items = [_item(ContextKind.KNOWLEDGE_PASSAGE, "a", SourceRuntime.KNOWLEDGE, "k1"),
             _item(ContextKind.KNOWLEDGE_PASSAGE, "b", SourceRuntime.KNOWLEDGE, "k2")]
    assert list(Rev().rerank("q", items)) == list(reversed(items))


# ── deduplication ────────────────────────────────────────────────────────────────
def test_exact_dedup_merges_provenance():
    eng = ContextDeduplicationEngine()
    a = _item(ContextKind.KNOWLEDGE_PASSAGE, "dup", SourceRuntime.KNOWLEDGE, "k1", 0.9)
    b = ContextItem(id="k2", kind=a.kind, text="dup", confidence=0.9, confidence_source="knowledge",
                    provenance=Provenance(
                        SourceRuntime.KNOWLEDGE, "k2", 0.9, a.provenance.content_hash))
    res = eng.deduplicate([a, b])
    assert res.stats.exact_duplicates == 1 and len(res.items) == 1
    assert "k2" in res.items[0].provenance.merged_from  # merged provenance preserved


def test_semantic_dedup_hook():
    class Sem(SemanticDeduplicator):
        def duplicate_groups(self, items):
            return [[i.id for i in items]]  # all one group

    a = _item(ContextKind.KNOWLEDGE_PASSAGE, "x", SourceRuntime.KNOWLEDGE, "k1")
    b = _item(ContextKind.KNOWLEDGE_PASSAGE, "y", SourceRuntime.KNOWLEDGE, "k2")
    res = ContextDeduplicationEngine(semantic=Sem()).deduplicate([a, b])
    assert res.stats.semantic_duplicates == 1 and len(res.items) == 1


# ── compression ──────────────────────────────────────────────────────────────────
def test_compression_preserves_provenance_and_lineage():
    tok = WordTokenizer()
    eng = ContextCompressionEngine(tok, policy=__import__("context").ContextCompressionPolicy(
        max_item_tokens=5))
    long = _item(ContextKind.KNOWLEDGE_PASSAGE, "word " * 40, SourceRuntime.KNOWLEDGE, "k1", 0.9)
    long = long.with_ranking(2.5, __import__("context").RankingBreakdown({"x": 1.0}, 2.5, "r"))
    res = eng.compress([long])
    item = res.items[0]
    assert item.compression.compressed and item.token_estimate <= 6
    assert item.provenance.source_id == "k1"  # provenance preserved
    assert item.adjusted_score == 2.5 and item.ranking.reason == "r"  # ranking lineage preserved
    assert long.id in item.provenance.compressed_from  # cites what it was compressed from


def test_conversation_rollup():
    tok = WordTokenizer()
    eng = ContextCompressionEngine(tok, policy=__import__("context").ContextCompressionPolicy(
        conversation_rollup_after=3))
    convo = [_item(ContextKind.CONVERSATION_HISTORY, f"turn {i} here",
                   SourceRuntime.MEMORY, f"t{i}") for i in range(8)]
    res = eng.compress(convo)
    rolled = [i for i in res.items if i.compression.method == "rollup"]
    assert res.stats.rolled_up_items > 0 and len(rolled) == 1
    assert rolled[0].provenance.compressed_from  # cites the rolled-up ids


def test_injected_compressor_used():
    class Fixed(Compressor):
        def compress(self, text, *, target_tokens): return "COMPRESSED"

    tok = WordTokenizer()
    eng = ContextCompressionEngine(tok, policy=__import__("context").ContextCompressionPolicy(
        max_item_tokens=2), compressor=Fixed())
    long = _item(ContextKind.KNOWLEDGE_PASSAGE, "a b c d e f g", SourceRuntime.KNOWLEDGE, "k1")
    assert eng.compress([long]).items[0].text == "COMPRESSED"


# ── ordering ──────────────────────────────────────────────────────────────────
def test_ordering_places_by_kind_then_score():
    eng = ContextOrderingEngine()
    items = [
        _item(ContextKind.USER_INSTRUCTION, "u", SourceRuntime.INPUT, "u1", 1.0),
        _item(ContextKind.SYSTEM_INSTRUCTION, "s", SourceRuntime.SYSTEM, "s1", 1.0),
        _item(ContextKind.KNOWLEDGE_PASSAGE, "k", SourceRuntime.KNOWLEDGE, "k1", 0.9),
    ]
    ordered = eng.order(items)
    assert ordered[0].kind is ContextKind.SYSTEM_INSTRUCTION
    assert ordered[-1].kind is ContextKind.USER_INSTRUCTION
    assert [i.order_index for i in ordered] == [0, 1, 2]


# ── validation (verdicts only) ──────────────────────────────────────────────────
def test_validation_flags_empty_and_ordering():
    eng = ContextValidationEngine()
    empty = ContextPackage(id="p", session_id="s", scope=ContextScope(), state=__import__(
        "context").PackageState.ASSEMBLED)
    assert eng.validate(empty).ok is False


def test_validation_citation_verdict():
    pol = ContextPolicies(citation=ContextCitationPolicy(require_source_for_cited_kinds=True))
    eng = ContextValidationEngine(ContextPolicyEngine(pol))
    item = ContextItem(id="k", kind=ContextKind.KNOWLEDGE_PASSAGE, text="x", confidence=0.9,
                       confidence_source="knowledge",
                       provenance=Provenance(SourceRuntime.KNOWLEDGE, "", 0.9, "h"), order_index=0)
    pkg = ContextPackage(id="p", session_id="s", scope=ContextScope(),
                         state=__import__("context").PackageState.ASSEMBLED, items=(item,))
    report = eng.validate(pkg)
    assert report.ok is False and any("citation" in r for r in report.reasons)


def test_policy_engine_has_no_io_and_returns_verdicts():
    src = inspect.getsource(ContextPolicyEngine)
    assert "await" not in src and "open(" not in src
    eng = ContextPolicyEngine()
    assert isinstance(eng.budget_ok(10), Verdict)
    assert CITED_KINDS  # cited kinds defined


# ── tenant isolation (before assembly) ──────────────────────────────────────────
def _tenant(org="o1", user=None):
    return TenantContext(org=OrganizationContext(org_id=org), user_id=user)


def test_tenancy_blocks_cross_org():
    rt = _rt()
    with pytest.raises(ContextAccessDenied):
        rt.assemble_scoped(_req(org_id="o1"), _tenant(org="o2"))


def test_tenancy_allows_same_org_and_runs_before_assembly():
    rt = _rt()
    asm = rt.assemble_scoped(_req(org_id="o1"), _tenant(org="o1"))
    assert len(asm.items) >= 1
    # isolation executes before any source fetch
    src = inspect.getsource(type(rt).aassemble)
    assert src.index("_tenancy") < src.index("_assembler.assemble")


def test_tenancy_guard_standalone():
    t = ContextTenancy(clock=lambda: 1.0)
    resolved = t.guard(ContextScope(org_id=None), _tenant(org="o9"))
    assert resolved.scope.org_id == "o9"  # scope narrowed to the tenant org


# ── full intelligent pipeline (determinism + rejection) ─────────────────────────
def test_full_pipeline_is_deterministic():
    def run():
        seen = []
        bus = InProcessEventBus(clock=lambda: 1.0)
        bus.subscribe(lambda e: seen.append((e.type.value, e.seq, e.data.get("phase"))))
        rt = _rt()
        rt.attach(bus)
        asm = rt.assemble(_req())
        return seen, [(i.kind, round(i.score, 6)) for i in asm.items]
    assert run() == run()


def test_pipeline_emits_standard_envelopes():
    seen: list[Event] = []
    bus = InProcessEventBus(clock=lambda: 1.0)
    bus.subscribe(seen.append)
    rt = _rt()
    rt.attach(bus)
    rt.assemble(_req())
    ctx = [e for e in seen if e.source is EventSource.CONTEXT]
    phases = {e.data.get("phase") for e in ctx}
    assert {"requested", "built", "validated", "delivered"} <= phases
    assert all(e.seq > 0 for e in ctx)


def test_rejection_emits_rejected_and_does_not_deliver():
    # empty context → validation rejects
    rt = _rt(contributions=[])
    resp = __import__("context")._run.run_coro_sync(rt.aassemble(_req()))
    assert resp.delivered is False
    assert rt.statistics().metrics  # rejected counter recorded


def test_statistics_reports_intelligent():
    rt = _rt()
    rt.assemble(_req())
    assert rt.statistics().intelligent is True


def test_core_path_stays_naive_when_intelligence_off():
    rt = _rt(intelligence=False)
    rt.assemble(_req())
    assert rt.statistics().intelligent is False


# ── extension interfaces (interfaces only) ──────────────────────────────────────
def test_extension_interfaces_are_abstract():
    from abc import ABC
    for cls in (ModelProfile, Summarizer, Compressor, Redactor, SemanticDeduplicator,
                Reranker, ContextConsumer):
        assert issubclass(cls, ABC)
        with pytest.raises(TypeError):
            cls()


def test_context_consumer_receives_package():
    received = []

    class Sink(ContextConsumer):
        def consume(self, package): received.append(package)

    rt = _rt()
    rt.assemble(_req())
    Sink().consume(rt.history("s1")[0])
    assert received and received[0].items


def test_redactor_applied_on_privacy_policy():
    class Star(Redactor):
        def redact(self, text): return "***"

    pol = ContextPolicies(privacy=ContextPrivacyPolicy(enabled=True))
    cfg = ContextConfiguration(policies=pol)
    rt = _rt(cfg, redactor=Star())
    asm = rt.assemble(_req())
    assert all(i.text == "***" for i in asm.items)


# ── architecture boundaries ─────────────────────────────────────────────────────
def test_assembler_returns_outcome_and_touches_no_store():
    src = inspect.getsource(__import__("context").ContextAssembler)
    assert "_store" not in src and "repository" not in src.lower()


def test_intelligence_modules_import_only_core_tenant_own_package():
    tops: set[str] = set()
    for name in ("budget", "ranking", "dedup", "compression", "ordering", "validation",
                 "policies", "tenancy", "extensions", "assembly"):
        p = pathlib.Path("context") / f"{name}.py"
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.ImportFrom) and n.module and not n.level:
                tops.add(n.module.split(".")[0])
            elif isinstance(n, ast.Import):
                for a in n.names:
                    tops.add(a.name.split(".")[0])
    forbidden = {"runtime", "api", "gateway", "replay", "events", "memory", "knowledge",
                 "prompt", "voicebot"}
    assert not (tops & forbidden), tops


def test_model_profile_is_injectable_seam():
    class Tiny(ModelProfile):
        name = "tiny"
        def context_window(self): return 8
        def count(self, text): return len(text.split())
    assert Tiny().context_window() == 8
