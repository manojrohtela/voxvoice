"""Reasoning session + working-set management (M5.5B-1).

`ReasoningSessionManager` owns the lifecycle of the per-conversation `ReasoningSession`
working set — get-or-create, record response package ids (temporary history). It manages
**temporary** session state only (through the `ReasoningRepository`); it holds no store
handle and performs no persistence beyond the repository. The `ReasoningSession` value object
is immutable (in `domain`); every mutation returns a new snapshot the manager persists.
"""

from __future__ import annotations

from collections.abc import Callable

from .domain import ReasoningScope, ReasoningSession
from .repositories import ReasoningRepository


class ReasoningSessionManager:
    def __init__(
        self, repository: ReasoningRepository, *, clock: Callable[[], float] | None = None
    ) -> None:
        import time

        self._repo = repository
        self._clock = clock or time.time

    async def get_or_create(
        self, session_id: str, *, scope: ReasoningScope | None = None
    ) -> ReasoningSession:
        existing = await self._repo.get_session(session_id)
        if existing is not None:
            return existing
        session = ReasoningSession(
            session_id=session_id, scope=scope or ReasoningScope(session_id=session_id),
            updated_at=self._clock(),
        )
        await self._repo.add_session(session)
        return session

    async def get(self, session_id: str) -> ReasoningSession | None:
        return await self._repo.get_session(session_id)

    async def record_response(
        self, session_id: str, response_id: str, *, scope: ReasoningScope | None = None
    ) -> ReasoningSession:
        session = await self.get_or_create(session_id, scope=scope)
        session = session.record_response(response_id, now=self._clock())
        await self._repo.update_session(session)
        return session

    async def clear(self, session_id: str) -> None:
        await self._repo.remove_session(session_id)
