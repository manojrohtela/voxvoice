"""Context Policy Engine (M5.3B-2) — deterministic, AI-free.

Context-specific policy value objects (built on generic `core.policies` where one fits) +
a `ContextPolicyEngine` that turns them into **verdicts** the runtime/engines execute. The
engine performs no I/O and no side effects: it decides, the caller acts.

Policies (all nine from the milestone): Budget, Ranking, Compression, Ordering, Validation,
Privacy, Overflow, Citation, SourceSelection. The `ContextPolicies` bundle is the single
config the factory threads into every engine (each engine reads its own sub-policy), so the
policy surface stays in one place and is validated once.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from core.policies import InvalidPolicy

from .domain import ContextItem, ContextKind

# ── default ranking weights (the ranker names the signals; the policy weights them) ──
DEFAULT_RANKING_WEIGHTS: Mapping[str, float] = {
    "conversation_relevance": 1.0,
    "semantic_relevance": 3.0,
    "knowledge_score": 2.0,
    "memory_score": 2.0,
    "tool_score": 2.0,
    "workflow_score": 1.5,
    "freshness": 1.0,
    "importance": 1.5,
    "confidence": 1.0,
    "source_priority": 1.0,
    "user_priority": 1.5,
    "system_priority": 2.0,
}

# ── deterministic per-kind priority (source/user/system priority signals + ordering) ──
DEFAULT_ORDER: tuple[ContextKind, ...] = (
    ContextKind.SYSTEM_INSTRUCTION,
    ContextKind.DEVELOPER_INSTRUCTION,
    ContextKind.CONVERSATION_HISTORY,
    ContextKind.WORKING_MEMORY,
    ContextKind.LONG_TERM_MEMORY,
    ContextKind.KNOWLEDGE_PASSAGE,
    ContextKind.TOOL_RESULT,
    ContextKind.WORKFLOW_RESULT,
    ContextKind.AGENT_OUTPUT,
    ContextKind.ENVIRONMENT,
    ContextKind.USER_INSTRUCTION,
)

# kinds that are always kept (reserved budget; never dropped/compressed away)
RESERVED_KINDS: frozenset[ContextKind] = frozenset(
    {ContextKind.SYSTEM_INSTRUCTION, ContextKind.DEVELOPER_INSTRUCTION}
)

# kinds requiring a citeable source (citation policy)
CITED_KINDS: frozenset[ContextKind] = frozenset(
    {ContextKind.KNOWLEDGE_PASSAGE, ContextKind.TOOL_RESULT, ContextKind.WORKFLOW_RESULT}
)


class OverflowStrategy(StrEnum):
    COMPRESS_THEN_DROP = "compress_then_drop"
    DROP_LOWEST = "drop_lowest"
    TRUNCATE_OLDEST = "truncate_oldest"
    REJECT = "reject"


# ── policy value objects ──────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class ContextBudgetPolicy:
    """Global + per-source + reserved + emergency token budgeting."""

    global_tokens: int = 0  # 0 → unbounded
    per_source_fraction: Mapping[str, float] = field(default_factory=dict)  # kind → [0,1] cap
    reserved_kinds: frozenset[ContextKind] = RESERVED_KINDS
    emergency_tokens: int = 256
    compression_threshold: float = 0.9  # utilization that triggers compression before drop
    overflow: OverflowStrategy = OverflowStrategy.COMPRESS_THEN_DROP
    adaptive: bool = True

    def __post_init__(self) -> None:
        if self.global_tokens < 0:
            raise InvalidPolicy("global_tokens must be >= 0")
        if self.emergency_tokens < 0:
            raise InvalidPolicy("emergency_tokens must be >= 0")
        if not (0.0 < self.compression_threshold <= 1.0):
            raise InvalidPolicy("compression_threshold must be in (0, 1]")
        for k, v in self.per_source_fraction.items():
            if not (0.0 <= v <= 1.0):
                raise InvalidPolicy(f"per_source_fraction[{k}] must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class ContextRankingPolicy:
    weights: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_RANKING_WEIGHTS))
    half_life_s: float = 3600.0

    def __post_init__(self) -> None:
        for k, v in self.weights.items():
            if v < 0:
                raise InvalidPolicy(f"ranking weight[{k}] must be >= 0")
        if self.half_life_s <= 0:
            raise InvalidPolicy("half_life_s must be > 0")


@dataclass(frozen=True, slots=True)
class ContextCompressionPolicy:
    enabled: bool = True
    max_item_tokens: int = 512  # trim items above this (knowledge/conversation compression)
    conversation_rollup_after: int = 12  # roll up conversation items beyond this count

    def __post_init__(self) -> None:
        if self.max_item_tokens <= 0:
            raise InvalidPolicy("max_item_tokens must be > 0")


@dataclass(frozen=True, slots=True)
class ContextOrderingPolicy:
    order: tuple[ContextKind, ...] = DEFAULT_ORDER


@dataclass(frozen=True, slots=True)
class ContextValidationPolicy:
    require_non_empty: bool = True
    enforce_budget: bool = True
    enforce_ordering: bool = True


@dataclass(frozen=True, slots=True)
class ContextPrivacyPolicy:
    enabled: bool = False  # redact via an injected Redactor when on


@dataclass(frozen=True, slots=True)
class ContextCitationPolicy:
    require_source_for_cited_kinds: bool = False


@dataclass(frozen=True, slots=True)
class ContextSourceSelectionPolicy:
    """Which source *names* are allowed (empty → all). Honours the include flags too."""

    allowed_sources: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ContextSafetyPolicy:
    enabled: bool = False  # a SafetyValidator hook gates content when on (interface only)


@dataclass(frozen=True, slots=True)
class ContextPolicies:
    """The full deterministic policy bundle for a runtime."""

    budget: ContextBudgetPolicy = field(default_factory=ContextBudgetPolicy)
    ranking: ContextRankingPolicy = field(default_factory=ContextRankingPolicy)
    compression: ContextCompressionPolicy = field(default_factory=ContextCompressionPolicy)
    ordering: ContextOrderingPolicy = field(default_factory=ContextOrderingPolicy)
    validation: ContextValidationPolicy = field(default_factory=ContextValidationPolicy)
    privacy: ContextPrivacyPolicy = field(default_factory=ContextPrivacyPolicy)
    citation: ContextCitationPolicy = field(default_factory=ContextCitationPolicy)
    source_selection: ContextSourceSelectionPolicy = field(
        default_factory=ContextSourceSelectionPolicy
    )
    safety: ContextSafetyPolicy = field(default_factory=ContextSafetyPolicy)


# ── verdicts ────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Verdict:
    ok: bool
    reason: str = ""


# ── the engine ────────────────────────────────────────────────────────────────
class ContextPolicyEngine:
    """Holds the policy bundle and produces **verdicts** (no I/O, no side effects). The
    budget/ranking/compression/ordering engines read their sub-policy from `policies`; the
    validation engine calls the verdict helpers here."""

    def __init__(self, policies: ContextPolicies | None = None) -> None:
        self._p = policies or ContextPolicies()

    @property
    def policies(self) -> ContextPolicies:
        return self._p

    # ── source-selection verdict ─────────────────────────────────────────────
    def admit_source(self, name: str) -> bool:
        allowed = self._p.source_selection.allowed_sources
        return not allowed or name in allowed

    # ── citation verdict ─────────────────────────────────────────────────────
    def citation_ok(self, item: ContextItem) -> Verdict:
        if not self._p.citation.require_source_for_cited_kinds:
            return Verdict(True)
        if item.kind in CITED_KINDS and not item.provenance.source_id:
            return Verdict(False, f"{item.kind.value} missing citation source")
        return Verdict(True)

    # ── privacy verdict (redaction is a side effect the runtime performs) ────
    def needs_redaction(self, item: ContextItem) -> bool:
        return self._p.privacy.enabled

    # ── budget verdict ───────────────────────────────────────────────────────
    def budget_ok(self, used_tokens: int) -> Verdict:
        g = self._p.budget.global_tokens
        if g and used_tokens > g:
            return Verdict(False, f"used {used_tokens} exceeds budget {g}")
        return Verdict(True)

    # ── safety verdict (hook only; no classifier in-runtime) ─────────────────
    def safety_enabled(self) -> bool:
        return self._p.safety.enabled


def source_runtime_priority(kind: ContextKind) -> float:
    """Deterministic 0..1 priority by kind (for the ranking source_priority signal)."""
    order = {k: i for i, k in enumerate(DEFAULT_ORDER)}
    idx = order.get(kind, len(order))
    return 1.0 - (idx / max(1, len(order)))
