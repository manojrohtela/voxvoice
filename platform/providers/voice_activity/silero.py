"""`SileroVoiceActivity` — real streaming VAD behind the unchanged `VoiceActivityProvider` contract
(M11.7).

The provider is **first-party and library-free**. It owns the per-session buffer, the windowing,
the background inference lifecycle, the O(1) `detect()` hot path and the failure taxonomy; the model
is reached only through the `SpeechModel` seam, whose one Silero implementation lives in
`silero_model.py` (AR-12).

**VAD is not endpointing.** This provider answers *"is speech present in the most recently analysed
audio?"* and nothing else. It creates no turn, closes no utterance and triggers no response —
deciding that a user's turn has ended is M11.8's.

## The architecture ADR-0045 SD7/SD8 requires

```
detect(frame) ──► validate ──► append PCM to a bounded buffer ──► wake ──► return latest verdict
                                          │
                                          ▼  (provider-owned task, one per session)
                              take one 512-sample window ──► model.probability() on the
                              session's single worker thread ──► publish latest verdict
```

`detect()` never runs the model, never awaits, never waits on a queue and never scans history. Its
Python-level work is a fixed number of steps; the only size-dependent work is the C-level copy of
the frame's own bytes into the buffer, which any VAD must do to see the audio at all.

## Windowing and timing

| term | value |
|---|---|
| accepted input | PCM16 LE, mono, **16 000 Hz** — anything else is refused, never reinterpreted |
| incoming frame duration | **any**; frames are concatenated, never assumed to be a fixed size |
| analysis window | 512 samples = **32 ms** (`frame_ms = 32`, exact: 512 / 16 000 s) |
| hop | 32 ms — windows are contiguous and non-overlapping |
| padding | **none** — a partial window waits for more audio and is discarded at `stop()` |
| max buffered audio | **320 ms** (10 windows, 10 240 bytes) |
| overflow policy | drop the **oldest** audio, count it, report it via `ReportsDroppedAudio` |
| inference cadence | as fast as windows become available, one at a time, per session |

The provider never resamples: the runtime's `AudioNormalizer` owns rate conversion (ADR-0044).
A session using this profile must therefore run at 16 kHz so ingress is normalized before the tap.

## `detect()` result semantics

- `VoiceActivitySample(speech=p >= threshold, confidence=p)` for the **most recent completed
  window**, where `p` is the model's speech probability (`emits_confidence=True`).
- **Startup state:** until the first window has been analysed, `speech=False, confidence=0.0` —
  *no evidence of speech yet*, not a claim that inference ran. `windows_analysed()` reports how many
  windows have actually completed.
- The verdict therefore lags the newest audio by at most one window plus the inference in flight.
  The runtime's `VoiceActivityEngine` applies hysteresis on top; this provider adds no smoothing.

## Failures

| cause | raised by | error |
|---|---|---|
| model missing / cannot load | `start()` | `ProviderUnavailable` |
| model load exceeds its deadline | `start()` | `ProviderTimeout` |
| model reports an unexpected rate/window | `start()` | `CapabilityUnsupported` |
| unknown or already-started session | `detect()` / `start()` | `ProviderProtocolError` |
| frame not PCM16 / not mono / not 16 kHz | `detect()` | `CapabilityUnsupported` |
| odd PCM byte count | `detect()` | `ProviderProtocolError` |
| inference failed in the background | **every later** `detect()` | `ProviderUnavailable` |

A background failure is **sticky**: the task ends, and every subsequent `detect()` raises instead
of returning a stale verdict — a VAD that silently kept reporting its last answer would be
pretending to work. The runtime's existing M11.4 auxiliary path records each as a degradation and
passes the frame through (DL-0013). `asyncio.CancelledError` is never translated.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from core.audio import AudioEncoding, AudioFrame
from core.errors import (
    CapabilityUnsupported,
    ProviderError,
    ProviderProtocolError,
    ProviderTimeout,
    ProviderUnavailable,
)
from core.providers.voice_activity import (
    VoiceActivityCapabilities,
    VoiceActivityProvider,
    VoiceActivitySample,
)

from .speech_model import SpeechModel, SpeechModelError, SpeechModelFactory

log = logging.getLogger("voxvoice.providers.voice_activity.silero")

#: The only input rate accepted, in Hz.
SAMPLE_RATE = 16000
#: Samples per analysis window.
WINDOW_SAMPLES = 512
#: Bytes per analysis window (PCM16 → 2 bytes per sample).
WINDOW_BYTES = WINDOW_SAMPLES * 2
#: Analysis window duration in ms — exactly 512 / 16 000 s.
WINDOW_MS = WINDOW_SAMPLES * 1000 // SAMPLE_RATE
#: Maximum audio held between `detect()` and inference, in windows and in ms.
MAX_BUFFERED_WINDOWS = 10
MAX_BUFFERED_MS = MAX_BUFFERED_WINDOWS * WINDOW_MS
MAX_BUFFERED_BYTES = MAX_BUFFERED_WINDOWS * WINDOW_BYTES
#: Deadline for loading one session's model, in seconds. A bound (§A10), not a measured value.
MODEL_LOAD_TIMEOUT_S = 10.0
#: Default speech threshold on the model's probability (Silero's documented default).
DEFAULT_THRESHOLD = 0.5

_BYTES_PER_MS = SAMPLE_RATE * 2 / 1000

#: The verdict before any window has been analysed: no evidence of speech yet.
INITIAL_SAMPLE = VoiceActivitySample(speech=False, confidence=0.0)


class _Session:
    """One session's buffer, model, worker and inference task. Touched only on the event loop,
    except `model`, which is touched only on `executor`'s single thread."""

    __slots__ = (
        "buffer",
        "model",
        "executor",
        "task",
        "wake",
        "latest",
        "failure",
        "dropped_bytes",
        "windows",
    )

    def __init__(self, model: SpeechModel, executor: ThreadPoolExecutor) -> None:
        self.buffer = bytearray()
        self.model = model
        self.executor = executor
        self.task: asyncio.Task[None] | None = None
        self.wake = asyncio.Event()
        self.latest = INITIAL_SAMPLE
        self.failure: ProviderError | None = None
        self.dropped_bytes = 0
        self.windows = 0


class SileroVoiceActivity(VoiceActivityProvider):
    """Streaming neural VAD over an injected `SpeechModelFactory`."""

    name = "silero"
    capabilities = VoiceActivityCapabilities(
        streaming=True, emits_confidence=True, frame_ms=WINDOW_MS
    )

    def __init__(
        self,
        model_factory: SpeechModelFactory,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        model_load_timeout_s: float = MODEL_LOAD_TIMEOUT_S,
    ) -> None:
        if not callable(model_factory):
            raise TypeError("SileroVoiceActivity requires a SpeechModelFactory")
        if not 0.0 < threshold < 1.0:
            raise ValueError(f"threshold must be in (0, 1), got {threshold}")
        if model_load_timeout_s <= 0:
            raise ValueError(f"model_load_timeout_s must be positive, got {model_load_timeout_s}")
        self._model_factory = model_factory
        self._threshold = threshold
        self._model_load_timeout_s = model_load_timeout_s
        self._sessions: dict[str, _Session] = {}

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def start(self, *, session_id: str) -> None:
        if session_id in self._sessions:
            raise ProviderProtocolError(f"silero vad: session {session_id!r} already started")
        # One worker per session: inference for a session is serial (the model is stateful) and
        # never competes with another session's backlog.
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vad-silero")
        loop = asyncio.get_running_loop()
        try:
            model = await asyncio.wait_for(
                loop.run_in_executor(executor, self._model_factory),
                timeout=self._model_load_timeout_s,
            )
            if model.sample_rate != SAMPLE_RATE or model.window_samples != WINDOW_SAMPLES:
                raise CapabilityUnsupported(
                    f"silero vad: model reports {model.sample_rate} Hz / "
                    f"{model.window_samples} samples, expected {SAMPLE_RATE} Hz / {WINDOW_SAMPLES}"
                )
        except asyncio.CancelledError:
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        except TimeoutError:
            executor.shutdown(wait=False, cancel_futures=True)
            raise ProviderTimeout("silero vad: model load exceeded its deadline") from None
        except ProviderError:
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        except SpeechModelError as exc:
            executor.shutdown(wait=False, cancel_futures=True)
            raise ProviderUnavailable(f"silero vad: {exc}") from None
        except Exception:  # noqa: BLE001 — CR6 backstop
            executor.shutdown(wait=False, cancel_futures=True)
            raise ProviderUnavailable("silero vad: the model could not be started") from None

        session = _Session(model, executor)
        session.task = asyncio.create_task(
            self._infer(session_id, session), name=f"vad-silero-inference:{session_id}"
        )
        self._sessions[session_id] = session

    async def stop(self, *, session_id: str) -> None:
        """Cancel and **await** the session's inference task, then release its worker.

        Idempotent. A model call already executing on the worker thread cannot be interrupted —
        native code has no cancellation point — so the task stops waiting for it immediately, its
        result is discarded, and the thread exits when that one call returns."""
        session = self._sessions.pop(session_id, None)
        if session is None:
            return
        task = session.task
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            await asyncio.wait({task})
        session.executor.shutdown(wait=False, cancel_futures=True)
        session.buffer.clear()

    # ── the hot path ─────────────────────────────────────────────────────────

    def detect(self, frame: AudioFrame, *, session_id: str) -> VoiceActivitySample:
        session = self._sessions.get(session_id)
        if session is None:
            raise ProviderProtocolError(f"silero vad: session {session_id!r} is not started")
        if session.failure is not None:
            raise _fresh(session.failure)
        if frame.encoding is not AudioEncoding.PCM16:
            raise CapabilityUnsupported(
                f"silero vad: requires pcm16 audio, got {frame.encoding.value}"
            )
        if frame.channels != 1:
            raise CapabilityUnsupported(f"silero vad: requires mono audio, got {frame.channels}")
        if frame.sample_rate != SAMPLE_RATE:
            raise CapabilityUnsupported(
                f"silero vad: requires {SAMPLE_RATE} Hz audio, got {frame.sample_rate}"
            )
        if len(frame.data) % 2:
            raise ProviderProtocolError("silero vad: pcm16 frame has an odd number of bytes")

        buffer = session.buffer
        buffer += frame.data
        excess = len(buffer) - MAX_BUFFERED_BYTES
        if excess > 0:
            # Bounded, oldest-first, whole samples (every stored frame has an even length). One
            # C-level delete — no Python loop, regardless of how far behind inference is.
            del buffer[:excess]
            session.dropped_bytes += excess
        session.wake.set()
        return session.latest

    # ── background inference ─────────────────────────────────────────────────

    async def _infer(self, session_id: str, session: _Session) -> None:
        loop = asyncio.get_running_loop()
        try:
            while True:
                await session.wake.wait()
                session.wake.clear()
                while len(session.buffer) >= WINDOW_BYTES:
                    window = bytes(session.buffer[:WINDOW_BYTES])
                    del session.buffer[:WINDOW_BYTES]
                    probability = await loop.run_in_executor(
                        session.executor, session.model.probability, window
                    )
                    session.latest = VoiceActivitySample(
                        speech=probability >= self._threshold, confidence=probability
                    )
                    session.windows += 1
        except asyncio.CancelledError:
            raise
        except SpeechModelError as exc:
            session.failure = ProviderUnavailable(f"silero vad: {exc}")
        except Exception:  # noqa: BLE001 — CR6: a library failure still leaves typed
            session.failure = ProviderUnavailable("silero vad: background inference failed")
        if session.failure is not None:
            log.warning(
                "silero vad session %s: inference stopped (%s)", session_id, session.failure
            )

    # ── observability ────────────────────────────────────────────────────────

    def drain_dropped_ms(self, session_id: str) -> float:
        """`ReportsDroppedAudio` — audio discarded by buffer overflow since the last drain."""
        session = self._sessions.get(session_id)
        if session is None or session.dropped_bytes == 0:
            return 0.0
        dropped, session.dropped_bytes = session.dropped_bytes, 0
        return dropped / _BYTES_PER_MS

    def windows_analysed(self, session_id: str) -> int:
        """How many windows have completed inference for this session (0 before the first)."""
        session = self._sessions.get(session_id)
        return session.windows if session is not None else 0

    def buffered_ms(self, session_id: str) -> float:
        """Audio currently waiting for inference, in ms — never more than `MAX_BUFFERED_MS`."""
        session = self._sessions.get(session_id)
        return len(session.buffer) / _BYTES_PER_MS if session is not None else 0.0

    def inference_failed(self, session_id: str) -> bool:
        """Whether background inference has stopped for this session (M11.15).

        Additive observability beside `windows_analysed` and `buffered_ms`, and the one that makes
        the other two interpretable: once inference stops, `buffered_ms` never falls again, so
        *"audio is still buffered"* means *"the worker is behind"* only while this is `False`.
        Without it the difference between a slow worker and a dead one can be learned only by
        waiting — which is a timeout pretending to be a diagnosis.

        Reports the fact the session already holds (`detect()` re-raises from it); it starts no
        inference, clears nothing, and publishes nothing."""
        session = self._sessions.get(session_id)
        return session is not None and session.failure is not None


def _fresh(error: ProviderError) -> ProviderError:
    """A new instance per raise, so repeated failures do not accumulate one shared traceback."""
    return type(error)(str(error))


__all__ = [
    "DEFAULT_THRESHOLD",
    "INITIAL_SAMPLE",
    "MAX_BUFFERED_BYTES",
    "MAX_BUFFERED_MS",
    "MAX_BUFFERED_WINDOWS",
    "MODEL_LOAD_TIMEOUT_S",
    "SAMPLE_RATE",
    "WINDOW_BYTES",
    "WINDOW_MS",
    "WINDOW_SAMPLES",
    "SileroVoiceActivity",
]
