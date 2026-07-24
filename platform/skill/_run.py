"""Deterministic sync<->async bridge (M6.2B-1) — the Skill Runtime copy.

Identical in spirit to every closed runtime's local `_run.py` (a local copy
per package, not a shared import — see `tool/_run.py`'s own docstring): drive
a non-suspending coroutine (the in-memory repository never awaits a real
Future) to completion with no event loop. Self-contained — Skill Runtime
imports no sibling runtime, including Tool Runtime.
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
            "skill sync driver requires non-blocking (in-memory/local) stores; the "
            "coroutine suspended — use the async API for network-backed stores"
        )
