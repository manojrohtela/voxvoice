"""Reasoning Scheduler (M5.5B-2) — orchestration scheduling only.

Owns **execution ordering** of queued inference, not execution. It is a deterministic priority
queue: requests are ordered by (descending priority, ascending arrival sequence) so ties are
stable and replay-safe. Concurrency and deferred execution are expressed as **hooks** (a
concurrency limit the caller honours, a `deferred_until` gate) — there is **no threading, no
event loop, no timer** here. The caller drains the schedule and runs each request through the
engine itself.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from itertools import count

from .domain import InferenceRequest


@dataclass(frozen=True, slots=True)
class ScheduledItem:
    request: InferenceRequest
    priority: int = 0
    deferred_until: float = 0.0
    seq: int = 0


class ReasoningScheduler:
    """Deterministic priority scheduler for inference requests. Ordering only — no threads."""

    def __init__(self, *, max_concurrency: int = 0) -> None:
        # heap key: (-priority, seq) → highest priority first, FIFO within a priority
        self._heap: list[tuple[int, int, ScheduledItem]] = []
        self._counter = count()
        self._max_concurrency = max_concurrency  # 0 → unbounded (a hook the caller honours)

    def submit(
        self, request: InferenceRequest, *, priority: int = 0, deferred_until: float = 0.0
    ) -> ScheduledItem:
        seq = next(self._counter)
        item = ScheduledItem(
            request=request, priority=priority, deferred_until=deferred_until, seq=seq
        )
        heapq.heappush(self._heap, (-priority, seq, item))
        return item

    def __len__(self) -> int:
        return len(self._heap)

    @property
    def max_concurrency(self) -> int:
        return self._max_concurrency

    def pop(self, *, now: float = 0.0) -> ScheduledItem | None:
        """The next ready item in deterministic order, or None if empty / all deferred.
        Deferred items (`deferred_until > now`) are skipped and left in place."""
        deferred: list[tuple[int, int, ScheduledItem]] = []
        result: ScheduledItem | None = None
        while self._heap:
            key = heapq.heappop(self._heap)
            item = key[2]
            if item.deferred_until > now:
                deferred.append(key)
                continue
            result = item
            break
        for key in deferred:
            heapq.heappush(self._heap, key)
        return result

    def drain(self, *, now: float = 0.0) -> tuple[ScheduledItem, ...]:
        """All currently-ready items in deterministic execution order (leaves deferred queued)."""
        out: list[ScheduledItem] = []
        while (item := self.pop(now=now)) is not None:
            out.append(item)
        return tuple(out)

    @property
    def statistics(self) -> dict[str, int]:
        return {"queued": len(self._heap), "max_concurrency": self._max_concurrency}
