"""Login throttling (M12.2, security model §3).

Counts **failed** logins per `(email, client)` within a sliding window and refuses further
attempts once the limit is reached. In-process and bounded: correct for the local, single-process
Stage 1 topology, and recorded as a Stage-2 item (a shared store is needed once there are
several API processes).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

#: Keys retained at most; the least recently failed key is dropped first.
_MAX_KEYS = 10_000


class LoginThrottle:
    def __init__(
        self,
        max_failures: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_failures
        self._window = window_seconds
        self._clock = clock
        self._failures: dict[tuple[str, str], deque[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: tuple[str, str], now: float) -> deque[float]:
        entries = self._failures.get(key, deque())
        while entries and now - entries[0] >= self._window:
            entries.popleft()
        return entries

    def blocked(self, email: str, client: str) -> bool:
        with self._lock:
            key = (email, client)
            entries = self._recent(key, self._clock())
            if not entries:
                self._failures.pop(key, None)
            return len(entries) >= self._max

    def record_failure(self, email: str, client: str) -> None:
        with self._lock:
            key = (email, client)
            now = self._clock()
            entries = self._recent(key, now)
            entries.append(now)
            self._failures.pop(key, None)
            self._failures[key] = entries  # re-insert: most recent last
            while len(self._failures) > _MAX_KEYS:
                self._failures.pop(next(iter(self._failures)))

    def clear(self, email: str, client: str) -> None:
        with self._lock:
            self._failures.pop((email, client), None)
