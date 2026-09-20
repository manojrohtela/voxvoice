"""The streaming-synthesis seam a real TTS plugin is built on (M11.10).

`CartesiaTTS` needs two things from a vendor's speech API and nothing else: open a synthesis for
one piece of text in a declared audio format, and read audio as the vendor produces it. This module
states exactly that — the same shape M11.5 gave WebRTC through `MediaEndpoint`, M11.6 gave Deepgram
through `TranscriptionSession`, and M11.9 gave Groq through `CompletionStream`, for the same two
load-bearing reasons:

1. **The SDK stays behind one door.** `CartesiaSpeechClient` (in `cartesia_client.py`) is the only
   module that imports the vendor library. The provider itself — format validation, frame
   normalization, typed failures, lifecycle — is first-party and library-free (AR-12, P-ADR-0002).
2. **The contract is testable without a network.** A deterministic fake satisfies these classes, so
   streaming, every failure class, cancellation and cleanup are provable hermetically.

Nothing here is vendor-shaped. A chunk carries **raw audio bytes in the format that was asked
for**, and a failure crosses as a neutral `FailureKind`. No vendor response object, SDK model or
base64 payload may cross this seam.

## Why the seam carries bytes rather than `AudioFrame`

`AudioFrame` lives on the `core` floor and is the runtime's currency. The seam sits *below* the
provider, between it and the vendor, and its job is narrower: hand over the bytes the vendor
produced. The provider is what turns bytes into a declared, framed `AudioFrame` — so there is
exactly one place where audio acquires its declared encoding, rate and channel count, and a
malformed vendor payload is caught there rather than travelling as a frame that lies about itself.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from providers.failure import FailureKind, SessionFailure


class SpeechStreamError(SessionFailure):
    """A synthesis stream could not do what was asked.

    Carries a `FailureKind` and a **sanitized** detail (ADR-0049 CR5/CR6). Vendor error text is
    never passed through: it routinely embeds endpoints, request ids and account identifiers."""


@dataclass(frozen=True, slots=True)
class SpeechSpec:
    """What one synthesis is asked for.

    The audio format is declared **up front and completely**, because a TTS vendor returns raw
    bytes whose meaning is set entirely by the request: asking for the wrong rate does not fail,
    it returns audio that plays at the wrong speed. Declaring it here is what lets the provider
    frame the result truthfully instead of guessing."""

    text: str
    voice: str | None
    language: str
    sample_rate: int


@dataclass(frozen=True, slots=True)
class SpeechChunk:
    """One piece of synthesized audio, already decoded to raw bytes.

    May be empty — vendors emit keep-alive and metadata events carrying no audio — and may end
    mid-sample, because a transport chunk boundary has nothing to do with a sample boundary.
    Neither is an error; the provider handles both."""

    audio: bytes


class SpeechStream:
    """One open synthesis. Audio arrives as the vendor produces it.

    A class rather than a `Protocol` so `aclose()` is callable on any stream the provider holds,
    including one that failed to start — a structural Protocol would let a half-implemented
    stand-in through silently."""

    def __aiter__(self) -> AsyncIterator[SpeechChunk]:
        """Yield audio chunks as the vendor produces them.

        Terminating normally means *the vendor finished this utterance cleanly*; raising
        `SpeechStreamError` means it **broke**. An implementation must not accumulate the whole
        utterance and replay it — that would make the streaming topology unverifiable one layer
        below where any pipeline test can see it."""
        raise NotImplementedError

    async def aclose(self) -> None:
        """Release the stream and the vendor's connection. Idempotent; never raises.

        Called on every exit path — normal end, failure, cancellation and barge-in — so an
        interrupted utterance cannot leak a vendor resource."""
        raise NotImplementedError


class SpeechClient:
    """Opens synthesis streams. Holds whatever credential it was **given** (ADR-0049 CR4)."""

    async def open(self, spec: SpeechSpec) -> SpeechStream:
        """Open a stream, returning only once it is usable.

        Raises `SpeechStreamError` — never returns a half-open stream, which a caller could not
        distinguish from a voice that has simply not started speaking yet."""
        raise NotImplementedError


__all__ = [
    "FailureKind",
    "SpeechChunk",
    "SpeechClient",
    "SpeechSpec",
    "SpeechStream",
    "SpeechStreamError",
]
