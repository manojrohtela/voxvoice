# Providers — Local Development Profile (M2C.2)

| | |
|---|---|
| **Status** | Implemented (M2C.2) · **Owner:** Technical Owner |
| **Location** | `platform/providers/{transport,stt,llm,tts}/` · registered by `bootstrap.build_local_registry` |

The first provider profile: a complete, in-process, deterministic implementation
of every capability the pipeline needs, so VoxVoice runs a real end-to-end
conversation with **no network and no credentials**. These are first-class
local-dev providers (not stubs) — a real vendor is a drop-in replacement behind
the same `core.providers` contract (DL-0009).

| Capability | Class | Behaviour |
|---|---|---|
| Transport | `providers.transport.local.LocalTransport` | Multi-session loopback keyed by `session_id`: per-session inbound queue (`feed`/`hangup`) + captured outbound (`sent`). The driver/test is the caller. |
| Speech-to-Text | `providers.stt.local.LocalSTT` | Deterministic: each non-empty audio frame carries its transcript as UTF-8; emits a Partial then a Final (one completed utterance per frame). |
| LLM | `providers.llm.echo.EchoLLM` | Streams a deterministic response derived from the last user message (`You said: …`), token by token. |
| Text-to-Speech | `providers.tts.local.LocalTTS` | Emits one audio frame carrying the utterance text (text loopback codec). |

## Contract refinement (M2C.2)
The M1 provider contracts were non-executable "interface essences" (STT had no
audio input). They were refined to execution-ready shapes — `AudioFrame`
(`core.audio`), `Transport.inbound`/`send` (session-scoped), `STT.transcribe`
(consumes an audio-frame stream), `TTS.synthesize` (utterance → frames). Per
Constitution §A6 these describe *essence*, not frozen signatures, and are
unreleased (no external consumers), so this is a refinement, not a versioned break.

## Replaceability
Real vendor plugins (streaming STT, an LLM vendor, a TTS vendor, WebRTC/telephony
transport) register into the same `ProviderRegistry` under the same capability
kinds — no runtime, orchestrator, or pipeline change. Registration lives only in
`bootstrap` (the composition root). Real vendor plugins are deferred until the
exposed keys are rotated (DL-0002).

## Rules honoured
No vendor SDK import in runtime code (AR-21); providers import only `core`
(AR-11); providers are resolved exclusively via the registry (never constructed
in runtime code). Local providers are stateless per call except the transport,
which is a per-session-keyed multi-session service.
