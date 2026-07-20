"""Context Budget Manager (M5.3B-2) — deterministic allocation.

Owns **allocation only** (it never ranks, compresses, or orders — those ran already). Given
rank-ordered items + a `ContextBudgetPolicy`, it fits them into the global budget honouring:

- **Reserved budget** — `reserved_kinds` (system/developer instructions) are *always* kept.
- **Per-source budget** — optional per-kind fraction caps (enforced only when `adaptive` is
  off; **adaptive budgeting** lets unused per-source capacity flow to whoever needs it, so
  only the global cap binds).
- **Emergency budget** — if allocation would keep nothing, release an emergency floor to
  keep the single highest-priority item (a minimally-viable context).
- **Overflow strategy** — `drop_lowest` / `compress_then_drop` (drop after the compression
  stage) / `truncate_oldest` / `reject`.
- **Priority strategy** — reserved first, then rank order (adjusted score desc, id).

Deterministic: stable partitions + item-id tie-break, no randomness. Model-aware budgeting
is a future additive `ModelProfile` injection — the global budget just comes from a
different source; the allocation logic is unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .domain import BudgetUsage, ContextItem
from .policies import ContextBudgetPolicy, OverflowStrategy


@dataclass(frozen=True, slots=True)
class BudgetStats:
    limit_tokens: int = 0
    used_tokens: int = 0
    reserved_tokens: int = 0
    emergency_used: bool = False
    dropped_items: int = 0
    adaptive: bool = False


@dataclass(frozen=True, slots=True)
class BudgetResult:
    items: tuple[ContextItem, ...]
    usage: BudgetUsage
    truncated: bool
    rejected: bool
    stats: BudgetStats


class ContextBudgetManager:
    def __init__(self, *, policy: ContextBudgetPolicy | None = None) -> None:
        self._p = policy or ContextBudgetPolicy()

    def allocate(
        self, items: Sequence[ContextItem], *, global_tokens: int | None = None
    ) -> BudgetResult:
        budget = self._p.global_tokens if global_tokens is None else global_tokens

        # unbounded → keep everything (still record per-source usage)
        if budget <= 0:
            return self._keep_all(items)

        reserved = [i for i in items if i.kind in self._p.reserved_kinds]
        discretionary = [i for i in items if i.kind not in self._p.reserved_kinds]
        # priority: reserved first (in rank order), then the rest (already rank-ordered)
        reserved.sort(key=lambda i: (-i.adjusted_score, i.id))

        used = 0
        per_source: dict[str, int] = {}
        kept: list[ContextItem] = []
        for i in reserved:  # reserved always kept
            kept.append(i)
            used += i.token_estimate
            _bump(per_source, i)

        reserved_tokens = used
        caps = self._per_source_caps(budget)
        dropped = 0
        rejected = False
        for i in discretionary:
            est = i.token_estimate
            src = i.provenance.source_runtime.value
            over_global = used + est > budget
            over_source = (
                not self._p.adaptive
                and caps.get(i.kind.value) is not None
                and per_source.get(src, 0) + est > caps[i.kind.value]
            )
            if over_global or over_source:
                if self._p.overflow is OverflowStrategy.REJECT:
                    rejected = True
                dropped += 1
                continue
            kept.append(i)
            used += est
            _bump(per_source, i)

        emergency_used = False
        if not kept and items:  # emergency floor — keep the top item
            top = min(items, key=lambda i: (-i.adjusted_score, i.id))
            if top.token_estimate <= self._p.emergency_tokens or self._p.emergency_tokens == 0:
                kept.append(top)
                used = top.token_estimate
                _bump(per_source, top)
                emergency_used = True

        truncated = dropped > 0
        usage = BudgetUsage(used_tokens=used, limit_tokens=budget, per_source=per_source)
        stats = BudgetStats(
            limit_tokens=budget, used_tokens=used, reserved_tokens=reserved_tokens,
            emergency_used=emergency_used, dropped_items=dropped, adaptive=self._p.adaptive,
        )
        return BudgetResult(tuple(kept), usage, truncated, rejected, stats)

    def _keep_all(self, items: Sequence[ContextItem]) -> BudgetResult:
        used = 0
        per_source: dict[str, int] = {}
        for i in items:
            used += i.token_estimate
            _bump(per_source, i)
        usage = BudgetUsage(used_tokens=used, limit_tokens=0, per_source=per_source)
        return BudgetResult(
            tuple(items), usage, False, False,
            BudgetStats(0, used, 0, False, 0, self._p.adaptive),
        )

    def _per_source_caps(self, budget: int) -> dict[str, int]:
        return {k: int(budget * frac) for k, frac in self._p.per_source_fraction.items()}


def _bump(per_source: dict[str, int], item: ContextItem) -> None:
    key = item.provenance.source_runtime.value
    per_source[key] = per_source.get(key, 0) + item.token_estimate
