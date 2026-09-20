# ADR-0044 — Audio representation, session sample rate, the two resampling points, and bounded media buffers

| | |
|---|---|
| **Status** | **Accepted 2026-08-24** (ratified at M11.1). Recommended by the Epic 11 **M11.0 Blueprint** (accepted; not yet persisted to `docs/architecture/epic11/`) and settled at the **M11.0a** decision gate (rulings **D-3**, **D-4**, **D-17**); this milestone ratifies it. |
| **Kind** | Epic 11 architecture ADR — records a load-bearing decision inside the Epic 11 Live Runtime. **Amends no Constitution Part A or Part B section.** |
| **Amends** | Nothing. It makes Constitution **§A8** and **AR-41** satisfiable for the first time, and supersedes no ADR. |
| **Deciders** | Technical Owner |
| **Related** | Constitution v2 **§A8** (streaming & audio architecture), **§A12** (additive extension), **§A11.1.7** (banned anti-patterns), **I1**; ARCHITECTURE_RULES **AR-40** (no unbounded queues in media paths), **AR-41** (one ingress, one egress resampler), **AR-52** (additive extension), **AR-22** (config validated at write time), **AR-24** (degradations recorded); [ADR-0002](ADR-0002-substrate-behind-contracts.md); [P-ADR-0002](P-ADR-0002-implementation-profile-epic11.md) (substrate retired, which frees `runtime/pipeline/` for first-party ownership); [ADR-0045](ADR-0045-streaming-adapter-pattern-and-vad-execution.md); [DL-0013](../../.voxvoice/DECISION_LOG.md) (auxiliary observation degrades, never breaks) |

## Status

**Accepted 2026-08-24**, ratified at milestone **M11.1**. **This ADR authorizes no code.** It fixes
the audio semantics that M11.2 and every later Epic 11 milestone must honour. Epics 5–10 remain
frozen; `v5.0.0` is not reopened; the 5447-test regression floor is binding.

## Context

Verified against the repository at `3779e932`:

`platform/core/audio.py` defines the entire audio surface:

```python
@dataclass(frozen=True, slots=True)
class AudioFrame:
    data: bytes
    sample_rate: int = 16000
```

Its own docstring already reads *"One chunk of PCM (or, for local/dev transports, an opaque
payload)"* and *"The Runtime normalizes sample rate at exactly one ingress and one egress point
(Constitution §A8) — providers do not resample."*

Three verified facts make that docstring aspirational rather than true:

1. **No resampler exists anywhere.** `grep -rn "resampl" platform/core platform/runtime
   platform/providers` returns zero matches.
2. **`sample_rate` is an ambient assumption, which AR-41 names as the prohibited condition.**
   `RuntimePipeline.__init__` declares `sample_rate: int = 16000`, and
   `ConversationOrchestrator` constructs `RuntimePipeline(...)` **without passing it**
   (`platform/runtime/orchestrator/orchestrator.py:380–394`). Every session therefore runs at a
   hardcoded default that no configuration can influence.
3. **The media path contains an unbounded queue.** `platform/providers/transport/local.py` uses
   `asyncio.Queue()` with no `maxsize`, which §A11.1.7 lists as a banned anti-pattern and AR-40
   prohibits outright.

The audio path has additionally **never carried audio**. The local profile is a text-loopback
codec: `LocalSTT` calls `frame.data.decode("utf-8")`, `LocalTTS` emits `text.encode("utf-8")`,
and `LocalVoiceActivity` reports speech iff the decoded payload is non-whitespace. This profile
is not legacy to be removed — it is the hermetic CI substrate that TESTING_POLICY rule 1
requires, and it must survive Epic 11 byte-identically.

The blast radius of changing the audio representation is small and exactly known: `AudioFrame`
is referenced by **10 production files** (`core/audio.py`, the four `core/providers/*` contracts,
the four `providers/*/local.py` plugins, and `runtime/pipeline/pipeline.py`) and 8 test files.

A frame carrying real media must additionally describe its encoding, its channel count, when it
was captured (true end-of-speech is the origin of the I1 measurement) and its ordering. None of
that exists.

## Decision

### AD1 — `AudioFrame` is extended additively; no second frame type is introduced

`AudioFrame` gains four **optional, keyword-defaulted** fields appended after the existing two.
`data` and `sample_rate` keep their positions, names and meanings, so
`AudioFrame(data=…, sample_rate=…)` is unchanged and `frozen=True, slots=True` is retained:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `encoding` | `AudioEncoding` | `AudioEncoding.OPAQUE` | how `data` is to be interpreted |
| `channels` | `int` | `1` | channel count |
| `captured_at` | `float \| None` | `None` | **monotonic** capture instant |
| `seq` | `int \| None` | `None` | per-stream ordering |

### AD2 — `AudioEncoding.OPAQUE` is the default, and that choice is load-bearing

`AudioEncoding` is a `StrEnum` added to the `core` floor. It is a **definition**, not behaviour,
so `core/__init__.py`'s *"This package contains no behaviour"* rule holds.

Its default member is **`OPAQUE`**, not `PCM16`. A `PCM16` default would make every local
text-loopback frame *falsely declare* itself as PCM — precisely the dishonesty the capability
conformance suite exists to catch (ADR-0045 **SD9**). `OPAQUE` is also the word `core/audio.py`
already uses for the local/dev payload, so the local providers become truthful with **zero
changes**. Real providers declare their encoding explicitly.

### AD3 — Sample rate is a session property, end to end

The session sample rate flows `StartSessionRequest → AgentSpec → RuntimePipeline`. The
`sample_rate: int = 16000` default on `RuntimePipeline` ceases to be the operative value; the
orchestrator passes the session's rate explicitly. This discharges the AR-41 clause *"sample rate
is a session property, never an ambient assumption."*

### AD4 — Exactly one ingress and exactly one egress resampling point, owned by `runtime/pipeline/audio/`

A first-party module `runtime/pipeline/audio/` owns normalization. `RuntimePipeline` invokes it
at exactly two sites:

```
transport.inbound() ─► [INGRESS RESAMPLE] ─► _tap_voice_activity ─► stt.transcribe
      tts.synthesize ─► [EGRESS RESAMPLE] ─► transport.send
```

**Providers never resample** — the rule `core/audio.py` already states becomes enforceable.

`runtime/pipeline/` is the correct owner because it is the only location satisfying every rule at
once: **AR-10** (`core` holds no behaviour, so the resampler cannot live there), **AR-11**
(`providers/` must not resample), **AR-41** (exactly one point each), and **§A14** (which assigns
the media pipeline to `runtime/pipeline/`). P-ADR-0002 retires the substrate binding, so this
directory is first-party and unreserved.

### AD5 — The ingress resampler sits *before* the voice-activity tap

VAD and STT must observe **identical normalized frames**. Today `_tap_voice_activity` yields
byte-identical frames onward; that invariant is preserved by normalizing before the tap, not
between the tap and STT.

### AD6 — Media buffers are bounded, and capacity is expressed in milliseconds of audio

Every media-path queue is a fixed-size buffer with a **declared** overflow policy (§A8, AR-40).
Capacity is expressed in **milliseconds of audio**, never in frame count: a frame count is
meaningless across sample rates and encodings. The transport declares its native frame size; the
per-agent audio configuration owns capacity and is **validated at configuration write time**
(AR-22; §A8 *"audio configuration is per-agent and validated"*).

### AD7 — Overflow semantics differ by direction, and the difference is deliberate

| Direction | Policy | Why |
|---|---|---|
| **Inbound** (caller → VAD/STT) | **`DROP_OLDEST`** | Stale caller audio is worthless in a live conversation; keeping the freshest preserves responsiveness. |
| **Outbound** (TTS → transport playback) | **Bounded back-pressure — never drop** | Dropping playback frames produces audible mid-sentence gaps. Back-pressure naturally throttles TTS generation. Exceeding the declared bound is a **degradation** (AD8), not a silent drop. |

The **shape** of this mechanism follows an existing first-party precedent rather than inventing
one: `platform/gateway/stream.py` already implements *"one client's ordered, bounded,
non-blocking delivery buffer"* with `OverflowPolicy{DROP_OLDEST, DROP_NEWEST, DISCONNECT}`, a
`dropped` counter and an `AcceptResult`. Epic 11 reuses that **pattern**; it does not reuse the
class, because the gateway buffer delivers event envelopes to clients and media semantics are
materially different.

### AD8 — Overflow is recorded as a degradation, observed at most once per turn, and never raises

An overflow episode increments a counter and is recorded as a declared degradation under
**AR-24**. It emits **at most one event per turn, never per frame**: ADR-0038 **TP10** established
that `InProcessEventBus.publish()` is synchronous run-to-completion and runs every subscriber on
the caller's stack, so per-frame publication inside the media path is the same defect at higher
frequency.

**No overflow condition ever raises into the conversation pipeline.** It degrades and records, on
the `_tap_voice_activity` / `_emit` precedent already in `RuntimePipeline` (DL-0013).

## Consequences

- M11.2 implements AD1–AD5; M11.2 also corrects the now-stale `runtime/pipeline/__init__.py`
  docstring, which still reads *"no substrate import yet; the substrate is wired in at M2."*
- The local text-loopback profile must remain **byte-identical**; G3 makes this an exit criterion.
- `core` gains one `StrEnum` definition and no behaviour.
- `LocalTransport`'s unbounded `asyncio.Queue` is replaced by a bounded buffer, removing a
  standing AR-40 / §A11.1.7 violation.
- Bounded buffers make `channels` and `captured_at` meaningful; `captured_at` becomes the origin
  of the true-end-of-speech instant that ADR-0047 depends on.

## Alternatives considered

- **A companion media frame type alongside `AudioFrame`.** Rejected: forks four capability
  contracts and ten call sites, and every stage would need to accept both.
- **A nested `AudioFormat` value object referenced from `AudioFrame`.** Rejected: it allocates a
  second object **per frame at media rate**, a real cost against I1 for three scalars.
- **`PCM16` as the default encoding.** Rejected — see AD2.
- **A resampler inside each provider.** Rejected: contradicts `core/audio.py`'s own rule and
  would produce N conversion points where AR-41 permits two.
- **A resampler in `core`.** Rejected: `core/__init__.py` states the package contains no
  behaviour.
- **Uniform `DROP_OLDEST` in both directions.** Rejected: audible mid-sentence gaps (AD7).
- **Unbounded outbound buffering.** Rejected outright by AR-40 and §A11.1.7.

## Evidence required later

1. A test proving the local profile's frames are unchanged and the 5447 floor holds.
2. A test proving exactly two conversion sites exist, and that VAD and STT receive identical
   frames.
3. Saturation tests in both directions asserting the declared policy, the counters, one event per
   turn, and that no exception reaches the pipeline.
4. A conformance assertion that a declared `encoding` matches the bytes carried.
