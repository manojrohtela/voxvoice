"""The provider-neutral LLM→TTS seam (M11.9, ADR-0048 PB1).

This is the smallest thing M11.10 needs and nothing more: *somewhere to hand a completed clause,
as soon as it is completed.*

```
LLM chunk ─► ClauseDetector ─► Clause 1 ─────► ClauseSink
                             ─► Clause 2 ─────► ClauseSink
                             ─► Clause 3 ─────► ClauseSink
```

## The seam is deliberately one-directional and knows nothing about either side

- It carries `Clause` — text produced by *something*. It names no LLM provider, imports none, and
  cannot reach one. A TTS subsystem written against it works identically whether the text came
  from Groq, from `EchoLLM`, or from a greeting script.
- It has no audio in it. Synthesis, frame ordering, playback scheduling and barge-in flush are
  M11.10's (ADR-0048 PB5–PB9), and putting an audio concept here would pre-empt that design.

## Ordering is the contract

`clause()` is awaited once per clause, in source order, before the next one is delivered. A sink
that reorders, parallelises or drops has broken the contract, not merely performed badly: clauses
become speech, and speech played out of order is a different answer.

`finish()` is called exactly once, after the last clause of a response, including when a response
produced no clauses at all. `abandon()` replaces it when the turn was cancelled or interrupted —
the distinction matters because a sink that queued audio must know whether to play out or discard.
"""

from __future__ import annotations

from .clauses import Clause, ClauseDetector


class ClauseSink:
    """Receives completed clauses incrementally.

    A class rather than a `Protocol` so a partially implemented sink fails loudly at construction
    instead of at the first cancelled turn — the path least likely to be exercised by hand."""

    async def clause(self, clause: Clause) -> None:
        """Accept one completed clause. Awaited before the next is produced."""
        raise NotImplementedError

    async def finish(self) -> None:
        """No more clauses: the response completed normally."""

    async def abandon(self) -> None:
        """No more clauses: the response was cancelled, interrupted or failed.

        Defaults to `finish()` so a simple sink need not distinguish them; a sink that holds
        queued audio must."""
        await self.finish()


class ClauseRouter:
    """Drives a `ClauseDetector` from streamed text and delivers what it completes to a sink.

    The glue lives here rather than in `RuntimePipeline` for one reason: the ordering,
    end-of-stream and cancellation guarantees above are the part most likely to be got subtly
    wrong, and here they can be tested without a transport, an STT provider or an event loop full
    of conversation."""

    __slots__ = ("_detector", "_sink", "_delivered", "_finished")

    def __init__(self, detector: ClauseDetector, sink: ClauseSink) -> None:
        self._detector = detector
        self._sink = sink
        self._delivered = 0
        self._finished = False

    @property
    def delivered(self) -> int:
        """How many clauses reached the sink. Distinct from the detector's emitted count only if
        delivery failed, which it cannot do silently."""
        return self._delivered

    async def feed(self, text: str) -> None:
        """Absorb one piece of streamed text and deliver any clauses it completed, in order."""
        for clause in self._detector.feed(text):
            await self._deliver(clause)

    async def finish(self) -> None:
        """Flush the buffered tail, deliver it, and close the sink. Idempotent.

        Idempotent because the pipeline's normal and error paths both end here; flushing twice
        would deliver the tail twice, and a duplicated clause is a duplicated sentence of speech."""
        if self._finished:
            return
        self._finished = True
        for clause in self._detector.flush():
            await self._deliver(clause)
        await self._sink.finish()

    async def abandon(self) -> None:
        """Drop the buffered tail and tell the sink the response was abandoned. Idempotent.

        The tail is **discarded, not flushed**: the turn is over, and synthesizing the fragment the
        model had reached would speak a sentence the conversation no longer wants."""
        if self._finished:
            return
        self._finished = True
        await self._sink.abandon()

    async def _deliver(self, clause: Clause) -> None:
        await self._sink.clause(clause)
        self._delivered += 1


class CollectingClauseSink(ClauseSink):
    """A sink that records what it was given, in order, and nothing else.

    Not a test double living in the test tree: the runtime itself needs a sink that is safe to
    wire when no consumer exists yet, and a recording one makes "clauses were produced, in this
    order, at these points" observable in production configurations too. M11.10 replaces it with
    the Playback path; until then it is the honest terminus."""

    __slots__ = ("clauses", "finished", "abandoned")

    def __init__(self) -> None:
        self.clauses: list[Clause] = []
        self.finished = False
        self.abandoned = False

    @property
    def texts(self) -> tuple[str, ...]:
        return tuple(clause.text for clause in self.clauses)

    async def clause(self, clause: Clause) -> None:
        self.clauses.append(clause)

    async def finish(self) -> None:
        self.finished = True

    async def abandon(self) -> None:
        self.abandoned = True
        self.finished = True


__all__ = ["ClauseRouter", "ClauseSink", "CollectingClauseSink"]
