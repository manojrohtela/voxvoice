"""Context assembler (M5.3B-1 core + M5.3B-2 intelligence) — the pipeline orchestrator.

Owns orchestration only: **source selection → retrieval (delegated to the registered
`ContextSource`s) → merge/normalize (via `ContextBuilder`) → [dedup → ranking → compression
→ budget → ordering] → immutable `ContextPackage`**. It never touches a store or a provider
— sources wrap the injected ports, the engines are pure. Deterministic; the caller supplies
the clock + package id.

When the intelligence engines are absent (M5.3B-1 core factory), it falls back to the
builder's naive path (normalize → fixed order → naive truncation). When present (M5.3B-2
factory), it runs the full deterministic pipeline. Tenancy + validation live at the runtime
level (they gate the lifecycle); this assembler owns the assembly transforms.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .budget import ContextBudgetManager
from .builder import ContextBuilder
from .compression import ContextCompressionEngine
from .dedup import ContextDeduplicationEngine
from .domain import BudgetUsage, ContextPackage, ContextScope
from .ordering import ContextOrderingEngine
from .ports import AssemblyRequest, ContextSource
from .ranking import ContextRankingEngine
from .sources import SourceSelector


@dataclass(frozen=True, slots=True)
class AssemblyOutcome:
    package: ContextPackage
    rejected: bool = False
    stats: dict[str, Any] = field(default_factory=dict)


class ContextAssembler:
    def __init__(
        self,
        *,
        sources: Sequence[ContextSource],
        selector: SourceSelector,
        builder: ContextBuilder,
        dedup: ContextDeduplicationEngine | None = None,
        ranking: ContextRankingEngine | None = None,
        compression: ContextCompressionEngine | None = None,
        budget: ContextBudgetManager | None = None,
        ordering: ContextOrderingEngine | None = None,
    ) -> None:
        self._sources = tuple(sources)
        self._selector = selector
        self._builder = builder
        self._dedup = dedup
        self._ranking = ranking
        self._compression = compression
        self._budget = budget
        self._ordering = ordering

    @property
    def sources(self) -> tuple[ContextSource, ...]:
        return self._sources

    @property
    def intelligent(self) -> bool:
        return any((self._dedup, self._ranking, self._compression, self._budget, self._ordering))

    async def assemble(
        self,
        request: AssemblyRequest,
        *,
        package_id: str,
        scope: ContextScope,
        now: float,
        budget_tokens: int,
        expires_at: float | None,
    ) -> AssemblyOutcome:
        # 1. source selection (deterministic; honours include flags)
        active = self._selector.select(self._sources, request)
        # 2. retrieval — delegated to each source (which wraps an injected port)
        contributions: list = []
        for source in active:
            contributions.extend(source.fetch(request))

        if not self.intelligent:  # M5.3B-1 core path
            package = self._builder.build(
                package_id=package_id, session_id=request.session_id, scope=scope,
                contributions=contributions, budget_tokens=budget_tokens, now=now,
                query=request.query, expires_at=expires_at,
            )
            return AssemblyOutcome(package)

        # 3. merge → normalize
        items = self._builder.normalize(contributions)
        stats: dict[str, Any] = {}

        # 4. deduplication
        if self._dedup is not None:
            res = self._dedup.deduplicate(items)
            items, stats["dedup"] = list(res.items), res.stats

        # 5. ranking (writes lineage: adjusted_score + breakdown)
        if self._ranking is not None:
            items = self._ranking.rank(items, now=now)

        # 6. compression (provenance-preserving)
        if self._compression is not None:
            cres = self._compression.compress(items)
            items, stats["compression"] = list(cres.items), cres.stats

        # 7. budget allocation
        truncated = False
        rejected = False
        if self._budget is not None:
            bres = self._budget.allocate(items, global_tokens=budget_tokens)
            items, truncated, rejected = list(bres.items), bres.truncated, bres.rejected
            usage, stats["budget"] = bres.usage, bres.stats
        else:
            usage = _usage_of(items, budget_tokens)

        # 8. ordering (deterministic final sequence)
        if self._ordering is not None:
            items = self._ordering.order(items)

        package = self._builder.package(
            package_id=package_id, session_id=request.session_id, scope=scope,
            items=tuple(items), usage=usage, truncated=truncated, now=now,
            query=request.query, expires_at=expires_at, strategy="intelligent",
        )
        return AssemblyOutcome(package, rejected=rejected, stats=stats)


def _usage_of(items: Sequence, budget_tokens: int) -> BudgetUsage:
    used = 0
    per_source: dict[str, int] = {}
    for i in items:
        used += i.token_estimate
        key = i.provenance.source_runtime.value
        per_source[key] = per_source.get(key, 0) + i.token_estimate
    return BudgetUsage(used_tokens=used, limit_tokens=budget_tokens, per_source=per_source)
