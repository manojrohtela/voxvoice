"""Provider session + working-set management (M5.6B-1).

`ProviderSessionManager` owns the lifecycle of the per-conversation `ProviderSession` working
set — get-or-create, record response ids (temporary history). It manages **temporary**
session state only (through the `ProviderRepository`); it holds no store handle and performs
no persistence beyond the repository. The `ProviderSession` value object is immutable (in
`domain`); every mutation returns a new snapshot the manager persists.
"""

from __future__ import annotations

from collections.abc import Callable

from .domain import ProviderScope, ProviderSession
from .repositories import ProviderRepository


class ProviderSessionManager:
    def __init__(
        self, repository: ProviderRepository, *, clock: Callable[[], float] | None = None
    ) -> None:
        import time

        self._repo = repository
        self._clock = clock or time.time

    async def get_or_create(
        self, session_id: str, *, scope: ProviderScope | None = None
    ) -> ProviderSession:
        existing = await self._repo.get_session(session_id)
        if existing is not None:
            return existing
        session = ProviderSession(
            session_id=session_id, scope=scope or ProviderScope(session_id=session_id),
            updated_at=self._clock(),
        )
        await self._repo.add_session(session)
        return session

    async def get(self, session_id: str) -> ProviderSession | None:
        return await self._repo.get_session(session_id)

    async def record_response(
        self, session_id: str, response_id: str, *, scope: ProviderScope | None = None
    ) -> ProviderSession:
        session = await self.get_or_create(session_id, scope=scope)
        session = session.record_response(response_id, now=self._clock())
        await self._repo.update_session(session)
        return session

    async def clear(self, session_id: str) -> None:
        await self._repo.remove_session(session_id)
