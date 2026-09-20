"""`SileroSpeechModel` — the **only** module that imports the VAD model library (M11.7, AR-12).

A `SpeechModel` over `pysilero-vad` (DL-0181): Silero VAD v6.2.0 weights, executed natively by a
bundled ggml runtime, with no Python dependencies of its own. Everything library-shaped lives
here — the import, the fixed window size, the library's chunk-size exception — and what leaves is
a float probability or a `SpeechModelError`.

## Why the import is lazy

The dependency is declared and locked, but importing it at module load would make every process
that imports the provider package load a native extension that only the `silero` VAD profile uses.
Deferring it to `load_silero_model()` keeps the hermetic suite and the local profile independent of
it, exactly as `AiortcMediaEndpoint` and `DeepgramTranscriptionClient` do.
"""

from __future__ import annotations

from .speech_model import SpeechModel, SpeechModelError

#: Silero VAD operates at 16 kHz only in this packaging.
SILERO_SAMPLE_RATE = 16000
#: Silero v5+ uses a fixed 512-sample window (32 ms at 16 kHz).
SILERO_WINDOW_SAMPLES = 512


class SileroSpeechModel:
    """One session's Silero model. Stateful; used from a single worker thread."""

    def __init__(self, detector: object) -> None:
        self._detector = detector

    @property
    def sample_rate(self) -> int:
        return SILERO_SAMPLE_RATE

    @property
    def window_samples(self) -> int:
        return SILERO_WINDOW_SAMPLES

    def probability(self, window: bytes) -> float:
        try:
            value = float(self._detector(window))  # type: ignore[operator]
        except Exception:  # noqa: BLE001 — classified, never chained (CR6)
            raise SpeechModelError("silero inference failed on a window") from None
        if not 0.0 <= value <= 1.0:
            raise SpeechModelError("silero returned a probability outside [0, 1]")
        return value


def silero_available() -> bool:
    """Whether the model library can be imported — checked by the composition root so that
    selecting the `silero` profile on a host without it fails at startup, not mid-call."""
    try:
        import pysilero_vad  # noqa: F401
    except Exception:  # noqa: BLE001 — ImportError or a native-library load failure
        return False
    return True


def load_silero_model() -> SpeechModel:
    """Load a fresh, independent model. Blocking (reads the bundled weights, initialises ggml) —
    the provider calls it off the event loop."""
    try:
        from pysilero_vad import SileroVoiceActivityDetector
    except Exception:  # noqa: BLE001
        raise SpeechModelError(
            "the pysilero-vad package is not installed or failed to load"
        ) from None
    try:
        detector = SileroVoiceActivityDetector()
    except Exception:  # noqa: BLE001
        raise SpeechModelError("the silero model could not be loaded") from None
    if detector.chunk_samples() != SILERO_WINDOW_SAMPLES:
        raise SpeechModelError("the installed silero packaging uses an unexpected window size")
    return SileroSpeechModel(detector)


__all__ = [
    "SILERO_SAMPLE_RATE",
    "SILERO_WINDOW_SAMPLES",
    "SileroSpeechModel",
    "load_silero_model",
    "silero_available",
]
