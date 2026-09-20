"""Audio value types shared across provider contracts (M2C.2; extended at M11.2).

A minimal, immutable frame carried between the transport, STT, and TTS
capabilities. Data-only (no behaviour) — lives on the core floor so every
provider contract can reference it without depending on a concrete codec.

M11.2 (ADR-0044 AD1/AD2) extends the frame **additively** so it can describe
real media: how its bytes are encoded, how many channels they carry, when they
were captured, and where they sit in the stream. Every added field is optional
and keyword-defaulted, so `AudioFrame(data=..., sample_rate=...)` is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

#: The platform's default frame rate in Hz. Named rather than repeated as a bare
#: literal so the one remaining default has a single definition. It is a *default*,
#: not the operative session value: the runtime takes its rate from the session
#: (ADR-0044 AD3), and `runtime.pipeline.RuntimePipeline` requires it explicitly.
DEFAULT_SAMPLE_RATE = 16000


class AudioEncoding(StrEnum):
    """How an `AudioFrame`'s bytes are to be interpreted (ADR-0044 AD2).

    `OPAQUE` is the default and that choice is load-bearing: for the local/dev
    profile a frame carries an undeclared payload (the text-loopback codec), and
    a `PCM16` default would make those frames *falsely declare* themselves as
    PCM — exactly the untruthfulness the capability conformance suite exists to
    catch. A provider that produces real audio declares its encoding explicitly.

    New members are an additive vocabulary extension (§A12, AR-52). Only the
    encodings the runtime can actually act on are declared — no vendor names.
    """

    OPAQUE = "opaque"  # undeclared payload; the runtime may move it but not reinterpret it
    PCM16 = "pcm16"  # signed 16-bit little-endian PCM


@dataclass(frozen=True, slots=True)
class AudioFrame:
    """One chunk of PCM (or, for local/dev transports, an opaque payload).

    `data` is the raw bytes; `sample_rate` is the frame's rate in Hz. The
    Runtime normalizes sample rate at exactly one ingress and one egress point
    (Constitution §A8, AR-41) — providers do not resample.

    The remaining fields describe the frame for a real media path and all carry
    behaviour-preserving defaults: `encoding` defaults to `OPAQUE` (see
    `AudioEncoding`), `channels` to mono, and `captured_at`/`seq` to absent.
    `captured_at`, when present, is a **monotonic** instant — it is the origin of
    the true end-of-speech measurement, so a wall clock would be a correctness
    bug (the same rule ADR-0038 applies to the latency tap)."""

    data: bytes
    sample_rate: int = DEFAULT_SAMPLE_RATE
    encoding: AudioEncoding = AudioEncoding.OPAQUE
    channels: int = 1
    captured_at: float | None = None
    seq: int | None = None
