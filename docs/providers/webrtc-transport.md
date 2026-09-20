# WebRTC Transport

**Status:** Active v1.0 (2026-09-07) · **Owner:** Technical Owner · **Milestone:** M11.5
**Authority:** [P-ADR-0002](../adr/P-ADR-0002-implementation-profile-epic11.md), [ADR-0044](../adr/ADR-0044-audio-representation-resampling-and-media-buffers.md), [ADR-0046](../adr/ADR-0046-media-path-fault-tolerance-and-provider-mechanism-reuse.md), [ADR-0049](../adr/ADR-0049-provider-credential-injection.md), AR-6, AR-12, AR-20, AR-23, AR-24, AR-40

The first **real** media transport. Before M11.5 every frame in this system was a symbol.

## Architecture

```
WebRTC peer ──► AiortcMediaEndpoint ──► WebRTCTransport ──► Transport.inbound()
                (the only aiortc          (first-party:            │
                 importer in the repo)     lifecycle, AR-6,        ▼
                                           buffering)      RuntimePipeline ingress
                                                            → AudioNormalizer
                                                            → VAD / STT
```

Three layers, and the split is the point:

| Layer | Owns | Knows aiortc? |
|---|---|---|
| `aiortc_endpoint.py` | peer connection, Opus decode, `av.AudioFrame` → `AudioFrame` | **yes — and it is the only one** |
| `webrtc.py` | lifecycle, AR-6 facts, bounded buffering, task ownership | no |
| `media_endpoint.py` | the Protocol between them | no |

`WebRTCTransport` implements the **unchanged** `TransportProvider` contract, so the runtime reaches
it exactly as it reaches `LocalTransport`. Nothing in the conversation loop knows a real transport
arrived.

## Dependency

**`aiortc`**, chosen as a *narrow rented commodity* under P-ADR-0002 and confined by **AR-12** — not
a substrate. Pipecat stays retired; there is no Pipecat import anywhere in the production path.

Resolution added **12 packages (35 → 47)**: `aiortc`, `av`, `aioice`, `pylibsrtp`, `cryptography`,
`pyopenssl`, `cffi`, `pycparser`, `pyee`, `dnspython`, `google-crc32c`, `ifaddr`. All licences are
permissive and commercial-compatible (BSD-3-Clause, MIT, MIT-0, Apache-2.0, ISC). `pip-audit`
reports **no known vulnerabilities**.

The import is **lazy**, inside the endpoint's constructor: the tree carries native extensions
(`av` → ffmpeg, `cryptography`, `pylibsrtp`), and paying for them at module scope would make
`providers.transport` unimportable wherever the wheels are unavailable — including in the
deterministic tests, which need no library at all.

## Media format — read, never assumed

WebRTC carries Opus; aiortc decodes before we see it. Every field is taken from the frame:

| Field | Source |
|---|---|
| `sample_rate` | `frame.sample_rate` (48 kHz is the WebRTC norm) |
| `channels` | the decoded layout |
| `encoding` | `PCM16`, **asserted** against the decoded format — a non-PCM16 frame is refused, not reinterpreted |
| `captured_at` | a **monotonic** instant at receipt (a wall clock here would be a correctness bug for end-of-speech) |
| `seq` | a per-session counter; RTP sequence numbers are not exposed at this layer |

Packetisation is assumed at **20 ms**, declared as `NOMINAL_FRAME_MS` rather than inferred, and used
only to convert millisecond capacities into queue depths.

**The session's canonical rate is a separate contract.** WebRTC's 48 kHz is not assumed to equal it.
Any difference is resolved by the runtime's single resampler (ADR-0044 **AD4**) — the transport
adds no second one and performs no conversion.

## AR-6 session-reality events

Four additive `EventType` members. `SESSION_*` describes what the Orchestrator *requested*;
`transport.*` describes what the transport *did*. Collapsing them would destroy the distinction
AR-6 exists to protect.

| Event | Emitted when | Never emitted for |
|---|---|---|
| `transport.connected` | `endpoint.connect()` returned | `start()` being called |
| `transport.media_ready` | the **first real inbound frame** arrived | an established connection with no media |
| `transport.disconnected` | the media path ended naturally | a fault |
| `transport.failed` | connect failed, the media path broke, or a send failed | a clean hangup, or cancellation |

Ordering, proven by test:

```
start() requested → CONNECTED → MEDIA_READY → …media… → DISCONNECTED
                              ↘ FAILED (connect / mid-stream / send)
```

**`MEDIA_READY` fires on the first frame, not on `start()`** — the difference between *"a
connection opened"* and *"audio is flowing"*.

**Disconnect vs failure** is decided by how the endpoint's inbound iterator ends: returning
normally means the path ended (hangup); raising means it broke (fault). That distinction is why
`MediaEndpointError` exists, and why an unmapped library exception is still reported as `FAILED`
rather than arriving as ordinary stream exhaustion — the pipeline reads exhaustion as caller
hangup (ADR-0045 SD4).

`LocalTransport` does **not** implement `ObservableTransport` and emits none of these. The loopback
never claims a connection it does not have.

## Lifecycle ownership

| Stage | Owner |
|---|---|
| transport construction | `bootstrap` (AR-20 — never the Orchestrator) |
| peer/media setup, negotiation | the media endpoint |
| start / stop / inbound / send | `WebRTCTransport` |
| conversation lifecycle | the runtime, unchanged |

`start` is idempotent — a second call is not a second connection. `stop` is idempotent and
deterministic: cancel the reader, close the endpoint, release both queues. That order matters —
closing first would let the reader observe a dying endpoint and report a spurious `FAILED`.

**No task survives shutdown.** The inbound reader is held by an explicit handle, cancelled and
awaited (§A11.1.7 bans fire-and-forget tasks). A consumer parked on an empty queue is released
rather than left waiting on a dead session.

## Buffering and backpressure (ADR-0044 AD6/AD7, AR-40)

| Direction | Bound | Overflow |
|---|---|---|
| Inbound | `INBOUND_CAPACITY_MS = 2000` | **DROP_OLDEST**, counted via `dropped(session_id)` |
| Outbound | `OUTBOUND_CAPACITY_MS = 1000` | **back-pressure — never drops** |

Capacities are in **milliseconds of audio**; a frame count is meaningless across sample rates and
encodings. Outbound never drops because a gap mid-sentence is audible, so the producer waits.

One subtlety worth stating: the end-of-stream sentinel is delivered even when the inbound queue is
full, evicting a stale frame to make room. A lost frame is recoverable; a lost terminator strands
every consumer forever.

## Fault tolerance (AR-23 / AR-24)

No WebRTC-specific breaker, failover controller or registry exists. The transport is a capability
like any other, so `ProviderGuard` wraps it unchanged. Library exceptions are mapped **at the
plugin boundary** into `core.errors` — `MediaEndpointError` becomes `ProviderUnavailable`, and no
aiortc exception travels further. `CancelledError` remains cancellation: cancelling `start()`
reports no transport fact at all.

## Configuration

```python
bootstrap.build_session_controller(
    transport=bootstrap.TRANSPORT_WEBRTC,
    media_endpoint=AiortcMediaEndpoint(signaller),
)
```

The default is **unchanged**: `TRANSPORT_LOCAL`. Selecting `webrtc` **requires** a media endpoint —
there is no implicit fallback, because a caller who asked for real media and silently received the
loopback would have been told a lie about their own configuration.

## Credentials

`AiortcMediaEndpoint` takes an injected **signaller**: how the offer/answer exchange happens (HTTP,
WebSocket, hosted room) is a deployment concern, not a transport one. M11.5 therefore introduces no
credential handling of its own — there is nothing to inject yet — and deliberately does not invent
any. When signalling needs credentials, they arrive through the **ADR-0049** seam, and no
credential, token or SDP ever enters an event payload, log or error (CR5/CR6).

## What M11.5 does not do

No real STT, TTS, LLM or VAD. No Playback Engine or Scheduler. No audio-driven endpointing. No
Epic 10 spine wiring (ADR-0050 SW1 — that is M11.12). No latency baseline or Stage-2 deployment
(M11.13). **No performance or conversational-quality claim is made**: this milestone proves a media
boundary, not a working conversation.
