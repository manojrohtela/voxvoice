"""`AudioNormalizer` — the Runtime's single sample-rate conversion mechanism (M11.2).

Component specification (Constitution §A11.3):
  responsibilities : convert one `AudioFrame` to the session's declared sample
                     rate, and nothing else. It is the *only* place in the
                     platform that changes a frame's rate (AR-41: "converted at
                     exactly one ingress and one egress point"). It holds no
                     session state beyond its target rate, performs no I/O, and
                     knows nothing about transports, providers or vendors.
  inputs           : one `AudioFrame` at any rate.
  outputs          : one `AudioFrame` at the target rate — or the *same object*
                     when no conversion is possible or required.
  dependencies     : `core.audio` and the stdlib (`array`) only. No vendor SDK,
                     no third-party library, no substrate.
  lifecycle        : none — construct with a target rate and call `normalize`.
  emitted events   : none. Conversion is not a runtime fact.
  failure modes    : a malformed PCM16 payload (odd byte count) is passed
                     through unconverted rather than raising — audio handling is
                     auxiliary to the conversation and must never break it
                     (DL-0013, the `_tap_voice_activity` precedent).
  recovery         : none needed; the next frame is independent.

Ownership: `runtime/pipeline/audio/` is the correct home because it is the only
location satisfying every rule at once — `core/` holds no behaviour (AR-10),
`providers/` must not resample (`core/audio.py`), AR-41 permits exactly two
conversion points, and §A14 assigns the media pipeline to `runtime/pipeline/`.
P-ADR-0002 retired the substrate binding, so this directory is first-party.

**No dependency is required and none is added.** `audioop` — the stdlib helper
that once provided `ratecv` — was removed in Python 3.13 (PEP 594), and the
running interpreter is 3.13, so conversion is implemented here over `array`.
Linear interpolation is deliberate: it is correct, dependency-free, and enough
for the seam M11.2 delivers. Conversion quality and cost are revisited when a
real provider first exercises this path with a rate mismatch (M11.5+), where a
library would be a governed dependency decision, not a silent import.
"""

from __future__ import annotations

from array import array

from core.audio import AudioEncoding, AudioFrame

#: Encodings this normalizer can actually convert. Anything else is passed through
#: unchanged: an `OPAQUE` payload is by definition one the runtime may move but not
#: reinterpret, and silently re-stamping its rate would be a lie about its content.
_CONVERTIBLE = frozenset({AudioEncoding.PCM16})


class AudioNormalizer:
    """Converts frames to one session's declared sample rate **and channel layout**.

    Channel normalization was added at M11.16.9, when the first real browser call failed with
    `CapabilityUnsupported`. `aiortc`'s Opus decoder is hard-coded to `layout = "stereo"`
    (`aiortc/codecs/opus.py`), so **every** frame arriving from a browser is 48 kHz **stereo** —
    while the session's canonical format is mono (`AudioFrame.channels` defaults to 1) and both
    Silero and Deepgram correctly refuse non-mono audio.

    Downmixing belongs here and nowhere else: ADR-0044 **AD4** and **AR-41** permit exactly one
    ingress and one egress conversion, both owned by the pipeline, so that providers never
    resample and the transport performs transport adaptation only. Fixing it in the transport
    would have made the endpoint lie about what the library handed it; fixing it in the providers
    would have put a converter behind every vendor boundary."""

    __slots__ = ("_target_rate", "_target_channels")

    def __init__(self, target_rate: int, target_channels: int = 1) -> None:
        if target_rate <= 0:
            raise ValueError(f"target sample rate must be positive, got {target_rate}")
        if target_channels < 1:
            raise ValueError(f"target channels must be positive, got {target_channels}")
        self._target_rate = target_rate
        self._target_channels = target_channels

    @property
    def target_rate(self) -> int:
        return self._target_rate

    @property
    def target_channels(self) -> int:
        return self._target_channels

    def normalize(self, frame: AudioFrame) -> AudioFrame:
        """Return `frame` at the target rate **and** channel count.

        Returns the **same object** when both already match — the common case on every frame of a
        correctly configured session, so the hot path allocates nothing. Also returns the same
        object when the payload cannot be converted (see `_CONVERTIBLE`), because passing an
        undeclared payload through with an honest rate is better than re-stamping it with one it
        does not have.
        """
        rate_ok = frame.sample_rate == self._target_rate
        channels_ok = frame.channels == self._target_channels
        if rate_ok and channels_ok:
            return frame
        if frame.encoding not in _CONVERTIBLE:
            return frame

        data, channels = frame.data, frame.channels
        if not channels_ok:
            # Downmix first: converting rate on fewer samples is both cheaper and avoids
            # interpolating across channels that are about to be summed anyway.
            mixed = _downmix_pcm16(data, channels, self._target_channels)
            if mixed is None:
                return frame
            data, channels = mixed, self._target_channels
        if not rate_ok:
            converted = _resample_pcm16(data, frame.sample_rate, self._target_rate, channels)
            if converted is None:
                return frame
            data = converted
        return AudioFrame(
            data=data,
            sample_rate=self._target_rate,
            encoding=frame.encoding,
            channels=channels,
            captured_at=frame.captured_at,
            seq=frame.seq,
        )


def _resample_pcm16(data: bytes, source_rate: int, target_rate: int, channels: int) -> bytes | None:
    """Linear-interpolation rate conversion for interleaved signed 16-bit PCM.

    Returns `None` when the payload is not usable as PCM16 (an odd byte count, or a
    length that does not divide evenly across the declared channels), so the caller
    can pass the frame through rather than raise into the conversation.
    """
    if channels < 1 or len(data) % 2 != 0:
        return None
    samples = array("h")
    samples.frombytes(data)
    total = len(samples)
    if total == 0 or total % channels != 0:
        return None

    frames_in = total // channels
    frames_out = max(1, round(frames_in * target_rate / source_rate))
    out = array("h", bytes(2 * frames_out * channels))

    # `step` walks the source in fractional frames; each output frame is the linear
    # blend of the two source frames it falls between (per channel, interleaved).
    step = (frames_in - 1) / (frames_out - 1) if frames_out > 1 and frames_in > 1 else 0.0
    for i in range(frames_out):
        position = i * step
        left = int(position)
        right = min(left + 1, frames_in - 1)
        weight = position - left
        base_l = left * channels
        base_r = right * channels
        base_o = i * channels
        for c in range(channels):
            a = samples[base_l + c]
            b = samples[base_r + c]
            out[base_o + c] = int(round(a + (b - a) * weight))
    return out.tobytes()


def _downmix_pcm16(data: bytes, channels: int, target_channels: int) -> bytes | None:
    """Mix interleaved signed 16-bit PCM down to `target_channels` by averaging.

    Only a reduction to mono is supported, which is the one conversion the session's canonical
    format needs. Averaging rather than dropping a channel is deliberate: a browser that puts the
    microphone on one side of a stereo pair would otherwise be silently muted.

    Returns `None` when the payload is not usable, so the caller passes the frame through rather
    than raising into the conversation."""
    if target_channels != 1 or channels < 1 or len(data) % 2 != 0:
        return None
    samples = array("h")
    samples.frombytes(data)
    total = len(samples)
    if total == 0 or total % channels != 0:
        return None
    if channels == 1:
        return data
    frames = total // channels
    out = array("h", bytes(2 * frames))
    for i in range(frames):
        base = i * channels
        out[i] = sum(samples[base + c] for c in range(channels)) // channels
    return out.tobytes()


__all__ = ["AudioNormalizer"]
