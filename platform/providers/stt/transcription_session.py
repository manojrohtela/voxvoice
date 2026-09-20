"""The streaming-transcription seam a real STT plugin is built on (M11.6).

`DeepgramSTT` needs four things from a vendor's streaming socket and nothing else: open one with a
declared audio format, push audio, say *"no more audio"*, and read results until the vendor is
done. This module states exactly that, as Protocols — the same shape M11.5 gave WebRTC through
`MediaEndpoint`, for the same two load-bearing reasons:

1. **The SDK stays behind one door.** `DeepgramTranscriptionClient` (in `deepgram_client.py`) is the
   only module that imports the vendor library. The provider itself — duplex upload/receive, result
   mapping, typed failures, format validation, task ownership — is first-party and library-free
   (AR-12, P-ADR-0002).
2. **The contract is testable without a network.** A deterministic fake satisfies these Protocols,
   so incremental streaming, every failure class, cancellation and cleanup are all provable
   hermetically (TESTING_POLICY rule 1). A provider whose only proof needed a live vendor account
   would be a provider nobody could regression-test.

Nothing here is vendor-shaped. A failure crosses the seam as a neutral `FailureKind`, which the
provider maps onto `core.errors`; a library exception never travels further than the adapter.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from providers.failure import FailureKind, SessionFailure


class TranscriptionSessionError(SessionFailure):
    """A transcription session could not do what was asked.

    Carries a `FailureKind` and a **sanitized** detail — see `SessionFailure`. M11.9 moved the
    carrying logic and the `FailureKind` vocabulary to `providers/failure.py` when the streaming
    LLM seam needed the identical classification; the shape here is unchanged, and
    `FailureKind` is still importable from this module for every existing caller."""


@dataclass(frozen=True, slots=True)
class StreamSpec:
    """The audio format and recognition settings a session is opened with.

    Declared up front because a streaming vendor interprets raw bytes by what it was told at
    connect time — a mismatch is not an error the vendor can report, it is silently wrong text."""

    language: str
    sample_rate: int
    channels: int
    model: str
    interim_results: bool = True


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    """One recognition result, as the vendor reported it.

    `is_final` is the **vendor's** statement that this text will not be revised. The provider maps
    it to `Final` and never promotes an interim result on its own — a locally fabricated Final
    would be a claim about the audio the vendor never made."""

    text: str
    is_final: bool
    words: tuple[tuple[str, float, float], ...] = field(default_factory=tuple)


@runtime_checkable
class TranscriptionSession(Protocol):
    """One open duplex transcription stream."""

    async def send(self, audio: bytes) -> None:
        """Push one chunk of already-validated audio. Raises `TranscriptionSessionError`."""

    async def finish(self) -> None:
        """Declare that no more audio will be sent.

        The vendor flushes whatever it still holds and then ends `results()` normally. Raises
        `TranscriptionSessionError`."""

    def results(self) -> AsyncIterator[TranscriptionResult]:
        """Yield results as the vendor produces them — **concurrently** with `send()`.

        Terminating normally means *the vendor finished the stream cleanly*; raising
        `TranscriptionSessionError` means it **broke**. After `close()`, iteration must end
        promptly — the provider relies on that to unblock the receive side when the upload side
        fails."""

    async def close(self) -> None:
        """Abort and release the stream. Idempotent; never raises."""


@runtime_checkable
class TranscriptionClient(Protocol):
    """Opens transcription sessions. Holds whatever credential it was **given** (ADR-0049 CR4)."""

    async def open(self, spec: StreamSpec) -> TranscriptionSession:
        """Open a session, returning only once it is usable. Raises `TranscriptionSessionError`
        — never returns a half-open session."""


__all__ = [
    "FailureKind",
    "StreamSpec",
    "TranscriptionClient",
    "TranscriptionResult",
    "TranscriptionSession",
    "TranscriptionSessionError",
]
