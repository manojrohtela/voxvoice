"""Deterministic sync↔async bridge (M5.2B-1) — the Knowledge Runtime copy.

Identical in spirit to `memory._run`: drive a non-suspending coroutine (the local
adapters never await a real Future) to completion with no event loop. Per the M5.2A
decision, the document **pipeline is async-first**; this bridge backs only the
synchronous `KnowledgePort`/`index`/`get`/`remove` surface for local adapters. A
network-backed store must use the async API or this fails loudly rather than blocking.

Self-contained (Knowledge never imports Memory); a shared extraction is a future
additive refactor.
"""

from __future__ import annotations

from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


def run_coro_sync(coro: Coroutine[Any, Any, T]) -> T:
    try:
        coro.send(None)
    except StopIteration as stop:
        return stop.value  # type: ignore[no-any-return]
    else:
        coro.close()
        raise RuntimeError(
            "knowledge sync driver requires non-blocking (in-memory/local) adapters; the "
            "coroutine suspended — use the async API for network-backed stores"
        )
