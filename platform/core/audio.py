"""Audio value types shared across provider contracts (M2C.2).

A minimal, immutable frame carried between the transport, STT, and TTS
capabilities. Data-only (no behaviour) — lives on the core floor so every
provider contract can reference it without depending on a concrete codec.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AudioFrame:
    """One chunk of PCM (or, for local/dev transports, an opaque payload).

    `data` is the raw bytes; `sample_rate` is the frame's rate in Hz. The
    Runtime normalizes sample rate at exactly one ingress and one egress point
    (Constitution §A8) — providers do not resample."""

    data: bytes
    sample_rate: int = 16000
