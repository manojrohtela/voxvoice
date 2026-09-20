# ADR-0048 — Clause-boundary LLM→TTS pipelining, LLM pre-warming, and the Playback Engine / Playback Scheduler

| | |
|---|---|
| **Status** | **Accepted 2026-08-24** (ratified at M11.1). Recommended by the Epic 11 **M11.0 Blueprint** and settled at the **M11.0a** decision gate (ruling **D-11**); this milestone ratifies it. |
| **Kind** | Epic 11 architecture ADR. **Amends no Constitution Part A or Part B section.** |
| **Amends** | Nothing. Supersedes no ADR. It **removes** a standing **AR-43** violation and builds two §A8 components that do not exist. |
| **Deciders** | Technical Owner |
| **Related** | Constitution v2 **§A8** (streaming principle, playback rules, per-stage budgets), **§A2.6 / AR-25** (accepted config must act), **I1**; ARCHITECTURE_RULES **AR-33** (public interfaces versioned, deprecation flow), **AR-40**, **AR-43** (a wait-for-whole-input step requires an ADR); [ADR-0005](ADR-0005-streaming-first-turn-intelligence.md); [ADR-0044](ADR-0044-audio-representation-resampling-and-media-buffers.md) (bounded media buffers); [ADR-0045](ADR-0045-streaming-adapter-pattern-and-vad-execution.md) **SD5/SD6** (streaming-input TTS); [ADR-0046](ADR-0046-media-path-fault-tolerance-and-provider-mechanism-reuse.md) **FT-D9** (AR-25 discharges) |

## Status

**Accepted 2026-08-24**, ratified at milestone **M11.1**. **This ADR authorizes no code.** The
Playback Engine and Playback Scheduler are **not implemented in M11.1**; they are built at M11.10.

## Context

**A wait-for-whole-input step already exists in the conversation loop.** Verified at `3779e932`,
`RuntimePipeline._respond()`:

```python
parts: list[str] = []
async for delta in self._llm.stream(context=context):
    if isinstance(delta, TokenDelta):
        parts.append(delta.text)
agent_text = "".join(parts).strip()
...
async for frame in self._tts.synthesize(agent_text, voice=..., language=...):
```

The **entire** LLM stream is drained before TTS is called. **AR-43** states that *"introducing a
wait-for-whole-input step into the conversation loop requires an ADR (it moves the structural
latency floor)."* With `EchoLLM` the cost is nil; with a real LLM this serializes full generation
ahead of first audio and makes **I1 (voice-to-voice p50 ≤ 1.0 s)** unreachable by construction.

Constitution §A8 states the required behaviour directly: *"STT partials flow during caller
speech… on high end-of-turn probability the LLM call is **pre-warmed** so first-token latency
lands inside the endpoint delay; **LLM tokens are chunked at clause boundaries into TTS**;
playback streams frame-by-frame with an interruption flush bounded by one jitter-buffer depth."*

§A8 further mandates two components that **do not exist anywhere in the repository**:

- the **Playback Engine** — *"provides ordered frames, immediate flush (barge-in), and priority
  slots (filler/acknowledgment audio pre-empts queued sentences without cancelling generation)"*;
- the **Playback Scheduler** — *"owns filler policy, greeting pre-cache, and interruption
  crossfade."*

Finally, `first_message` is **accepted and never read**: it flows
`api → StartSessionRequest.first_message → AgentSpec.first_message`, and `grep -rn "first_message"
platform/runtime` returns only the two dataclass/assignment sites — no consumer. No greeting is
ever played. Under **AR-25** this cannot be left as it is.

## Decision

### PB1 — LLM output is chunked at clause boundaries and streamed into TTS

The response path streams: LLM token deltas accumulate to a clause boundary, and each completed
clause is handed to TTS as it forms. Playback begins on the **first** synthesized clause, not on
the last generated token.

### PB2 — The wait-for-whole-input step is removed, not relocated

`_respond()` ceases to materialize `agent_text` before synthesis. No later stage reintroduces a
whole-input wait. This ADR **discharges** the AR-43 obligation that the current code already
incurs; it does not authorize a new one.

### PB3 — Clause chunking consumes the streaming-input TTS path

Chunking uses ADR-0045 **SD5**'s streaming-input method where the provider declares
`streaming_input=True` (SD6). Where a provider declares `False`, the runtime falls back to
per-utterance `synthesize()` and **records the degradation** (AR-24) — it does not silently
serialize.

### PB4 — LLM pre-warming is driven by end-of-turn probability

Where the Smart Turn engine indicates a high end-of-turn probability, the LLM call is pre-warmed
so first-token latency lands inside the endpoint delay (§A8). Pre-warming is an **optimization
that must not change conversation semantics**: a pre-warm that is not consumed is discarded, and
no pre-warm may emit a turn event or mutate conversation state (AR-5 — the Orchestrator remains
the sole writer).

### PB5 — The Playback Engine owns frame delivery

It provides **ordered frames**, **immediate flush** for barge-in, and **priority slots** so
filler/acknowledgement audio pre-empts queued sentences **without cancelling generation** (§A8).

### PB6 — Interruption flush is bounded by one jitter-buffer depth

Per §A8. Flush is a bounded operation, not a best-effort drain.

### PB7 — The Playback Scheduler owns filler policy, greeting pre-cache and interruption crossfade

Per §A8. It is the policy owner; the Engine is the mechanism.

### PB8 — `first_message` is **implemented** through the greeting pre-cache, not deprecated

The greeting is played at session start through the Scheduler's greeting pre-cache, and it is
**interruptible** like any other assistant speech.

Deprecation was the alternative and is rejected: **AR-33** versions every public interface with a
deprecation flow, and `first_message` is already exposed on the public API contract, so removal
costs more than implementation. §A8 independently mandates the greeting pre-cache mechanism, so
implementing is an integration rather than a new concept. And a voice agent that never greets is
not a live runtime.

### PB9 — All playback buffering is bounded

Playback queues obey ADR-0044 **AD6/AD7**: capacity in milliseconds, **outbound is back-pressure
and never drops**, and overflow is a recorded degradation that never raises into the pipeline.

### PB10 — Ordering follows §A8, and the taps follow the real order

The stage boundaries `llm_first_token`, `tts_first_audio` and `first_audio_out` keep their
ADR-0038 meanings. `tts_first_audio` now marks the first audio of the **first clause**, which is
the intended acceleration; ADR-0047 **ME5** keeps the raw instants available so any derived
interval remains re-derivable.

## Consequences

- M11.9 implements PB1–PB4; M11.10 implements PB5–PB9. Neither is started in M11.1.
- Two new §A8 components enter `runtime/pipeline/`; the Turn, Endpoint, Interrupt and Smart Turn
  frameworks are **not** redesigned — the Engine consumes their existing signals.
- The structural latency floor drops from *full generation + first synthesis* to *first clause +
  first synthesis*, which is what makes the G9 I1 gate achievable.
- `first_message` stops being dead configuration; an AR-25 defect is discharged.
- Barge-in gains a real flush path; ADR-0046 **FT-D9** makes barge-in reachable in the first place.

## Alternatives considered

- **Keep whole-response synthesis and try to meet I1 by choosing a faster LLM.** Rejected: it
  leaves the structural floor in place and makes the budget dependent on vendor luck.
- **Chunk at token boundaries rather than clause boundaries.** Rejected: §A8 specifies clause
  boundaries, and sub-clause synthesis produces audibly wrong prosody.
- **Per-clause `synthesize()` calls without SD5.** Rejected in ADR-0045: connection overhead per
  clause and prosody seams.
- **Deprecate `first_message`.** Rejected — PB8.
- **Fold playback into `RuntimePipeline` without a distinct Engine/Scheduler.** Rejected: §A8
  names both, and AR-50 forbids a module whose responsibility needs "and" — frame delivery and
  filler/greeting/crossfade policy are two responsibilities.
- **Implement filler audio in Epic 11.** Not decided here: the Scheduler *owns* filler policy, but
  Epic 11 implements only what the greeting and interruption paths require.

## Evidence required later

1. A test proving no whole-response materialization remains on the response path.
2. A test proving playback begins on the first clause, measured through the ADR-0038 taps.
3. A test proving a `streaming_input=False` provider degrades and **records** it.
4. A test proving an unconsumed pre-warm changes no conversation state and emits no turn event.
5. A barge-in test proving flush is bounded and priority slots pre-empt without cancelling
   generation.
6. A test proving the greeting plays before first caller audio and is interruptible.
7. Playback saturation tests asserting back-pressure, never drop.
