"""Clause-boundary detection and the LLM→TTS seam (M11.9, ADR-0048 PB1).

The detector is pure and synchronous, so every case here is a statement about text: given these
chunk boundaries, exactly these clauses, in exactly this order. Nothing sleeps, nothing is timed,
and nothing needs a provider — which is the point, because the detector must behave identically
whichever provider produced the text.

Ordering and incrementality are proven with `asyncio.Event`s rather than delays: a sink that
blocks inside `clause()` while the stream is held open is evidence; a sink that received a clause
"quickly" is not.
"""

from __future__ import annotations

import asyncio

import pytest

from runtime.speech import (
    DEFAULT_MAX_BUFFER_CHARS,
    DEFAULT_MIN_CLAUSE_CHARS,
    Clause,
    ClauseBoundary,
    ClauseDetector,
    ClausePolicy,
    ClauseRouter,
    ClauseSink,
    CollectingClauseSink,
)

from .fake_completion import RecordingClauseSink, settle


def clauses(*chunks: str, policy: ClausePolicy | None = None) -> list[tuple[str, str]]:
    """Feed `chunks` to a fresh detector and flush; return (text, boundary) in emission order."""
    detector = ClauseDetector(policy)
    out: list[tuple[str, str]] = []
    for chunk in chunks:
        out += [(c.text, c.boundary.value) for c in detector.feed(chunk)]
    out += [(c.text, c.boundary.value) for c in detector.flush()]
    return out


def texts(*chunks: str, policy: ClausePolicy | None = None) -> list[str]:
    return [text for text, _ in clauses(*chunks, policy=policy)]


# ── 5–11: what a clause is, and where chunk boundaries may fall ──────────────


def test_5_multiple_chunks_form_one_clause():
    """The authorization's worked example: three chunks, one clause."""
    assert texts("Hello,", " how are", " you today?") == ["Hello, how are you today?"]


def test_6_punctuation_split_across_chunks():
    """A terminator arriving alone in its own chunk still ends the clause it belongs to."""
    assert texts("Hello there", ".", " Next one.") == ["Hello there.", "Next one."]


def test_7_word_split_across_chunks():
    """A vendor splits mid-word constantly; the clause must be spelled correctly regardless."""
    assert texts("Hel", "lo wor", "ld! Ye", "s?") == ["Hello world!", "Yes?"]


def test_8_unicode_split_across_chunks():
    """Devanagari, a combining mark and an emoji modifier, each split at a chunk boundary.

    The detector concatenates and never re-encodes or normalizes, so no codepoint is damaged and
    no cluster is reordered. Written with explicit escapes rather than literal glyphs so the
    assertion cannot be quietly changed by an editor renormalizing the file."""
    assert texts("\u0928\u092e", "\u0938\u094d\u0924\u0947\u0964") == [
        "\u0928\u092e\u0938\u094d\u0924\u0947\u0964"
    ]
    # A combining acute (U+0301) separated from its base letter by the chunk boundary.
    assert texts("Cafe", "\u0301 is open.") == ["Cafe\u0301 is open."]
    # An emoji whose skin-tone modifier lands in the next chunk.
    assert texts("Great \U0001f44d", "\U0001f3fd work.") == [
        "Great \U0001f44d\U0001f3fd work."
    ]


def test_9_newline_is_a_boundary():
    assert clauses("Line one\nLine two\n") == [
        ("Line one", "newline"),
        ("Line two", "newline"),
    ]


def test_9b_a_newline_boundary_can_be_switched_off():
    """The policy is honoured, not merely declared (AR-25)."""
    policy = ClausePolicy(newline_is_boundary=False)
    assert texts("Line one\nLine two.", policy=policy) == ["Line one\nLine two."]


def test_9c_blank_lines_produce_no_empty_clause():
    assert texts("\n\n", "Hi.\n", "\n") == ["Hi."]


def test_10_multiple_clauses_in_one_chunk():
    assert texts("One. Two! Three?") == ["One.", "Two!", "Three?"]


def test_11_multiple_clauses_across_chunks():
    assert texts("One. Tw", "o! Thr", "ee?") == ["One.", "Two!", "Three?"]


def test_11b_a_closing_quote_stays_with_its_clause():
    """The boundary is confirmed one character late precisely so this case is right."""
    assert texts('He said "stop." Then left.') == ['He said "stop."', "Then left."]


def test_11c_a_decimal_point_is_not_a_sentence_end():
    """A voice agent quotes prices and durations; `3.5` must not become two clauses."""
    assert texts("Cost is 3.5 units. Done.") == ["Cost is 3.5 units.", "Done."]
    assert texts("Version 1.2.3 shipped.") == ["Version 1.2.3 shipped."]


def test_11d_an_abbreviation_still_splits_and_that_is_documented():
    """R-53, asserted rather than left as prose: the limitation is real and known.

    If this test ever fails because abbreviations were handled, the risk is discharged and the
    register must be updated — which is exactly why the limitation is pinned by a test."""
    assert texts("Dr. Rao will call.") == ["Dr.", "Rao will call."]


# ── 12–16: flushing, emptiness, duplication, ordering, prematurity ───────────


def test_12_final_partial_clause_is_flushed():
    """Text the model produced without terminal punctuation is never silently lost."""
    assert clauses("A full one.", " and a tail") == [
        ("A full one.", "punctuation"),
        ("and a tail", "end_of_stream"),
    ]


def test_12b_a_flushed_tail_that_ended_on_punctuation_reports_punctuation():
    """The last sentence of a response is a sentence, not an artefact of the stream ending."""
    assert clauses("All done.") == [("All done.", "punctuation")]


def test_13_empty_chunks_change_nothing():
    """Vendors emit role-only and finish-only chunks; absorbing one must be a no-op."""
    detector = ClauseDetector()
    assert detector.feed("") == ()
    assert detector.feed("Par") == ()
    assert detector.feed("") == ()
    assert detector.buffered == "Par"
    assert [c.text for c in detector.feed("tial.")] == []
    assert [c.text for c in detector.flush()] == ["Partial."]


def test_14_no_clause_is_ever_duplicated():
    """Every emitted clause carries a unique, monotonically increasing index."""
    detector = ClauseDetector()
    emitted: list[Clause] = []
    for chunk in ["One. ", "Two! ", "Three? ", "Four"]:
        emitted += detector.feed(chunk)
    emitted += detector.flush()
    assert [c.index for c in emitted] == [0, 1, 2, 3]
    assert len({c.text for c in emitted}) == len(emitted)
    assert detector.clauses_emitted == 4


def test_14b_flushing_twice_emits_nothing_the_second_time():
    detector = ClauseDetector()
    detector.feed("A tail")
    assert [c.text for c in detector.flush()] == ["A tail"]
    assert detector.flush() == ()


def test_15_ordering_is_exactly_source_order():
    """The authorization's ordering case: "A. B. C." arrives as A, then B, then C."""
    assert texts("A. B. C.") == ["A.", "B.", "C."]
    assert texts("A", ". B", ". C", ".") == ["A.", "B.", "C."]


def test_16_no_premature_clause_emission():
    """A comma, a space and an unterminated fragment complete nothing.

    This is the property the "emit every chunk as a clause" mutant violates."""
    detector = ClauseDetector()
    for chunk in ["Hello,", " how are", " you"]:
        assert detector.feed(chunk) == ()
    assert detector.clauses_emitted == 0
    assert detector.buffered == "Hello, how are you"


# ── 18, 44: boundedness ──────────────────────────────────────────────────────


def test_18_the_clause_buffer_is_bounded():
    """A pathological stream with no boundary at all never grows the buffer past the ceiling."""
    policy = ClausePolicy(max_buffer_chars=32)
    detector = ClauseDetector(policy)
    for _ in range(500):
        detector.feed("x" * 7)
        assert len(detector.buffered) < policy.max_buffer_chars


def test_18b_overflow_emits_rather_than_truncates():
    """Nothing is dropped: the concatenation of every clause is the whole input."""
    policy = ClausePolicy(max_buffer_chars=20)
    source = "y" * 95
    emitted = clauses(source, policy=policy)
    assert "".join(text for text, _ in emitted) == source
    assert [boundary for _, boundary in emitted] == ["overflow"] * 4 + ["end_of_stream"]


def test_44_memory_is_bounded_under_a_long_no_boundary_stream():
    """The detector's total retained state stays flat across a very long unpunctuated stream."""
    detector = ClauseDetector(ClausePolicy(max_buffer_chars=64))
    for _ in range(20_000):
        detector.feed("z" * 13)
    assert len(detector.buffered) < 64


# ── policy validation ────────────────────────────────────────────────────────


def test_the_default_policy_is_the_documented_one():
    """45: configuration defaults remain what the documentation says they are."""
    policy = ClausePolicy()
    assert policy.min_clause_chars == DEFAULT_MIN_CLAUSE_CHARS == 0
    assert policy.max_buffer_chars == DEFAULT_MAX_BUFFER_CHARS == 400
    assert policy.newline_is_boundary is True
    assert {".", "!", "?", "।"} <= policy.terminators


@pytest.mark.parametrize(
    "kwargs",
    [
        {"terminators": frozenset(), "newline_is_boundary": False},
        {"min_clause_chars": -1},
        {"max_buffer_chars": 0},
        {"max_buffer_chars": 5, "min_clause_chars": 10},
    ],
)
def test_an_impossible_policy_is_refused_at_construction(kwargs):
    with pytest.raises(ValueError):
        ClausePolicy(**kwargs)


def test_a_minimum_clause_length_is_honoured_when_configured():
    """Off by default, but real when an operator sets it."""
    policy = ClausePolicy(min_clause_chars=12)
    assert texts("Sure. I can help with that.", policy=policy) == [
        "Sure. I can help with that."
    ]


# ── 41, 42, 17: the seam delivers incrementally and in order ─────────────────


async def test_41_the_sink_receives_clauses_incrementally():
    """Clauses reach the sink as they complete, not in one batch at the end."""
    sink = RecordingClauseSink()
    router = ClauseRouter(ClauseDetector(), sink)
    await router.feed("First one. ")
    assert sink.texts == ["First one."]  # delivered before any further text existed
    await router.feed("Second one. ")
    assert sink.texts == ["First one.", "Second one."]
    assert sink.finished == 0
    await router.finish()
    assert sink.finished == 1


async def test_42_end_of_stream_flushes_through_the_router():
    sink = RecordingClauseSink()
    router = ClauseRouter(ClauseDetector(), sink)
    await router.feed("A sentence. And a tail")
    assert sink.texts == ["A sentence."]
    await router.finish()
    assert sink.texts == ["A sentence.", "and a tail".capitalize()]
    assert sink.boundaries[-1] == "end_of_stream"


async def test_the_router_preserves_order_and_indices():
    sink = RecordingClauseSink()
    router = ClauseRouter(ClauseDetector(), sink)
    for chunk in ["A. ", "B. ", "C. "]:
        await router.feed(chunk)
    await router.finish()
    assert sink.texts == ["A.", "B.", "C."]
    assert sink.indices == [0, 1, 2]
    assert router.delivered == 3


async def test_several_clauses_completed_by_one_chunk_arrive_in_source_order():
    """The ordering contract, stated where a reordering defect can actually show.

    Feeding one clause at a time cannot detect reordering — there is nothing to reorder. A single
    chunk carrying three complete clauses can, which is why this case exists separately."""
    sink = RecordingClauseSink()
    router = ClauseRouter(ClauseDetector(), sink)
    await router.feed("First one. Second one. Third one. ")
    assert sink.texts == ["First one.", "Second one.", "Third one."]
    assert sink.indices == [0, 1, 2]


async def test_the_router_finishes_once_however_often_it_is_called():
    """Idempotent: a duplicated finish would duplicate the tail clause — a repeated sentence."""
    sink = RecordingClauseSink()
    router = ClauseRouter(ClauseDetector(), sink)
    await router.feed("Tail")
    await router.finish()
    await router.finish()
    assert sink.texts == ["Tail"]
    assert sink.finished == 1


async def test_an_abandoned_response_drops_the_tail_and_says_so():
    """A cancelled turn must not speak the fragment the model had reached."""
    sink = RecordingClauseSink()
    router = ClauseRouter(ClauseDetector(), sink)
    await router.feed("Complete one. Partial tail")
    await router.abandon()
    assert sink.texts == ["Complete one."]
    assert (sink.abandoned, sink.finished) == (1, 0)


async def test_abandoning_after_finishing_does_nothing():
    sink = RecordingClauseSink()
    router = ClauseRouter(ClauseDetector(), sink)
    await router.finish()
    await router.abandon()
    assert (sink.finished, sink.abandoned) == (1, 0)


async def test_a_response_with_no_text_still_closes_the_sink():
    sink = RecordingClauseSink()
    router = ClauseRouter(ClauseDetector(), sink)
    await router.feed("")
    await router.finish()
    assert sink.texts == []
    assert sink.finished == 1


async def test_delivery_is_awaited_before_the_next_clause_is_produced():
    """Ordering is a contract, not a coincidence: while the sink holds clause 1, clause 2 has not
    been delivered — proven by blocking inside the sink rather than by timing."""
    hold = asyncio.Event()
    sink = RecordingClauseSink(hold=hold)
    router = ClauseRouter(ClauseDetector(), sink)
    task = asyncio.create_task(router.feed("One. Two. Three."))
    assert await settle(sink.received.is_set), "no clause was ever delivered"
    assert sink.texts == ["One."]
    assert not task.done()
    hold.set()
    await task
    # "Three." is still held back: its boundary is confirmed by the character *after* the full
    # stop, and no such character exists until the stream ends.
    assert sink.texts == ["One.", "Two."]
    await router.finish()
    assert sink.texts == ["One.", "Two.", "Three."]


# ── the default sink ─────────────────────────────────────────────────────────


async def test_the_collecting_sink_records_in_order():
    sink = CollectingClauseSink()
    router = ClauseRouter(ClauseDetector(), sink)
    await router.feed("One. Two.")
    await router.finish()
    assert sink.texts == ("One.", "Two.")
    assert sink.finished is True and sink.abandoned is False


async def test_an_abandoned_collecting_sink_records_the_abandonment():
    sink = CollectingClauseSink()
    await ClauseRouter(ClauseDetector(), sink).abandon()
    assert sink.abandoned is True


async def test_a_half_implemented_sink_fails_loudly():
    """A sink that never implemented `clause()` must not be discovered on a live call."""

    class Broken(ClauseSink):
        pass

    with pytest.raises(NotImplementedError):
        await ClauseRouter(ClauseDetector(), Broken()).feed("Boom. Next")


def test_the_clause_boundary_reasons_are_the_documented_set():
    assert {b.value for b in ClauseBoundary} == {
        "punctuation",
        "newline",
        "overflow",
        "end_of_stream",
    }
