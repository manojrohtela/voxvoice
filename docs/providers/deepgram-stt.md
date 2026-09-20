# Deepgram Streaming STT

**Status:** Active v1.0 (2026-09-14) · **Owner:** Technical Owner · **Milestone:** M11.6
**Authority:** [P-ADR-0002](../adr/P-ADR-0002-implementation-profile-epic11.md), [ADR-0005](../adr/ADR-0005-streaming-first-turn-intelligence.md), [ADR-0044](../adr/ADR-0044-audio-representation-resampling-and-media-buffers.md), [ADR-0045](../adr/ADR-0045-streaming-adapter-pattern-and-vad-execution.md), [ADR-0046](../adr/ADR-0046-media-path-fault-tolerance-and-provider-mechanism-reuse.md), [ADR-0049](../adr/ADR-0049-provider-credential-injection.md), AR-11, AR-12, AR-20, AR-21, AR-23, AR-24
**Decisions:** DL-0178 (dependency), DL-0179 (design), DL-0180 (unverified claims and risks)

The first **real** speech-to-text provider. Before M11.6 every transcript in this system was the
audio frame's own bytes read back as text.

## Architecture

```
Transport.inbound ─► pipeline ingress ─► ProviderGuard.stream ─► DeepgramSTT.transcribe
                     (AudioNormalizer)                             │         ▲
                                                     upload task ──┘         │ receive loop
                                                          │                  │
                                                          ▼                  │
                                            TranscriptionSession (Protocol)  │
                                                          │                  │
                                            DeepgramTranscriptionClient ─────┘
                                            (the only Deepgram SDK importer)
```

| Layer | Owns | Knows the SDK? |
|---|---|---|
| `providers/stt/deepgram_client.py` | SDK import (lazy), connect parameters, message types, close codes, handshake statuses, classification | **yes — and it is the only one** (AST-asserted) |
| `providers/stt/deepgram.py` | duplex, Partial/Final mapping, typed errors, audio-format validation, task ownership | no |
| `providers/stt/transcription_session.py` | the Protocols between them, a neutral `FailureKind` | no |

`DeepgramSTT` implements the **unchanged** `STTProvider.transcribe(frames, *, language,
sample_rate)` contract. No core contract changed at M11.6.

## Streaming semantics

- **Duplex.** One upload task pulls, validates and sends frames; the generator concurrently yields
  results as they arrive. Neither side waits for the other.
- **Incremental.** A Partial for frame 1 is yielded before frame 2 exists — proven by a test whose
  audio source is gated by the test itself, and mutation-checked: a batch-emulating variant fails it.
- **Partial stays Partial, Final stays Final.** `Final` only for a vendor result marked `is_final`.
  Nothing is promoted at end-of-stream, timeout or close (mutation-checked).
- **Empty results are dropped.** The pipeline treats every Partial as *user is speaking*; forwarding
  the vendor's empty silence results would fabricate speech.
- **`stability=0.0`.** The vendor gives confidence, not stability; interim text is revisable.
- **No endpointing.** A `Final` is the vendor's finalized segment. Turn completion is M11.7+.

## Audio format

| Check | When | Failure |
|---|---|---|
| language ∈ declared `capabilities.languages` | before connecting | `CapabilityUnsupported` |
| session rate ∈ `SUPPORTED_SAMPLE_RATES` (8k/16k/24k/32k/44.1k/48k) | before connecting | `CapabilityUnsupported` |
| frame `encoding is PCM16` | per frame | `CapabilityUnsupported` |
| frame `channels == 1` | per frame | `CapabilityUnsupported` |
| frame `sample_rate ==` stream rate | per frame | `CapabilityUnsupported` |
| even byte count | per frame | `ProviderProtocolError` |

The provider **never resamples** — the runtime's single `AudioNormalizer` owns that (ADR-0044).
Empty frames are skipped, never sent (an empty binary message is Deepgram's legacy close signal).

## Failure mapping

| Cause | Neutral `FailureKind` | Raised |
|---|---|---|
| handshake deadline (10 s bound), HTTP 408, close 1011 `NET-0001` | `TIMEOUT` | `ProviderTimeout` |
| unreachable, HTTP 402/5xx, close 1011 | `UNAVAILABLE` | `ProviderUnavailable` |
| HTTP 401/403 | `AUTH` | `ProviderAuthError` |
| HTTP 429 | `RATE_LIMITED` | `ProviderRateLimited` |
| other HTTP 4xx, close 1003/1007/1008/1009, undecodable message | `PROTOCOL` | `ProviderProtocolError` |
| abnormal close (1001/1006/…), or vendor ends before the audio | `TRUNCATED` | `ProviderStreamTruncated` |

Vendor exceptions are classified and discarded (`from None`), never chained: the SDK's handshake
errors carry request headers. Vendor close *reasons* are used only to classify and are never
surfaced. `asyncio.CancelledError` is never translated. Breaker, per-item deadline and degradation
recording are `ProviderGuard`'s — the pipeline already wraps every `transcribe()` call, and a typed
failure through it is recorded as `UNAVAILABLE` degradation (AR-24, DL-0177).

## Bounded resources, deterministic cleanup

- **No queue** between caller and vendor: at most one frame in flight on the upload side;
  backpressure reaches the transport's bounded inbound buffer.
- Receive side bounded by the websocket client's incoming buffer: **32 messages**, each at most
  **1 MiB** (websockets `legacy` client defaults, which SDK 7.8.1 uses); beyond that the socket
  stops reading and TCP applies backpressure to the vendor. Neither side drops anything, so no
  overflow degradation exists to record; the only drop policy on the media path remains the
  transport's (M11.5, drop-oldest, 2000 ms).
- On every exit path — clean end, typed failure, cancellation, early `aclose()` — the upload task
  is cancelled and **awaited** (`asyncio.wait`, so the caller's own cancellation still propagates)
  and the session is closed. Tests assert no `deepgram-stt-upload` task survives.

## Selection and credentials

```python
bootstrap.build_registry(stt="deepgram", credentials=<CredentialProvider>)
bootstrap.build_session_controller(stt="deepgram", credentials=<CredentialProvider>)
```

- The default profile is **unchanged**: `stt="local"`.
- `bootstrap.py` resolves exactly one name, `deepgram_api_key`, through the injected ADR-0049
  `CredentialProvider` (`VOXVOICE_SECRET_DEEPGRAM_API_KEY` under `EnvCredentialProvider`) and passes
  the **resolved value** to `DeepgramTranscriptionClient` (CR3/CR4). The plugin never reads the
  environment (AST-asserted).
- No credential provider, or no credential → `ValueError` naming the credential (never its value),
  **no fallback to `LocalSTT`**.
- `ChainedCredentialProvider` rotation works unchanged.
- The key is held in a name-mangled attribute and redacted from `repr`.

## Testing

| File | What it proves |
|---|---|
| `tests/test_deepgram_stt.py` | contract, capabilities, incremental duplex, mapping, validation, every failure class, cancellation, cleanup, guard/degradation reuse, AST boundaries |
| `tests/test_deepgram_client.py` | adapter classification and redaction against a stand-in SDK in `sys.modules` |
| `tests/test_deepgram_bootstrap.py` | selection, refusal, least-privilege resolution, end-to-end WebRTC → STT → transcripts |
| `tests/test_conformance_capabilities.py` | Deepgram judged by the shared STT suite (probed with PCM16 via `ProviderUnderTest.frame`) |
| `tests/fake_transcription.py` | the deterministic vendor socket all of the above share |

No test needs a real credential, the network, or the SDK installed.

## What is not claimed

See DL-0180. In short: **no live Deepgram call has been made.** Close codes, handshake statuses and
message order are implemented from the SDK source and vendor documentation, not observed.
**Hinglish is not declared** (`languages=("en",)`); P-ADR-0002 already names Sarvam for Hinglish.
The platform's default session language `en-IN` is therefore refused by this profile (R-37).
