"""Context Ordering Engine (M5.3B-2) — deterministic final sequence.

Places items into the configured kind order (system → developer → conversation → working
memory → long-term memory → knowledge → tools → workflow → agent → environment → user),
then by context-adjusted score desc, then by item id. Assigns a contiguous `order_index`.
Deterministic; item id breaks every tie. Ordering owns sequencing only — it never re-ranks
or drops.
"""

from __future__ import annotations

from collections.abc import Sequence

from .domain import ContextItem
from .policies import ContextOrderingPolicy


class ContextOrderingEngine:
    def __init__(self, *, policy: ContextOrderingPolicy | None = None) -> None:
        p = policy or ContextOrderingPolicy()
        self._order = {k: i for i, k in enumerate(p.order)}

    def order(self, items: Sequence[ContextItem]) -> list[ContextItem]:
        fallback = len(self._order)

        def key(i: ContextItem) -> tuple[int, float, str]:
            return (self._order.get(i.kind, fallback), -i.adjusted_score, i.id)

        ordered = sorted(items, key=key)
        return [i.with_order(idx) for idx, i in enumerate(ordered)]
