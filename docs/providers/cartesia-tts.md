# Cartesia Streaming TTS

**Status:** Active v1.0 (2026-09-18) · **Owner:** Technical Owner · **Milestone:** M11.10
**Authority:** [ADR-0045](../adr/ADR-0045-streaming-adapter-pattern-and-vad-execution.md) SD5/SD6, [ADR-0044](../adr/ADR-0044-audio-representation-resampling-and-media-buffers.md) AD4, [ADR-0046](../adr/ADR-0046-media-path-fault-tolerance-and-provider-mechanism-reuse.md), [ADR-0048](../adr/ADR-0048-llm-tts-clause-pipelining-and-playback.md) PB3, [ADR-0049](../adr/ADR-0049-provider-credential-injection.md), [P-ADR-0002](../adr/P-ADR-0002-implementation-profile-epic11.md) §3, AR-11, AR-12, AR-21, AR-23, AR-25, AR-41
**Decisions:** DL-0192 (dependency and licensing), DL-0193 (design), DL-0196 (limitations and risks)

> **TTS converts provider-neutral text into provider-neutral audio; the Playback Scheduler decides
> when and in what order that audio is played.** This provider produces the audio. It queues
> nothing, orders nothing and sends nothing — see [playback](../runtime/playback.md).

## Architecture

```
PlaybackScheduler
        │  one clause of text
        ▼
ProviderGuard("tts")  ── deadline · typed errors · breaker (AR-23, unchanged)
        ▼
CartesiaTTS.synthesize()  ─────► AudioFrame(PCM16, declared rate, mono) per 20 ms
        │  SpeechSpec / SpeechChunk — raw bytes only
        ▼
CartesiaSpeechClient ──► cartesia.AsyncCartesia  ◄── the only SDK import in the repository
```

| Layer | Owns | Knows the SDK? |
|---|---|---|
| `providers/tts/cartesia_client.py` | the `cartesia` import (lazy), client construction, SSE event reading, base64 decoding, error classification | **yes — and it is the only one** (AST-asserted) |
| `providers/tts/cartesia.py` | format contract, re-framing, typed failures, stream lifecycle, capabilities | no |
| `providers/tts/speech_stream.py` | the neutral seam between them | no |

`CartesiaTTS` implements the **unchanged** `TTSProvider.synthesize` contract.

## Library

- **Package:** `cartesia` 4.2.0 (Cartesia, Inc.), **Apache-2.0**.
- **New transitive dependencies: none.** Every one of `anyio`, `distro`, `httpx`, `pydantic`,
  `sniffio` and `typing-extensions` was already in the tree after M11.9. 52 → **53** locked
  packages — exactly one. `pip-audit`: no known vulnerabilities.
- **No framework was added.**

## Why SSE, and why `streaming_input=False`

Cartesia's WebSocket transport is the one that would accept text *as it is produced*. It is an
optional extra pinned to `websockets<16`, and the platform already locks `websockets` 17.1 for
Deepgram (M11.6). Resolving `cartesia[websockets]` against that pin was **attempted**, and it
downgrades Cartesia two major versions to **2.0.17** while adding aiohttp, pydub, multidict,
propcache and yarl.

So the adapter uses SSE. Audio output is genuinely streamed; text *input* is one utterance per
call, and `capabilities.streaming_input` declares **False** honestly rather than emulating it. The
runtime then takes ADR-0048 PB3's documented path: synthesize clause by clause and **record the
degradation** (AR-24). This is a real limitation, tracked as **R-59**.

## Audio format and where normalization happens

| Property | Value |
|---|---|
| requested container/encoding | `raw` / `pcm_s16le` — bit-identical to `AudioEncoding.PCM16` |
| sample rate | the provider's configured rate; vendor supports 8000/16000/22050/24000/44100/48000 |
| channels | 1 (mono) |
| emitted frame duration | **20 ms** — the platform's media cadence |

**No resampling happens in this plugin.** The provider asks the vendor for the rate it is
configured with and declares it; if the session runs at a different rate, the runtime's
`AudioNormalizer` converts at the **single egress point** (ADR-0044 AD4, AR-41). Configuring the
provider's rate to match the session's simply leaves the normalizer with nothing to do. There is
exactly one resampler in the platform and it is not here.

Vendor chunk boundaries have nothing to do with sample boundaries, so the provider re-frames:
partial samples are carried across chunks, and a stream that ends mid-sample is reported as
`ProviderStreamTruncated` rather than rendered as noise.

## Failures and degradation

| Cause | Surfaces as |
|---|---|
| request deadline exceeded | `ProviderTimeout` |
| vendor unreachable, 5xx | `ProviderUnavailable` |
| credential rejected (401/403) | `ProviderAuthError` |
| rate limited (429) | `ProviderRateLimited` |
| malformed request (400/404/422), undecodable base64, vendor `error` event | `ProviderProtocolError` |
| stream ended mid-sample, or produced no audio at all | `ProviderStreamTruncated` |

Classification is by **exception class and HTTP status, never message text**. Vendor text is never
passed through (ADR-0049 CR5/CR6). `asyncio.CancelledError` is never translated: a barge-in is not
the vendor failing. **No TTS-specific breaker exists** — `ProviderGuard` governs it exactly as
M11.4 established, and the SDK's own retries are disabled (`max_retries=0`) so there is one
retry authority.

A synthesis that returns **no audio at all** is an error, not a silent utterance: letting it pass
would leave the assistant mute while every subsystem reported success.

### A documented limitation of failover

`call_with_failover` selects a candidate before a call begins. Once audio has been spoken, no
provider switch can be transparent — a second voice would resume a sentence it did not start, mid
word. M11.10 therefore does not attempt mid-utterance failover and does not invent resume
semantics for it (**R-61**).

## Configuration

```python
bootstrap.build_registry(tts="cartesia", tts_voice="<vendor voice id>", credentials=…)
bootstrap.build_session_controller(tts="cartesia", tts_voice="…", credentials=…)
```

- **Default unchanged: `local`.** `LocalTTS` remains the default for every existing caller.
- `"cartesia"` resolves exactly one credential — `cartesia_api_key`
  (`VOXVOICE_SECRET_CARTESIA_API_KEY`) — at the composition root and nowhere else (CR3/CR4). The
  plugin never reads the environment (AST-asserted).
- A **voice is required**. Cartesia has no meaningful default, and a request without one is
  refused by the vendor mid-call; refusing at startup turns a caller-visible failure into a
  configuration error.
- Missing credential, missing voice, or a host without the SDK → **`ValueError` at startup**.
  **There is no fallback to `LocalTTS`**: an operator who asked for a real voice and silently got
  the text-loopback codec would be told a lie about their own configuration.
- `tts_client=` is the hermetic test seam (a deterministic client; no SDK, no credential).

## Declared capabilities

| Flag | Value | Why |
|---|---|---|
| `streaming` | `True` | audio is yielded as the vendor produces it |
| `streaming_input` | `False` | see above — declared, not emulated |
| `markup` | `False` | no SSML is sent; claiming it would let markup arrive as spoken brackets |
| `voices` | `()` | voices are opaque vendor ids an operator configures; the catalogue changes without a release of ours |
| `sample_rates` | the vendor's raw-PCM rates | taken from the SDK's own type, not from a doc page |

## Known limitations

- **No live call was ever made** (R-58). Every behaviour is proven against a deterministic fake and
  against the installed SDK's own classes. Real chunk cadence, error bodies and rate-limit
  behaviour are unverified.
- No audio-quality, naturalness, latency or throughput claim of any kind.
- Per-utterance text input only (R-59); no mid-utterance failover (R-61).
- Hinglish and multilingual quality are **not** claimed (R-47 stands).
