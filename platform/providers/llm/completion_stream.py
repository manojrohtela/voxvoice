"""The streaming-completion seam a real LLM plugin is built on (M11.9).

`GroqLLM` needs two things from a vendor's streaming chat API and nothing else: open a completion
for an assembled context, and read text as the model produces it. This module states exactly that,
as Protocols — the same shape M11.5 gave WebRTC through `MediaEndpoint` and M11.6 gave Deepgram
through `TranscriptionSession`, for the same two load-bearing reasons:

1. **The SDK stays behind one door.** `GroqCompletionClient` (in `groq_client.py`) is the only
   module that imports the vendor library. The provider itself — chunk normalization, typed
   failures, capability checks, cancellation and closure — is first-party and library-free
   (AR-12, P-ADR-0002).
2. **The contract is testable without a network.** A deterministic fake satisfies these Protocols,
   so incremental streaming, every failure class, cancellation and cleanup are all provable
   hermetically (TESTING_POLICY rule 1).

Nothing here is vendor-shaped. A chunk carries **text and nothing else**; a failure crosses as a
neutral `FailureKind`. No vendor response object, token type, SDK object or vendor metadata may
cross this seam — which is what keeps `runtime/` and `core/` free of vendor types (AR-21).

## What this seam deliberately does not carry

- **No tool calls.** M11.9 does not implement tool calling, so the seam has no shape for it. A
  seam field nothing consumes is exactly the accepted-and-ignored configuration AR-25 forbids;
  when tool calling is implemented, the field arrives with its consumer.
- **No token counts, log-probabilities, finish reasons or usage.** Same rule: the runtime reads
  none of them today.
- **No timing.** The provider does not stamp chunks. ADR-0038 owns latency instants, the tap is
  taken in the pipeline against an injected clock, and a provider-supplied timestamp would be a
  second, unreconcilable clock.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from providers.failure import FailureKind, SessionFailure


class CompletionStreamError(SessionFailure):
    """A completion stream could not do what was asked.

    Carries a `FailureKind` and a **sanitized** detail (ADR-0049 CR5/CR6). A vendor message is
    never passed through verbatim: vendor errors routinely embed endpoints, account identifiers
    and occasionally credential fragments."""


@dataclass(frozen=True, slots=True)
class CompletionSpec:
    """What one completion is asked for.

    `messages` is the runtime's already-assembled, provider-neutral context — the same
    `list[dict]` role/content shape the `LLMProvider` contract has carried since M2C.2. The
    adapter translates it into whatever the vendor wants and never the other way round."""

    messages: tuple[tuple[str, str], ...]
    model: str


@dataclass(frozen=True, slots=True)
class CompletionChunk:
    """One piece of generated text, exactly as the vendor produced it.

    `text` may be empty — vendors emit role-only and finish-only chunks — and may split words,
    punctuation and multi-byte characters at arbitrary points. Neither is an error, and neither is
    repaired here: the clause detector downstream is written to survive both."""

    text: str


class CompletionStream:
    """One open completion. Text arrives as the model produces it.

    Defined as an ABC rather than a `Protocol` because `aclose()` must be callable on any stream
    the provider holds, including one that failed to start — a structural Protocol would let a
    half-implemented stand-in through silently."""

    def __aiter__(self) -> AsyncIterator[CompletionChunk]:
        """Yield chunks as the vendor produces them.

        Terminating normally means *the vendor finished the completion cleanly*; raising
        `CompletionStreamError` means it **broke**. An implementation must not buffer the whole
        response and replay it — that is the wait-for-whole-input step ADR-0048 PB2 exists to
        remove, and a seam that permitted it would make the removal unverifiable."""
        raise NotImplementedError

    async def aclose(self) -> None:
        """Release the stream and the vendor's connection. Idempotent; never raises.

        Called on every exit path — normal end, failure and cancellation — so a caller that
        withdraws mid-generation cannot leak a vendor resource."""
        raise NotImplementedError


class CompletionClient:
    """Opens completion streams. Holds whatever credential it was **given** (ADR-0049 CR4)."""

    async def open(self, spec: CompletionSpec) -> CompletionStream:
        """Open a stream, returning only once it is usable.

        Raises `CompletionStreamError` — never returns a half-open stream, because a caller cannot
        distinguish one from a model that has simply not produced its first token yet."""
        raise NotImplementedError


__all__ = [
    "CompletionChunk",
    "CompletionClient",
    "CompletionSpec",
    "CompletionStream",
    "CompletionStreamError",
    "FailureKind",
]
