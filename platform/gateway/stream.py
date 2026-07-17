"""EventStream — one client's ordered, bounded, non-blocking delivery buffer (M4.2).

Decouples the Runtime (which offers events synchronously during bus dispatch) from a
possibly-slow client (which drains asynchronously over its transport). `offer` is
**non-blocking** — it never awaits and never blocks the Runtime thread. When the
buffer is full a configurable **overflow policy** protects against slow consumers
without touching other connections or the Runtime.

Ordering is FIFO (monotonic `seq` preserved); each item is delivered at most once.
"""

from __future__ import annotations

import asyncio
from collections import deque
from enum import StrEnum


class OverflowPolicy(StrEnum):
    DROP_OLDEST = "drop_oldest"  # keep the freshest events (default for live)
    DROP_NEWEST = "drop_newest"  # keep the earliest, reject the incoming
    DISCONNECT = "disconnect"  # signal the caller to drop a slow client


class OfferResult(StrEnum):
    DELIVERED = "delivered"
    DROPPED = "dropped"  # buffered but an item was dropped per policy
    OVERFLOW = "overflow"  # DISCONNECT policy: caller must disconnect


class EventStream:
    """A bounded FIFO of serialized frames for one client."""

    def __init__(
        self,
        *,
        maxsize: int = 1000,
        policy: OverflowPolicy = OverflowPolicy.DROP_OLDEST,
    ) -> None:
        self._buf: deque[str] = deque()
        self._maxsize = max(1, maxsize)
        self._policy = policy
        self._waiter: asyncio.Future[None] | None = None
        self._closed = False
        self._dropped = 0

    @property
    def dropped(self) -> int:
        return self._dropped

    @property
    def depth(self) -> int:
        return len(self._buf)

    @property
    def closed(self) -> bool:
        return self._closed

    # ── producer side (non-blocking; called on the Runtime/bus thread) ──────
    def offer(self, frame: str) -> OfferResult:
        if self._closed:
            return OfferResult.DROPPED
        result = OfferResult.DELIVERED
        if len(self._buf) >= self._maxsize:
            if self._policy is OverflowPolicy.DROP_NEWEST:
                self._dropped += 1
                return OfferResult.DROPPED
            if self._policy is OverflowPolicy.DISCONNECT:
                return OfferResult.OVERFLOW
            # DROP_OLDEST
            self._buf.popleft()
            self._dropped += 1
            result = OfferResult.DROPPED
        self._buf.append(frame)
        self._wake()
        return result

    # ── consumer side (async; the transport adapter drains this) ────────────
    async def __aiter__(self):
        while True:
            while self._buf:
                yield self._buf.popleft()
            if self._closed:
                return
            await self._wait()

    def close(self) -> None:
        self._closed = True
        self._wake()

    # ── internals ────────────────────────────────────────────────────────────
    def _wake(self) -> None:
        if self._waiter is not None and not self._waiter.done():
            self._waiter.set_result(None)

    async def _wait(self) -> None:
        if self._buf or self._closed:
            return
        self._waiter = asyncio.get_event_loop().create_future()
        await self._waiter
        self._waiter = None
