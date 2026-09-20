# The End-to-End Voice Conversation

**Status:** Active v1.0 (2026-09-18) · **Owner:** Technical Owner · **Milestone:** M11.11
**Authority:** Constitution §A4.2, §A8; [ADR-0047](../adr/ADR-0047-audio-driven-endpointing-and-metric-turn.md), [ADR-0048](../adr/ADR-0048-llm-tts-clause-pipelining-and-playback.md), [ADR-0049](../adr/ADR-0049-provider-credential-injection.md), [P-ADR-0002](../adr/P-ADR-0002-implementation-profile-epic11.md); AR-20, AR-21, AR-23, AR-25, AR-41
**Decisions:** DL-0197 (composition), DL-0198 (R-50 response dispatch), DL-0199 (greeting is not a turn), DL-0200 (limitations and risks)

> **VAD is speech detection; endpointing decides when a speech turn ends.**
>
> **TTS converts provider-neutral text into provider-neutral audio; the Playback Scheduler decides
> when and in what order that audio is played.**

M11.11 is the first milestone where every Epic 11 component runs as one conversation. It builds no
new subsystem — it establishes the composition, fixes the two integration defects that made the
path unexecutable, and proves the whole chain hermetically.

## The runtime path

```
caller ─► WebRTC ─► AudioFrame ─► [ingress normalize] ─► VAD tap ─► STT
                                        │                            │
                                        ▼                            ▼
                             AudioEndpointDetector            Partial / Final
                                        │                            │
                                 speech_start / speech_end           │
                                        ▼                            ▼
                                 EndpointCoordinator ──► Turn Engine ──► response
                                                                          │
                                LLM stream ─► ClauseDetector ─► Clause ───┘
                                        │
                                        ▼
                         PlaybackScheduler ─► TTS ─► AudioFrame
                                        │
                                        ▼
                            PlaybackEngine (bounded, ordered)
                                        │
                              [egress normalize] ─► guarded transport ─► caller
```

Every arrow is an existing contract. Nothing bypasses the Turn Engine, endpointing,
`ProviderGuard`, `ProviderRegistry`, the clause detector, the Scheduler or the Engine.

## Composing the real stack

```python
controller = bootstrap.build_real_session_controller(
    credentials=EnvCredentialProvider(),       # ADR-0049; resolves three names, nothing else
    media_endpoint=AiortcMediaEndpoint(...),   # the real WebRTC peer
    tts_voice="<cartesia voice id>",
)
```

`REAL_PROVIDER_PROFILE` names what "the real stack" means in exactly one place:

| Capability | Profile | Credential |
|---|---|---|
| Transport | `webrtc` | — (a media endpoint is supplied) |
| STT | `deepgram` | `deepgram_api_key` |
| Voice activity | `silero` | — (local model) |
| LLM | `groq` | `groq_api_key` |
| TTS | `cartesia` | `cartesia_api_key` + a voice id |
| Response path | `streaming_playback=True` | — |

**Nothing falls back.** Each profile refuses to start without its credential, so a missing key is
a startup error and never a quiet downgrade to a local provider — asserted directly.

**Defaults are untouched.** `build_session_controller()` with no arguments still assembles the
entirely local profile with `streaming_playback=False`, so no existing deployment changes
behaviour because this composition exists.

### Why `streaming_playback` lives on `WorkerConfiguration`

It is a property of *how the runtime was composed*, not of the agent a customer configured. The
same agent must behave identically whichever deployment runs it, and putting a runtime-composition
detail on `AgentSpec` would place it on the public Control Plane contract. So it sits on
`WorkerConfiguration` beside the other worker-level lifecycle settings, set by the composition
root and reachable by no session request.

## Two integration defects M11.11 had to fix

Both are category-1 under the milestone rule — without them the path does not execute.

### 1. A pending transcript was never answered (closes R-50)

M11.8 left the response to be triggered *by a transcript*, assuming a Final arrives after end of
speech. Real recognizers finalize as soon as the caller stops — before the 240 ms trailing-silence
hold elapses — so by the time audio closed the turn the transcript was **already pending and
nothing was left to trigger it**. The caller was answered only at the next utterance or at hangup.

`_close_audio_turn` now *schedules* the response (through the same `_start_response` the endpoint
pathway already uses) when a transcript is pending. Endpointing still invokes no LLM or TTS
inline — scheduling is what keeps the inbound audio path non-blocking, since this runs inside the
VAD tap. No M11.8 semantics change: same turn, same single response, same boundaries.

**R-50 is closed**, with a two-turn conversation as the evidence.

### 2. The greeting is not a turn (M11.10 defect)

M11.10's Scheduler fired the response's first-audio callback for the greeting too. At session
start that callback advances the Turn Engine to `ASSISTANT_SPEAKING` — and there is no turn yet,
so the first greeted session raised `NoActiveTurnError` and failed to start.

The greeting no longer stamps `tts_first_audio` and no longer drives turn transitions. That is
also the correct metric semantics: a greeting has no `speech_end` to be measured from, so stamping
a turn boundary for it would be precisely the synthetic timing §A8's chain must never carry.

## Greeting behaviour

`AgentSpec.first_message` → `PlaybackScheduler.precache_greeting()` → `play_greeting()`, at
session start, **only** when the session runs the playback path. Bounded, cancellable, played
exactly once, through the Scheduler and Engine to the transport like any other audio. A greeting
that cannot be synthesized is **not fatal**: `GreetingUnavailable` is caught, the caller is simply
not greeted, and the conversation proceeds.

`GreetingState` keeps its existing vocabulary — no enum member was added, because that is a live
symbol an Epic 10 historical count could observe (R-62).

## metric.turn

The chain and every key name are M11.8's, unchanged; no new EventType, no Epic 10 write.

`speech_start → speech_end → endpoint_decided → stt_final → llm_first_token → tts_first_audio →
first_audio_out`, proven stamped by the real path, in order, **per response** — turn 2 stamps its
own boundaries. A response that produces no audio stamps neither audio boundary, and back-pressure
moves `first_audio_out` rather than being papered over.

## Hermetic end-to-end tests

`tests/test_e2e_conversation.py` drives the **production runtime** — `RuntimeSessionService` →
`SessionWorker` → `ConversationOrchestrator` → `RuntimePipeline` — using the real provider
*classes* (`DeepgramSTT`, `SileroVoiceActivity`, `GroqLLM`, `CartesiaTTS`, `WebRTCTransport`) over
deterministic seams that stand in only for the **vendors**: a socket, a recognizer, a model, a
voice. The fakes simulate provider behaviour, never runtime behaviour.

`tests/fake_conversation.py` adds the two things a *conversation* needs that per-component fakes
did not: a media endpoint a test speaks into over time, and a recognizer that finalizes when the
caller stops — the way a real one does.

The harness waits on the **response lifecycle**, not on a fixed number of event-loop turns. The
first version used a spin budget and failed exactly as that deserves: passing alone, failing beside
another test file.

No network, no credentials, no vendor SDK — asserted structurally over the harness's own AST.

## Live smoke test — **NOT EXECUTED**

No provider credential is configured in this environment (`VOXVOICE_SECRET_*` is unset and there is
no `.env`), so **no live call was made to Deepgram, Groq or Cartesia, and no real WebRTC peer was
established.** Nothing in this milestone claims otherwise. R-54 and R-58 therefore remain open, and
a new R-63 records that the integrated path itself is unproven against live vendors.

To execute it later:

```bash
export VOXVOICE_SECRET_DEEPGRAM_API_KEY=…
export VOXVOICE_SECRET_GROQ_API_KEY=…
export VOXVOICE_SECRET_CARTESIA_API_KEY=…
# plus a reachable WebRTC peer for AiortcMediaEndpoint, and a Cartesia voice id
```

then compose with `bootstrap.build_real_session_controller(...)` and place one short spoken
exchange through it. What that would prove and what it would not: it establishes that the vendors
behave as their SDKs' types suggest; it establishes **nothing** about latency, audio quality,
naturalness, reliability or scalability, none of which M11.11 measures.

## What M11.11 did not do

No Epic 10 spine wiring, no telemetry, no metering or billing, no performance or production
certification, no resilience ladder, no filler audio, no crossfade, no speculative parallel
synthesis, no LLM pre-warming. No subsystem was redesigned.
