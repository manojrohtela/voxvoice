"""Deterministic `SpeechModel`s for hermetic streaming-VAD tests (M11.7).

The whole model under test control: what it answers, whether it fails, and — the case that matters
most — whether it **blocks**. Blocking is controlled by `threading.Event`s, because the provider
runs inference on a real worker thread; nothing here sleeps for a duration.

Shared by the Silero provider tests, the voice-activity conformance suite and the bootstrap tests.
"""

from __future__ import annotations

import threading
from array import array

from providers.voice_activity.speech_model import SpeechModelError

SAMPLE_RATE = 16000
WINDOW_SAMPLES = 512
WINDOW_BYTES = WINDOW_SAMPLES * 2


class EnergyModel:
    """Speech probability = the window's peak absolute sample / 32767. Silence → 0.0.

    Not a VAD — a transparent, deterministic stand-in whose verdict a test can predict exactly."""

    sample_rate = SAMPLE_RATE
    window_samples = WINDOW_SAMPLES

    def __init__(self) -> None:
        self.windows: list[bytes] = []

    def probability(self, window: bytes) -> float:
        if len(window) != WINDOW_BYTES:
            raise SpeechModelError("wrong window size")
        self.windows.append(window)
        peak = max((abs(s) for s in array("h", window)), default=0)
        return min(1.0, peak / 32767)


class BlockingModel(EnergyModel):
    """Blocks inside `probability()` until the test releases it — the SD7 proof fixture.

    `entered` is set when a window is *inside* the model (so the test knows inference is genuinely
    in progress on the worker thread), `release` lets it return, and `returned` is set afterwards
    (so the test can prove the worker thread actually finished rather than assume it)."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.returned = threading.Event()
        self.thread_names: list[str] = []

    def probability(self, window: bytes) -> float:
        self.thread_names.append(threading.current_thread().name)
        if threading.current_thread() is threading.main_thread():
            # Fail fast rather than deadlock: blocking here would freeze the event loop, and a
            # test that can only catch synchronous inference by hanging is not evidence.
            raise SpeechModelError("inference ran on the caller's thread")
        self.entered.set()
        self.release.wait()
        try:
            return super().probability(window)
        finally:
            self.returned.set()


class FailingModel(EnergyModel):
    """Classifies `fail_after` windows, then raises."""

    def __init__(self, fail_after: int = 0, error: BaseException | None = None) -> None:
        super().__init__()
        self.fail_after = fail_after
        self.error = error if error is not None else SpeechModelError("inference exploded")

    def probability(self, window: bytes) -> float:
        if len(self.windows) >= self.fail_after:
            raise self.error
        return super().probability(window)


class WrongRateModel(EnergyModel):
    sample_rate = 8000


def pcm_window(amplitude: int, *, windows: int = 1) -> bytes:
    """`windows` whole analysis windows of a constant-amplitude signal."""
    return array("h", [amplitude] * (WINDOW_SAMPLES * windows)).tobytes()


__all__ = [
    "SAMPLE_RATE",
    "WINDOW_BYTES",
    "WINDOW_SAMPLES",
    "BlockingModel",
    "EnergyModel",
    "FailingModel",
    "WrongRateModel",
    "pcm_window",
]
