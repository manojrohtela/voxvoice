"""Context builder (M5.3B-1) — merge → normalize → order → naive budget → package.

Turns the raw `SourceContribution`s gathered from the sources into an immutable
`ContextPackage`: assigns deterministic content-hash ids + full provenance, estimates
tokens via the injected `Tokenizer`, applies a **fixed insertion order** (the configured
source order, then score desc, then id), and enforces the global budget by **naive
truncation** only. No ranking, no compression, no adaptive budgeting (that is M5.3B-2).

Deterministic throughout: content-hash ids, stable sort, injected clock supplied by the
caller. Exact-id duplicates collapse (content-hash identity) — this is id-uniqueness, not
the semantic DeduplicationEngine (M5.3B-2).
"""

from __future__ import annotations

from collections.abc import Sequence

from .config import DEFAULT_SOURCE_ORDER
from .domain import (
    BudgetMetadata,
    BudgetUsage,
    ContextItem,
    ContextKind,
    ContextMetadata,
    ContextPackage,
    ContextScope,
    PackageState,
    Provenance,
)
from .ids import content_hash, item_id_for
from .ports import SourceContribution, Tokenizer


def _clamp(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


class ContextBuilder:
    def __init__(
        self,
        tokenizer: Tokenizer,
        *,
        source_order: Sequence[ContextKind] = DEFAULT_SOURCE_ORDER,
    ) -> None:
        self._tokenizer = tokenizer
        self._order = {k: i for i, k in enumerate(source_order)}

    def build(
        self,
        *,
        package_id: str,
        session_id: str,
        scope: ContextScope,
        contributions: Sequence[SourceContribution],
        budget_tokens: int,
        now: float,
        query: str | None,
        expires_at: float | None = None,
    ) -> ContextPackage:
        items = self._normalize(contributions)
        items = self._order_items(items)
        kept, truncated, used, per_source = self._apply_budget(items, budget_tokens)

        confidence = (
            sum(i.confidence for i in kept) / len(kept) if kept else 1.0
        )
        source_counts: dict[str, int] = {}
        for i in kept:
            source_counts[i.kind.value] = source_counts.get(i.kind.value, 0) + 1

        return ContextPackage(
            id=package_id,
            session_id=session_id,
            scope=scope,
            state=PackageState.REQUESTED,
            items=tuple(kept),
            budget=BudgetUsage(
                used_tokens=used, limit_tokens=budget_tokens, per_source=per_source
            ),
            metadata=ContextMetadata(strategy="insertion_order", source_counts=source_counts),
            truncated=truncated,
            confidence=_clamp(confidence),
            created_at=now,
            expires_at=expires_at,
            org_id=scope.org_id,
            conversation_id=scope.conversation_id,
            query=query,
        )

    # ── merge + normalize ─────────────────────────────────────────────────────
    def _normalize(self, contributions: Sequence[SourceContribution]) -> list[ContextItem]:
        seen: set[str] = set()
        out: list[ContextItem] = []
        for c in contributions:
            chash = content_hash(c.text)
            iid = item_id_for(c.kind.value, c.source_runtime.value, c.source_id, c.text)
            if iid in seen:  # id-uniqueness (identical content from the same source)
                continue
            seen.add(iid)
            confidence = c.confidence if c.confidence is not None else _clamp(c.score)
            out.append(
                ContextItem(
                    id=iid,
                    kind=c.kind,
                    text=c.text,
                    confidence=_clamp(confidence),
                    confidence_source=c.source_runtime.value,
                    provenance=Provenance(
                        source_runtime=c.source_runtime,
                        source_id=c.source_id,
                        original_score=c.score,
                        content_hash=chash,
                        extra=dict(c.metadata),
                    ),
                    budget=BudgetMetadata(
                        token_estimate=self._tokenizer.count(c.text),
                        counted_by=self._tokenizer.name,
                    ),
                )
            )
        return out

    # ── deterministic ordering (fixed source order, no ranking) ───────────────
    def _order_items(self, items: list[ContextItem]) -> list[ContextItem]:
        def key(i: ContextItem) -> tuple[int, float, str]:
            return (self._order.get(i.kind, len(self._order)), -i.provenance.original_score, i.id)

        items.sort(key=key)
        return [i.with_order(idx) for idx, i in enumerate(items)]

    # ── naive budget (truncation only) ────────────────────────────────────────
    def _apply_budget(
        self, items: Sequence[ContextItem], budget_tokens: int
    ) -> tuple[list[ContextItem], bool, int, dict[str, int]]:
        kept: list[ContextItem] = []
        used = 0
        truncated = False
        per_source: dict[str, int] = {}
        for i in items:
            est = i.budget.token_estimate
            if budget_tokens > 0 and used + est > budget_tokens:
                truncated = True
                continue  # naive: skip items that overflow (no ranking/compression)
            kept.append(i)
            used += est
            key = i.provenance.source_runtime.value
            per_source[key] = per_source.get(key, 0) + est
        # re-index the kept items so order_index is contiguous
        kept = [i.with_order(idx) for idx, i in enumerate(kept)]
        return kept, truncated, used, per_source
