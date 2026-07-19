"""Ranking Engine (M5.1B-2) — deterministic memory scoring.

Scores each candidate as a weighted linear blend of neutral signals via the shared
`core.policies.RankingPolicy` (the module names the signals; the policy weights them):

- **recency**    — monotonic decay of age since last access/creation (injected clock)
- **frequency**  — a saturating function of `access_count`
- **importance** — the record's stored importance
- **similarity** — semantic score from the vector search (0..1)
- **affinity**   — 1.0 when the record shares the query's conversation, else 0.0
- **keyword**    — lexical term-overlap score (0..1)

Fully deterministic: no system randomness, injected clock only. An optional injected
`Reranker` (extension point) may reorder the final hits; absent by default.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from core.policies import RankingPolicy

from .domain import MemoryRecord, MemoryScope

# Default signal weights — importance/similarity dominate, recency/frequency refine.
DEFAULT_WEIGHTS: dict[str, float] = {
    "recency": 1.0,
    "frequency": 0.5,
    "importance": 2.0,
    "similarity": 3.0,
    "affinity": 1.0,
    "keyword": 2.0,
}


@dataclass(frozen=True, slots=True)
class ScoredRecord:
    record: MemoryRecord
    score: float
    signals: Mapping[str, float]


class RankingEngine:
    def __init__(
        self,
        *,
        policy: RankingPolicy | None = None,
        clock: Callable[[], float] | None = None,
        half_life_s: float = 3600.0,
    ) -> None:
        import time

        self._policy = policy or RankingPolicy(weights=dict(DEFAULT_WEIGHTS))
        self._clock = clock or time.time
        if half_life_s <= 0:
            raise ValueError("half_life_s must be > 0")
        self._half_life = half_life_s

    @property
    def policy(self) -> RankingPolicy:
        return self._policy

    def rank(
        self,
        records: Sequence[MemoryRecord],
        *,
        similarity: Mapping[str, float] | None = None,
        keyword: Mapping[str, float] | None = None,
        conversation_scope: MemoryScope | None = None,
        now: float | None = None,
    ) -> list[ScoredRecord]:
        """Score every record and return them sorted by score desc (id tie-break)."""
        now = self._clock() if now is None else now
        sim = similarity or {}
        kw = keyword or {}
        conv = conversation_scope.conversation_id if conversation_scope else None

        scored: list[ScoredRecord] = []
        for r in records:
            signals = {
                "recency": self._recency(r, now),
                "frequency": _frequency(r.metadata.access_count),
                "importance": r.metadata.importance,
                "similarity": sim.get(r.id.value, 0.0),
                "affinity": 1.0 if conv and r.scope.conversation_id == conv else 0.0,
                "keyword": kw.get(r.id.value, 0.0),
            }
            scored.append(ScoredRecord(r, self._policy.score(signals), signals))

        scored.sort(key=lambda s: (-s.score, s.record.id.value))
        return scored

    def _recency(self, record: MemoryRecord, now: float) -> float:
        ref = record.metadata.last_access_at or record.metadata.created_at
        age = max(0.0, now - ref)
        return math.pow(0.5, age / self._half_life)  # 1.0 fresh → 0.5 at one half-life


def _frequency(access_count: int) -> float:
    """Saturating: 0 → 0, grows toward 1 with diminishing returns."""
    return access_count / (access_count + 1.0)
