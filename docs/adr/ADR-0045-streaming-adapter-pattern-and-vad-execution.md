# ADR-0045 — The streaming provider adapter pattern: duplex STT behind the unchanged contract, streaming-input TTS, and an O(1) voice-activity seam

| | |
|---|---|
| **Status** | **Accepted 2026-08-24** (ratified at M11.1). Recommended by the Epic 11 **M11.0 Blueprint** and settled at the **M11.0a** decision gate (rulings **D-5**, **D-6**); this milestone ratifies it. |
| **Kind** | Epic 11 architecture ADR. **Amends no Constitution Part A or Part B section.** |
| **Amends** | Nothing. Supersedes no ADR. It extends two capability contracts **additively** under §A12 / AR-52 and leaves a third untouched. |
| **Deciders** | Technical Owner |
| **Related** | Constitution v2 **§A6** (capability contracts and their interface essence), **§A6.3** (contract test suites), **§A6.4** (declared degraded modes), **§A8** (streaming stages overlap), **§A11.1.7** (no fire-and-forget tasks without handles); ARCHITECTURE_RULES **AR-22**, **AR-24**, **AR-40**, **AR-43**, **AR-52**; [ADR-0005](ADR-0005-streaming-first-turn-intelligence.md) (streaming-first; batch only in declared degraded mode); [ADR-0044](ADR-0044-audio-representation-resampling-and-media-buffers.md); [ADR-0046](ADR-0046-media-path-fault-tolerance-and-provider-mechanism-reuse.md) (typed errors, timeouts); [AR-6](../../.voxvoice/ARCHITECTURE_RULES.md) (session status must reflect call reality) |

## Status

**Accepted 2026-08-24**, ratified at milestone **M11.1**. **This ADR authorizes no code.** It fixes
the adapter semantics M11.5–M11.10 must honour.

## Context

Verified against the repository at `3779e932`.

**STT.** `core/providers/stt.py` declares a single async generator that consumes a pull-based
frame iterator:

```python
def transcribe(self, frames: AsyncIterator[AudioFrame], *, language: str, sample_rate: int)
        -> AsyncIterator[Partial | Final]
```

Constitution §A6 states the STT interface essence as *"open stream (language, sample rate) →
partial hypotheses (text, stability) and finals (text, word timings)"* — which this signature
**matches**.

Every real streaming STT vendor, however, is a **duplex WebSocket**: an independent send loop
pushing audio and an independent receive loop yielding hypotheses, with their own lifecycle,
keepalives and end-of-stream signalling. The two rates are not coupled; a single generator
signature implies they are.

**TTS.** `core/providers/tts.py` declares `synthesize(text, *, voice, language) ->
AsyncIterator[AudioFrame]` — a **one-shot** call over a complete utterance. Constitution §A6
states the TTS essence as *"open stream (voice, language); **accept text chunks** → audio
frames"*. The current signature therefore **does not match** its constitutional essence.

**VAD.** `core/providers/voice_activity.py` declares a **synchronous** `detect(frame, *,
session_id) -> VoiceActivitySample`, called **once per inbound frame** from
`RuntimePipeline._tap_voice_activity`. `VoiceActivityCapabilities.frame_ms` exists — documented
as *"native analysis frame size in ms; 0 = frame-size agnostic"* — and **nothing in the
repository consumes it**. The contract's own docstring already anticipates stateful providers:
*"any per-session buffers/models are set up in `start` and torn down in `stop`, keyed by
`session_id`."*

A real streaming VAD needs a fixed analysis window, a model and per-session buffering. Running
inference synchronously on the inbound hot path would violate **AR-40** and PERFORMANCE_POLICY §3
(*"anything >10 ms on the loop found by profiling is a defect"*).

One further verified hazard: `RuntimePipeline.run()` treats exhaustion of the STT iterator as
**caller hangup** and logs *"conversation ended"*. A vendor disconnect that ends the generator
quietly would therefore be read as the caller hanging up — a real-world-fact error under **AR-6**.

## Decision

The three capabilities get **three different answers**, because their contracts stand in three
different relationships to their constitutional essence.

### SD1 — STT: wrap duplex behind the **unchanged** `transcribe()` contract

`core/providers/stt.py` is **not modified**. A duplex vendor is adapted inside its plugin.

### SD2 — The adapter owns its send task explicitly

The adapter creates an internal task that drains `frames` into the vendor socket while the
generator body yields from the receive side. That task is held by an **explicit handle**,
cancelled on generator close, and **awaited**. §A11.1.7 bans *"fire-and-forget tasks without
handles"*; an adapter that spawns and forgets is a defect, not an implementation style.

### SD3 — Receive-side buffering is bounded

The receive queue is bounded with a declared overflow policy, per ADR-0044 **AD6/AD7** and AR-40.

### SD4 — Mid-stream vendor failure is a **typed error**, never a silent stream end

A vendor disconnect, timeout, credential rejection or protocol error surfaces as a typed error
(ADR-0046), never as ordinary iterator exhaustion. **A vendor disconnect must never be
indistinguishable from a caller hangup** — that would make session status stop reflecting call
reality (AR-6) and would emit the wrong lifecycle event.

### SD5 — TTS: the existing `synthesize()` remains; a streaming-input path is added additively

`synthesize(text, …)` is **kept unchanged** and `LocalTTS` is **behaviourally unchanged**. A
streaming-input method is added alongside it, accepting an async iterator of text chunks and
yielding audio frames as they are produced.

### SD6 — `streaming_input` is a declared capability flag, defaulting `False`

`TTSCapabilities` gains `streaming_input: bool = False`. A provider that cannot accept chunked
input declares `False`; the runtime adapts and **records the degradation** (AR-24, §A6.4).

### SD7 — VAD: **no contract change**. `detect()` is specified as O(1) and non-blocking

`core/providers/voice_activity.py` is **not modified**. `detect()` is specified as a cheap read of
continuously-updated per-session state — it performs no blocking I/O and no inference on the
caller's stack.

### SD8 — Windowed inference is provider-owned, and `frame_ms` becomes load-bearing

The provider buffers inbound frames into its own **bounded** per-session ring (allocated in
`start`, released in `stop` — exactly what the contract was designed for) and runs windowed
inference on a provider-owned task. `detect()` returns the most recent verdict.

The verdict therefore lags by at most one analysis window. That lag is **inherent to windowed
VAD**, not introduced here, and `frame_ms` is the field that declares it. `frame_ms` stops being
decorative and becomes a contract term the conformance suite asserts.

### SD9 — Capability flags must be true, and untruthfulness is a merge-blocking defect

Every flag in this ADR (`streaming`, `partials`, `streaming_input`, `frame_ms`,
`emits_confidence`) is asserted by the §A6.3 contract suite. A provider that cannot meet an O(1)
`detect()` or streaming semantics declares the honest flag and runs in **declared degraded mode**
(§A6.4, ADR-0005, AR-24) — it does not quietly under-deliver.

## Consequences

- STT vendors are integrated with **zero** contract change (M11.6).
- `TTSCapabilities` and `core/providers/tts.py` gain one flag and one method, both additive; the
  5447 floor is unaffected (M11.10).
- ADR-0048's clause-boundary pipelining consumes SD5 — without a streaming-input path, per-clause
  `synthesize()` calls would pay connection overhead per clause and produce prosody seams.
- The VAD framework's engine, hysteresis and state model are **untouched**; they were never in
  question.
- M11.3's conformance suite acquires real teeth: task ownership, bounded buffering, typed
  mid-stream failure, `frame_ms` truthfulness and `detect()` cost all become assertions.

## Alternatives considered

- **One uniform answer for all three capabilities.** Rejected: it would either change a contract
  that already matches its essence (STT) or leave one that does not (TTS).
- **Extend STT with an explicit `open`/`send`/`close` lifecycle.** Rejected: the generator
  signature already expresses §A6's STT essence, and a second lifecycle would add surface without
  adding capability. Revisit only if SD2's task ownership proves unworkable in practice.
- **Per-clause `synthesize()` calls instead of SD5.** Rejected: connection overhead per clause and
  audible prosody seams, both directly against I1 and §A8.
- **Executor offload for VAD.** Rejected: a thread-pool hop per ~20 ms frame is roughly 50 hops per
  second per session, **and it does not solve windowing** — the provider must still buffer to its
  analysis window. It adds cost without addressing the actual problem.
- **An additive async/batched VAD method.** Rejected: a second execution path through a framework
  whose engine is sound and not in question, for no capability gain, since the verdict lags by one
  window either way.
- **Substrate/transport ownership of VAD.** Unavailable: [P-ADR-0002](P-ADR-0002-implementation-profile-epic11.md)
  retires the pipeline-substrate binding.

## Evidence required later

1. Conformance assertions: send-task ownership and awaited shutdown; bounded receive buffering;
   typed mid-stream failure distinguishable from caller hangup.
2. A test proving a vendor disconnect emits a transport/provider failure event, **not** a hangup.
3. A conformance assertion that `detect()` performs no blocking I/O and holds a bounded cost.
4. A `frame_ms` truthfulness assertion.
5. A degraded-mode test: `streaming_input=False` or `streaming=False` is recorded under AR-24.
6. The 5447 floor green with `LocalTTS`, `LocalSTT` and `LocalVoiceActivity` behaviourally
   unchanged.
