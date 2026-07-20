"""Context Ranking Engine (M5.3B-2) — deterministic item scoring + lineage.

Scores each `ContextItem` as a weighted linear blend of neutral signals via the shared
`core.policies.RankingPolicy` (the module names the signals; the policy weights them). The
source's own score (already ranked by Memory/Knowledge/…) enters as the matching
per-source signal — Context blends, it never re-ranks a source against its store.

Signals: conversation_relevance · semantic_relevance · knowledge_score · memory_score ·
tool_score · workflow_score · freshness (half-life decay, injected clock) · importance ·
confidence · source_priority · user_priority · system_priority.

**Ranking lineage** is written onto every item: `adjusted_score` (the context-adjusted
score) + a `RankingBreakdown` (per-signal contributions + a human-readable `reason`). The
original source score/runtime/confidence are already on the item (never mutated). Fully
deterministic: no randomness, injected clock, item id breaks every tie. An optional
injected `Reranker` (extension) may reorder last.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence

from core.policies import RankingPolicy

from .domain import ContextItem, ContextKind, RankingBreakdown, SourceRuntime
from .policies import ContextRankingPolicy, source_runtime_priority


def _clamp(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


class ContextRankingEngine:
    def __init__(
        self,
        *,
        policy: ContextRankingPolicy | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        import time

        self._p = policy or ContextRankingPolicy()
        self._policy = RankingPolicy(weights=dict(self._p.weights))
        self._clock = clock or time.time

    def rank(
        self, items: Sequence[ContextItem], *, now: float | None = None
    ) -> list[ContextItem]:
        """Score every item, write its lineage, and return them sorted by adjusted score
        desc (item-id tie-break)."""
        now = self._clock() if now is None else now
        scored: list[ContextItem] = []
        for item in items:
            signals = self._signals(item, now)
            total = self._policy.score(signals)
            reason = _reason(signals)
            scored.append(item.with_ranking(total, RankingBreakdown(dict(signals), total, reason)))
        scored.sort(key=lambda i: (-i.adjusted_score, i.id))
        return scored

    def _signals(self, item: ContextItem, now: float) -> Mapping[str, float]:
        src = item.provenance.source_runtime
        score = _clamp(item.provenance.original_score)
        is_convo = item.kind is ContextKind.CONVERSATION_HISTORY
        is_semantic = src in (SourceRuntime.KNOWLEDGE, SourceRuntime.MEMORY)
        return {
            "conversation_relevance": score if is_convo else 0.0,
            "semantic_relevance": score if is_semantic else 0.0,
            "knowledge_score": score if src is SourceRuntime.KNOWLEDGE else 0.0,
            "memory_score": score if src is SourceRuntime.MEMORY else 0.0,
            "tool_score": score if src is SourceRuntime.TOOL else 0.0,
            "workflow_score": score if src is SourceRuntime.WORKFLOW else 0.0,
            "freshness": self._freshness(item, now),
            "importance": _clamp(_importance(item)),
            "confidence": _clamp(item.confidence),
            "source_priority": source_runtime_priority(item.kind),
            "user_priority": 1.0 if item.kind is ContextKind.USER_INSTRUCTION else 0.0,
            "system_priority": 1.0 if item.kind in _SYSTEM_KINDS else 0.0,
        }

    def _freshness(self, item: ContextItem, now: float) -> float:
        raw = item.provenance.extra.get("timestamp") or item.metadata.get("timestamp")
        try:
            ts = float(raw) if raw is not None else 0.0
        except (TypeError, ValueError):
            ts = 0.0
        if ts <= 0:
            return 0.0
        age = max(0.0, now - ts)
        return math.pow(0.5, age / self._p.half_life_s)


_SYSTEM_KINDS = frozenset({ContextKind.SYSTEM_INSTRUCTION, ContextKind.DEVELOPER_INSTRUCTION})


def _importance(item: ContextItem) -> float:
    raw = item.provenance.extra.get("importance") or item.metadata.get("importance")
    try:
        return float(raw) if raw is not None else item.confidence
    except (TypeError, ValueError):
        return item.confidence


def _reason(signals: Mapping[str, float]) -> str:
    """A short, deterministic summary naming the top contributing signals."""
    top = sorted((s for s in signals.items() if s[1] > 0), key=lambda kv: (-kv[1], kv[0]))[:3]
    return "+".join(k for k, _ in top) if top else "priors_only"
