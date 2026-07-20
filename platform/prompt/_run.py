"""Deterministic sync↔async bridge (M5.4B-1) — the Prompt Runtime copy.

Identical in spirit to `memory._run` / `knowledge._run` / `context._run`: drive a
non-suspending coroutine (the local adapters never await a real Future) to completion with
no event loop. The construction pipeline is async-first; this bridge backs only the
synchronous `PromptPort`/`build`/`get`/`remove`/`history` surface for local adapters. A
network-backed store must use the async API or this fails loudly rather than blocking.

Self-contained (Prompt imports no sibling runtime); a shared extraction is a future
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
            "prompt sync driver requires non-blocking (in-memory/local) adapters; the "
            "coroutine suspended — use the async API for network-backed stores"
        )
