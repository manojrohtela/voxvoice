"""The Silero model adapter — without the native library (M11.7).

`silero_model.py` is the one module that imports `pysilero_vad`. Its behaviour is proven by
installing a **stand-in module** in `sys.modules`, so the suite behaves identically whether or not
the real wheel is installed (it is under `uv sync`, and is not in a bare dev venv).

What these tests do not prove is how the real model classifies real speech. That was checked once,
functionally, outside the suite (DL-0182) and is not a quality claim.
"""

from __future__ import annotations

import sys
import types

import pytest

from providers.voice_activity.silero_model import (
    SILERO_SAMPLE_RATE,
    SILERO_WINDOW_SAMPLES,
    SileroSpeechModel,
    load_silero_model,
    silero_available,
)
from providers.voice_activity.speech_model import SpeechModel, SpeechModelError


class _Detector:
    def __init__(self, *, value: float = 0.25, chunk: int = 512, error: Exception | None = None):
        self.value, self.chunk, self.error = value, chunk, error
        self.calls: list[bytes] = []

    def chunk_samples(self) -> int:
        return self.chunk

    def __call__(self, audio: bytes) -> float:
        self.calls.append(audio)
        if self.error is not None:
            raise self.error
        return self.value


def _install(
    monkeypatch, detector: _Detector | None = None, *, ctor_error: Exception | None = None
):
    module = types.ModuleType("pysilero_vad")

    def factory():
        if ctor_error is not None:
            raise ctor_error
        return detector or _Detector()

    module.SileroVoiceActivityDetector = factory
    monkeypatch.setitem(sys.modules, "pysilero_vad", module)


def test_the_declared_format_is_silero_v5_plus():
    assert (SILERO_SAMPLE_RATE, SILERO_WINDOW_SAMPLES) == (16000, 512)


def test_load_returns_a_model_satisfying_the_seam(monkeypatch):
    _install(monkeypatch)
    model = load_silero_model()
    assert isinstance(model, SpeechModel)
    assert (model.sample_rate, model.window_samples) == (16000, 512)


def test_probability_passes_the_window_through_and_returns_the_score(monkeypatch):
    detector = _Detector(value=0.75)
    model = SileroSpeechModel(detector)
    assert model.probability(b"\x00" * 1024) == 0.75
    assert detector.calls == [b"\x00" * 1024]


def test_a_library_error_is_classified_and_never_chained():
    model = SileroSpeechModel(_Detector(error=RuntimeError("InvalidChunkSizeError /secret")))
    with pytest.raises(SpeechModelError) as raised:
        model.probability(b"\x00" * 10)
    assert "secret" not in str(raised.value)
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


@pytest.mark.parametrize("value", [-0.1, 1.5, float("nan")])
def test_an_out_of_range_score_is_refused(value):
    with pytest.raises(SpeechModelError):
        SileroSpeechModel(_Detector(value=value)).probability(b"\x00" * 1024)


def test_a_missing_library_is_a_typed_error_and_reported_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "pysilero_vad", None)  # `import` raises ImportError
    assert silero_available() is False
    with pytest.raises(SpeechModelError, match="not installed"):
        load_silero_model()


def test_an_importable_library_is_reported_available(monkeypatch):
    _install(monkeypatch)
    assert silero_available() is True


def test_a_model_that_cannot_load_is_a_typed_error(monkeypatch):
    _install(monkeypatch, ctor_error=OSError("weights at /secret unreadable"))
    with pytest.raises(SpeechModelError) as raised:
        load_silero_model()
    assert "secret" not in str(raised.value)


def test_a_packaging_with_a_different_window_is_refused(monkeypatch):
    _install(monkeypatch, _Detector(chunk=256))
    with pytest.raises(SpeechModelError, match="window size"):
        load_silero_model()
