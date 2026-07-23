"""Deterministic sync↔async bridge (M5.6B-1) — the Provider Runtime copy.

Identical in spirit to the closed runtimes' `_run`: drive a non-suspending coroutine (the
local adapters + the deterministic `EchoProviderAdapter` never await a real Future) to
completion with no event loop. The orchestration is async-first; this bridge backs
`execute`/`get`/`remove`/`history` for local adapters. A network-backed adapter/store must
use the async API or this fails loudly rather than blocking.

Self-contained (Provider Runtime imports no sibling runtime); a shared extraction is a future
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
            "provider sync driver requires non-blocking (in-memory/local) adapters; the "
            "coroutine suspended — use the async API for network-backed adapters/stores"
        )
