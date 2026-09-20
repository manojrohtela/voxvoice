"""The Playback Scheduler — what is spoken, in what order (M11.10, ADR-0048 PB1/PB3/PB7/PB8).

Constitution §A8 gives the Scheduler *"filler policy, greeting pre-cache, and interruption
crossfade"*. It is the **policy** owner; `playback.py`'s Engine is the mechanism. Epic 11
implements only what the greeting and interruption paths require (ADR-0048's closing note), so
filler audio and crossfade are deliberately absent here.

```
Clause 1 ─► TTS ─► frames ─┐
Clause 2 ─► TTS ─► frames ─┼─► PlaybackEngine ─► transport
Clause 3 ─► TTS ─► frames ─┘
        strictly in this order
```

## Ordering is the whole point, and it is structural

Clause *n+1* is not synthesized until clause *n*'s audio has been handed to the Engine in full.
There is no second task that could overtake, no per-clause queue to reorder, and no parallelism —
so "clause 2 cannot overtake clause 1" is a property of the shape, not of a check that could be
forgotten. The authorization permits overlapping synthesis only if bounded back-pressure makes it
safe; it is not implemented, because it buys latency the milestone is not allowed to claim and
costs the one invariant it must not lose.

Back-pressure still does its job: `PlaybackEngine.enqueue` waits when the buffer is full, so a
slow transport stops synthesis rather than accumulating audio.

## Streaming input, honestly

A provider declaring `streaming_input=True` gets the whole clause stream through
`synthesize_stream`. One declaring `False` — which is every real provider the platform has today —
is called once per clause, and the runtime **records the degradation** rather than silently
serializing (ADR-0048 PB3, AR-24, ADR-0045 SD6).

## What it must never do

Own conversation state, decide when a turn ends, invoke the LLM, run endpointing or VAD, or know a
vendor. It receives text and a `TTSProvider`, and it produces ordered audio.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING

from core.audio import AudioFrame
from core.providers.tts import TTSProvider

from .clauses import Clause
from .playback import PlaybackEngine
from .sink import ClauseSink

if TYPE_CHECKING:
    from collections.abc import Iterable

#: The most audio a pre-cached greeting may hold, in milliseconds. A greeting is synthesized
#: before anyone speaks and held in memory, so it needs its own explicit bound — the Engine's
#: back-pressure does not apply to something that has not been enqueued yet. 30 s is far longer
#: than any sensible greeting and short enough that a misconfigured one cannot exhaust a host.
#: An unmeasured but deliberately generous ceiling (R-60).
DEFAULT_GREETING_CAPACITY_MS = 30_000.0


class GreetingUnavailable(RuntimeError):
    """The greeting could not be pre-cached, so there is nothing to play.

    Its own type because a failed greeting must never end a call: the caller simply is not
    greeted, and the session proceeds. The pipeline treats it as non-fatal."""


class PlaybackScheduler(ClauseSink):
    """Turns completed clauses into ordered assistant audio.

    A `ClauseSink`, so it plugs into M11.9's seam unchanged: the clause detector hands it
    completed text and knows nothing about audio, and this knows nothing about the LLM."""

    __slots__ = (
        "_tts",
        "_engine",
        "_voice",
        "_language",
        "_synthesize",
        "_on_degraded",
        "_on_first_audio",
        "_greeting",
        "_greeting_ms",
        "_greeting_capacity_ms",
        "_greeting_played",
        "_clauses_spoken",
        "_cancelled",
    )

    def __init__(
        self,
        tts: TTSProvider,
        engine: PlaybackEngine,
        *,
        voice: str | None = None,
        language: str = "en",
        synthesize: Callable[..., AsyncIterator[AudioFrame]] | None = None,
        on_degraded: Callable[[str], None] | None = None,
        on_first_audio: Callable[[], None] | None = None,
        greeting_capacity_ms: float = DEFAULT_GREETING_CAPACITY_MS,
    ) -> None:
        self._tts = tts
        self._engine = engine
        self._voice = voice
        self._language = language
        # The guarded call. The pipeline passes `ProviderGuard.stream`-wrapped synthesis so the
        # breaker, deadlines and typed errors stay exactly where AR-23 put them; absent one, the
        # provider is called directly (the shape every other runtime component uses).
        self._synthesize = synthesize
        self._on_degraded = on_degraded
        # Fired on the first audio frame of a response — the `tts_first_audio` boundary. The
        # Scheduler reports *that* it happened; the pipeline owns the clock (ADR-0038 TP7).
        self._on_first_audio = on_first_audio
        self._greeting: list[AudioFrame] | None = None
        self._greeting_ms = 0.0
        self._greeting_capacity_ms = greeting_capacity_ms
        self._greeting_played = False
        self._clauses_spoken = 0
        self._cancelled = False

    @property
    def engine(self) -> PlaybackEngine:
        return self._engine

    def attach(
        self,
        *,
        send: Callable[[AudioFrame], Awaitable[None]],
        on_first_audio: Callable[[], None] | None = None,
    ) -> None:
        """Bind this Scheduler to one session's output route.

        Called once, by the pipeline, when the session starts. It exists because the Engine's
        route to the caller — the guarded transport behind the single egress normalizer — belongs
        to a session, while the Scheduler and Engine can be constructed and inspected anywhere."""
        self._engine.attach(send)
        if on_first_audio is not None:
            self._on_first_audio = on_first_audio

    @property
    def clauses_spoken(self) -> int:
        return self._clauses_spoken

    @property
    def greeting_ready(self) -> bool:
        return self._greeting is not None

    @property
    def greeting_played(self) -> bool:
        return self._greeting_played

    def begin_response(self) -> None:
        """Start a new assistant response.

        Load-bearing: `abandon()` leaves the Scheduler cancelled so that nothing from the
        interrupted response can still be spoken, and without an explicit reset the *next* turn
        would inherit that and the assistant would go permanently mute after its first barge-in.
        The reset is per response and explicit, rather than inferred from a clause arriving, so
        the boundary is one a test can point at."""
        self._cancelled = False
        self._clauses_spoken = 0

    # ── the clause seam (M11.9) ──────────────────────────────────────────────

    async def clause(self, clause: Clause) -> None:
        """Speak one completed clause. Returns only once its audio is fully handed to the Engine,
        which is what stops the next clause from overtaking it."""
        if self._cancelled:
            return
        await self._speak(clause.text)
        self._clauses_spoken += 1

    async def finish(self) -> None:
        """The response is complete: let queued audio play out."""
        if self._cancelled:
            return
        await self._engine.drain()

    async def abandon(self) -> None:
        """The response was cancelled or interrupted: stop and discard queued audio.

        Discarding is the point. Audio synthesized before a barge-in is an answer to a question
        the caller has already moved on from, and playing it out would be the stale-audio defect
        the interruption path exists to prevent."""
        self._cancelled = True
        await self._engine.cancel()

    # ── greeting pre-cache (ADR-0048 PB8, D-11) ──────────────────────────────

    async def precache_greeting(self, text: str) -> None:
        """Synthesize the agent's `first_message` and hold it, ready to play.

        Called **by an active session**, never at bootstrap and never at application startup: the
        authorization is explicit, and synthesizing at import time would spend a vendor request
        per process for a greeting nobody asked for.

        Bounded: the held audio may not exceed `greeting_capacity_ms`. Cancellable: it is an
        ordinary await, so the session's cancellation propagates. Failure raises
        `GreetingUnavailable` rather than ending the session."""
        spoken = text.strip()
        if not spoken:
            return
        frames: list[AudioFrame] = []
        total = 0.0
        from .playback import frame_duration_ms

        try:
            async for frame in self._frames_for(spoken):
                total += frame_duration_ms(frame)
                if total > self._greeting_capacity_ms:
                    raise GreetingUnavailable(
                        "greeting pre-cache: the greeting exceeds the configured capacity"
                    )
                frames.append(frame)
        except asyncio.CancelledError:
            raise  # a cancelled pre-cache withdraws; it is not a greeting failure
        except GreetingUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 — a greeting must never end a call
            raise GreetingUnavailable("greeting pre-cache: synthesis failed") from exc
        if not frames:
            raise GreetingUnavailable("greeting pre-cache: synthesis produced no audio")
        self._greeting = frames
        self._greeting_ms = total

    async def play_greeting(self) -> bool:
        """Play the pre-cached greeting, **exactly once**. Returns whether it played.

        Returning `False` rather than raising when nothing is cached is deliberate: a session
        with no greeting is the ordinary case, not an error."""
        if self._greeting is None or self._greeting_played or self._cancelled:
            return False
        self._greeting_played = True
        for frame in self._greeting:
            if self._cancelled:
                return True
            # Deliberately **not** `_on_first_audio`. That callback marks the first audio of a
            # *response* — it stamps `tts_first_audio` and advances the Turn Engine to
            # ASSISTANT_SPEAKING. A greeting is neither: it is session-opening audio that belongs
            # to no turn, there is no `speech_end` for it to be measured from, and at session
            # start there is no active turn to advance (M11.11 found exactly that — the greeting
            # raised `NoActiveTurnError` before a caller had said anything). Stamping a turn
            # metric for it would be the synthetic timing §A8's chain must never carry.
            await self._engine.enqueue(frame)
        # Released once spoken: holding a session's worth of audio for a greeting that will never
        # be played again is exactly the accumulation the boundedness rule forbids.
        self._greeting = None
        self._greeting_ms = 0.0
        return True

    # ── synthesis ────────────────────────────────────────────────────────────

    async def _speak(self, text: str) -> None:
        spoken = text.strip()
        if not spoken:
            return
        first = self._clauses_spoken == 0
        async for frame in self._frames_for(spoken):
            if self._cancelled:
                # Stop pulling audio the moment a barge-in lands, rather than finishing the
                # utterance into a queue that has already been discarded.
                return
            if first and self._on_first_audio is not None:
                self._on_first_audio()
                first = False
            await self._engine.enqueue(frame)

    def _frames_for(self, text: str) -> AsyncIterator[AudioFrame]:
        """Audio for one piece of text, through the guarded provider call when one was given."""
        if self._synthesize is not None:
            return self._synthesize(text, voice=self._voice, language=self._language)
        return self._tts.synthesize(text, voice=self._voice, language=self._language)

    def note_input_mode(self) -> None:
        """Record the streaming-input degradation once per response, if the provider declares
        `streaming_input=False` (ADR-0045 SD6, ADR-0048 PB3).

        Recorded rather than assumed: a provider that cannot accept text as it is produced is
        synthesized clause by clause, which is a real, observable adaptation — and AR-24 forbids
        the runtime from adapting invisibly."""
        if self._on_degraded is None:
            return
        if not getattr(self._tts.capabilities, "streaming_input", False):
            self._on_degraded(self._tts.name)


async def clause_text_stream(clauses: Iterable[str]) -> AsyncIterator[str]:
    """Adapt a finished sequence of clauses to the streaming-input contract.

    Used only by the `streaming_input=True` branch, which no real provider reaches today — see the
    module docstring."""
    for text in clauses:
        yield text


__all__ = [
    "DEFAULT_GREETING_CAPACITY_MS",
    "GreetingUnavailable",
    "PlaybackScheduler",
    "clause_text_stream",
]
