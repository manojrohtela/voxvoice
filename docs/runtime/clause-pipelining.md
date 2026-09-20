# Clause-Boundary LLM→TTS Pipelining

**Status:** Active v1.0 (2026-09-17) · **Owner:** Technical Owner · **Milestone:** M11.9
**Authority:** Constitution §A8, §A4.2; [ADR-0048](../adr/ADR-0048-llm-tts-clause-pipelining-and-playback.md) PB1/PB2/PB3/PB10; AR-21, AR-25, AR-43, AR-50
**Decisions:** DL-0189 (design), DL-0190 (clause policy), DL-0191 (limitations and risks)

> **LLM streaming produces incremental text; clause detection decides when text is ready for the
> TTS pipeline.**

Constitution §A8: *"LLM tokens are chunked at clause boundaries into TTS."* This is that chunking.
It lives in `runtime/speech/`, which the §A4.2 module map already assigned to *"sentence-chunked
streaming synthesis"* — no new home was invented.

## The chain

```
STT final / user turn complete
        ▼
ConversationOrchestrator → RuntimePipeline._generate()
        ▼
LLM.stream()  ──► TokenDelta chunks, as the model produces them
        ▼
ClauseDetector.feed()  ──► completed clauses only
        ▼
ClauseRouter  ──► ClauseSink.clause(), awaited, in source order
        ▼
M11.10: TTS streaming input + Playback Engine
```

| Component | Responsibility |
|---|---|
| `runtime/speech/clauses.py` | `ClausePolicy`, `ClauseDetector`, `Clause`, `ClauseBoundary` |
| `runtime/speech/sink.py` | `ClauseSink` (the seam), `ClauseRouter` (the glue), `CollectingClauseSink` |

## Why a detector rather than "one chunk, one unit"

A vendor chunk is a **tokenizer artefact**: it splits mid-word, mid-punctuation and
mid-codepoint, and splits differently for the same sentence on a different model. Synthesizing per
chunk produces audibly wrong prosody (ADR-0048 rejects it explicitly); waiting for the whole
response puts the latency floor back (PB2). A clause is the smallest unit that is both
**speakable** and **available early**.

## The clause-boundary policy

Every threshold is named in `ClausePolicy`; no call site carries a bare number.

| Knob | Default | Meaning |
|---|---|---|
| `terminators` | `. ! ? … । 。 ！ ？` | sentence-terminal punctuation. A comma is **not** a boundary — a comma-delimited fragment synthesized alone carries the wrong intonation. The danda and full-width stops are included because the platform's market is Indian English; that is a punctuation claim, **not** a Hinglish quality claim. |
| `trailers` | `" ' ” ’ ) ] 》 』 」` | closing marks that belong to the clause they close |
| `newline_is_boundary` | `True` | a line break is a real prosodic break |
| `min_clause_chars` | `0` (**off**) | a minimum length, available but not applied by default |
| `max_buffer_chars` | `400` | the hard ceiling on buffered text |

### A punctuation boundary is confirmed one character late

A terminator does not end a clause by itself. `He said "stop."` ends at the quotation mark, not at
the full stop; emitting on the full stop hands the synthesizer `He said "stop.` followed by a
clause beginning with a stray quote. So a terminator **marks** a candidate end and the next
character decides — a trailer extends it, anything else confirms it.

The cost is that a clause is delivered when the following character arrives rather than the instant
its full stop does — for a stream, one more token. For the **last** clause of a response there is
no following character, which is exactly what `flush()` handles, so nothing ever waits on a token
that will never come.

### One narrow linguistic exception

A period with a digit on **both** sides is a decimal point, not a full stop, so `3.5 units` and
`Version 1.2.3` stay whole. A voice agent quotes prices, durations and versions constantly. The
rule needs no language, no abbreviation list and no model, so it behaves identically everywhere.

**Abbreviations are not handled**: `Dr. Rao` splits. Handling them needs a per-language rule table
whose quality nothing in M11.9 measures (R-53). The limitation is pinned by a test, so discharging
it cannot happen silently.

### Why the minimum clause length defaults to off

The first value tried (12 characters) silently swallowed ordinary short sentences — `I am fine.`
was withheld from the speech path to satisfy an invented threshold. Any non-zero value is a guess
about prosody that nothing here measured. A boundary is punctuation; brevity is not a reason to
keep speech waiting. The knob remains for an operator who measures the case (R-52).

## Boundedness

The buffer is emitted and cleared the moment it reaches `max_buffer_chars`, so **no input sequence
can grow it further** — asserted structurally and across a 20 000-chunk unpunctuated stream.

Overflow **emits, never truncates**: the concatenation of every clause equals the whole input,
which a test asserts directly. Silently truncating a caller-visible answer would be far worse than
cutting it where the policy says. The clause carries `ClauseBoundary.OVERFLOW` so a forced boundary
is distinguishable from a sentence end without inspecting the text.

The clause path starts **no task and opens no queue** — asserted by an AST guard, not merely by a
test that happened to pass.

## The LLM→TTS seam

`ClauseSink` is the smallest thing M11.10 needs: somewhere to hand a completed clause, as soon as
it is completed.

- It carries `Clause` — text produced by *something*. It names no LLM provider and cannot reach
  one (AST-asserted), so a TTS subsystem written against it works identically whether the text came
  from Groq, from `EchoLLM`, or from a greeting script.
- It contains **no audio concept**. Synthesis, frame ordering, playback scheduling and barge-in
  flush are M11.10's (PB5–PB9).

**Ordering is the contract.** `clause()` is awaited once per clause, in source order, before the
next is delivered. Clauses become speech, and speech played out of order is a different answer.
Each clause carries its `index`, so ordering and de-duplication are assertable by a consumer rather
than merely believed.

`finish()` is called exactly once after a response completes — including when it produced no
clauses. `abandon()` replaces it when the turn was cancelled, interrupted or failed; the buffered
tail is then **discarded, not flushed**, because the conversation has moved on and speaking the
fragment the model had reached would answer a question no longer being asked. Both are idempotent:
a duplicated flush is a duplicated sentence of speech.

## Integration and configuration

A detector and router are built **per response**; clause state belongs to one assistant turn, and a
shared detector would carry one answer's tail into the next (asserted). The sink is long-lived,
because M11.10's consumer will be.

`RuntimePipeline(clause_sink=…, clause_policy=…)`. **Both default to `None`, and with no sink the
detector is not constructed at all** — the response path behaves exactly as M11.8's. No API field,
no `AgentSpec` field and no session-level configuration was added, because M11.9 has no consumer
that would act on one.

## What M11.9 did *not* do

**ADR-0048 PB2 is not yet discharged.** `_generate()` still returns the joined text, and the
existing whole-utterance `synthesize()` call still consumes it, because no TTS provider can accept
clauses until M11.10 builds the streaming-input path (PB3) and the Playback Engine (PB5). M11.9
builds the **producing** half and proves it delivers incrementally; M11.10 removes the wait by
connecting the consuming half. Claiming the removal here would be claiming a latency improvement
the audio path does not have.

Also not done, deliberately: real TTS, playback, clause-to-audio synthesis, greeting playback,
LLM pre-warming (PB4), Epic 10 spine wiring, and any change to the Turn Engine, endpointing, STT,
VAD or transport.

## Evidence

`tests/test_clause_pipelining.py` (41 tests) and the integration cases in
`tests/test_llm_streaming_integration.py`. The headline property — **the first clause reaches the
sink before the LLM stream finishes** — is proven by holding the vendor open after its first chunk
and asserting what the sink already holds, never by timing.

Nine mutants are killed by tests named for the property they break: boundary detection disabled,
every chunk emitted as a clause, the tail dropped, clauses reordered, clauses duplicated, the final
flush removed, the whole response buffered before yielding, cancellation swallowed, and a provider
failure treated as a clean completion.

**No latency claim is made.** The tests establish streaming *topology* and correctness. First-clause
latency against a real model is M11.13's, with a stated environment, model, input and sample size.
