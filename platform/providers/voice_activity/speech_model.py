"""The speech-probability model seam a real streaming VAD is built on (M11.7).

`SileroVoiceActivity` needs exactly one thing from a model: *given one fixed-size window of PCM,
how likely is it that the window contains speech?* This module states that, as a Protocol — the
same shape M11.5 gave WebRTC (`MediaEndpoint`) and M11.6 gave STT (`TranscriptionClient`), for the
same two reasons:

1. **The library stays behind one door.** `silero_model.py` is the only module that imports the
   model library. The provider — bounded buffering, windowing, background inference, the O(1)
   `detect()` hot path, lifecycle and typed failures — is first-party and library-free (AR-12).
2. **The contract is testable without the model.** A deterministic fake satisfies this Protocol,
   including a fake that **blocks**, so "detect() never waits for inference" is provable with
   synchronization primitives rather than a stopwatch (TESTING_POLICY rules 1–2).

A model instance is **stateful** (recurrent state carries across windows) and **not shared**: the
provider creates one per session, calls it from exactly one worker thread, and discards it at stop.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable


class SpeechModelError(Exception):
    """The model could not be loaded or could not classify a window.

    Raised by a model implementation and translated at the provider boundary into the
    `core.errors` taxonomy — a library exception never travels further (AR-12, ADR-0049 CR6)."""


@runtime_checkable
class SpeechModel(Protocol):
    """One session's speech-probability model."""

    @property
    def sample_rate(self) -> int:
        """The only sample rate the model accepts, in Hz."""

    @property
    def window_samples(self) -> int:
        """The exact number of samples in one analysis window."""

    def probability(self, window: bytes) -> float:
        """Speech probability in [0, 1] for one window of signed 16-bit little-endian mono PCM.

        **Synchronous and potentially expensive** — which is exactly why the provider never calls
        it from `detect()`. Raises `SpeechModelError`."""


#: Builds a fresh, independent model. Called once per session, off the event loop.
SpeechModelFactory = Callable[[], SpeechModel]


__all__ = ["SpeechModel", "SpeechModelError", "SpeechModelFactory"]
