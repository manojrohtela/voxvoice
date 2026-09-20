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

#: Inbound media capacity, expressed as **milliseconds of audio** (ADR-0044 AD6) — a frame count
#: is meaningless across sample rates and encodings. Two seconds is generous for a loopback
#: transport whose caller is the test driver, and it is a *bound*: AR-40 and §A11.1.7 forbid an
#: unbounded queue on a media path regardless of how unlikely overflow is in practice.
INBOUND_CAPACITY_MS = 2_000

#: The nominal frame duration this dev transport assumes when converting the declared capacity to
#: a queue depth. Declared here rather than inferred, so the bound is readable rather than magic.
NOMINAL_FRAME_MS = 20

INBOUND_CAPACITY_FRAMES = INBOUND_CAPACITY_MS // NOMINAL_FRAME_MS


class LocalTransport(TransportProvider):
    name = "local"
    capabilities = TransportCapabilities(media_kinds=("audio",), reconnection=False)

    def __init__(self) -> None:
        self._inbound: dict[str, asyncio.Queue[AudioFrame | None]] = {}
        self._sent: dict[str, list[AudioFrame]] = {}
        self._dropped: dict[str, int] = {}

    def _queue(self, session_id: str) -> asyncio.Queue[AudioFrame | None]:
        return self._inbound.setdefault(
            session_id, asyncio.Queue(maxsize=INBOUND_CAPACITY_FRAMES)
        )

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
        """Inject one caller audio frame for a session.

        Bounded with a **declared overflow policy** (ADR-0044 AD6/AD7): the inbound direction is
        `DROP_OLDEST`, because stale caller audio is worthless in a live conversation and keeping
        the freshest preserves responsiveness. Dropping never blocks and never raises — an
        overflowing media path degrades and records, it does not break the call (DL-0013)."""
        queue = self._queue(session_id)
        if queue.full():
            try:
                queue.get_nowait()  # DROP_OLDEST
                self._dropped[session_id] = self._dropped.get(session_id, 0) + 1
            except asyncio.QueueEmpty:  # pragma: no cover — drained concurrently
                pass
        await queue.put(frame)

    def dropped(self, session_id: str) -> int:
        """Frames discarded by the overflow policy — observability, per ADR-0044 AD8."""
        return self._dropped.get(session_id, 0)

    async def hangup(self, session_id: str) -> None:
        """Caller hangs up — ends the session's inbound stream."""
        await self._queue(session_id).put(None)

    def sent(self, session_id: str) -> list[AudioFrame]:
        """Frames played back to the caller for a session (captured)."""
        return list(self._sent.get(session_id, []))
