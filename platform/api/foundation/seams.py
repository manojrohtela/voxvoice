"""Deterministic seams: Clock and IdGenerator (M8.2).

Constitution §A2.4 — conversation/behaviour is deterministic; only genuinely
non-deterministic edges (wall-clock time, random id minting) are isolated behind
injectable seams so tests are byte-for-byte reproducible while production uses the
real clock and collision-resistant ids. The foundation's *behaviour* (validation,
permission evaluation, isolation) never depends on either seam's non-determinism.

Default production seams use the real clock and `core.ids.gen_id` (the shared
`org_xxx`/`usr_xxx` prefixed-id primitive). Tests inject `FixedClock` +
`SequentialIdGenerator` for determinism.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

from core.ids import gen_id

# ── id prefixes (one place; extend additively) ──────────────────────────────────
PREFIX_USER = "usr"
PREFIX_ORG = "org"
PREFIX_WORKSPACE = "ws"
PREFIX_MEMBERSHIP = "mbr"
PREFIX_WS_MEMBERSHIP = "wm"  # M8.7 — workspace membership
PREFIX_ROLE_BINDING = "rb"
PREFIX_ENVIRONMENT = "env"
PREFIX_PROJECT = "proj"
PREFIX_PROJECT_MEMBERSHIP = "pm"  # M8.8 — project membership
PREFIX_AGENT = "agt"  # M8.10 — agent (builder config object)
PREFIX_AGENT_VERSION = "agv"  # M8.10 — immutable agent version
PREFIX_TOOL = "tool"  # M8.11 — tool declaration
PREFIX_AGENT_TOOL = "atl"  # M8.11 — agent↔tool attachment
PREFIX_SESSION = "cvs"  # M8.12 — conversation (runtime) session
PREFIX_KNOWLEDGE_COLLECTION = "kc"  # M8.15 — knowledge collection
PREFIX_KNOWLEDGE_DOCUMENT = "kd"  # M8.15 — knowledge document
PREFIX_KNOWLEDGE_CHUNK = "kch"  # M8.15 — knowledge chunk
PREFIX_ALERT_RULE = "alr"  # M8.17 — observability alert rule
PREFIX_EVENT = "evt"


class Clock(ABC):
    """A source of monotonically-usable wall-clock seconds."""

    @abstractmethod
    def now(self) -> float: ...


class SystemClock(Clock):
    """Production clock — the real wall clock."""

    def now(self) -> float:
        return time.time()


class FixedClock(Clock):
    """Deterministic clock for tests. `advance()` steps it forward explicitly."""

    def __init__(self, start: float = 0.0, step: float = 1.0) -> None:
        self._t = start
        self._step = step

    def now(self) -> float:
        self._t += self._step
        return self._t


class IdGenerator(ABC):
    """Mints prefixed, collision-resistant aggregate ids."""

    @abstractmethod
    def new(self, prefix: str) -> str: ...


class DefaultIdGenerator(IdGenerator):
    """Production generator — delegates to the shared `core.ids.gen_id`."""

    def new(self, prefix: str) -> str:
        return gen_id(prefix)


class SequentialIdGenerator(IdGenerator):
    """Deterministic generator for tests — `usr_1`, `usr_2`, ... per prefix."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def new(self, prefix: str) -> str:
        n = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = n
        return f"{prefix}_{n}"
