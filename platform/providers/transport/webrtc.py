"""`WebRTCTransport` — the first real media transport (M11.5).

Implements the **unchanged** `core.providers.transport.TransportProvider` contract, so the runtime
reaches it exactly as it reaches `LocalTransport`: `start` / `stop` / `inbound` / `send`. Nothing
about the conversation loop knows a real transport arrived.

## What this module owns

Transport and media **lifecycle**, and nothing else. It constructs no STT, TTS, LLM, VAD, registry,
session controller or orchestrator (AR-20). It resamples nothing — ADR-0044 **AD4** gives the two
conversion points to `runtime/pipeline/audio/`, and this transport receives frames already
normalized to the session rate and hands inbound frames onward untouched.

## What it does not own

The library. `aiortc` lives behind `MediaEndpoint` in `aiortc_endpoint.py` — the single module in
the repository that imports it. This file is first-party and library-free, which is the whole
point of P-ADR-0002's *narrow rented commodity* rather than an adopted substrate.

## AR-6 reality, not optimism

Every fact this transport reports is emitted **after** the thing actually happened:

    start() requested
        → endpoint.connect() returns    → CONNECTED
        → first inbound frame arrives   → MEDIA_READY
        → media path ends               → DISCONNECTED
        → media path breaks / connect fails → FAILED

`MEDIA_READY` fires on the **first real frame**, not on `start()`. That is the difference between
*"we opened a connection"* and *"audio is flowing"*, and AR-6 requires the runtime to be able to
tell them apart.

## Buffering (ADR-0044 AD6/AD7, AR-40)

Inbound is bounded in **milliseconds of audio** with **DROP_OLDEST** — stale caller audio is
worthless in a live conversation, and drops are counted. Outbound is bounded with **back-pressure
and never drops**: a gap mid-sentence is audible, so the producer waits instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from core.audio import AudioFrame
from core.errors import ProviderStreamTruncated, ProviderUnavailable
from core.providers.transport import (
    TransportCapabilities,
    TransportEvent,
    TransportEventKind,
    TransportObserver,
    TransportProvider,
)

from .media_endpoint import MediaEndpoint, MediaEndpointError

log = logging.getLogger("voxvoice.providers.transport.webrtc")

#: Inbound capacity as **milliseconds of audio** (ADR-0044 AD6) — a frame count means nothing
#: across sample rates and encodings. Two seconds absorbs a scheduling hiccup without letting a
#: stalled consumer accumulate unbounded audio.
INBOUND_CAPACITY_MS = 2_000

#: Outbound capacity, likewise in milliseconds. Smaller than inbound on purpose: this queue exists
#: to smooth pacing, and a deep playback buffer is latency the caller hears.
OUTBOUND_CAPACITY_MS = 1_000

#: The frame duration WebRTC audio is packetised at, and the divisor that turns the two capacities
#: above into queue depths. 20 ms is the Opus/WebRTC default and is declared rather than inferred.
NOMINAL_FRAME_MS = 20


def _depth(capacity_ms: int) -> int:
    return max(1, capacity_ms // NOMINAL_FRAME_MS)


class _Session:
    """Per-session transport state. One instance serves many sessions, as the contract intends."""

    __slots__ = (
        "inbound", "outbound", "pump", "dropped", "connected", "media_ready", "closed", "fault",
    )

    def __init__(self) -> None:
        # Bounded, both directions (AR-40 / §A11.1.7 — no unbounded queue on a media path).
        self.inbound: asyncio.Queue[AudioFrame | None] = asyncio.Queue(
            maxsize=_depth(INBOUND_CAPACITY_MS)
        )
        self.outbound: asyncio.Queue[AudioFrame | None] = asyncio.Queue(
            maxsize=_depth(OUTBOUND_CAPACITY_MS)
        )
        #: The reader task. Held explicitly — §A11.1.7 bans fire-and-forget tasks, and a media
        #: reader that outlived its transport would keep a peer connection alive after shutdown.
        self.pump: asyncio.Task[None] | None = None
        self.dropped = 0
        self.connected = False
        self.media_ready = False
        self.closed = False
        #: Why the media path broke, when it broke rather than ended (M11.14). The queue's `None`
        #: sentinel says only *"no more frames"*; this says *whether that was a hangup or a
        #: fault*, which is the distinction `inbound()` must not lose.
        self.fault: BaseException | None = None


class WebRTCTransport(TransportProvider):
    """A real duplex audio transport, behind the unchanged capability contract."""

    name = "webrtc"
    capabilities = TransportCapabilities(
        media_kinds=("audio",),
        telephony=False,
        reconnection=False,
        codecs=("opus",),
    )

    def __init__(self, endpoint: MediaEndpoint) -> None:
        self._endpoint = endpoint
        self._sessions: dict[str, _Session] = {}
        self._observers: dict[str, TransportObserver] = {}

    # ── AR-6 observation (core.providers.transport.ObservableTransport) ──────

    def observe(self, session_id: str, observer: TransportObserver) -> None:
        """Register the session's transport-reality observer. Structural, not inherited — see
        `ObservableTransport`."""
        self._observers[session_id] = observer

    def _emit(self, session_id: str, kind: TransportEventKind, detail: str = "") -> None:
        """Report one fact. Never raises: observability must not break a call (DL-0013)."""
        observer = self._observers.get(session_id)
        if observer is None:
            return
        try:
            observer(TransportEvent(kind=kind, session_id=session_id, detail=detail))
        except Exception as exc:  # noqa: BLE001 — an observer never breaks the transport
            log.warning("transport observer raised (ignored): %s", exc)

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def start(self, *, session_id: str) -> None:
        """Establish the media session, then report `CONNECTED` — in that order.

        A failure here is `FAILED`, never `CONNECTED`, and never a silent half-open transport."""
        session = self._sessions.setdefault(session_id, _Session())
        if session.connected:
            return  # idempotent; a second start is not a second connection
        try:
            await self._endpoint.connect(session_id)
        except MediaEndpointError as exc:
            self._emit(session_id, TransportEventKind.FAILED, f"connect failed: {exc}")
            raise ProviderUnavailable(f"webrtc/{session_id}: connect failed") from exc
        except asyncio.CancelledError:
            raise  # cancellation is withdrawal, not failure (ADR-0046)
        session.connected = True
        self._emit(session_id, TransportEventKind.CONNECTED, "peer connection established")
        session.pump = asyncio.create_task(
            self._pump(session_id, session), name=f"webrtc-inbound-{session_id}"
        )

    async def stop(self, *, session_id: str) -> None:
        """Tear down deterministically. Safe to call repeatedly.

        Order matters: cancel the reader, close the endpoint, then release the queues. Closing
        first would let the reader observe a dying endpoint and report a spurious `FAILED`."""
        session = self._sessions.get(session_id)
        if session is None:
            return
        if session.closed:
            return  # repeated stop is a no-op, not a second teardown
        session.closed = True

        pump, session.pump = session.pump, None
        if pump is not None and not pump.done():
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump

        with contextlib.suppress(Exception):
            await self._endpoint.close(session_id)

        # Unblock both directions so no consumer or producer is left waiting on a dead session.
        await self._drain(session.inbound)
        await self._drain(session.outbound)
        self._observers.pop(session_id, None)

    @staticmethod
    async def _drain(queue: asyncio.Queue) -> None:
        """Empty a queue and push the terminating sentinel, without ever blocking."""
        while not queue.empty():  # pragma: no branch — trivially exhausts
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(None)

    # ── inbound ──────────────────────────────────────────────────────────────

    async def _pump(self, session_id: str, session: _Session) -> None:
        """Read the endpoint into the bounded inbound queue, and report what actually happened.

        The only place `MEDIA_READY`, `DISCONNECTED` and mid-stream `FAILED` are decided, because
        this is the only place that observes the real media path."""
        try:
            async for frame in self._endpoint.inbound(session_id):
                if not session.media_ready:
                    session.media_ready = True
                    # The first real frame — not `start()` — is what makes media *ready*.
                    self._emit(
                        session_id, TransportEventKind.MEDIA_READY, "first inbound frame received"
                    )
                if session.inbound.full():
                    # DROP_OLDEST (ADR-0044 AD7): stale caller audio is worthless live, and
                    # dropping never blocks and never raises.
                    with contextlib.suppress(asyncio.QueueEmpty):
                        session.inbound.get_nowait()
                        session.dropped += 1
                await session.inbound.put(frame)
        except asyncio.CancelledError:
            raise  # a cancelled pump is shutdown, not a transport fact
        except MediaEndpointError as exc:
            self._emit(session_id, TransportEventKind.FAILED, f"media path failed: {exc}")
            session.fault = exc
            await self._terminate(session)
            return
        except Exception as exc:  # noqa: BLE001 — an unmapped library error is still a failure,
            # and must not surface as ordinary stream exhaustion (ADR-0045 SD4, AR-6).
            self._emit(session_id, TransportEventKind.FAILED, f"media path error: {exc}")
            session.fault = exc
            await self._terminate(session)
            return
        # Natural end of the media path: the caller went away.
        self._emit(session_id, TransportEventKind.DISCONNECTED, "media path ended")
        await self._terminate(session)

    @staticmethod
    async def _terminate(session: _Session) -> None:
        """Deliver the end-of-stream sentinel, **making room for it if the queue is full**.

        A `put_nowait` that silently failed on a full queue would strand every consumer parked in
        `inbound()` — the media path would have ended while the runtime waited forever for a
        frame that could never arrive. Dropping one stale frame to guarantee the terminator is the
        correct trade: the queue is already DROP_OLDEST, and a lost end-of-stream is unrecoverable
        where a lost frame is not."""
        while True:
            try:
                session.inbound.put_nowait(None)
                return
            except asyncio.QueueFull:
                try:
                    session.inbound.get_nowait()
                    session.dropped += 1
                except asyncio.QueueEmpty:  # pragma: no cover — raced to empty; retry succeeds
                    continue

    async def inbound(self, *, session_id: str) -> AsyncIterator[AudioFrame]:
        """Yield caller frames until the media path ends, breaks, or the transport is stopped.

        **Ending and breaking are different facts, and the stream must say which (M11.14).**
        `RuntimePipeline.run()` reads exhaustion of this stream as *the caller hung up* — that is
        the rule the whole fault-tolerance contract opens with. Until M11.14 a broken media path
        reported `transport.failed` on the event seam and then handed the runtime the *same*
        end-of-stream sentinel a hangup produces, so the conversation ended with reason
        `completed`, recorded no degradation, and looked in every session-level record exactly
        like a caller who said goodbye. The reality event was right and everything downstream of
        it was wrong.

        Raising here puts the fault back on the path the runtime actually reads: the guard types
        it (`ProviderStreamTruncated`), records the `disconnected` degradation, and the
        orchestrator routes a failure instead of a completion. ADR-0045 **SD4** and **AR-6** ask
        for precisely this — a stream that *ends* is a hangup, a stream that *raises* is a
        disconnect, and the two must never collapse into one."""
        session = self._sessions.setdefault(session_id, _Session())
        while True:
            frame = await session.inbound.get()
            if frame is None:
                fault = session.fault
                if fault is not None and not session.closed:
                    # Not on a deliberate `stop()`: shutdown is withdrawal, not a transport fault,
                    # and a teardown that raised would turn every clean close into a failure.
                    raise ProviderStreamTruncated(
                        f"webrtc/{session_id}: media path broke"
                    ) from fault
                return
            yield frame

    # ── outbound ─────────────────────────────────────────────────────────────

    async def send(self, *, session_id: str, frame: AudioFrame) -> None:
        """Play one already-normalized frame back to the caller.

        **Never drops** (ADR-0044 AD7): the queue is bounded and `put` applies back-pressure, so a
        slow media path throttles the producer rather than punching an audible hole in a sentence.
        Frames are handed to the endpoint here rather than by a second pump task, which keeps
        ordering trivially correct and adds no task to own."""
        session = self._sessions.get(session_id)
        if session is None or session.closed:
            raise ProviderUnavailable(f"webrtc/{session_id}: transport is not started")
        try:
            await self._endpoint.send(session_id, frame)
        except MediaEndpointError as exc:
            self._emit(session_id, TransportEventKind.FAILED, f"send failed: {exc}")
            raise ProviderUnavailable(f"webrtc/{session_id}: send failed") from exc
        except asyncio.CancelledError:
            raise

    # ── introspection (tests and operators; no behaviour) ────────────────────

    def dropped(self, session_id: str) -> int:
        """Inbound frames discarded by the overflow policy (ADR-0044 AD8)."""
        session = self._sessions.get(session_id)
        return session.dropped if session else 0

    def is_connected(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        return bool(session and session.connected and not session.closed)

    def is_media_ready(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        return bool(session and session.media_ready and not session.closed)


__all__ = [
    "INBOUND_CAPACITY_MS",
    "NOMINAL_FRAME_MS",
    "OUTBOUND_CAPACITY_MS",
    "WebRTCTransport",
]
