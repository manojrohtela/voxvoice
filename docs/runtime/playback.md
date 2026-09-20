# Playback Engine and Playback Scheduler

**Status:** Active v1.0 (2026-09-18) · **Owner:** Technical Owner · **Milestone:** M11.10
**Authority:** Constitution §A8, §A4.2; [ADR-0048](../adr/ADR-0048-llm-tts-clause-pipelining-and-playback.md) PB1/PB2/PB3/PB5/PB6/PB7/PB8/PB9/PB10; [ADR-0044](../adr/ADR-0044-audio-representation-resampling-and-media-buffers.md) AD4/AD6/AD7; AR-21, AR-23, AR-25, AR-41, AR-50
**Decisions:** DL-0193 (design), DL-0194 (buffering and back-pressure), DL-0195 (greeting pre-cache), DL-0196 (limitations and risks)

> **TTS converts provider-neutral text into provider-neutral audio; the Playback Scheduler decides
> when and in what order that audio is played.**
>
> **VAD is speech detection; endpointing decides when a speech turn ends.**

Constitution §A8 names two components the repository did not have: an Engine that *"provides
ordered frames, immediate flush (barge-in), and priority slots"*, and a Scheduler that *"owns
filler policy, greeting pre-cache, and interruption crossfade"*. M11.10 builds both, in
`runtime/speech/` — the §A4.2 home for Speech Out.

## The chain

```
LLM stream ─► ClauseDetector ─► Clause ─► PlaybackScheduler ─► TTS ─► AudioFrame
                                                  │
                                                  ▼
                                          PlaybackEngine (bounded, ordered)
                                                  │
                                    egress normalizer ─► guarded transport ─► caller
```

| Component | Responsibility | File |
|---|---|---|
| **Playback Scheduler** | *what* is spoken and *in what order*; greeting pre-cache | `runtime/speech/scheduler.py` |
| **Playback Engine** | bounded buffering, ordered delivery, immediate flush | `runtime/speech/playback.py` |

Two components rather than one because AR-50 forbids a module whose responsibility needs an "and":
frame delivery and speech-unit policy are two things.

## Ordering, and why it is structural

Clause *n+1* is not synthesized until clause *n*'s audio has been handed to the Engine in full.
There is no second task that could overtake, no per-clause queue to reorder, and **no parallelism**
— so *"clause 2 cannot overtake clause 1"* is a property of the shape, not of a check that could be
forgotten. Inside the Engine, a **single consumer** pulls frames, so frames leave in exactly the
order they were enqueued.

The authorization permits overlapping synthesis only where bounded back-pressure makes it safe. It
is **not implemented**: it buys latency this milestone is not allowed to claim and risks the one
invariant it must not lose.

Each frame in the tests carries an identifying payload (utterance index and position), so a
reordering, a duplication or a loss shows up in an assertion rather than merely being likely to.

## Buffering and back-pressure

ADR-0044 **AD6/AD7** and M11.3 draw the line: inbound audio may drop oldest, because a late caller
frame is worthless; **outbound speech must never be silently dropped**, because a dropped frame is
a word the caller never hears while every subsystem reports success.

| Property | Value |
|---|---|
| capacity | **400 ms of audio** (`DEFAULT_CAPACITY_MS`), configurable |
| unit | **milliseconds**, never a frame count — a frame means different things at 8 kHz and 48 kHz |
| when full | the producer **waits** (`await` on a condition); nothing is dropped |
| secondary ceiling | 256 frames, for `OPAQUE` audio that declares no duration |
| oversize frame | a frame longer than the whole capacity is admitted when the buffer is empty — refusing to speak is worse than exceeding the bound by one frame |

The wait is an `await`, never a synchronous block, so a slow transport never stalls the event loop
that the transport itself needs in order to drain. Back-pressure propagates all the way back
through synthesis: a transport that accepts nothing stops the vendor being asked for more audio.

**Only cancellation discards audio.** `frames_discarded` counts exactly that, which is what
separates a barge-in from silent loss. The capacity is also what bounds the barge-in flush
(PB6): at most 400 ms is ever thrown away, so the flush is a bounded operation rather than a
best-effort drain of unknown length.

400 ms is an **unmeasured implementation default** (R-60), chosen as a few multiples of the 20 ms
media cadence rather than from a measured jitter distribution.

## Playback state

`IDLE → PLAYING → DRAINING → COMPLETED`, with `CANCELLED` and `FAILED` as early terminals.
Deliberately small: a state the Engine cannot behave differently in is a log line.

This is **not conversation state**. The Turn Engine owns whose turn it is; this owns whether audio
is moving. Nothing here is consulted to decide a turn, and an AST test asserts the Engine imports
no turn, endpoint, voice-activity, interrupt or LLM module — it imports `core.audio` and the
standard library, and nothing else.

`FAILED` is distinct from `CANCELLED` because nobody asked for it, and a producer waiting for
buffer space on a failed engine is released and raises rather than waiting for a sender that is
never coming back.

## Cancellation and barge-in

On cancellation the Engine discards its queue, stops its sender and refuses further audio; the
Scheduler stops pulling from TTS mid-utterance, so a barge-in stops *generation*, not merely
delivery. Both are idempotent — a second barge-in during the first must not raise.

`abandon()` leaves the Scheduler cancelled so nothing from the interrupted response can still be
spoken. `begin_response()` resets it at the start of the next turn; without that explicit reset the
assistant would go permanently mute after its first barge-in.

With `allow_interruptions=False` no interrupt coordinator is wired, so ordinary speech pre-empts
nothing — unchanged from M3.4. M11.10 creates **no second VAD or interrupt mechanism**; it acts on
the existing one's confirmed pre-empt.

## The greeting pre-cache (ADR-0048 PB8, D-11)

`AgentSpec.first_message` → `PlaybackScheduler.precache_greeting()` → held frames →
`play_greeting()`.

- **Called by an active session**, never at bootstrap and never at application startup.
- **Bounded**: at most 30 s of held audio (`DEFAULT_GREETING_CAPACITY_MS`); a longer greeting is
  refused rather than held.
- **Cancellable**: an ordinary await, so the session's cancellation propagates.
- **Plays exactly once**, and the held audio is released once spoken rather than kept for a
  session that will never replay it.
- **Never fatal**: a failed pre-cache raises `GreetingUnavailable`; the caller simply is not
  greeted and the session proceeds.

Pre-caching readies audio; it never starts playback on its own — asserted directly.

## metric.turn

M11.8's chain and key names are **unchanged**; M11.10 renames nothing and adds no metric event.

| Boundary | Stamped when |
|---|---|
| `tts_first_audio` | the **first audio frame of the response actually exists** — not when synthesis was requested, and not when the text was ready |
| `first_audio_out` | **after the transport has accepted** the first frame |

So back-pressure *moves* `first_audio_out` rather than being papered over, and a response that
produces no audio stamps neither — asserted, because synthetic timing is exactly what this metric
must never carry. The assistant-speaking turn transition and the barge-in monitor also start at the
first real frame rather than on an assumption made earlier.

## Integration and configuration

`RuntimePipeline(playback=…)`. **Default `None`**, and with no Scheduler the response path is
byte-for-byte M11.9's: whole-utterance synthesis, no engine, no clause routing into audio. No API
field and no `AgentSpec` field was added.

When a Scheduler *is* wired, the Scheduler becomes the clause sink and the response path is
clause-driven: **ADR-0048 PB2's whole-input wait is gone for that session** — text becomes audio
while the model is still writing. It remains in place for the default profile, which is why PB2 is
described as discharged *for sessions that wire playback* rather than outright. Flipping the
default belongs to M11.11, which owns the first real end-to-end conversation.

## What M11.10 did not do

Filler audio, interruption crossfade, LLM pre-warming (PB4), Epic 10 spine wiring, billing,
performance certification, and the first real end-to-end voice conversation. No redesign of the
Turn Engine, endpointing, STT, VAD, LLM, transport, ProviderGuard, ProviderRegistry or AudioFrame.

## Evidence

`tests/test_playback.py` (50), `tests/test_playback_integration.py` (41), `tests/test_cartesia_tts.py`
(62). Ordering, back-pressure and cancellation are proven with events, never with sleeps.

Fourteen mutants are killed by tests named for the property they break: reordered frames, a dropped
frame, a duplicated frame, an unbounded buffer, back-pressure that drops instead of waiting, clause
2 overtaking clause 1, playback continuing after cancellation, TTS still being pulled after
cancellation, an ignored interrupt, stale queued audio after an interrupt, `tts_first_audio` stamped
before audio exists, `first_audio_out` stamped before the transport accepted it, a TTS failure
treated as success, and audio replayed after a partial failure.

**No latency claim is made.** The tests establish ordering, boundedness and correctness. First-audio
latency against a real vendor is M11.13's, with a stated environment, voice, input and sample size.
