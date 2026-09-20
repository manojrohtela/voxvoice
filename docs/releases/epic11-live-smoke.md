# Epic 11 — Live Environment Bring-Up & Real Browser Smoke (M11.16.5)

**Status:** Executed 2026-09-20 · **Verdict: `BLOCKED`**
**Related:** [`../providers/webrtc-external-client.md`](../providers/webrtc-external-client.md) · [`../providers/webrtc-signalling.md`](../providers/webrtc-signalling.md) · [ADR-0049](../adr/ADR-0049-provider-credential-injection.md) · [ADR-0051](../adr/ADR-0051-epic11-certification-control-set.md) CE5#15 · [ADR-0053](../adr/ADR-0053-stt-latency-is-measured-from-end-of-audio-not-from-the-endpoint-decision.md) · DL-0153 · R-33, R-34, R-71

> **No live conversation was executed.** No browser ran the path, no provider was called, no media
> crossed a network. This document records which gates were evaluated, what each evaluation
> actually showed, and what is missing. **Nothing here is `E11-15` evidence**, and `E11-12` is
> untouched.

---

## 1. Why this is `BLOCKED` and not a partial result

`LIVE_SMOKE_PARTIALLY_READY` would require *meaningful live infrastructure, provider or browser
evidence*. **None of the three exists.** Four of the seven gates are blocked at the root — there is
no host to deploy to and no credential to call a provider with — and the remaining three are
downstream of those. What was gathered is **local, read-only evidence about the code and this
machine's topology**, which is not live evidence and is not labelled as such (§23).

---

## 2. Readiness gates

| Gate | Status | How it was established |
|---|---|---|
| **G1 — Stage-2 A1 host** | **BLOCKED** | No Oracle Ampere A1 (2 OCPU / 12 GB) is provisioned. DL-0153 still records the provisioning as *"NOT VERIFIED — owner input required"* with no IP, DNS name or instance id. No OCI CLI, no `~/.oci` configuration, no Terraform and no Ansible exist on this machine, so there is no path to create or query one. |
| **G2 — Public HTTPS** | **BLOCKED** | Downstream of G1. No host, therefore no DNS name, no certificate and no reachable origin. Nothing was invented. |
| **G3 — Public UDP media path** | **BLOCKED** | Downstream of G1. No host interface exists to open a port on, and no cloud security list to inspect. Local topology was measured instead (§4) and is **not** a substitute. |
| **G4 — Deepgram credential** | **BLOCKED** | `deepgram_api_key` resolves **ABSENT** through the ADR-0049 `CredentialProvider`. Presence only; no value was read, printed or logged. |
| **G5 — Groq credential** | **BLOCKED** | `groq_api_key` resolves **ABSENT**, same method. |
| **G6 — Cartesia credential + voice** | **BLOCKED** | `cartesia_api_key` resolves **ABSENT**; `VOXVOICE_TTS_VOICE_ID` is **ABSENT**. |
| **G7 — Real browser execution** | **BLOCKED** | Chrome and Safari **are** installed, so a browser *environment* exists. What does not exist is anything to point it at (G1–G3), any provider for it to reach (G4–G6), and — decisively — **an operator at the microphone**. §13 requires a human to physically speak; this execution context has no microphone and no person. A scripted synthetic substitute is forbidden by §10 and §13 and was not built. |

**The forbidden substitute was not used.** The only VoxVoice-adjacent host known to this machine is
`140.245.10.153` — the **1 GB E2 micro** that DL-0153 explicitly excludes. It was not deployed to,
not reused and not counted toward G1.

---

## 3. What *was* verified (local, read-only, not live)

These are honest operational facts about the code as it stands. They are evidence that the path is
*correctly refusing to run without its prerequisites* — not evidence that it runs.

### 3.1 The live profile refuses to start, and does not fall back

`REAL_PROVIDER_PROFILE` resolves exactly as declared and unmodified:

```
transport = webrtc · stt = deepgram · voice_activity = silero
llm = groq · tts = cartesia · streaming_playback = True
```

Constructing it with no credential present produces a **startup refusal**, not a downgrade:

```
ValueError: the deepgram stt profile requires the 'deepgram_api_key' credential,
which is not configured; refusing to fall back to the local transcriber
```

This is the §8/§9 property under test: a missing credential is a failure, never a silent local
provider. It holds.

### 3.2 `transport=webrtc` + `tts=local` was not used

The combination M11.16.4 recorded as incompatible (`AudioEncoding.OPAQUE` vs PCM16) was not used,
not broadened and not worked around.

### 3.3 `metric.turn` semantics are untouched

`observability/turn_latency.py` still derives `("stt", "endpoint_decided", "stt_final")`.
ADR-0053 remains **PROPOSED** and was not ratified, applied or edited.

---

## 4. Topology finding — **R-71 (new)**

Gathering ICE on this machine returned **two** candidates, not one:

```
typ=host   scope=PRIVATE   count=1
typ=srflx  scope=PUBLIC    count=1
```

A server-reflexive candidate can only come from a STUN server. The cause is in the library:
`aiortc/rtcicetransport.py` (1.15.0) returns `[RTCIceServer("stun:stun.l.google.com:19302")]`
whenever `iceServers` is `None`, and **this repository never passes an `RTCConfiguration`**.

Three consequences:

1. **M11.16.4's documentation was factually wrong** — it stated *"host candidates only. No STUN or
   TURN is configured."* Corrected in place.
2. **There is an undeclared outbound dependency on a third-party STUN service at every call
   setup.** It is not configurable through the composition root, is named in no ADR, and silently
   decides ICE behaviour if a firewall blocks it. For a platform whose whole architecture is
   "providers are explicit and injected", a hard-coded external service reached by default is a
   real gap.
3. The NAT outlook is *better* than "host only" implied, but this **does not** remove the
   public-IP requirement for the documented Stage-2 deployment.

**Not fixed here.** Choosing an ICE-server policy is an architecture decision (plausibly an ADR),
not a bring-up fix, and §22 prefers zero production-code changes. Raised as **R-71**.

> **Resolved at M11.16.6** by [ADR-0054](../adr/ADR-0054-ice-server-policy-is-explicit-at-the-composition-root.md):
> the composition root now always states the ICE policy, the default is *explicitly* no ICE
> servers, STUN is configured through `VOXVOICE_ICE_STUN_URLS`, and TURN is refused at startup.
> **R-71 CLOSED.** This section is retained as the record of the discovery.

---

## 5. Live path status

| Component | Status |
|---|---|
| Browser | `NOT_EXECUTED` |
| WebRTC inbound | `NOT_EXECUTED` |
| Deepgram STT | `NOT_EXECUTED` — no credential |
| Silero VAD | `NOT_EXECUTED` |
| Audio endpointing | `NOT_EXECUTED` |
| Groq LLM | `NOT_EXECUTED` — no credential |
| Clause detector / sink | `NOT_EXECUTED` |
| Cartesia TTS | `NOT_EXECUTED` — no credential |
| Playback Engine / Scheduler | `NOT_EXECUTED` |
| WebRTC outbound | `NOT_EXECUTED` |
| Browser playback | `NOT_EXECUTED` |

**No live event timeline exists.** `transport.connected`, `transport.media_ready`, `speech_start`,
`speech_end`, `stt_final`, `llm_first_token`, `tts_first_audio`, `first_audio_out`,
`transport.disconnected` and `metric.turn` were **not** produced, because no session ran. No
timing is reported, because fabricating one is the failure mode this whole sequence of milestones
exists to prevent.

No transcript, audio, model output or credential value was captured — none was produced.

---

## 6. Certification impact — none

`E11-12` stays **INCONCLUSIVE**; `E11-15` stays **NOT EXECUTED**. The control catalogue was run for
verification only and not modified; the Epic 11 digest is unchanged at `25c9f152…`, 14/16.

---

## 7. Exact remaining prerequisites

1. **Provision the Oracle Ampere A1** (2 OCPU / 12 GB) with a public IPv4 — per
   `platform/ORACLE_DEPLOY.md`, opening ports at **both** the Oracle security list and host
   iptables (the documented "#1 Oracle gotcha"). The 1 GB E2 micro is excluded.
2. **DNS + HTTPS** for that host — a browser will not grant a microphone without a secure context.
3. **Inbound UDP** on the media range.
4. **Three credentials** — `VOXVOICE_SECRET_DEEPGRAM_API_KEY`, `…_GROQ_API_KEY`,
   `…_CARTESIA_API_KEY` — supplied to the host environment only, never to the repository.
5. **`VOXVOICE_TTS_VOICE_ID`.**
6. **An operator at a browser** to grant the microphone and speak the smoke utterance.

Items 1–5 are external provisioning actions that no amount of engineering in this repository can
substitute for. Item 6 requires a person.
