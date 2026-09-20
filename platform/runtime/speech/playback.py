"""The Playback Engine — bounded, ordered delivery of assistant audio (M11.10).

ADR-0048 PB5/PB6/PB9.

Constitution §A8 names two components this package did not have: an Engine that *"provides ordered
frames, immediate flush (barge-in), and priority slots"*, and a Scheduler that owns policy. This is
the Engine — the **mechanism**. `scheduler.py` is the policy.

```
synthesized AudioFrames ─► enqueue() ─► bounded buffer (capacity in ms) ─► sender task ─► transport
                              │                                               │
                    back-pressure when full                         ordered, one at a time
```

## What it is responsible for

Accepting frames, preserving their order absolutely, bounding how much audio it holds, sending
toward the transport, and stopping *immediately* on cancellation. Nothing else.

## What it must never do

Know a vendor, know the LLM, own conversation state, invoke a provider, decide when a turn ends,
or write to the Epic 10 spine. It imports `core` and the standard library, and that is all — a
structural fact a test asserts rather than a convention a reader must trust.

## Back-pressure, not dropping

ADR-0044 **AD6/AD7** and M11.3 draw the line that matters: inbound audio may drop oldest, because a
late caller frame is worthless; **outbound speech must never be silently dropped**, because a
dropped frame is a word the caller never hears while every subsystem reports success. So the buffer
is bounded by *duration*, and a producer that outruns it **waits**. The wait is an `await` on an
`asyncio.Condition` — never a synchronous block, which would stall the event loop that the
transport itself needs to drain.

The capacity is expressed in **milliseconds of audio**, not a frame count: a frame count means
different things at 8 kHz and 48 kHz, and the thing being bounded is how far ahead of the caller's
ear the runtime is allowed to run.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from enum import StrEnum

from core.audio import AudioEncoding, AudioFrame

#: How much assistant audio may sit between synthesis and the transport.
#:
#: Large enough that ordinary synthesis jitter never stalls the sender, small enough that a
#: barge-in discards only a short tail — the flush is bounded by exactly this much audio
#: (ADR-0048 PB6). It is an **unmeasured implementation default** (R-60): it was chosen as a few
#: multiples of the 20 ms media cadence, not from a measured jitter distribution.
DEFAULT_CAPACITY_MS = 400.0

#: Bytes per PCM16 sample, mono.
BYTES_PER_SAMPLE = 2


class PlaybackState(StrEnum):
    """Where the Engine is in its life. Deliberately small: a state the Engine cannot behave
    differently in is a log line, not a state.

    This is **not** conversation state. The Turn Engine owns whose turn it is; this owns whether
    audio is moving. Nothing here is consulted to decide a turn, and a test asserts that the
    Engine never imports the turn, endpoint or voice-activity packages."""

    #: Constructed, not started. No task exists.
    IDLE = "idle"
    #: Started; the sender task is running and will send whatever is enqueued.
    PLAYING = "playing"
    #: Told there is no more audio; sending what remains, then completing.
    DRAINING = "draining"
    #: Everything enqueued has reached the transport.
    COMPLETED = "completed"
    #: Stopped early — barge-in, hangup or shutdown. Queued audio was discarded.
    CANCELLED = "cancelled"
    #: The transport failed. Distinct from CANCELLED because nobody asked for it.
    FAILED = "failed"


def frame_duration_ms(frame: AudioFrame) -> float:
    """How much audio a frame carries, in milliseconds.

    PCM16 declares enough to compute it exactly. An `OPAQUE` frame — the local/dev text-loopback
    codec — declares no duration at all, and inventing one would make the buffer's bound a
    fiction. Such a frame counts as zero duration and is instead bounded by the frame ceiling, so
    the local profile still cannot grow the buffer without limit."""
    if frame.encoding is not AudioEncoding.PCM16:
        return 0.0
    if frame.sample_rate <= 0 or frame.channels <= 0:
        return 0.0
    samples = len(frame.data) / (BYTES_PER_SAMPLE * frame.channels)
    return samples / frame.sample_rate * 1000.0


class PlaybackEngine:
    """Sends assistant audio to the transport, in order, under a bounded buffer.

    One Engine per session. `send` is the only thing it knows about the outside world — a
    callable taking one frame — so it cannot reach a provider, and the transport's own guard and
    error typing stay where they already are (AR-23)."""

    __slots__ = (
        "_send",
        "_capacity_ms",
        "_max_frames",
        "_queue",
        "_buffered_ms",
        "_condition",
        "_task",
        "_state",
        "_closing",
        "_error",
        "_frames_sent",
        "_frames_discarded",
    )

    def __init__(
        self,
        send: Callable[[AudioFrame], Awaitable[None]] | None = None,
        *,
        capacity_ms: float = DEFAULT_CAPACITY_MS,
        max_frames: int = 256,
    ) -> None:
        if capacity_ms <= 0:
            raise ValueError("capacity_ms must be positive")
        if max_frames <= 0:
            raise ValueError("max_frames must be positive")
        self._send: Callable[[AudioFrame], Awaitable[None]] | None = send
        self._capacity_ms = capacity_ms
        # A second ceiling, in frames, for audio that declares no duration (the local profile's
        # OPAQUE frames). Without it a duration-only bound would be no bound at all for them.
        self._max_frames = max_frames
        self._queue: deque[AudioFrame] = deque()
        self._buffered_ms = 0.0
        self._condition = asyncio.Condition()
        self._task: asyncio.Task[None] | None = None
        self._state = PlaybackState.IDLE
        self._closing = False
        self._error: BaseException | None = None
        self._frames_sent = 0
        self._frames_discarded = 0

    # ── observation ──────────────────────────────────────────────────────────

    @property
    def state(self) -> PlaybackState:
        return self._state

    @property
    def buffered_ms(self) -> float:
        """Audio held between synthesis and the transport. Never exceeds the capacity."""
        return self._buffered_ms

    @property
    def queued_frames(self) -> int:
        return len(self._queue)

    @property
    def frames_sent(self) -> int:
        return self._frames_sent

    @property
    def frames_discarded(self) -> int:
        """Frames dropped by a cancellation. **Only** a cancellation discards audio; back-pressure
        never does, which is the invariant separating a barge-in from silent loss."""
        return self._frames_discarded

    @property
    def error(self) -> BaseException | None:
        return self._error

    # ── lifecycle ────────────────────────────────────────────────────────────

    def attach(self, send: Callable[[AudioFrame], Awaitable[None]]) -> None:
        """Supply the route to the caller.

        Late-bound on purpose: the Engine is a mechanism that can be constructed anywhere, but the
        transport, its guard and the egress normalizer belong to one session — so the route is a
        session fact, handed over when the session starts rather than guessed at construction."""
        self._send = send

    async def start(self) -> None:
        """Start the sender. Idempotent."""
        if self._send is None:
            raise RuntimeError("playback engine: no send route attached")
        if self._task is not None:
            return
        self._state = PlaybackState.PLAYING
        self._closing = False
        self._error = None
        self._task = asyncio.create_task(self._run(), name="playback-engine")

    async def enqueue(self, frame: AudioFrame) -> None:
        """Hand one frame to playback, **waiting** while the buffer is full.

        The wait is the back-pressure: it propagates a slow transport all the way back to
        synthesis, so the runtime stops generating audio faster than the caller can hear it
        instead of accumulating it. Nothing is dropped here, ever."""
        if self._state is PlaybackState.CANCELLED:
            # A cancelled engine accepts nothing more. Silently taking the frame would let audio
            # from before a barge-in reach the caller after it.
            return
        if self._state is PlaybackState.FAILED:
            raise PlaybackFailed("playback engine: the transport failed") from self._error
        duration = frame_duration_ms(frame)
        async with self._condition:
            await self._condition.wait_for(lambda: self._admits(duration))
            if self._state is PlaybackState.CANCELLED:
                return
            self._queue.append(frame)
            self._buffered_ms += duration
            self._condition.notify_all()

    def _admits(self, duration_ms: float) -> bool:
        """Whether one more frame fits. A frame larger than the whole capacity is admitted when
        the buffer is empty — otherwise it could never be sent at all, and refusing to speak is
        worse than briefly exceeding the bound by one frame."""
        if self._state is PlaybackState.CANCELLED:
            return True  # stop waiting; `enqueue` then discards
        if not self._queue:
            return True
        if len(self._queue) >= self._max_frames:
            return False
        return self._buffered_ms + duration_ms <= self._capacity_ms

    async def drain(self) -> None:
        """Declare that no more audio is coming and wait until what is queued has been sent.

        This is what makes "the assistant finished speaking" a fact rather than an assumption:
        the turn completes when the caller has actually been sent the audio."""
        if self._task is None:
            self._state = PlaybackState.COMPLETED
            return
        async with self._condition:
            self._closing = True
            self._condition.notify_all()
        await asyncio.wait({self._task})
        if self._state is PlaybackState.PLAYING or self._state is PlaybackState.DRAINING:
            self._state = PlaybackState.COMPLETED
        self._task = None
        if self._error is not None and self._state is PlaybackState.FAILED:
            raise PlaybackFailed("playback engine: the transport failed") from self._error

    async def cancel(self) -> None:
        """Stop immediately and discard what has not been sent (barge-in, hangup, shutdown).

        **Bounded by construction** (ADR-0048 PB6): the buffer holds at most `capacity_ms`, so the
        flush is at most that much audio and cannot become a best-effort drain of unknown length.
        Idempotent — a second barge-in during the first must not raise."""
        async with self._condition:
            self._state = PlaybackState.CANCELLED
            self._frames_discarded += len(self._queue)
            self._queue.clear()
            self._buffered_ms = 0.0
            self._closing = True
            self._condition.notify_all()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            # `asyncio.wait` rather than `await task`: it never re-raises the child's
            # cancellation, so the caller's own cancellation stays the only one that propagates.
            await asyncio.wait({task})

    async def stop(self) -> None:
        """Shut the engine down for good. Idempotent; safe to call from a `finally`."""
        await self.cancel()

    # ── the sender ───────────────────────────────────────────────────────────

    async def _run(self) -> None:
        """Pull frames in order and send them, one at a time.

        A single consumer is what guarantees ordering: there is no second task that could
        overtake it, so frames leave in exactly the order they were enqueued."""
        try:
            while True:
                async with self._condition:
                    await self._condition.wait_for(lambda: bool(self._queue) or self._closing)
                    if not self._queue:
                        if self._closing:
                            return
                        continue
                    frame = self._queue.popleft()
                    self._buffered_ms = max(0.0, self._buffered_ms - frame_duration_ms(frame))
                    if self._state is PlaybackState.PLAYING and self._closing:
                        self._state = PlaybackState.DRAINING
                    # Notify *before* the send so a waiting producer is released as soon as space
                    # exists, rather than only after a slow transport returns.
                    self._condition.notify_all()
                assert self._send is not None  # attached before `start` admits a sender
                await self._send(frame)
                self._frames_sent += 1
        except asyncio.CancelledError:
            raise  # a cancelled sender is a cancelled sender; never a transport failure
        except BaseException as exc:  # noqa: BLE001 — the transport failed under us
            if not isinstance(exc, Exception):
                raise
            self._error = exc
            self._state = PlaybackState.FAILED
            async with self._condition:
                # Release any producer waiting for space; it will see FAILED and raise rather
                # than wait forever on a sender that is never coming back.
                self._condition.notify_all()


class PlaybackFailed(RuntimeError):
    """Playback could not deliver audio to the transport.

    A plain runtime error rather than a `ProviderError`: the transport's own failures are already
    typed by its guard before they reach here, and re-typing them would claim this layer knows
    which provider broke."""


__all__ = [
    "BYTES_PER_SAMPLE",
    "DEFAULT_CAPACITY_MS",
    "PlaybackEngine",
    "PlaybackFailed",
    "PlaybackState",
    "frame_duration_ms",
]
