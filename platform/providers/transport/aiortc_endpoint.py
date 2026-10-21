"""`AiortcMediaEndpoint` — **the only module in the repository that imports aiortc** (M11.5).

The boundary P-ADR-0002 and **AR-12** require. Everything outside this file — the transport's
lifecycle, its AR-6 facts, its bounded buffering, the runtime, `core` — is library-free and would
survive swapping aiortc for anything else that satisfies `MediaEndpoint`.

## What crosses the boundary

Only `core.audio.AudioFrame`. An `av.AudioFrame`, an `RTCPeerConnection`, a `MediaStreamTrack` —
none of them leave this module. That is what stops a media library from becoming the architecture.

## The import is lazy, and that is deliberate

`aiortc` pulls a native-extension tree (`av` → ffmpeg, `cryptography`, `pylibsrtp`). Importing it
at module scope would make every test run, every lint pass and every unrelated import pay for it,
and would make `providers.transport` unimportable anywhere the wheels are unavailable. Importing
inside the constructor keeps the cost where the capability is actually used, and keeps the
deterministic `MediaEndpoint` fakes usable with no library present at all.

## Media format — stated, not assumed

WebRTC carries Opus; aiortc decodes it before we see it, so what arrives here is PCM. The values
below are **read from each frame**, never assumed:

* **sample rate** — from `frame.sample_rate` (48 kHz is the WebRTC norm; the *session's* canonical
  rate is a separate contract and the runtime's resampler owns any difference — ADR-0044 AD4);
* **channels** — from the frame's layout;
* **encoding** — `PCM16`, asserted against the decoded format rather than declared blind;
* **captured_at** — a **monotonic** instant taken at receipt (ADR-0044 AD1: a wall clock here
  would be a correctness bug for the end-of-speech measurement built on it);
* **seq** — a per-session counter, since RTP sequence numbers are not exposed at this layer.

Frames whose decoded format is not 16-bit PCM are refused rather than reinterpreted: silently
treating float samples as PCM16 would produce noise that looks like a codec bug three layers away.

## ICE policy is stated, never inherited (M11.16.6, R-71)

`RTCPeerConnection` is **always** constructed with an explicit `RTCConfiguration`, including when
the policy is "no ICE servers". Omitting it makes aiortc substitute a public STUN server of its
own choosing, which is how this deployment spent three milestones contacting a third party on
every call setup without anyone deciding to. See `ice.py` and ADR-0054.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from core.audio import AudioEncoding, AudioFrame

from .ice import NO_ICE_SERVERS, IcePolicy
from .media_endpoint import MediaEndpointError

log = logging.getLogger("voxvoice.providers.transport.aiortc")

#: The decoded sample format this endpoint accepts. aiortc's Opus decoder produces signed 16-bit.
_PCM16_FORMATS = frozenset({"s16", "s16p"})


#: How many outbound frames may wait for the peer connection. The Playback Engine already bounds
#: how far ahead of the caller's ear the runtime runs (400 ms, ADR-0048 PB6); this is the
#: transport's
#: own floor so a stalled peer cannot grow an unbounded queue behind it (AR-40).
OUTBOUND_QUEUE_FRAMES = 64

#: How far behind the audio timeline the outbound track may fall before it re-anchors instead of
#: catching up. A barge-in or a silent pause leaves a gap that is **not** audio owed to the
#: listener; replaying it as a burst is what makes speech sound rushed. 200 ms is comfortably
#: longer than one frame and far shorter than any audible pause.
_PACING_SLACK_S = 0.2

#: How long `inbound()` waits for the ICE transport's verdict once the inbound track has ended
#: (M13.1). The track ends **before** ICE reports failure — aioice signals consent loss by closing
#: its own connection, and that close reaches the receiver before it reaches the ICE monitor — so a
#: classification made the instant the track ends would call every dead network path a hangup.
#:
#: Measured in-process over twelve runs on one machine: the verdict follows the track's end by
#: 0.26-0.83 ms. This is ~120x that worst case, which is headroom for a loaded host rather than a
#: cross-platform guarantee; it is **not** a proven bound and is deliberately a named constant so
#: it can be retuned. Exceeding it is safe by construction: the wait fails **open** to the
#: pre-M13.1 behaviour, so the cost of being wrong is a late `DISCONNECTED`, never a wrong fact.
ICE_SETTLE_DEADLINE_S = 0.1


def _build_outbound_track(queue: asyncio.Queue) -> Any:
    """An `aiortc` audio track fed from `queue`.

    Subclassed **at call time** rather than at import time, so this module still imports with no
    library present — the same reason every other aiortc reference here is function-local.

    **The track paces to wall clock, because a real-time media track must.** M11.16.9 found the
    original reasoning — *"the queue is the pacing"* — to be wrong in the one way that matters: a
    TTS vendor synthesizes far faster than speech is spoken, so the Playback Engine hands over a
    whole utterance in a fraction of its duration. Without pacing the frames leave in a burst, the
    receiver's jitter buffer accelerates playout to catch up, and **the assistant sounds like it
    is rushing**. That is exactly what the first live call sounded like.

    This is not the arbitrary test timing M11.15 removed. That was synchronization guesswork in
    tests; this is the media clock, and `aiortc`'s own `MediaPlayer` does the same thing
    (`contrib/media.py`: `wait = self._start + data_time - time.time()`). Pacing is computed from
    **accumulated sample time**, not a per-frame sleep, so it cannot drift, and it re-anchors after
    an idle gap so a silent pause is never repaid as a burst."""
    import fractions
    import time as _time

    import av
    from aiortc import MediaStreamTrack
    from aiortc.mediastreams import MediaStreamError

    class _OutboundTrack(MediaStreamTrack):
        """PCM16 `AudioFrame`s from the runtime, as `av.AudioFrame`s for the peer connection."""

        kind = "audio"

        def __init__(self) -> None:
            super().__init__()
            self._queue = queue
            #: Presentation timestamp, in samples. Monotonic and derived from the audio actually
            #: sent, so the peer reconstructs the original timeline rather than a clock's.
            self._pts = 0
            #: When the next frame is due, on the monotonic clock. `None` until the first frame.
            self._due: float | None = None

        async def _pace(self, samples: int, sample_rate: int) -> None:
            """Release this frame no earlier than its place in the audio timeline.

            Re-anchors when more than `_PACING_SLACK_S` behind — after a barge-in or a silent
            pause the backlog is not real audio owed to the listener, and repaying it as a burst
            is the very thing being fixed."""
            now = _time.monotonic()
            if self._due is None or now > self._due + _PACING_SLACK_S:
                self._due = now
            delay = self._due - now
            if delay > 0:
                await asyncio.sleep(delay)
            self._due += samples / sample_rate

        async def recv(self) -> Any:
            frame = await self._queue.get()
            if frame is None:  # the sentinel a closing session puts on the queue
                raise MediaStreamError
            channels = max(1, frame.channels)
            samples = len(frame.data) // (2 * channels)
            if samples <= 0:
                raise MediaEndpointError("outbound frame carried no samples")
            layout = "mono" if channels == 1 else "stereo"
            out = av.AudioFrame(format="s16", layout=layout, samples=samples)
            out.planes[0].update(frame.data)
            out.sample_rate = frame.sample_rate
            out.pts = self._pts
            out.time_base = fractions.Fraction(1, frame.sample_rate)
            self._pts += samples
            await self._pace(samples, frame.sample_rate)
            return out

    return _OutboundTrack()


class AiortcMediaEndpoint:
    """A `MediaEndpoint` backed by an `aiortc` peer connection.

    Construction requires a **signalling callable** supplied by the composition root: establishing
    a WebRTC session needs an offer/answer exchange, and *how* that exchange happens (HTTP, a
    WebSocket, a hosted room) is a deployment concern, not a transport one. Keeping it injected is
    what lets the transport stay first-party and lets M11.5 ship without inventing a signalling
    server (which would be M11.5-adjacent scope creep).
    """

    def __init__(self, signaller: Any, *, ice_policy: IcePolicy = NO_ICE_SERVERS) -> None:
        try:
            from aiortc import RTCPeerConnection  # noqa: F401 — presence check only
        except ImportError as exc:  # pragma: no cover — exercised by the availability test
            raise MediaEndpointError(
                "aiortc is not installed; the WebRTC transport cannot be constructed"
            ) from exc
        self._signaller = signaller
        #: Stated by the composition root and **always** passed to the library (M11.16.6, R-71).
        #: The default is explicitly *no* ICE servers, which is not the same as passing nothing:
        #: omitting the configuration makes aiortc fall back to a public STUN server of its own
        #: choosing, and a network topology decided by a library default is not a decision.
        self._ice_policy = ice_policy
        log.info("aiortc endpoint: ICE policy — %s", ice_policy.describe())
        self._connections: dict[str, Any] = {}
        self._tracks: dict[str, Any] = {}
        self._outbound: dict[str, asyncio.Queue] = {}
        self._seq: dict[str, int] = {}
        #: Why a session's media path broke, when the break was reported by ICE rather than by the
        #: track. Consumed by `inbound`, which is the only place that can turn it into the right
        #: AR-6 fact — see `_watch_connection_state`.
        self._faults: dict[str, str] = {}
        #: The teardown a connection-state watcher started, held so it is never an orphan (§12).
        self._closers: dict[str, asyncio.Task] = {}
        #: Set once this session's ICE transport reaches a terminal state, so a track that has
        #: already ended can wait for the verdict instead of guessing it — see `_settled_fault`.
        self._ice_settled: dict[str, asyncio.Event] = {}

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def connect(self, session_id: str) -> None:
        """Negotiate the peer connection through the injected signaller.

        Returns only when the connection is genuinely usable — a half-open connection reported as
        established is exactly the fabricated fact AR-6 forbids."""
        pc = self._new_peer_connection()
        self._connections[session_id] = pc
        self._faults.pop(session_id, None)  # a fresh session inherits no previous fault
        # Created **here**, before any listener can exist: `asyncio.Event` is sticky, so a verdict
        # that arrives before the first `wait()` is still seen — but one that arrives before the
        # Event itself would be lost. A fresh session therefore gets a fresh, unset Event.
        self._ice_settled[session_id] = asyncio.Event()
        self._watch_connection_state(session_id, pc)
        # The outbound track is attached **before** negotiation, so the offer advertises the audio
        # this runtime will send. Attaching it afterwards would produce a recvonly session that
        # silently drops every assistant frame — which is what `send()` did until M11.16.3, because
        # nothing ever populated `_outbound`.
        queue: asyncio.Queue = asyncio.Queue(maxsize=OUTBOUND_QUEUE_FRAMES)
        self._outbound[session_id] = queue
        try:
            pc.addTrack(_build_outbound_track(queue))
            track = await self._signaller.negotiate(session_id, pc)
        except Exception as exc:  # noqa: BLE001 — library/transport errors are mapped here (AR-12)
            await self._safe_close(session_id)
            raise MediaEndpointError(f"negotiation failed: {exc}") from exc
        if track is None:
            await self._safe_close(session_id)
            raise MediaEndpointError("negotiation produced no inbound audio track")
        # Registered only now: the transceivers that carry the ICE transport do not exist until
        # negotiation has produced them. A failure before this point still has a path — it either
        # fails `negotiate` outright or leaves `connectionState` at `failed`, which the watcher
        # above already handles, because the peer connection is not closing yet.
        self._watch_ice_state(session_id, pc)
        self._tracks[session_id] = track
        self._seq[session_id] = 0

    def _watch_connection_state(self, session_id: str, pc: Any) -> None:
        """Close the connection when ICE reports it **failed** (M13.1).

        ## The gap this closes

        aiortc ends the inbound track by itself in exactly one case: a **clean** close, where the
        peer's DTLS `close_notify` drives every DTLS transport to `closed` and
        `RTCPeerConnection.__updateConnectionState` then auto-closes. That is the tab-closed path,
        and it already worked.

        An **abrupt** loss — a sleeping laptop, a dropped Wi-Fi link, a killed browser — sends no
        `close_notify`. aioice's consent-freshness checks eventually fail, ICE moves to `failed`,
        `connectionstatechange` is emitted, and aiortc leaves the connection open: its own comment
        notes it has no `disconnected` state, and nothing closes a failed one. Nobody was listening
        here, so the receiver was never stopped, `recv()` never raised, `inbound()` never returned,
        and the session stayed alive with no media and no event — indefinitely.

        **Detection time is not ours to promise.** It is whatever aioice's consent freshness takes
        (`CONSENT_FAILURES` × `CONSENT_INTERVAL`, ~30 s at the time of writing) plus aiortc's
        propagation, and it depends on the path. Treat it as an expected bound to validate on a
        real network, never as a guarantee.

        ## Why this reports `FAILED`, not `DISCONNECTED`

        Closing the connection ends the track *cleanly*, which would otherwise make `inbound()`
        return normally and the transport emit `DISCONNECTED` — "the caller went away". A dead
        network path is not a hangup, and the console renders the two differently. So the cause is
        recorded here and `inbound()` raises instead, which is what makes the transport emit
        `FAILED`. AR-6 asks for the reality the transport observed; "they hung up" would be a
        fabrication.
        """

        def on_connection_state_change() -> None:
            if getattr(pc, "connectionState", None) != "failed":
                return  # `closed` arrives from our own close; `connected` is not our business
            if session_id not in self._connections:
                return  # already torn down: nothing to close, and nobody left to await a task
            if session_id in self._closers:
                return  # exactly once per session
            log.warning("aiortc: ICE reported failed for %s; closing the connection", session_id)
            self._faults[session_id] = "ice connection failed"
            task = asyncio.ensure_future(self._safe_close(session_id))
            self._closers[session_id] = task
            # Dropped as soon as it finishes, so a completed teardown is not mistaken for one in
            # flight by a later `_safe_close` (which would otherwise await an already-done task).
            task.add_done_callback(lambda _task, sid=session_id: self._closers.pop(sid, None))

        pc.on("connectionstatechange", on_connection_state_change)

    def _ice_transports(self, pc: Any) -> list[Any]:
        """The distinct ICE transports behind this peer connection.

        Reached through documented public properties only — `getTransceivers()` ->
        `receiver.transport` -> `.transport` — which is how aiortc wires its own state updates.
        Probed with `getattr` rather than called outright because a peer-connection double is a
        legitimate `MediaEndpoint` implementation detail: a library-shaped surface must not become
        something the deterministic fakes are required to grow."""
        get_transceivers = getattr(pc, "getTransceivers", None)
        if not callable(get_transceivers):
            return []
        found: list[Any] = []
        seen: set[int] = set()
        for transceiver in get_transceivers() or ():
            dtls = getattr(getattr(transceiver, "receiver", None), "transport", None)
            ice = getattr(dtls, "transport", None)
            # One transport serves every bundled transceiver, so identity — not position — is
            # what makes this a set. Registering twice would be harmless but is still wrong.
            if ice is None or id(ice) in seen or not callable(getattr(ice, "on", None)):
                continue
            seen.add(id(ice))
            found.append(ice)
        return found

    def _watch_ice_state(self, session_id: str, pc: Any) -> None:
        """Record an ICE **transport** failure — the only place that fact survives (M13.1).

        ## Why `connectionState` cannot answer this

        aioice reports consent loss by closing its own connection. That close tears down the local
        DTLS transport first, `dtlsStates == {"closed"}` makes `RTCPeerConnection` auto-close, and
        `close()` raises its own closed flag **before** the ICE monitor promotes the transport to
        `failed`. From then on the peer connection answers `"closed"` and never reaches its
        `"failed" in iceStates` branch — so `connectionstatechange` reports a clean close for a
        path that died. The ICE transport does reach `failed`, and emits `"statechange"` when it
        does; this is the one observer that sees it.

        ## Why recording it is only half the fix

        Measured in-process over six runs, the transition lands **0.4-0.8 ms after** the inbound
        track has already ended, so an observer alone would always be too late. `_settled_fault`
        is the other half. Those timings are one machine's observation, never a bound.

        No teardown is started from here. On this path aiortc is already closing the connection
        itself, and the `connectionState` watcher still owns the paths where it is not."""
        transports = self._ice_transports(pc)
        if not transports:
            # Nothing here will ever report a verdict, so there is nothing to wait for: drop the
            # Event rather than make this session's track end pay the settle deadline for an
            # answer that cannot arrive. This is the shape a peer-connection double has, and it
            # is why the deterministic fakes see exactly the pre-M13.1 timing.
            self._ice_settled.pop(session_id, None)
            return
        for ice in transports:
            self._watch_one_ice_transport(session_id, ice)

    def _watch_one_ice_transport(self, session_id: str, ice: Any) -> None:
        def on_ice_state_change() -> None:
            state = getattr(ice, "state", None)
            if state == "failed":
                # `setdefault`, because the `connectionState` watcher may have recorded the same
                # fault first and the detail it produces is asserted exactly. Either way the
                # session carries one reason, not two.
                self._faults.setdefault(session_id, "ice connection failed")
                log.warning("aiortc: ICE transport failed for %s", session_id)
            if state in ("failed", "closed"):
                # Fault recorded first, verdict signalled second: a waiter woken by this Event
                # must never observe the signal without the reason that caused it.
                settled = self._ice_settled.get(session_id)
                if settled is not None:
                    settled.set()

        # aiortc drops every listener on this transport once it reaches `closed`, so there is
        # nothing to deregister and nothing to leak.
        ice.on("statechange", on_ice_state_change)

    async def _settled_fault(self, session_id: str) -> str | None:
        """The ICE verdict for a track that has just ended — waited for, not guessed.

        The track ends before ICE reports anything, so "was this a hangup or a dead path?" cannot
        be answered at that instant. It is answered here, by waiting on the **condition** under a
        bounded deadline (the M11.15 R-70 rule) rather than by racing it.

        Failing open on timeout is the deliberate trade: no fault means `inbound()` returns and the
        transport reports `DISCONNECTED`, exactly as it did before M13.1. A hangup can therefore
        never become a failure — the only fault this can surface is one an ICE transport actually
        reported — and the worst case is a `DISCONNECTED` late by the deadline."""
        settled = self._ice_settled.get(session_id)
        if settled is not None and not settled.is_set():
            try:
                await asyncio.wait_for(settled.wait(), ICE_SETTLE_DEADLINE_S)
            except TimeoutError:
                # Caught narrowly on purpose: a `CancelledError` here is shutdown withdrawing the
                # session, and swallowing it would report a cancelled call as a hangup (ADR-0046).
                log.warning(
                    "aiortc: ICE gave no verdict for %s within %.3gs; "
                    "reporting the media path as ended",
                    session_id,
                    ICE_SETTLE_DEADLINE_S,
                )
        return self._faults.pop(session_id, None)

    def _new_peer_connection(self) -> Any:
        """Construct the peer connection under this deployment's stated ICE policy.

        `configuration` is passed **unconditionally**, including for the empty policy: aiortc
        substitutes its own public STUN server whenever `iceServers` is `None`, so omitting the
        argument is what produced R-71."""
        from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection

        return RTCPeerConnection(
            configuration=RTCConfiguration(
                iceServers=[RTCIceServer(urls=url) for url in self._ice_policy.stun_urls]
            )
        )

    async def close(self, session_id: str) -> None:
        await self._safe_close(session_id)

    async def _safe_close(self, session_id: str) -> None:
        pc = self._connections.pop(session_id, None)
        # A watcher may already be tearing this session down (M13.1). Awaited rather than
        # cancelled — cancelling mid-`pc.close()` would leave the connection half-closed — and
        # skipped when *this* call is that task, which would otherwise await itself forever.
        closer = self._closers.pop(session_id, None)
        if closer is not None and closer is not asyncio.current_task():
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await closer
        # Woken, **then** dropped, and before `pc.close()` below. A waiter in `inbound()` holds
        # its own reference, so removing it from the map cannot strand it — but dropping it
        # without setting it would park the pump for the whole settle deadline on every ordinary
        # teardown. Setting it is honest: teardown *is* a terminal verdict.
        settled = self._ice_settled.pop(session_id, None)
        if settled is not None:
            settled.set()
        self._tracks.pop(session_id, None)
        queue = self._outbound.pop(session_id, None)
        self._seq.pop(session_id, None)
        if queue is not None:
            # Wake a track parked in `recv()`; without the sentinel its task would outlive the
            # session, which is the orphan §12 forbids. `put_nowait` because teardown must not
            # block on a full queue.
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:  # pragma: no cover — full means the track is not parked
                pass
        release = getattr(self._signaller, "release", None)
        if release is not None:
            release(session_id)
        if pc is not None:
            try:
                await pc.close()
            except Exception as exc:  # noqa: BLE001 — teardown is best-effort by design
                log.warning("aiortc close failed for %s (ignored): %s", session_id, exc)

    # ── inbound ──────────────────────────────────────────────────────────────

    async def inbound(self, session_id: str) -> AsyncIterator[AudioFrame]:
        """Decode caller audio into `AudioFrame`s until the track ends.

        Returning normally means the track ended; raising `MediaEndpointError` means it broke.
        The transport turns that distinction into `DISCONNECTED` versus `FAILED`."""
        from aiortc.mediastreams import MediaStreamError

        track = self._tracks.get(session_id)
        if track is None:
            raise MediaEndpointError(f"no inbound track for session {session_id}")
        while True:
            try:
                frame = await track.recv()
            except MediaStreamError:
                fault = self._faults.pop(session_id, None)
                if fault is None:
                    # Nothing recorded *yet* does not mean nothing happened: on an abrupt loss the
                    # track ends a fraction of a millisecond before ICE reports the failure that
                    # ended it. Ask, under a bounded deadline, rather than assume.
                    fault = await self._settled_fault(session_id)
                if fault is not None:
                    # The track ended because ICE reported the connection failed, so this is a
                    # broken media path and must leave as one (M13.1). The transport turns this
                    # into `FAILED`; returning would make it `DISCONNECTED`.
                    raise MediaEndpointError(f"inbound media ended: {fault}") from None
                return  # the track ended — a hangup, not a fault
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — anything else broke the media path
                raise MediaEndpointError(f"inbound media error: {exc}") from exc
            yield self._to_audio_frame(session_id, frame)

    def _to_audio_frame(self, session_id: str, frame: Any) -> AudioFrame:
        """`av.AudioFrame` → `core.audio.AudioFrame`, with every field read rather than assumed."""
        format_name = getattr(getattr(frame, "format", None), "name", "")
        if format_name not in _PCM16_FORMATS:
            raise MediaEndpointError(
                f"unsupported decoded sample format {format_name!r}; expected 16-bit PCM"
            )
        layout = getattr(frame, "layout", None)
        channels = len(getattr(layout, "channels", ()) or ()) or 1
        planes = getattr(frame, "planes", None) or ()
        if not planes:
            raise MediaEndpointError("decoded frame carried no audio plane")
        # **Slice the plane to the samples it actually holds.** `av` allocates a plane larger
        # than the decoded audio — a 960-sample stereo frame arrives in a 23040-byte buffer of
        # which only 3840 bytes are audio — so `bytes(plane)` hands back five-sixths stale
        # memory. Passing that on kept the signal level plausible while making the stream
        # unintelligible: it cost M11.16.9 an entire debugging session, because every provider
        # received real speech padded with garbage and simply recognized nothing.
        valid = (getattr(frame, "samples", 0) or 0) * channels * 2
        payload = bytes(planes[0])
        if valid and valid <= len(payload):
            payload = payload[:valid]
        seq = self._seq.get(session_id, 0)
        self._seq[session_id] = seq + 1
        return AudioFrame(
            data=payload,
            sample_rate=int(frame.sample_rate),
            encoding=AudioEncoding.PCM16,
            channels=channels,
            captured_at=time.monotonic(),
            seq=seq,
        )

    # ── outbound ─────────────────────────────────────────────────────────────

    async def send(self, session_id: str, frame: AudioFrame) -> None:
        """Hand one already-normalized frame to the outbound track.

        The frame arrives at the session's canonical rate — the runtime's egress resampler ran
        before `transport.send()` (ADR-0044 AD4). This method performs **transport adaptation
        only**: wrapping PCM bytes in the library's frame type. It does not resample."""
        queue = self._outbound.get(session_id)
        if queue is None:
            raise MediaEndpointError(f"session {session_id} has no outbound track")
        if frame.encoding is not AudioEncoding.PCM16:
            raise MediaEndpointError(
                f"outbound frame is {frame.encoding.value!r}; the media path carries PCM16"
            )
        try:
            await queue.put(frame)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MediaEndpointError(f"outbound media error: {exc}") from exc


__all__ = ["AiortcMediaEndpoint"]
