# The external WebRTC client path (M11.16.4)

**Status:** Active v1.0 · 2026-09-20
**Related:** [`webrtc-signalling.md`](webrtc-signalling.md) · [ADR-0044](../adr/ADR-0044-audio-representation-resampling-and-media-buffers.md) · [ADR-0049](../adr/ADR-0049-provider-credential-injection.md) · [ADR-0051](../adr/ADR-0051-epic11-certification-control-set.md) CE5#15 · R-33, R-34

M11.16.3 proved the WebRTC stack works between two `aiortc` peers in one process. What it could
not prove was that a peer **outside** the runtime could ever reach it, because the `SdpExchange`
seam had no implementation. This document describes the channel that closes that gap — and, just
as importantly, exactly how far it goes.

> **What this is not.** Nothing here is `E11-15` evidence. `E11-15` requires a scripted spoken
> call on a **Stage-2 host** with **real providers**, and neither exists. Three distinct kinds of
> evidence are kept apart throughout:
>
> | Evidence | What it proves | Where |
> |---|---|---|
> | aiortc ↔ aiortc, in process | the WebRTC stack and the media conversion work | `test_webrtc_signalling.py` layer B |
> | a real external peer over the real HTTP signalling channel | the **path** works end to end | `test_webrtc_external_client.py` layer C |
> | a browser on a Stage-2 host with live vendors | the system was **deployed** | **does not exist** |

---

## 1. Shape

```
   browser                         Control Plane                     Runtime
      │  POST /v1/webrtc/rendezvous      │                              │
      │ ───────────────────────────────► │  reserve()                   │
      │ ◄── rendezvous_id + token ────── │                              │
      │                                  │                              │
      │  POST /v1/webrtc/sessions        │  bind(session_id)            │
      │ ═══════════════════════════════► │ ───────────────────────────► │  connect()
      │            (in flight)           │  ◄── exchange(offer) ─────── │
      │  GET  …/offer  ◄── the offer ─── │                              │
      │  POST …/answer ───────────────►  │  ─── the answer ───────────► │
      │ ◄══ 201 session_id ════════════  │                              │  media
```

**The runtime offers, the peer answers.** That is set by M11.16.3: the endpoint attaches its
outbound track and *then* negotiates, so the offer advertises the audio the runtime will send.
Over HTTP the peer is a client and nothing can push an offer to it, so the offer waits in a
**rendezvous** the peer comes and fetches.

**`POST /v1/webrtc/sessions` *is* the existing session endpoint.** It calls
`api.routers.sessions.create_session` directly — agent lookup, org scoping, persistence, the 502
mapping and the `session.started` webhook are the ones that already exist, not a second copy — and
carries the rendezvous claim beside it in a context variable that a controller wrapper reads
between the session row being written and the runtime being started. Ending a session uses the
ordinary `POST /v1/sessions/{id}/end`, and the same wrapper releases the rendezvous there. Why a
separate path exists at all is §13.

**The session-create call and the signalling exchange overlap.** The create call cannot return
until the peer connection is usable — `MediaEndpoint.connect()` returning means exactly that
(AR-6) — and the connection cannot become usable until the peer has answered the offer that same
call produced. The browser therefore issues its signalling requests while create is in flight.
This is the one non-obvious thing about the shape, and it is forced rather than chosen.

---

## 2. The three endpoints

| | Auth | Bound |
|---|---|---|
| `POST /v1/webrtc/rendezvous` | org API key (`x-api-key`) | ≤ 64 open slots; 120 s TTL |
| `GET /v1/webrtc/rendezvous/{id}/offer` | signalling token | 5 s per poll, then `204` |
| `POST /v1/webrtc/rendezvous/{id}/answer` | signalling token | SDP ≤ 256 KB |
| `POST /v1/webrtc/sessions` | org API key | blocks until the peer connection is usable |

`204` on the offer poll means *not yet*. It is not an error and it is not a spin: each call waits
on the rendezvous itself and returns on a predictable bound.

**There is no ICE-candidate endpoint.** aiortc emits a complete local description during
`setLocalDescription` (non-trickle), so every candidate rides the SDP and a second channel would
carry nothing. The browser client waits for `iceGatheringState === "complete"` before answering,
for the same reason.

There is **no room, no roster, no presence, no renegotiation, no TURN and no SFU** (§4/§6). One
external peer joins one session, and nothing here knows what a second peer would mean.

---

## 3. Session ownership

A rendezvous is **org-scoped at reservation** and **single-use at binding**:

* another org's attempt to bind is answered `404`, not `403` — org B must not be able to probe
  for org A's identifiers;
* a second session cannot claim a bound rendezvous (`409`);
* a rendezvous that is `CLOSED` or `FAILED` rejects further signalling;
* ending the session releases the slot, and an unused slot expires after 120 s.

`RendezvousState`: `RESERVED → BOUND → OFFERED → ANSWERED`, with `CLOSED` and `FAILED` terminal.
It describes one offer/answer exchange and says nothing about the conversation.

---

## 4. Credentials, and the prerequisite this milestone does not solve

**The browser never receives a provider credential.** It cannot: the signalling plane has no word
for one. A test asserts that `rendezvous.py`, the router and the client page contain no occurrence
of `deepgram`, `groq`, `cartesia`, `credentialprovider`, `voxvoice_secret`, `authorization` or
`bearer`. Provider credentials are resolved only by the composition root through the ADR-0049
`CredentialProvider`, on the server, as before.

**Signalling does not ride the org API key.** Reservation mints a **single-purpose token**,
returned once, stored only as a SHA-256 hash and compared in constant time. It can exchange one
SDP pair for one session and do nothing else — it cannot start a session, read a transcript or
reach a provider.

> **Outstanding production prerequisite (§9, §41).** The *reservation* call still needs the org
> API key, and the reference page asks its operator to type one in. That is acceptable for a
> first-party operator page and **is not acceptable for an end-user-facing browser client**: a
> production deployment must mint the rendezvous server-side, from its own backend, and hand the
> browser only the `rendezvous_id` and the signalling token. Building an end-user identity system
> was explicitly out of scope here, so the smallest safe boundary was taken and the remainder is
> recorded rather than hidden.

Full SDP is never logged — only its byte length, the state and the rendezvous id. SDP carries host
candidates and network topology that nothing here needs to emit.

---

## 5. CORS

No CORS middleware exists unless `WEBRTC_ALLOWED_ORIGINS` is set, so a deployment that never asked
for cross-origin access has no permissive default to misconfigure. When set:

* origins are an explicit allow-list, never `*` outside `APP_ENV=dev` (a startup error otherwise);
* `allow_credentials` is **false** — the peer authenticates with an explicit header, never a
  cookie, so the `*`-with-credentials trap cannot arise;
* methods are `GET`/`POST`/`OPTIONS`; headers are `content-type`, `x-api-key`,
  `x-signalling-token`.

The reference page is served **same-origin** from the API, so the common case needs no CORS at
all.

---

## 6. The browser client

`GET /v1/webrtc/client` serves a single static page. It does four things — get a microphone,
reserve a rendezvous, answer the offer, play what comes back — and nothing else.

* **Microphone only.** `getUserMedia({ audio: true, video: false })`; the browser never prompts
  for a camera.
* **Secure context.** The page is served over HTTP from the API because `http://localhost` is a
  secure context and `file://` is not. A non-localhost deployment needs **HTTPS**, or the browser
  will refuse the microphone.
* It holds no provider endpoint, key or host, and a test asserts that.

---

## 7. Configuration

The WebRTC profile is a **separate application**, `api.webrtc.app`:

```
RUNTIME_TRANSPORT=webrtc VOXVOICE_TTS_VOICE_ID=… \
  uvicorn "api.webrtc.app:build_webrtc_app" --factory --host 0.0.0.0 --port 8000
```

It mounts the **same** `agents` and `sessions` routers, unmodified. It differs from the default
app in exactly three ways: the signalling router, the CORS policy, and which controller is
injected. The default app (`api.main:app`) reads none of the settings below.

| Setting | Default | Meaning |
|---|---|---|
| `RUNTIME_TRANSPORT` | `local` | `webrtc` selects the whole real stack (`REAL_PROVIDER_PROFILE`) |
| `WEBRTC_SIGNALLING` | `false` | mounts `/v1/webrtc/*`; implied by `RUNTIME_TRANSPORT=webrtc` |
| `WEBRTC_ALLOWED_ORIGINS` | *(empty)* | comma-separated allow-list; empty adds no CORS at all |
| `VOXVOICE_TTS_VOICE_ID` | *(unset)* | required when `RUNTIME_TRANSPORT=webrtc`; configuration, not a credential, and vendor-neutral by design (AR-21) |

Every default is the behaviour that existed before this milestone. Selecting `webrtc`
incompletely is a **startup error**, never a silent downgrade to the loopback. No secret, host or
public IP appears in configuration files.

---

## 8. Network requirements and ICE policy

| | |
|---|---|
| **Signalling** | one TCP port, the API's own. HTTPS in production (also required for the microphone). |
| **Media** | UDP, ephemeral ports, bidirectional, to the host's **reachable** address. |
| **ICE** | **whatever this deployment states, and nothing more** — by default, host candidates only. No TURN. |

### 8.1 The policy is stated, never inherited (ADR-0054)

> **This section was wrong twice, and the history is worth keeping.** M11.16.4 wrote *"host
> candidates only. No STUN or TURN is configured."* M11.16.5 measured ICE gathering and found a
> `srflx` candidate, proving the opposite: `aiortc` substitutes
> `RTCIceServer("stun:stun.l.google.com:19302")` whenever `iceServers` is `None`
> (`rtcicetransport.py`, 1.15.0), and the repository passed no configuration. **Every call setup
> since M11.5 contacted a third-party STUN service that nobody had chosen** (R-71). M11.16.6
> resolved it — not by rewording this paragraph, but by making the policy a decision.

`RTCPeerConnection` is now **always** constructed with an explicit `RTCConfiguration`, including
when the answer is "no ICE servers". *Passing nothing* and *passing nothing-to-use* are different
facts, and only the second is a decision.

| | |
|---|---|
| **Default** | `iceServers=[]` — **explicitly none**. Gathering yields host candidates only and contacts nothing. Verified by measurement, not assertion. |
| **STUN** | `VOXVOICE_ICE_STUN_URLS=stun:host:port[,…]`. Stated by the deployment, validated at startup, translated by the composition root. |
| **TURN** | **Not implemented.** A `turn:`/`turns:` URL is **refused at startup**, never ignored. |
| **Observable** | logged once at endpoint construction and reported by `/health`. |

### 8.2 What each layer actually does

Kept separate because conflating them is how the previous two errors were written:

| | |
|---|---|
| **Candidate gathering** | enumerating local addresses this host could be reached at. |
| **STUN** | *discovering* the public address/port a NAT mapped for this host, so it can be offered as a `srflx` candidate. **It carries no media.** |
| **TURN** | *relaying* media through a third host when no direct path exists. **Not present here.** |
| **DTLS** | the handshake that keys the media once a candidate pair is chosen. |
| **RTP/SRTP** | the media itself, flowing over the chosen pair. |

**STUN is not a media relay, and it is not guaranteed NAT traversal.** Symmetric NATs, restrictive
firewalls and blocked UDP all defeat it. When direct connectivity is genuinely impossible the
answer is TURN, which is a **deployment prerequisite**, not a component of this system.

### 8.3 Deployment consequences

1. The Stage-2 host must permit **inbound UDP** on the media range.
2. If the host's public IPv4 is **carried on its interface**, the default policy is sufficient.
3. If the host's public IPv4 is **1:1 NAT-mapped** rather than carried on the interface — the
   common cloud-VM shape — the only host candidate will be an unroutable private address, and the
   deployment **must** configure STUN or it will negotiate and then carry no media. *Stated as a
   prerequisite to verify on the real host, not as a measured fact: no Stage-2 host exists yet.*
4. If direct connectivity still fails, **TURN is a deployment prerequisite**. None was built and
   none is configured.

## 9. Stage-2 deployment

**Not executed.** The authorized Stage-2 target is an **Oracle Ampere A1, 2 OCPU, 12 GB**, and no
such host is provisioned. The 1 GB E2 micro is explicitly forbidden and was not substituted. No
deployment evidence is claimed, and `E11-15` remains `NOT EXECUTED`.

What a Stage-2 run needs, stated so the list is auditable:

1. the A1 host, with a public IP and inbound UDP;
2. HTTPS (microphone permission and the org key both require it);
3. `VOXVOICE_SECRET_DEEPGRAM_API_KEY`, `…_GROQ_API_KEY`, `…_CARTESIA_API_KEY` — all three
   currently **absent** (presence checked through the ADR-0049 seam; no value was read, printed
   or logged);
4. `VOXVOICE_TTS_VOICE_ID`;
5. `RUNTIME_TRANSPORT=webrtc` and, if the page is not served same-origin,
   `WEBRTC_ALLOWED_ORIGINS`;
6. an **ICE policy decision for that host** (§8.1). The default contacts nothing and offers host
   candidates only; a host whose public IPv4 is NAT-mapped needs `VOXVOICE_ICE_STUN_URLS`.

---

## 10. A composition that cannot work, and is not refused

`transport=webrtc` with `tts=local` **negotiates successfully and then fails every spoken turn.**
`LocalTTS` emits `AudioEncoding.OPAQUE` — the text-loopback codec ADR-0044 AD2 describes — and the
real media path carries PCM16, so `AiortcMediaEndpoint.send()` refuses the frame, the transport
raises `ProviderUnavailable`, the guard records a typed degradation and the breaker opens.

The refusal is correct — reinterpreting an opaque payload as PCM16 would emit noise — but it
arrives **mid-turn**, where AR-25 would have it arrive **at startup**. `build_registry` accepts
the combination today.

This is **recorded, not fixed**: adding a composition rule is a change to the provider registry's
contract, and it is not within M11.16.4's authority. The real profile (`REAL_PROVIDER_PROFILE`)
pairs `webrtc` with Cartesia and is unaffected.

---

## 11. Testing

`platform/tests/test_webrtc_external_client.py` — 55 tests, five layers:

| | |
|---|---|
| **A** | the rendezvous registry and the binding controller: reservation, ownership, state machine, validation, bounds, timeouts, claim/release |
| **B** | the endpoints over the real app: auth, status codes, CORS, configuration refusals |
| **C** | a **real** `RTCPeerConnection` negotiating over the **real HTTP endpoints** and carrying real PCM16 in both directions, including one test driven through `POST /v1/sessions` |
| **D** | repeated connect/media/disconnect leaks no task, peer, track or rendezvous |
| **E** | security: no credential vocabulary, no full SDP in logs, closed/foreign rendezvous refused |

No `skip`, `skipif` or `xfail` anywhere (`E11-1`). Every wait is on a condition under a bounded
deadline — no fixed sleeps, no spin budgets, no retry counts (the M11.15 R-70 rule).

One finding worth keeping: the external peer's inbound pump must be cancelled **after** the peer
connection is closed, not before. Cancelling it mid-`recv()` leaves aiortc holding a receiver it
never winds down, and four ICE consent tasks then outlive every session — which the leak test
caught.

---

## 12. Troubleshooting

| Symptom | Cause |
|---|---|
| `204` forever on the offer poll | the session-create call was never made, or named a different rendezvous |
| create returns 504 / negotiation timeout | the peer never answered within 8 s |
| `404` on bind | the rendezvous belongs to another org, or expired (120 s) |
| `409` on answer | no offer is awaiting one, or the answer already arrived |
| connects, then silence | no reachable ICE candidate — the host is behind NAT (see §8) |
| every turn fails with `ProviderUnavailable` | `tts=local` on a real media path (see §10) |
| browser refuses the microphone | not a secure context — use `http://localhost` or HTTPS |

---

## 13. Why this is additions-only

Epic 10's **`E10-7`** freezes every pre-existing `platform/api/` file: *"additions are not
modifications"* ([ADR-0043](../adr/ADR-0043-m10.13-certification.md) CD7). A first draft of this milestone added a `webrtc_rendezvous_id`
field to `SessionCreate`, bound it in `api/routers/sessions.py`, and turned `api/main.py` into an
app factory. All four files are frozen, and the Epic 10 certification digest moved from
`7e57bc14…` to `08d595bf…` as a result — caught by running the control, not by reading the diff.

Everything was reverted and rebuilt as additions:

| Added | |
|---|---|
| `api/webrtc/rendezvous.py` | the registry, and the `SdpExchange` the runtime drives |
| `api/webrtc/binding.py` | the context variable and the controller wrapper |
| `api/webrtc/config.py` | the WebRTC profile's settings |
| `api/webrtc/app.py` | the WebRTC deployment's application |
| `api/webrtc/client.html` | the reference browser client |
| `api/routers/webrtc.py` | the four endpoints |

`api/main.py`, `api/config.py`, `api/schemas.py` and `api/routers/sessions.py` are byte-identical
to the frozen baseline, and the Epic 10 digest is back to `7e57bc14…`.

**The constraint produced the better design.** The default application is now untouched *by
construction* rather than by care — it cannot acquire a signalling surface, a CORS policy or a
real transport from a setting, because it reads none of them. And calling `create_session`
directly is a stronger form of reuse than a new field would have been: the path that runs is the
one that already exists and is already tested, rather than the same path with one more branch in
it.
