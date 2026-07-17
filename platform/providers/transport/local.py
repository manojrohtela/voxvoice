"""LocalTransport — in-process loopback transport for local dev + tests (M2C.2).

Implements the `TransportProvider` contract as a **multi-session service** keyed
by `session_id` (per-session inbound queue + captured outbound frames), so a
single instance can be registered once and shared across concurrent sessions —
matching how a real transport service (WebRTC/telephony) manages many calls. For
local development the test/driver IS the caller (it `feed`s frames and `hangup`s).
A real transport is a drop-in replacement behind the same contract.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from core.audio import AudioFrame
from core.providers.transport import TransportCapabilities, TransportProvider


class LocalTransport(TransportProvider):
    name = "local"
    capabilities = TransportCapabilities(media_kinds=("audio",), reconnection=False)

    def __init__(self) -> None:
        self._inbound: dict[str, asyncio.Queue[AudioFrame | None]] = {}
        self._sent: dict[str, list[AudioFrame]] = {}

    def _queue(self, session_id: str) -> asyncio.Queue[AudioFrame | None]:
        return self._inbound.setdefault(session_id, asyncio.Queue())

    async def start(self, *, session_id: str) -> None:
        self._queue(session_id)
        self._sent.setdefault(session_id, [])

    async def stop(self, *, session_id: str) -> None:
        await self._queue(session_id).put(None)  # sentinel → inbound() terminates

    async def inbound(self, *, session_id: str) -> AsyncIterator[AudioFrame]:
        queue = self._queue(session_id)
        while True:
            frame = await queue.get()
            if frame is None:
                return
            yield frame

    async def send(self, *, session_id: str, frame: AudioFrame) -> None:
        self._sent.setdefault(session_id, []).append(frame)

    # ── local-dev / driver affordances (this transport is the caller) ────────
    async def feed(self, session_id: str, frame: AudioFrame) -> None:
        """Inject one caller audio frame for a session."""
        await self._queue(session_id).put(frame)

    async def hangup(self, session_id: str) -> None:
        """Caller hangs up — ends the session's inbound stream."""
        await self._queue(session_id).put(None)

    def sent(self, session_id: str) -> list[AudioFrame]:
        """Frames played back to the caller for a session (captured)."""
        return list(self._sent.get(session_id, []))
