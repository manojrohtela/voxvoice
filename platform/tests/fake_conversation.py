"""Deterministic vendors for a *live-shaped* conversation (M11.11).

M11.5–M11.10's fakes answer "does this component behave correctly": a fixed list of frames, one
utterance, a scripted answer. An end-to-end conversation needs something the others do not — a
media path a test can **speak into over time**, and a recognizer that finalizes when the caller
stops, the way a real one does.

So this module adds exactly two things and reuses everything else:

- `ConversationEndpoint` — a `MediaEndpoint` a test pushes frames into and hangs up, recording
  what the runtime sends back out.
- `scripted_recognizer` — a `Responder` that emits interims while audio is loud and a **Final**
  when it goes quiet, taking its words from a script.

Nothing here sleeps for a duration. Both halves yield control the way a real socket does, so
ordering is decided by the event loop rather than by timing.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

from core.audio import AudioEncoding, AudioFrame
from providers.stt.transcription_session import TranscriptionResult

#: Below this peak sample a 20 ms frame counts as silence, for the recognizer's benefit only.
#: The *runtime's* speech decision is the real VAD's; this only decides when the fake vendor
#: finalizes, which is what a real vendor's own endpointer would do independently.
SILENCE_PEAK = 1000


def frame_is_loud(audio: bytes) -> bool:
    """Whether a PCM16 chunk carries signal. Reads the first sample: the harness's frames are
    constant-amplitude, so one sample is the whole story and scanning would be theatre."""
    if len(audio) < 2:
        return False
    return abs(int.from_bytes(audio[:2], "little", signed=True)) > SILENCE_PEAK


def scripted_recognizer(words: list[str]):
    """A `Responder` that finalizes an utterance when the caller stops speaking.

    One script entry per utterance, consumed in order; when the script runs out the recognizer
    stays silent rather than inventing text, because a fake that keeps answering would hide a
    runtime that asked more often than it should."""
    state = {"speaking": False, "index": 0}

    def responder(audio: bytes) -> list[TranscriptionResult]:
        loud = frame_is_loud(audio)
        if loud:
            if state["index"] >= len(words):
                return []
            state["speaking"] = True
            # An interim per loud frame: the runtime treats every Partial as "the user is
            # speaking", so emitting them is what makes the fake behave like a real vendor.
            return [TranscriptionResult(text=words[state["index"]], is_final=False)]
        if not state["speaking"]:
            return []
        state["speaking"] = False
        text = words[state["index"]]
        state["index"] += 1
        return [TranscriptionResult(text=text, is_final=True, words=((text, 0.0, 0.5),))]

    return responder


class TurnWatcher:
    """Waits for the **runtime's own** turn lifecycle events (M11.15, closes R-70).

    M11.11's harness waited a fixed number of event-loop spins for a response. That is a bet on
    scheduling, and it is a losing one here: the real `SileroVoiceActivity` runs inference on a
    **worker thread**, so the number of loop iterations between pushing audio and the turn
    finishing is not a property of the runtime at all — it depends on the OS. M11.14 recorded the
    consequence (R-70) after one full-suite run failed a metering assertion that passed 25/25 in
    isolation.

    The replacement is not a bigger budget or a retry. It is the fact the runtime already
    publishes: `turn.completed` and `turn.cancelled`, emitted by the Turn Engine through the
    session's own event bus. Waiting on those means the harness waits for *the turn being over*,
    which is the condition the assertions actually care about.

    `turn.completed` is stamped after the clause router has finished and the Playback Engine has
    drained, so by the time it fires every frame of the answer has reached the transport — which
    is why this is a sound signal for tests that then assert on outbound audio.

    The timeout is a **deadline, not a synchronization mechanism**: correctness comes entirely
    from the event, and the timeout exists only so that a runtime which never finishes the turn
    *fails* instead of hanging. A hang is not evidence.
    """

    #: Generous by design. A hermetic turn completes in milliseconds; this only bounds a defect,
    #: so it is far larger than any legitimate turn and is never used to "give the code time".
    DEADLINE_S = 10.0

    def __init__(self, bus) -> None:  # noqa: ANN001 — the session's InProcessEventBus
        from core.events import EventType

        self._finished = asyncio.Event()
        self.completed = 0
        self.cancelled = 0
        self._subscription = bus.subscribe(
            self._on_event,
            types=frozenset({EventType.TURN_COMPLETED, EventType.TURN_CANCELLED}),
        )

    def _on_event(self, event) -> None:  # noqa: ANN001
        from core.events import EventType

        if event.type is EventType.TURN_COMPLETED:
            self.completed += 1
        else:
            self.cancelled += 1
        self._finished.set()

    def arm(self) -> None:
        """Ready the watcher for the next turn. Explicit, so a test says which turn it means."""
        self._finished.clear()

    async def wait(self, *, deadline_s: float | None = None) -> None:
        """Block until this turn reaches a terminal phase, or fail loudly."""
        await asyncio.wait_for(
            self._finished.wait(), timeout=deadline_s or self.DEADLINE_S
        )


#: One Silero analysis window at 16 kHz, in milliseconds (512 samples). Audio shorter than this
#: cannot be analysed yet and is not "behind" — it is a partial window waiting for its rest.
VAD_WINDOW_MS = 512 / 16_000 * 1000


async def vad_drained(provider, session_id: str, *, deadline_s: float = 10.0) -> None:
    """Wait until the voice-activity provider has analysed the audio pushed so far (M11.15).

    **Why a turn boundary needs this (R-70).** `SileroVoiceActivity.detect()` returns the *last
    completed* inference and hands the frame to a worker thread; `turn.completed` therefore says
    the assistant finished answering, **not** that the VAD has caught up with the caller's audio.
    A harness that pushes the next utterance immediately makes every one of its frames land
    against a stale verdict, and the detector never opens a second utterance — which is exactly
    what the first attempt at this fix did, and it is R-67 wearing a different hat.

    The condition is a **runtime fact read from the provider's own public observability**
    (`buffered_ms`), not a spin count and not a sleep: there is no number here describing how long
    to wait, only a description of what "caught up" means. The deadline exists solely so a stuck
    worker fails instead of hanging."""
    loop_deadline = time.monotonic() + deadline_s
    failed = getattr(provider, "inference_failed", None)
    while provider.buffered_ms(session_id) >= VAD_WINDOW_MS:
        if failed is not None and failed(session_id):
            # Inference has stopped, so this audio will never be analysed. Waiting for it would
            # be waiting for something that cannot happen — the distinction `inference_failed`
            # exists to make, and the difference between a diagnosis and a timeout.
            return
        if time.monotonic() > loop_deadline:
            raise TimeoutError(
                f"voice activity did not drain within {deadline_s}s "
                f"({provider.buffered_ms(session_id):.0f} ms still buffered)"
            )
        # Yields to the event loop so the executor's completion callbacks are delivered. It waits
        # for no duration and grants no budget — the `while` condition is what decides.
        await asyncio.sleep(0)


class ConversationEndpoint:
    """A `MediaEndpoint` a test speaks into, and reads the assistant's audio out of.

    The inbound stream stays open until `hangup()`, which is what makes a *conversation* — several
    utterances with the runtime responding between them — rather than a single scripted burst."""

    def __init__(self) -> None:
        self.connected: list[str] = []
        self.closed: list[str] = []
        #: Outbound frames, in the order the runtime sent them. The end of the whole path.
        self.written: list[AudioFrame] = []
        self.send_error: Exception | None = None
        self.started = asyncio.Event()
        self._inbound: asyncio.Queue[AudioFrame | None] = asyncio.Queue()

    # ── test controls ────────────────────────────────────────────────────────

    async def push(self, frame: AudioFrame) -> None:
        """Deliver one caller frame, then yield so the runtime can act on it."""
        self._inbound.put_nowait(frame)
        await asyncio.sleep(0)

    async def hangup(self) -> None:
        """The caller hangs up: the media path ends normally, which is not a failure."""
        self._inbound.put_nowait(None)
        await asyncio.sleep(0)

    @property
    def fills(self) -> list[int]:
        """The first byte of each outbound frame — the identity ordering is asserted against."""
        return [f.data[0] if f.data else -1 for f in self.written]

    # ── MediaEndpoint ────────────────────────────────────────────────────────

    async def connect(self, session_id: str) -> None:
        self.connected.append(session_id)

    async def close(self, session_id: str) -> None:
        self.closed.append(session_id)

    async def inbound(self, session_id: str) -> AsyncIterator[AudioFrame]:
        self.started.set()
        while True:
            frame = await self._inbound.get()
            if frame is None:
                return
            yield frame

    async def send(self, session_id: str, frame: AudioFrame) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.written.append(frame)


class SlowConversationEndpoint(ConversationEndpoint):
    """An endpoint that accepts no outbound audio until released — the back-pressure fixture."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()

    async def send(self, session_id: str, frame: AudioFrame) -> None:
        await self.gate.wait()
        await super().send(session_id, frame)


def pcm_tone(amplitude: int, *, nbytes: int, seq: int | None = None, rate: int = 16000):
    """One constant-amplitude PCM16 mono frame."""
    body = int(amplitude).to_bytes(2, "little", signed=True) * (nbytes // 2)
    return AudioFrame(
        data=body, sample_rate=rate, encoding=AudioEncoding.PCM16, channels=1, seq=seq
    )


__all__ = [
    "SILENCE_PEAK",
    "VAD_WINDOW_MS",
    "vad_drained",
    "ConversationEndpoint",
    "SlowConversationEndpoint",
    "TurnWatcher",
    "frame_is_loud",
    "pcm_tone",
    "scripted_recognizer",
]
