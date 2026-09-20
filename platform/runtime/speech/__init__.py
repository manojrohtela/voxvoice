"""runtime.speech — Speech Out (Constitution §A4.2, §A8).

Sentence-chunked streaming synthesis, playback scheduling, filler/acknowledgment
policy, and interruption flush. Boundary only at M1; implemented at M3.

**M11.10** adds the other half: the **Playback Engine** (bounded, ordered delivery of assistant
audio to the transport) and the **Playback Scheduler** (clause order, greeting pre-cache), which
§A8 names and ADR-0048 PB5-PB9 specify. Filler policy and interruption crossfade are deliberately
**not** implemented — Epic 11 builds only what the greeting and interruption paths require.

**M11.9** implements the first half of §A8's *"LLM tokens are chunked at clause boundaries into
TTS"* (ADR-0048 PB1): the clause detector, its explicit policy, and the provider-neutral sink the
completed clauses are handed to. **Synthesis, playback scheduling, filler policy and interruption
flush are not here yet** — they are M11.10's (PB5–PB9), and nothing in this package touches audio.
"""

from .clauses import (
    DEFAULT_MAX_BUFFER_CHARS,
    DEFAULT_MIN_CLAUSE_CHARS,
    DEFAULT_TERMINATORS,
    DEFAULT_TRAILERS,
    Clause,
    ClauseBoundary,
    ClauseDetector,
    ClausePolicy,
)
from .playback import (
    DEFAULT_CAPACITY_MS,
    PlaybackEngine,
    PlaybackFailed,
    PlaybackState,
    frame_duration_ms,
)
from .scheduler import (
    DEFAULT_GREETING_CAPACITY_MS,
    GreetingUnavailable,
    PlaybackScheduler,
)
from .sink import ClauseRouter, ClauseSink, CollectingClauseSink

__all__ = [
    "DEFAULT_MAX_BUFFER_CHARS",
    "DEFAULT_MIN_CLAUSE_CHARS",
    "DEFAULT_TERMINATORS",
    "DEFAULT_TRAILERS",
    "Clause",
    "ClauseBoundary",
    "ClauseDetector",
    "ClausePolicy",
    "ClauseRouter",
    "ClauseSink",
    "CollectingClauseSink",
    "DEFAULT_CAPACITY_MS",
    "DEFAULT_GREETING_CAPACITY_MS",
    "GreetingUnavailable",
    "PlaybackEngine",
    "PlaybackFailed",
    "PlaybackScheduler",
    "PlaybackState",
    "frame_duration_ms",
]
