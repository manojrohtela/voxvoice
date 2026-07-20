"""Context session + window management (M5.3B-1).

`ContextSessionManager` owns the lifecycle of the per-conversation `ContextSession`
working set — get-or-create, append to the bounded rolling `ContextWindow`, record
assembled package ids (history), and expire. It manages the **temporary** session state
only (through the `ContextRepository`); it holds no store handle and performs no long-term
persistence. The `ContextSession`/`ContextWindow` value objects are immutable (in
`domain`); every mutation returns a new snapshot the manager persists.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from .config import WindowConfig
from .domain import (
    ContextItem,
    ContextPackage,
    ContextScope,
    ContextSession,
    ContextWindow,
)
from .repositories import ContextRepository


class ContextSessionManager:
    def __init__(
        self,
        repository: ContextRepository,
        *,
        window: WindowConfig | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        import time

        self._repo = repository
        self._window = window or WindowConfig()
        self._clock = clock or time.time

    async def get_or_create(
        self, session_id: str, *, scope: ContextScope | None = None
    ) -> ContextSession:
        existing = await self._repo.get_session(session_id)
        if existing is not None:
            return existing
        session = ContextSession(
            session_id=session_id,
            scope=scope or ContextScope(session_id=session_id),
            window=ContextWindow(max_items=self._window.max_items),
            updated_at=self._clock(),
        )
        await self._repo.add_session(session)
        return session

    async def get(self, session_id: str) -> ContextSession | None:
        return await self._repo.get_session(session_id)

    async def push_to_window(
        self, session_id: str, items: Sequence[ContextItem], *, scope: ContextScope | None = None
    ) -> ContextSession:
        """Append items to the session's rolling window (evicting the oldest beyond the
        bound); persist + return the new session snapshot."""
        session = await self.get_or_create(session_id, scope=scope)
        window = session.window.push(*items)
        session = session.with_window(window, now=self._clock())
        await self._repo.update_session(session)
        return session

    async def record_package(self, package: ContextPackage) -> ContextSession:
        """Record an assembled package id in the session history; persist + return it."""
        session = await self.get_or_create(package.session_id, scope=package.scope)
        session = session.record_package(package.id, now=self._clock())
        await self._repo.update_session(session)
        return session

    async def clear(self, session_id: str) -> None:
        await self._repo.remove_session(session_id)
