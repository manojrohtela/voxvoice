"""Deterministic sync↔async bridge (M5.1B-1).

The storage capability contracts (`core.providers.stores`) are `async` — a vendor
store may do real I/O — but `core.ports.MemoryPort` (M5.0) and the synchronous
in-process Event Bus are `sync`. `run_coro_sync` drives a coroutine that performs no
real suspension (the in-memory/local adapters never `await` a Future) straight to
completion, with **no event loop**, deterministically.

This is valid only for non-blocking adapters (in-memory, local). A network-backed
store must be driven through the async API (`MemoryRuntime.astore/aretrieve/aforget`);
if such a coroutine suspends, this driver fails loudly rather than silently blocking.
"""

from __future__ import annotations

from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


def run_coro_sync(coro: Coroutine[Any, Any, T]) -> T:
    """Run a non-suspending coroutine to completion synchronously and return its
    result. Raises if the coroutine actually suspends (i.e. it awaited a real
    Future) — that means a blocking adapter was used behind the sync port."""
    try:
        coro.send(None)
    except StopIteration as stop:  # normal completion
        return stop.value  # type: ignore[no-any-return]
    else:  # it yielded a real awaitable → not drivable without an event loop
        coro.close()
        raise RuntimeError(
            "memory sync driver requires non-blocking (in-memory/local) adapters; the "
            "coroutine suspended — use the async API for network-backed stores"
        )
