# Epic 11 — ICE Policy Ratification, Stage-2 Readiness & Live Execution Gate (M11.16.6)

**Status:** Executed 2026-09-20 · **Verdict: `PARTIALLY_READY`**
**Related:** [ADR-0054](../adr/ADR-0054-ice-server-policy-is-explicit-at-the-composition-root.md) · [`epic11-live-smoke.md`](epic11-live-smoke.md) · [`../providers/webrtc-external-client.md`](../providers/webrtc-external-client.md) · [ADR-0049](../adr/ADR-0049-provider-credential-injection.md) · [ADR-0053](../adr/ADR-0053-stt-latency-is-measured-from-end-of-audio-not-from-the-endpoint-decision.md) · DL-0153, DL-0229, DL-0230

> **No live conversation was executed.** The live-execution gate (§13) was not met and nothing was
> simulated to stand in for it. What this milestone did complete is **R-71**: the ICE policy is now
> a decision, implemented, tested and documented.

---

## A. Entry state

| | |
|---|---|
| HEAD | `3779e932182ed06ec5411c83ba53f0c2b585b8b8` ✓ |
| main | `c3c7397af5d9713565ed2772f91a6cb488582b06` ✓ |
| `v5.0.0` | tag `9d60ee894e53636f…` → HEAD ✓ |
| Branch | `feat/m10.2-durable-event-foundation` ✓ |
| `v6*` tags / stashes | 0 / 0 |
| Working tree | intentionally uncommitted |
| Packages | 53 (`uv lock --check` clean; **no dependency change**) |
| Epic 9 certification | `c044b3f6a23066df…` unchanged |
| Epic 10 certification | `7e57bc14764201b1…` unchanged |
| Epic 10 freeze | `d3d4858d5f18b7f6…` unchanged |
| Epic 11 | `not_certified`, 14/16, `25c9f152…` unchanged |

---

## B. R-71 — discovery

M11.16.5 gathered ICE on a NAT'd host and found **two** candidates where the documentation
promised one: `host` (private) **and `srflx` (public)**. A server-reflexive candidate can only come
from a STUN server, and nothing in this repository configured one.

## C. Actual `aiortc` behaviour, read at the source

`aiortc` 1.15.0, `rtcicetransport.py`:

```python
if iceServers is None:
    iceServers = self.getDefaultIceServers()   # -> [stun:stun.l.google.com:19302]
```

`AiortcMediaEndpoint.connect()` constructed `RTCPeerConnection()` with no configuration.
**Every peer connection since M11.5 contacted a third-party STUN service that nobody chose.**

Measured both ways, offline:

| Construction | Candidate types |
|---|---|
| no configuration (the old code) | `host`, **`srflx`** |
| `RTCConfiguration(iceServers=[])` | `host` only |

## D. Selected policy — ADR-0054

**The composition root always states the policy; the library default is never reached.**

| | |
|---|---|
| **Default** | `iceServers=[]` — **explicitly none**. Host candidates only; contacts nothing. |
| **STUN** | `VOXVOICE_ICE_STUN_URLS=stun:host:port[,…]`, validated at startup. |
| **TURN** | **not implemented**; `turn:`/`turns:` URLs are **refused at startup**, never ignored. |
| **Observable** | logged once at endpoint construction; reported by `/health`. |

*Passing nothing* and *passing nothing-to-use* are different facts, and only the second is a
decision. That distinction is the whole of R-71.

## E. STUN and TURN, stated so the documentation cannot drift again

STUN lets a host **discover** the public address/port its NAT mapped for it, so it can offer a
`srflx` candidate. **It carries no media and guarantees no traversal** — symmetric NATs,
restrictive firewalls and blocked UDP all defeat it. When direct connectivity is genuinely
impossible the answer is **TURN**, which relays, and which remains a **deployment prerequisite**
rather than a component of this system. None was built.

## F. Implementation

| File | Role |
|---|---|
| `providers/transport/ice.py` *(new)* | `IcePolicy`, validation, `NO_ICE_SERVERS`. **Library-free.** |
| `providers/transport/aiortc_endpoint.py` | the only translator into `RTCConfiguration` (AR-12); passes it unconditionally |
| `bootstrap.build_webrtc_media_endpoint(..., ice_policy=…)` | the documented composition path |
| `api/webrtc/config.py` | carries the URLs as **plain strings**; imports no provider module |
| `api/webrtc/app.resolve_ice_policy` | the composition root translates and validates (AR-14) |

## G. Deployment implications

1. Inbound **UDP** on the media range is required.
2. A host whose public IPv4 is **carried on its interface** needs no STUN — the default suffices.
3. A host whose public IPv4 is **1:1 NAT-mapped** (the common cloud-VM shape) will otherwise offer
   only an unroutable private candidate and **must** configure STUN. *Stated as a prerequisite to
   verify on the real host, not as a measured fact — no Stage-2 host exists.*
4. If direct connectivity still fails, **TURN is a deployment prerequisite**.

---

## H–J. Stage-2, credential and browser readiness

| Gate | Status | Evidence |
|---|---|---|
| **G1 — A1 host** | **BLOCKED — not provisioned** (distinct from *provisioned but unreachable*) | DL-0153 still records provisioning as *"NOT VERIFIED — owner input required"*, with no IP, DNS name or instance id. No `oci` CLI, no `~/.oci`, no Terraform, no Ansible — no authorized cloud access exists, so nothing was provisioned and nothing was guessed. |
| **G2 — Public IPv4 / DNS** | **BLOCKED** | Downstream of G1. No address was invented. |
| **G3 — HTTPS** | **BLOCKED** | Downstream of G1/G2. No certificate architecture was invented. |
| **G4 — UDP media range** | **BLOCKED** | Downstream of G1. No host interface or cloud security list exists to inspect. |
| **G5 — Deepgram** | **BLOCKED** | `deepgram_api_key` **ABSENT** via the ADR-0049 seam (presence only; no value read, printed or logged). |
| **G6 — Groq** | **BLOCKED** | `groq_api_key` **ABSENT**, same method. |
| **G7 — Cartesia** | **BLOCKED** | `cartesia_api_key` **ABSENT**, same method. |
| **G8 — `VOXVOICE_TTS_VOICE_ID`** | **BLOCKED** | ABSENT. |
| **G9 — Operator/browser** | **BLOCKED** | Chrome and Safari exist, so a browser *environment* does. There is nothing to point one at, no provider to reach, and **no operator at a microphone**. A synthetic browser, scripted aiortc peer or injected audio is forbidden and was not built. |
| **G10 — Deployed application** | **BLOCKED** | No deployment target exists; the application runs nowhere. |

**The excluded 1 GB E2 micro was not substituted**, and no local provider, fake browser, scripted
peer or synthetic audio was used in its place.

## K. Live execution result

**NOT EXECUTED.** The §13 gate requires *all* prerequisites; ten of ten external gates are blocked.

## L. Event evidence

**None.** `transport.connected`, `transport.media_ready`, `speech_start`, `speech_end`,
`stt_final`, `llm_first_token`, `tts_first_audio`, `first_audio_out`, `transport.disconnected` and
`metric.turn` were **not** produced, because no session ran. No timing is reported. No transcript,
audio, model output or credential value exists to store.

## M. `metric.turn` / ADR-0053

**ADR-0053 remains PROPOSED** — not ratified, applied or edited. The legally applicable semantics
are therefore the unchanged `endpoint_decided → stt_final`, and `observability/turn_latency.py`
still derives exactly that. **No STT measurement exists** and **no STT budget claim is made**;
absence was not converted to zero. `E11-12` remains **INCONCLUSIVE**.

---

## N–V. Risk status

| Risk | Status |
|---|---|
| **R-33** | **OPEN** — unchanged. No real peer or media-endpoint evidence was added. |
| **R-34** | **OPEN** — unchanged. No browser executed the path. |
| **R-54** | **OPEN** — unchanged. Live-provider resilience unexercised. |
| **R-58** | **OPEN** — unchanged. |
| **R-63** | **OPEN** — unchanged. |
| **R-66** | **OPEN** — unchanged; ADR-0053 still PROPOSED. |
| **R-68** | **OPEN** — unchanged. Failover still has one provider per capability in any real composition. |
| **R-69** | **OPEN** — unchanged. |
| **R-71** | **CLOSED** — ADR-0054 accepted, implemented at the composition root, proved by 16 focused tests (including measured suppression), and the two incorrect documentation statements corrected. |

## W. Regression

| Gate | Result |
|---|---|
| R-71 focused tests | 16 passed |
| WebRTC suites (5 files) | 153 passed, ×3 consecutive |
| Full suite | 6635 passed, 0 failed |
| CI-parity (warnings-as-errors) | 6635 passed, 0 failed |
| collect-only | 6635 |
| Ruff, both declared scopes | All checks passed |
| Credential-literal scan | 1418 files; identifier matches only, **0 values** |
| Epic 9 / Epic 10 cert / Epic 10 freeze | all three **unchanged** |

## X. Remaining blockers

Unchanged from M11.16.5, and all external:

1. Provision the **Oracle Ampere A1** (2 OCPU / 12 GB) with a public IPv4; open ports at **both**
   the Oracle security list and host iptables.
2. **DNS + HTTPS** — a browser will not grant a microphone without a secure context.
3. **Inbound UDP** on the media range.
4. **Three credentials** on the host environment only, never in the repository.
5. **`VOXVOICE_TTS_VOICE_ID`.**
6. **An operator at a browser** to grant the microphone and speak the utterance.

New, and now decidable rather than accidental: **choose the ICE policy for that host.** If its
public IPv4 is NAT-mapped, set `VOXVOICE_ICE_STUN_URLS`; if direct connectivity fails, TURN becomes
a deployment prerequisite.
