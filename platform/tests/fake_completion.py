"""Deterministic `CompletionClient`s for hermetic streaming-LLM tests (M11.9).

The whole vendor under test control: what text it produces, where the chunk boundaries fall,
whether it fails and *when*, and whether it ever finishes. Nothing here sleeps for a duration —
every ordering question is settled with `asyncio.Event`, because a test that proves incremental
delivery by sleeping proves only that the machine was slow enough.

Shared by the Groq provider tests, the clause-pipelining tests and the bootstrap tests.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from providers.llm.completion_stream import (
    CompletionChunk,
    CompletionClient,
    CompletionSpec,
    CompletionStream,
    CompletionStreamError,
    FailureKind,
)
from runtime.speech import Clause, ClauseSink


async def settle(until, *, spins: int = 500) -> bool:
    """Let the event loop run until `until()` holds, or until it goes quiet. Never blocks.

    The alternative — `await some_event.wait()` — is what a test naturally reaches for, and it is
    a trap: a defect that stops the event from ever being set turns the test into a **hang**
    instead of a failure, and a hang is not evidence. This yields with `asyncio.sleep(0)`, which
    waits for no duration at all, a bounded number of times, and then returns whether the
    condition held — so the assertion that follows is what proves the property, and a broken
    implementation fails in milliseconds with a readable message."""
    for _ in range(spins):
        if until():
            return True
        await asyncio.sleep(0)
    return until()


class FakeCompletionStream(CompletionStream):
    """Yields a scripted list of text chunks, then ends — or fails where the script says to.

    `gate` (when supplied) is awaited **before each chunk after the first**, so a test can hold
    generation open and assert what the downstream has already received. That is what makes
    "the first clause reached the sink before the LLM finished" a fact rather than a race."""

    def __init__(
        self,
        chunks: list[str],
        *,
        fail_at: int | None = None,
        kind: FailureKind = FailureKind.UNAVAILABLE,
        gate: asyncio.Event | None = None,
        started: asyncio.Event | None = None,
    ) -> None:
        self._chunks = chunks
        self._fail_at = fail_at
        self._kind = kind
        self._gate = gate
        self._started = started
        #: How many chunks the consumer actually pulled — the evidence for "stopped consuming".
        self.yielded = 0
        self.closed = 0

    async def __aiter__(self) -> AsyncIterator[CompletionChunk]:
        for index, text in enumerate(self._chunks):
            if self._fail_at is not None and index == self._fail_at:
                raise CompletionStreamError(self._kind, "scripted failure")
            if index and self._gate is not None:
                if self._started is not None:
                    self._started.set()
                await self._gate.wait()
            self.yielded += 1
            yield CompletionChunk(text=text)
        if self._fail_at is not None and self._fail_at >= len(self._chunks):
            raise CompletionStreamError(self._kind, "scripted failure at end")

    async def aclose(self) -> None:
        self.closed += 1


class FakeCompletionClient(CompletionClient):
    """Opens `FakeCompletionStream`s, or fails to open at all."""

    def __init__(
        self,
        chunks: list[str] | None = None,
        *,
        open_error: FailureKind | None = None,
        fail_at: int | None = None,
        kind: FailureKind = FailureKind.UNAVAILABLE,
        gate: asyncio.Event | None = None,
        started: asyncio.Event | None = None,
        opened_event: asyncio.Event | None = None,
        hang_on_open: asyncio.Event | None = None,
    ) -> None:
        self._chunks = chunks if chunks is not None else ["Hello. ", "How are you?"]
        self._open_error = open_error
        self._fail_at = fail_at
        self._kind = kind
        self._gate = gate
        self._started = started
        self._opened_event = opened_event
        self._hang_on_open = hang_on_open
        #: Every spec the client was asked to open — the evidence for what the runtime actually
        #: sent, including the model and the assembled context.
        self.specs: list[CompletionSpec] = []
        self.streams: list[FakeCompletionStream] = []

    async def open(self, spec: CompletionSpec) -> CompletionStream:
        self.specs.append(spec)
        if self._opened_event is not None:
            self._opened_event.set()
        if self._hang_on_open is not None:
            # Blocks until the test releases it — the setup-phase cancellation fixture.
            await self._hang_on_open.wait()
        if self._open_error is not None:
            raise CompletionStreamError(self._open_error, "scripted open failure")
        stream = FakeCompletionStream(
            list(self._chunks),
            fail_at=self._fail_at,
            kind=self._kind,
            gate=self._gate,
            started=self._started,
        )
        self.streams.append(stream)
        return stream


class RecordingClauseSink(ClauseSink):
    """A `ClauseSink` that records deliveries **and lets a test block inside one**.

    Blocking inside `clause()` is how ordering is proven without timing: the sink holds the first
    clause, the test asserts the LLM stream has not finished, then releases."""

    def __init__(self, hold: asyncio.Event | None = None, error: Exception | None = None) -> None:
        self.texts: list[str] = []
        self.boundaries: list[str] = []
        self.indices: list[int] = []
        self.finished = 0
        self.abandoned = 0
        self.received = asyncio.Event()
        self._hold = hold
        self._error = error

    async def clause(self, clause: Clause) -> None:
        self.texts.append(clause.text)
        self.boundaries.append(clause.boundary.value)
        self.indices.append(clause.index)
        self.received.set()
        if self._error is not None:
            raise self._error
        if self._hold is not None:
            await self._hold.wait()

    async def finish(self) -> None:
        self.finished += 1

    async def abandon(self) -> None:
        self.abandoned += 1


__all__ = [
    "settle",
    "FakeCompletionClient",
    "FakeCompletionStream",
    "RecordingClauseSink",
]
