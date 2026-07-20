"""Context Deduplication Engine (M5.3B-2) — exact + semantic-hook dedup.

Collapses duplicate `ContextItem`s while **preserving provenance**: the survivor records
the ids it absorbed (`merged_from`). Exact dedup is by content hash (deterministic); an
injected `SemanticDeduplicator` extension may add near-duplicate groups (off by default).
Duplicate statistics are returned for observability. No text is altered — dedup only drops
+ records lineage; compression is a separate stage.

Deterministic: the survivor of a group is the earliest by (order in input) — callers run
dedup *after* ranking so the highest-ranked item survives. Item ids break every tie.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .domain import ContextItem
from .extensions import SemanticDeduplicator


@dataclass(frozen=True, slots=True)
class DedupStats:
    input_items: int = 0
    output_items: int = 0
    exact_duplicates: int = 0
    semantic_duplicates: int = 0

    @property
    def removed(self) -> int:
        return self.exact_duplicates + self.semantic_duplicates


@dataclass(frozen=True, slots=True)
class DedupResult:
    items: tuple[ContextItem, ...]
    stats: DedupStats


class ContextDeduplicationEngine:
    def __init__(self, *, semantic: SemanticDeduplicator | None = None) -> None:
        self._semantic = semantic

    def deduplicate(self, items: Sequence[ContextItem]) -> DedupResult:
        # 1. exact dedup by content hash — survivor keeps input order (highest rank first)
        survivors: list[ContextItem] = []
        by_hash: dict[str, int] = {}  # content_hash → index in survivors
        exact = 0
        for item in items:
            h = item.provenance.content_hash
            if h and h in by_hash:
                idx = by_hash[h]
                survivors[idx] = survivors[idx].with_merged((item.id,))
                exact += 1
                continue
            if h:
                by_hash[h] = len(survivors)
            survivors.append(item)

        # 2. optional semantic dedup (injected extension; deterministic groups)
        semantic = 0
        if self._semantic is not None:
            survivors, semantic = self._apply_semantic(survivors)

        stats = DedupStats(
            input_items=len(items),
            output_items=len(survivors),
            exact_duplicates=exact,
            semantic_duplicates=semantic,
        )
        return DedupResult(tuple(survivors), stats)

    def _apply_semantic(self, items: list[ContextItem]) -> tuple[list[ContextItem], int]:
        by_id = {i.id: i for i in items}
        merged_away: set[str] = set()
        removed = 0
        for group in self._semantic.duplicate_groups(items):
            present = [gid for gid in group if gid in by_id and gid not in merged_away]
            if len(present) < 2:
                continue
            keep = present[0]  # deterministic: the injected engine defines group order
            absorbed = tuple(present[1:])
            by_id[keep] = by_id[keep].with_merged(absorbed)
            merged_away.update(absorbed)
            removed += len(absorbed)
        out = [by_id[i.id] for i in items if i.id not in merged_away]
        return out, removed
