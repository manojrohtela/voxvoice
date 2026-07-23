"""Deterministic sync↔async bridge (M6.1B-1) — the Tool Runtime copy.

Identical in spirit to the closed runtimes' `_run` (a local copy per package, not a
shared import — see `provider/_run.py`): drive a non-suspending coroutine (the local
adapters + the deterministic `EchoToolAdapter` never await a real Future) to
completion with no event loop.

Self-contained (Tool Runtime imports no sibling runtime); a shared extraction is a
future additive refactor, matching the M5.8-recorded technical debt item for the
same duplication in Memory/Knowledge/Context/Prompt/Reasoning/Provider.
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
            "tool sync driver requires non-blocking (in-memory/local) adapters; the "
            "coroutine suspended — use the async API for network-backed adapters/stores"
        )
