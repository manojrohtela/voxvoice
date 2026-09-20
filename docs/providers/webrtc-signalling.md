# WebRTC Signalling and the Real-Peer Path

**Status:** Active v1.0 (2026-09-20) · **Owner:** Technical Owner · **Milestone:** M11.16.3
**Authority:** [ADR-0044](../adr/ADR-0044-audio-representation-resampling-and-media-buffers.md) **AD1/AD4**, [ADR-0046](../adr/ADR-0046-media-path-fault-tolerance-and-provider-mechanism-reuse.md), [ADR-0049](../adr/ADR-0049-provider-credential-injection.md), [P-ADR-0002](../adr/P-ADR-0002-implementation-profile-epic11.md); **AR-6, AR-12, AR-40, AR-41**; R-34
**Companion:** [WebRTC transport](webrtc-transport.md) — the transport's lifecycle and AR-6 facts, unchanged by this milestone.

> **This does not make `E11-15` satisfiable.** It builds the *infrastructure* a live certification
> run needs. `E11-15` additionally requires a provisioned Stage-2 host, vendor credentials, and an
> authorized external peer — none of which exists yet.

## What was missing

M11.5 shipped `AiortcMediaEndpoint` with an **injected** signaller and deliberately no
implementation — a documented decision (**R-34**), not an oversight: how an offer/answer reaches a
peer is a deployment concern. M11.16.2 then established the consequence: there was no way to
obtain a real peer at any price. Three gaps, all now closed:

| Gap | State before | State now |
|---|---|---|
| No signaller implementation | only the injected parameter existed | `PeerSignaller` in `providers/transport/signalling.py` |
| No composition path | `bootstrap` never constructed the endpoint | `bootstrap.build_webrtc_media_endpoint(exchange)` |
| `aiortc` not installed | in `uv.lock`, absent from the venv | installed by `uv sync --extra dev`; **no dependency change** |

A fourth gap was found while closing them: **`send()` could never work.** It read
`self._outbound[session_id]`, and nothing ever populated that map — so every assistant frame
raised `"no outbound track"`. The endpoint now creates the queue and attaches a real outbound
track *before* negotiation, so the offer advertises sendrecv.

## Architecture

```
PeerSignaller.negotiate(session_id, pc)
        │  1. offer created from the pc (outbound track already attached)
        │  2. setLocalDescription  → ICE candidates populated
        ▼
   SdpExchange.exchange(session_id, offer_sdp)  ──►  the deployment's channel
        │                                             ◄──  remote answer SDP
        │  3. validate, then setRemoteDescription
        │  4. await the inbound audio track
        ▼
   the track  ──►  AiortcMediaEndpoint.inbound()  ──►  core.audio.AudioFrame  ──►  runtime
```

Two seams, deliberately separated:

- **`SdpExchange`** is *the deployment's*. One async method moves an SDP out and brings one back,
  over HTTP, a WebSocket, or whatever channel already exists.
- **`PeerSignaller`** is *first-party and complete*. It owns the negotiation sequence, its state
  machine, its bound and its validation — so the exchange is the only thing a deployment writes.

**The local side offers.** The endpoint attaches its outbound track before `negotiate` runs, so
the offer advertises the audio this runtime will send and the exchange is a single round trip the
caller drives. That is what lets `MediaEndpoint.connect()` remain a plain `await` that either
yields a usable connection or raises — the contract AR-6 depends on.

## What was deliberately not built

No signalling *server*: no rooms, membership, authentication, TURN service, SFU or media server
(§6). None is required to obtain one peer connection for one session, and every one would be
architecture this milestone has no authority to add.

## Lifecycle

`NEW → NEGOTIATING → CONNECTED → CLOSED`, with `FAILED` reachable from `NEGOTIATING` and
`CONNECTED`. Illegal transitions are rejected deterministically, so a double-negotiate is an error
rather than a race. This is **transport-negotiation state only** — it is not the conversation
state machine and does not duplicate it.

Teardown releases the peer connection, the inbound track, the outbound queue, the sequence counter
and the signaller's per-session state, and puts a sentinel on the queue so a track parked in
`recv()` cannot outlive the session. It is idempotent.

## Bounds and timeouts

| Bound | Value | Rationale |
|---|---|---|
| Negotiation (end to end) | **10 s** | Matches `ProviderGuard.DEFAULT_CALL_TIMEOUT_S` — the repository's existing convention for one external call — rather than a new number. |
| Max SDP | **256 KB** | Far above any legitimate audio SDP, far below anything that could exhaust memory. The payload comes from outside the process. |
| Outbound queue | **64 frames** | The transport's own floor beneath the Playback Engine's 400 ms bound (ADR-0048 PB6), so a stalled peer cannot grow an unbounded queue (AR-40). |

There is no retry loop and no fixed sleep anywhere in the path.

## Security boundary

Signalling is control-plane infrastructure and carries **no provider credential** — not in the
SDP, not in a message, not in a log line. A test asserts the module contains no credential
vocabulary at all (`api_key`, `authorization`, `bearer`, or any vendor name).

Inputs are validated *before* the library sees them: type, non-empty, size, and an SDP version
line. That last check turns "the remote returned an HTML error page" into a clear message rather
than a library stack trace. Full SDP is never logged — it carries host candidates and network
topology nothing here needs to emit; only its byte length is.

## The aiortc boundary

AR-12 confines a vendor SDK to its plugin. Two modules now import aiortc, **both inside
`providers/transport/`**: `aiortc_endpoint.py` and `signalling.py`. The second joined because
M11.5's own interface — `negotiate(session_id, pc) -> track` — has the signaller drive the peer
connection, so it must construct one library type to apply the remote answer. Keeping it out would
have meant changing an interface this milestone is required to preserve.

Two guards enforce the rule: one admits exactly those two module names, and a second asserts that
**every** module importing aiortc lives in the transport plugin — so an admitted module that
drifted elsewhere would still fail.

## Configuration

Explicitly **opt-in, never a default**:

```python
endpoint = bootstrap.build_webrtc_media_endpoint(my_exchange)
controller = bootstrap.build_real_session_controller(
    credentials=..., media_endpoint=endpoint, tts_voice=...,
)
```

`build_registry()`'s default transport remains `local`. Nothing switches to WebRTC because aiortc
happens to be importable.

## Testing

Three layers, and the distinction is load-bearing:

| Layer | What it proves | Evidence value |
|---|---|---|
| **A. Signaller units** | state machine, validation, bounds, cancellation | hermetic |
| **B. Real aiortc ↔ aiortc** | genuine SDP/ICE/DTLS/RTP negotiation carrying real PCM16 both ways | **hermetic real-WebRTC-stack** |
| **C. Lifecycle/resources** | idempotent close, no orphaned task, peer, track or queue | hermetic |

**Layer B is not `E11-15` evidence.** Two aiortc peers inside one process is not a Stage-2
external peer, and nothing in this repository may present it as one.

One testing note worth keeping: the resource test waits for aiortc's ICE machinery to wind down on
**real timers**. `sleep(0)` yields cannot advance them, so a yield-based drain reports the library
mid-teardown as a leak. The wait is on the condition with a deadline, per the M11.15 R-70 rule.

## Stage-2 deployment prerequisites

Still outstanding, and none of them is code:

1. A provisioned **Oracle Ampere A1** (2 OCPU / 12 GB) — DL-0153 records this as an open external
   prerequisite. The 1 GB E2 micro is excluded.
2. **Deepgram, Groq and Cartesia credentials**, supplied through `CredentialProvider` (ADR-0049).
3. An **`SdpExchange` implementation** wired to a real client, plus the network path for it
   (a public IP for media relay; see the retained deployment notes).
4. **`E11-15` remains blocked** until all three exist and a scripted spoken call actually runs.

## Risks

**R-34 is not closed.** Its condition — a WebRTC profile that is end-to-end runnable against a
*browser* — is not met: no `SdpExchange` implementation ships here, and no browser client exists.
What changed is that the profile is now runnable against *a real peer* at all, which R-34's
closure will require but is not by itself.

R-54, R-58, R-63, R-66, R-68 and R-69 are untouched by this milestone.
