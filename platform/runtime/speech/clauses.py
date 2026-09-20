"""Clause-boundary detection over incremental LLM text (M11.9, ADR-0048 PB1).

Constitution §A8: *"LLM tokens are chunked at clause boundaries into TTS."* This module is that
chunking, and `runtime/speech/` is where the Constitution's §A4.2 module map already put it —
*"sentence-chunked streaming synthesis"* — so no new home is invented.

```
LLM chunk ─► ClauseDetector.feed() ─► completed clauses ─► ClauseSink ─► (M11.10: TTS)
                       │
                       └─ incomplete tail stays buffered, bounded, until a boundary or the flush
```

## Why a detector rather than "one chunk, one unit"

A vendor chunk is a **tokenizer artefact**. It splits mid-word, mid-punctuation and mid-codepoint,
and it splits differently for the same sentence on a different model. Synthesizing per chunk
produces audibly wrong prosody (ADR-0048 rejects it explicitly); waiting for the whole response
puts the latency floor back (PB2). A clause is the smallest unit that is both **speakable** and
**available early**.

## What this module is not

- **Not language-model dependent.** It reads characters, not meaning. No model, no tokenizer, no
  vendor library, no network — so it behaves identically for every provider and is fully
  deterministic under test.
- **Not a sentence splitter with linguistic pretensions.** One narrow, language-neutral exception
  exists — a period between two digits is a decimal point, not a full stop, because a voice agent
  quotes prices and durations constantly. Abbreviations and honorifics are **not** handled: "Dr.
  Rao" splits where a linguist would not. That is a documented limitation (R-53), not an oversight;
  handling it needs a per-language rule table whose quality nothing in M11.9 measures.
- **Not the owner of what happens next.** It emits clauses; routing them is `ClauseRouter`'s, and
  synthesizing them is M11.10's.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

#: Characters that end a clause. Sentence-terminal punctuation only — a comma does not end a
#: clause, because a comma-delimited fragment synthesized alone carries the wrong intonation.
#: Devanagari danda (U+0964) and the full-width CJK stops are included because the platform's
#: target market is Indian English and Hinglish (§A8, SPEC), where a danda is a full stop; this is
#: a boundary-character claim, **not** a claim about Hinglish speech quality (R-47 stands).
DEFAULT_TERMINATORS = frozenset(".!?…।。！？")

#: Closing marks allowed to follow a terminator and still belong to the same clause, so
#: `He said "stop."` does not split before the quotation mark.
DEFAULT_TRAILERS = frozenset('"\'”’)]》』」')

#: A clause shorter than this is **not** emitted on punctuation; it keeps accumulating.
#:
#: **Zero by default — the minimum is off.** The knob exists because a model that opens with
#: "Sure." hands the synthesizer a one-word utterance whose prosody cannot be joined to what
#: follows, and an operator who measures that may want to suppress it. It defaults to off because
#: any non-zero value is a guess about prosody that nothing in M11.9 measured, and the first value
#: tried (12) silently swallowed ordinary short sentences such as "I am fine." — withholding a
#: complete clause from the speech path to satisfy an invented threshold. A boundary is
#: punctuation; brevity is not a reason to keep speech waiting (R-52).
DEFAULT_MIN_CLAUSE_CHARS = 0

#: The hard ceiling on buffered text. Reaching it emits what is buffered as a clause rather than
#: discarding any of it: a model that produces a very long run without punctuation must still be
#: speakable, and silently truncating a caller-visible answer would be the worse failure by far.
#: 400 characters is roughly a long spoken sentence; also an untuned implementation default (R-52).
DEFAULT_MAX_BUFFER_CHARS = 400


class ClauseBoundary(StrEnum):
    """Why a clause was emitted. Carried so a test — and later, an operator — can tell an
    ordinary sentence end from a forced one without inspecting the text."""

    #: Sentence-terminal punctuation.
    PUNCTUATION = "punctuation"
    #: A line break. Models emit these to structure answers, and they are real prosodic breaks.
    NEWLINE = "newline"
    #: The buffer reached its ceiling with no boundary in sight. Nothing is lost; the clause is
    #: simply cut where the policy said it must be.
    OVERFLOW = "overflow"
    #: The stream ended with text still buffered.
    END_OF_STREAM = "end_of_stream"


@dataclass(frozen=True, slots=True)
class Clause:
    """One completed unit of speakable text.

    `index` is the clause's position in its response, from 0, and is what makes ordering and
    de-duplication assertable by a consumer rather than merely believed."""

    text: str
    boundary: ClauseBoundary
    index: int


@dataclass(frozen=True, slots=True)
class ClausePolicy:
    """The complete, explicit clause-boundary policy. Every threshold is named here; no
    call site carries a bare number (AI_AGENT_RULES rule 19)."""

    terminators: frozenset[str] = DEFAULT_TERMINATORS
    trailers: frozenset[str] = DEFAULT_TRAILERS
    #: Whether a line break ends a clause.
    newline_is_boundary: bool = True
    min_clause_chars: int = DEFAULT_MIN_CLAUSE_CHARS
    max_buffer_chars: int = DEFAULT_MAX_BUFFER_CHARS

    def __post_init__(self) -> None:
        # Refuse a policy that cannot work, at construction, rather than behaving oddly later.
        if not self.terminators and not self.newline_is_boundary:
            raise ValueError("a clause policy needs at least one kind of boundary")
        if self.min_clause_chars < 0:
            raise ValueError("min_clause_chars cannot be negative")
        if self.max_buffer_chars <= 0:
            raise ValueError("max_buffer_chars must be positive")
        if self.max_buffer_chars < self.min_clause_chars:
            # Otherwise overflow would fire before a clause could ever reach its minimum, and
            # every clause would be emitted as OVERFLOW — a policy that silently means something
            # other than what it says.
            raise ValueError("max_buffer_chars must be at least min_clause_chars")


class ClauseDetector:
    """Accumulates streamed text and emits clauses as they complete.

    Stateful and **not** safe to share between responses: one detector per assistant turn. Memory
    is bounded by `policy.max_buffer_chars` by construction — the buffer is emitted and cleared the
    moment it reaches the ceiling, so no input sequence can grow it further."""

    __slots__ = ("_policy", "_buffer", "_emitted", "_pending")

    def __init__(self, policy: ClausePolicy | None = None) -> None:
        self._policy = policy or ClausePolicy()
        self._buffer = ""
        self._emitted = 0
        # Where a completed clause ends inside the buffer, once a terminator has been seen —
        # see `_absorb` for why the boundary is confirmed one character late.
        self._pending: int | None = None

    @property
    def policy(self) -> ClausePolicy:
        return self._policy

    @property
    def buffered(self) -> str:
        """The incomplete tail. Never longer than `policy.max_buffer_chars`."""
        return self._buffer

    @property
    def clauses_emitted(self) -> int:
        return self._emitted

    def feed(self, text: str) -> tuple[Clause, ...]:
        """Absorb one piece of streamed text; return whatever clauses it completed.

        Usually empty — most chunks complete nothing, and returning a clause per chunk is exactly
        the defect this module exists to prevent. A single chunk containing several sentences
        returns several clauses, in order."""
        if not text:
            # A vendor's role-only or finish-only chunk. Absorbing it must not disturb the buffer
            # and must not emit anything.
            return ()
        completed: list[Clause] = []
        for character in text:
            clause = self._absorb(character) or self._overflowed()
            if clause is not None:
                completed.append(clause)
        return tuple(completed)

    def flush(self) -> tuple[Clause, ...]:
        """End of stream: emit whatever is buffered, however short.

        The minimum length does **not** apply here. It exists to avoid stranding a fragment
        awkwardly *before* more text arrives; at end of stream no more text is coming, and
        withholding the tail would silently lose part of the answer."""
        tail = self._buffer.strip()
        # A tail whose pending boundary covers everything really did end on punctuation; saying
        # END_OF_STREAM for it would report the stream's shape rather than the text's.
        complete = self._pending is not None and not self._buffer[self._pending :].strip()
        self._buffer = ""
        self._pending = None
        if not tail:
            return ()
        boundary = ClauseBoundary.PUNCTUATION if complete else ClauseBoundary.END_OF_STREAM
        return (self._emit(tail, boundary),)

    def _absorb(self, character: str) -> Clause | None:
        """Add one character and return the clause it completed, if any.

        **A punctuation boundary is confirmed one character late, on purpose.** A terminator does
        not end a clause by itself: `He said "stop."` ends at the quotation mark, not at the full
        stop, and emitting on the full stop would hand the synthesizer `He said "stop.` and then a
        clause beginning with a stray quote. So a terminator only *marks* a candidate end, and the
        next character decides — a trailer extends it, anything else confirms it.

        The cost is that a clause is delivered when the following character arrives rather than
        the instant its full stop does. For a stream that is one more token; for the last clause of
        a response there is no following character at all, which is precisely what `flush()`
        handles, so nothing waits on a token that will never come.

        Work per character is constant with respect to the response length: only the buffer's end
        is examined, and the buffer is bounded by the policy."""
        policy = self._policy
        self._buffer += character
        if policy.newline_is_boundary and character in "\r\n":
            # A line break is a boundary in its own right, with nothing to defer for: no trailer
            # follows a newline.
            text = self._buffer.strip()
            self._buffer = ""
            self._pending = None
            # A blank or leading line carries no speech; drop it rather than emit an empty clause.
            return self._emit(text, ClauseBoundary.NEWLINE) if text else None
        if character in policy.terminators:
            self._pending = len(self._buffer)
            return None
        if self._pending is None:
            return None
        if character in policy.trailers:
            # Closing a terminated clause — the boundary moves out to include it.
            self._pending = len(self._buffer)
            return None
        # The character after the boundary: the clause is confirmed, and this character starts
        # the next one — unless the "terminator" was a decimal point.
        end = self._pending
        self._pending = None
        if self._is_decimal_point(end, character):
            return None
        text = self._buffer[:end].strip()
        if len(text) < policy.min_clause_chars:
            # Too short to stand alone under this policy — keep accumulating, and let the next
            # terminator mark a new candidate.
            return None
        self._buffer = self._buffer[end:]
        return self._emit(text, ClauseBoundary.PUNCTUATION) if text else None

    def _is_decimal_point(self, end: int, following: str) -> bool:
        """Whether the marked boundary is a decimal point rather than a full stop.

        `3.5` must not become `3.` and `5`, and a voice agent quotes numbers constantly — prices,
        durations, versions. The rule is deliberately the narrowest one that fixes it: a `.` with a
        digit on **both** sides is not a sentence end. It needs no language, no abbreviation list
        and no model, so it behaves identically in every language the platform serves.

        Abbreviations (`Dr. Rao`, `e.g.`) are **not** handled and do split — that would need a
        per-language rule table whose quality nothing in M11.9 measures (R-53)."""
        return (
            end >= 2
            and self._buffer[end - 1] == "."
            and self._buffer[end - 2].isdigit()
            and following.isdigit()
        )

    def _overflowed(self) -> Clause | None:
        """The bounded-buffer guarantee, checked after every character.

        A pathological stream — one long run with no punctuation and no newline — is emitted in
        `max_buffer_chars` pieces rather than accumulated. Nothing is dropped and nothing is
        truncated; the text simply arrives as several clauses instead of one."""
        if len(self._buffer) < self._policy.max_buffer_chars:
            return None
        text = self._buffer.strip()
        self._buffer = ""
        self._pending = None
        if not text:
            return None
        return self._emit(text, ClauseBoundary.OVERFLOW)

    def _emit(self, text: str, boundary: ClauseBoundary) -> Clause:
        clause = Clause(text=text, boundary=boundary, index=self._emitted)
        self._emitted += 1
        return clause


__all__ = [
    "DEFAULT_MAX_BUFFER_CHARS",
    "DEFAULT_MIN_CLAUSE_CHARS",
    "DEFAULT_TERMINATORS",
    "DEFAULT_TRAILERS",
    "Clause",
    "ClauseBoundary",
    "ClauseDetector",
    "ClausePolicy",
]
