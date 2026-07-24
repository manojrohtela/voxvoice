"""Deterministic sync<->async bridge (M6.5B-1) — the Agent Runtime's own
local copy (a local copy per package, not a shared import — see
`tool/_run.py`'s own docstring). Self-contained — Agent Runtime imports no
sibling runtime, including Tool Runtime, Skill Runtime, Hook Framework, and
Workflow Runtime.
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
            "agent sync driver requires non-blocking (in-memory/local) stores; the "
            "coroutine suspended — use the async API for network-backed stores"
        )
