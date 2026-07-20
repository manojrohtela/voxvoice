"""Context Compression Engine (M5.3B-2) — shrink to fit, never lose lineage.

Deterministic core compression that **preserves provenance, ranking lineage, and citation
metadata**: every compressed item keeps its `provenance` (incl. `merged_from`), its
`ranking` breakdown, its `confidence`/`adjusted_score`, and records `compressed_from` + a
`CompressionMetadata`. Nothing is deleted silently — over-long items are trimmed and marked.

- **Knowledge compression** — trim over-long knowledge passages to a token budget (word
  boundary), preserving the citation (offsets stay in provenance).
- **Conversation compression** — deterministic rollup of the oldest conversation items
  beyond a count into a single summary item (cites the rolled-up ids).
- **Semantic compression / summarization** — via injected `Compressor`/`Summarizer` ports
  (off by default; no LLM in-runtime).

The engine only rewrites text + metadata; it never drops an item (dropping is the
BudgetManager's job) and never re-ranks.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .domain import (
    ContextItem,
    ContextKind,
    Provenance,
    RankingBreakdown,
    SourceRuntime,
)
from .extensions import Compressor, Summarizer
from .ids import content_hash, item_id_for
from .policies import ContextCompressionPolicy
from .ports import Tokenizer


@dataclass(frozen=True, slots=True)
class CompressionStats:
    input_items: int = 0
    output_items: int = 0
    compressed_items: int = 0
    rolled_up_items: int = 0
    tokens_before: int = 0
    tokens_after: int = 0


@dataclass(frozen=True, slots=True)
class CompressionResult:
    items: tuple[ContextItem, ...]
    stats: CompressionStats


class ContextCompressionEngine:
    def __init__(
        self,
        tokenizer: Tokenizer,
        *,
        policy: ContextCompressionPolicy | None = None,
        summarizer: Summarizer | None = None,
        compressor: Compressor | None = None,
    ) -> None:
        self._tok = tokenizer
        self._p = policy or ContextCompressionPolicy()
        self._summarizer = summarizer
        self._compressor = compressor

    def compress(self, items: Sequence[ContextItem]) -> CompressionResult:
        before = sum(i.token_estimate for i in items)
        if not self._p.enabled:
            stats = CompressionStats(len(items), len(items), 0, 0, before, before)
            return CompressionResult(tuple(items), stats)

        # 1. conversation rollup (deterministic; oldest-beyond-N → one summary item)
        items, rolled = self._rollup_conversation(items)

        # 2. per-item trim/compress for over-long items (knowledge + others)
        out: list[ContextItem] = []
        compressed = 0
        for item in items:
            if item.token_estimate > self._p.max_item_tokens and item.text.strip():
                out.append(self._compress_item(item))
                compressed += 1
            else:
                out.append(item)

        after = sum(i.token_estimate for i in out)
        stats = CompressionStats(
            input_items=len(items) + rolled, output_items=len(out),
            compressed_items=compressed, rolled_up_items=rolled,
            tokens_before=before, tokens_after=after,
        )
        return CompressionResult(tuple(out), stats)

    # ── per-item compression (injected port, else deterministic trim) ────────
    def _compress_item(self, item: ContextItem) -> ContextItem:
        target = self._p.max_item_tokens
        if self._compressor is not None:
            text = self._compressor.compress(item.text, target_tokens=target)
            method = "compressor"
        else:
            text = _trim_to_tokens(item.text, target, self._tok)
            method = "trim"
        return item.with_compressed_text(
            text, method=method, new_tokens=self._tok.count(text), sources=(item.id,)
        )

    # ── conversation rollup (deterministic; provenance-preserving) ───────────
    def _rollup_conversation(
        self, items: Sequence[ContextItem]
    ) -> tuple[list[ContextItem], int]:
        convo = [i for i in items if i.kind is ContextKind.CONVERSATION_HISTORY]
        limit = self._p.conversation_rollup_after
        if limit <= 0 or len(convo) <= limit:
            return list(items), 0

        # keep the most recent `limit`; roll up the oldest into one summary item
        keep_ids = {i.id for i in convo[-limit:]}
        old = [i for i in convo if i.id not in keep_ids]
        summary_text = (
            self._summarizer.summarize(old) if self._summarizer is not None
            else _deterministic_rollup(old)
        )
        rolled = _rollup_item(old, summary_text, self._tok)
        out: list[ContextItem] = []
        inserted = False
        for i in items:
            if i.kind is ContextKind.CONVERSATION_HISTORY and i.id not in keep_ids:
                if not inserted:  # place the rollup where the first old item was
                    out.append(rolled)
                    inserted = True
                continue
            out.append(i)
        return out, len(old)


def _trim_to_tokens(text: str, target_tokens: int, tok: Tokenizer) -> str:
    """Deterministic word-boundary trim to ~target tokens, with an ellipsis marker."""
    words = text.split()
    lo, hi = 0, len(words)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if tok.count(" ".join(words[:mid])) <= target_tokens:
            lo = mid
        else:
            hi = mid - 1
    kept = words[:lo]
    return (" ".join(kept) + " …") if lo < len(words) else " ".join(kept)


def _deterministic_rollup(items: Sequence[ContextItem]) -> str:
    """A vendor-free rollup: first sentence of each old item, joined."""
    parts = []
    for i in items:
        head = i.text.strip().split(". ")[0].strip()
        if head:
            parts.append(head)
    return " | ".join(parts)


def _rollup_item(old: Sequence[ContextItem], text: str, tok: Tokenizer) -> ContextItem:
    ids = tuple(i.id for i in old)
    chash = content_hash(text)
    iid = item_id_for(ContextKind.CONVERSATION_HISTORY.value, "context", "rollup", text)
    # inherit the best ranking of the rolled-up items so ordering stays sensible
    best = max(old, key=lambda i: i.adjusted_score)
    from .domain import BudgetMetadata, CompressionMetadata

    return ContextItem(
        id=iid,
        kind=ContextKind.CONVERSATION_HISTORY,
        text=text,
        confidence=best.confidence,
        confidence_source="context",
        provenance=Provenance(
            source_runtime=SourceRuntime.SYSTEM,
            source_id="rollup",
            original_score=best.provenance.original_score,
            content_hash=chash,
            compressed_from=ids,
        ),
        adjusted_score=best.adjusted_score,
        ranking=RankingBreakdown(
            dict(best.ranking.signals), best.ranking.total, "conversation_rollup"
        ),
        budget=BudgetMetadata(token_estimate=tok.count(text), counted_by=tok.name),
        compression=CompressionMetadata(
            compressed=True, method="rollup",
            original_tokens=sum(i.token_estimate for i in old), final_tokens=tok.count(text),
        ),
    )
