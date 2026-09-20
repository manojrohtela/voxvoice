# Silero Streaming VAD

**Status:** Active v1.0 (2026-09-14) · **Owner:** Technical Owner · **Milestone:** M11.7
**Authority:** [ADR-0045](../adr/ADR-0045-streaming-adapter-pattern-and-vad-execution.md) SD7/SD8/SD9, [ADR-0044](../adr/ADR-0044-audio-representation-resampling-and-media-buffers.md), [ADR-0046](../adr/ADR-0046-media-path-fault-tolerance-and-provider-mechanism-reuse.md), [P-ADR-0002](../adr/P-ADR-0002-implementation-profile-epic11.md), AR-11, AR-12, AR-20, AR-23, AR-24, AR-40
**Decisions:** DL-0181 (dependency and licensing), DL-0182 (design), DL-0183 (limitations and risks)

> **VAD is not endpointing.** This provider answers *"is speech present in the most recently
> analysed audio?"* It creates no turn, closes no utterance, triggers no response and changes no
> metric. Deciding that a user's turn has ended — and remapping `metric.turn` — is M11.8's.

## Architecture

```
Transport.inbound ─► AudioNormalizer ─► VAD tap ─► VoiceActivityCoordinator ─► detect(frame)
                                                                                   │  O(1)
                                        append PCM to a bounded per-session buffer ◄┘
                                                         │ wake
                          provider-owned inference task ─┘
                                   │ one 512-sample window at a time
                                   ▼
                    session's single worker thread: SpeechModel.probability(window)
                                   │
                                   ▼
                    latest verdict ──► returned by the next detect()
```

| Layer | Owns | Knows the library? |
|---|---|---|
| `providers/voice_activity/silero_model.py` | `pysilero_vad` import (lazy), the fixed window, library errors | **yes — and it is the only one** (AST-asserted) |
| `providers/voice_activity/silero.py` | buffer, windowing, inference task + worker, `detect()`, lifecycle, typed failures, drop accounting | no |
| `providers/voice_activity/speech_model.py` | the `SpeechModel` Protocol between them | no |

`SileroVoiceActivity` implements the **unchanged** `VoiceActivityProvider` contract: `start` and
`stop` stay async, `detect` stays synchronous. The only core change is one additive, optional
Protocol, `ReportsDroppedAudio` (below).

## Library and model

- **Package:** `pysilero-vad` 3.4.0 (rhasspy / Michael Hansen), MIT. **Zero** runtime Python dependencies.
- **Model:** Silero VAD **v6.2.0**, shipped inside the wheel as `ggml-silero-v6.2.0.bin` (885 KB) and
  executed natively by a bundled **ggml** runtime (`libggml*`). No ONNX Runtime, no PyTorch, no NumPy.
- **Format:** 16 kHz, mono, signed 16-bit PCM; fixed 512-sample window; returns a probability in [0, 1].
- Why this rather than `webrtcvad-wheels` (the other narrow option): a real speech probability (the
  engine and interrupt framework consume confidence), a neural model rather than a GMM, and active
  maintenance (last release 2026-07 vs 2024-09). See DL-0181.

## Licensing

| Component | License | Source verified | Notes |
|---|---|---|---|
| `pysilero-vad` wrapper | MIT (© 2025 Michael Hansen) | wheel `LICENSE.md` + upstream repo | |
| Silero VAD model weights | MIT (© 2020-present Silero Team) | upstream `snakers4/silero-vad` LICENSE | **not included in the wheel** |
| ggml runtime | MIT (© 2023-2026 the ggml authors) | upstream `ggml-org/ggml` LICENSE | **not included in the wheel**; built from a modified tree (`0.9.4-dirty`) |
| `libgomp` (Linux wheels only) | GPL-3.0 **with GCC Runtime Library Exception** | bundled `.libs` | exception permits redistribution as a runtime library |

All components are permissive or runtime-excepted, so commercial use is compatible. **Redistribution
caveat:** MIT requires reproducing the copyright notices, and the wheel carries only the wrapper's.
Any artifact we redistribute (container image, installer) must include the Silero and ggml notices
ourselves. This is recorded as R-43 and is **not** a claim of completed legal clearance.

## Audio format

| Check | When | Failure |
|---|---|---|
| encoding is PCM16 | every `detect()` | `CapabilityUnsupported` |
| mono | every `detect()` | `CapabilityUnsupported` |
| sample rate is 16 000 Hz | every `detect()` | `CapabilityUnsupported` |
| even byte count | every `detect()` | `ProviderProtocolError` |
| model reports 16 kHz / 512 samples | `start()` | `CapabilityUnsupported` |

The provider **never resamples**: the runtime's `AudioNormalizer` owns sample-rate conversion
(ADR-0044). A session on this profile must therefore declare `sample_rate=16000`, so that WebRTC's
48 kHz audio is normalized at ingress before the VAD tap (R-44).

## Windowing and buffering

| Term | Value |
|---|---|
| incoming frame duration | any — frames are concatenated, never assumed fixed-size |
| analysis window | 512 samples = **32 ms** exactly (`frame_ms = 32`) |
| hop | 32 ms — contiguous, non-overlapping |
| padding | none — a partial window waits; it is discarded at `stop()` |
| max buffered audio | **320 ms** (10 windows = 10 240 bytes) per session |
| overflow | oldest audio dropped in one C-level delete; counted in ms |
| inference cadence | as soon as a full window exists, one window at a time, per session |

## Background inference and lifecycle

- `start(session_id)` creates a **single-worker** `ThreadPoolExecutor` for the session, loads the
  model **on that worker** (bounded by a 10 s deadline), and starts one named asyncio task
  (`vad-silero-inference:<session>`). Starting a session twice is a typed error.
- The task waits on an `asyncio.Event` that `detect()` sets, then drains whole windows, awaiting
  `run_in_executor(worker, model.probability, window)` for each. Inference for one session is
  serial (the model is stateful); sessions never share a worker.
- `stop(session_id)` removes the session, **cancels and awaits** the task, and shuts the worker down
  without waiting. Idempotent; stopping an unknown session is a no-op.
- A model call already executing natively cannot be interrupted. `stop()` does not wait for it; its
  result is discarded, and the worker thread exits when that one call returns (R-45).
- Cancelling `start()` propagates `CancelledError` and registers nothing.

## `detect()` semantics

- Returns the verdict of the **most recent completed window**:
  `VoiceActivitySample(speech = p >= threshold, confidence = p)`; default threshold 0.5.
- **Startup state:** `speech=False, confidence=0.0` until the first window completes — *no evidence
  of speech yet*, not a claim that inference ran. `windows_analysed(session_id)` reports how many
  windows actually completed.
- **O(1) and non-blocking**, proven three ways: (1) behaviourally, with a model **blocked inside
  inference on its worker thread** while `detect()` is called 50 times under a sentinel that fails on
  sleep, socket or file I/O; (2) a call-count probe constant from 2 B to 1 MiB frames with the buffer
  full; (3) structurally, `detect()` contains no `await`, no loop and no reference to the model.
  A synchronous-inference mutant fails (1) and (3) immediately.

## Failures and degradation

| Cause | Surfaces as | Runtime record (existing M11.4 path) |
|---|---|---|
| library missing when the profile is selected | `ValueError` at **bootstrap** | — (never starts) |
| model cannot load | `ProviderUnavailable` from `start()` | `auxiliary_disabled`, VAD off for the session |
| model load exceeds 10 s | `ProviderTimeout` from `start()` | `auxiliary_disabled` |
| unsupported frame format | `CapabilityUnsupported` / `ProviderProtocolError` from `detect()` | `auxiliary_disabled` per frame; frame passes through |
| inference fails in the background | **sticky** `ProviderUnavailable` from every later `detect()` | `auxiliary_disabled` per frame |
| buffer overflow | counted; `drain_dropped_ms()` via `ReportsDroppedAudio` | **`audio_dropped`** (new `DegradationKind`) |

A failed session never returns a stale verdict. Library exceptions are classified and discarded
(`from None`), so paths and addresses in native error text never surface. VAD remains **auxiliary**
(DL-0013): no failure ends a call. The runtime governs VAD through `ProviderGuard`'s auxiliary
path exactly as M11.4 established; no VAD-specific breaker or failover exists.

### `ReportsDroppedAudio` (additive, `core.providers.voice_activity`)

An optional `runtime_checkable` Protocol with one method, `drain_dropped_ms(session_id) -> float`.
`VoiceActivityCoordinator.drain_dropped_ms()` relays it (0.0 for providers that don't implement it),
and the pipeline's VAD tap records one `audio_dropped` degradation for any frame after which audio
was discarded. `LocalVoiceActivity` is untouched and does not implement it.

## Configuration

```python
bootstrap.build_registry(voice_activity="silero")
bootstrap.build_session_controller(voice_activity="silero")
```

- Default unchanged: **no VAD**. `voice_activity="local"` ≡ the legacy `include_voice_activity=True`.
- `"silero"` checks at bootstrap that `pysilero_vad` is importable; if not → `ValueError`,
  **no fallback** to `LocalVoiceActivity`. Combining it with `include_voice_activity=True` is refused.
- No credential is needed or resolved.
- `speech_model_factory=` is the hermetic test seam (a fake model; no library).

## Existing consumers are unchanged

When a VAD is registered, the orchestrator has always wired its verdicts into the existing
`VoiceActivityEngine`, and from there into the Turn, Endpoint and Interrupt coordinators and the
`speech_end` latency tap. M11.7 does not modify any of those consumers, their thresholds, or
`metric.turn`. They now receive real verdicts instead of text-payload verdicts. How they should
*use* real audio evidence is M11.8's decision (R-46).

## Testing

| File | What it proves |
|---|---|
| `tests/test_silero_vad.py` | contract, capabilities, format, lifecycle, the blocking-inference proof, O(1), windowing, incremental streaming, bounded buffer + drops, failures, cancellation, runtime degradation, AST boundaries |
| `tests/test_silero_model.py` | the library adapter against a stand-in `pysilero_vad` module |
| `tests/test_silero_bootstrap.py` | selection, refusal, local preservation, real PCM through ingress into the existing engine |
| `tests/test_conformance_voice_activity.py` | Silero judged by the shared M11.3 VAD suite |
| `tests/fake_speech_model.py` | energy, blocking, failing and wrong-format models |

No test needs the native library. Outside the suite, the real library was run once through the real
provider on a 5.39 s synthesized-speech clip (269 × 20 ms frames → 168 windows, speech reported,
0 ms dropped, no task left): **a functional check, not a quality or latency result.**

## Known limitations

- 16 kHz only; the session must run at 16 kHz on this profile (R-44).
- No measured false-positive/false-negative rates, and nothing on noisy telephony or Hinglish speech (R-47).
- Per-frame degradation records under a sustained failure (R-48).
- The wheel's ggml build is from a modified tree, and upstream notices are missing (R-43).
- Native calls cannot be interrupted at `stop()` (R-45).
- No latency or CPU claims; baselines are M11.13's.
