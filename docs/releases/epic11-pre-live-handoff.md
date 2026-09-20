# Epic 11 — Pre-Live Owner Handoff (M11.16.8)

**Status:** Active · 2026-09-20 · **Verdict: `READY_FOR_OWNER_PROVISIONING`**
**Related:** [`epic11-live-execution.md`](epic11-live-execution.md) · [`epic11-live-readiness.md`](epic11-live-readiness.md) · [`../providers/webrtc-external-client.md`](../providers/webrtc-external-client.md) · [ADR-0054](../adr/ADR-0054-ice-server-policy-is-explicit-at-the-composition-root.md) · [ADR-0049](../adr/ADR-0049-provider-credential-injection.md) · [ADR-0053](../adr/ADR-0053-stt-latency-is-measured-from-end-of-audio-not-from-the-endpoint-decision.md) · DL-0153, DL-0232

**This is the operational handoff.** The engineering side of the live path is complete. What
remains is procurement and one person with a microphone. This document exists so that the next
engineer or operator has to rediscover nothing.

> **No secret appears in this document, and none may be added to it.** Credentials are recorded as
> `PRESENT` / `ABSENT` only — never values, never partial values, never in examples.

---

## 0. Two findings that will otherwise bite during the live run

**(a) The repository's deployment runbooks are Dograh-era and must not be followed past their
reusable phases.** `platform/DEPLOY.md`, `platform/ORACLE_DEPLOY.md` and
`platform/scripts/oracle_setup.sh` are all marked **SUPERSEDED (M1)** — they deploy the retired
engine plus coturn. Their **Oracle/host/TLS knowledge is reusable**; their bring-up is not. §5
below states exactly which parts to use and which to skip.

**(b) The media UDP port range cannot be pinned from configuration.** `aioice` 0.10.2 binds with
`local_addr=(address, 0)` (`aioice/ice.py:984`) — **port 0 means the OS chooses**, and neither
`aioice` nor `aiortc` exposes a port-range option. Consequences:

* the old runbook's `49152–49200/udp` range is a **coturn** range and is **not** what this
  deployment uses;
* the firewall must permit the host's **ephemeral UDP range**, or the operator must narrow it
  first — on Linux, `sysctl net.ipv4.ip_local_port_range` (default typically `32768 60999`).

Narrowing the sysctl and opening only that range is the smaller surface and is the recommended
owner action. **This is a deployment decision, not an engineering blocker.**

---

## 1. Infrastructure checklist — OWNER INPUT

| Item | Required | Value | Status |
|---|---|---|---|
| Oracle Ampere A1 provisioned | yes | `____` | ☐ |
| Shape `VM.Standard.A1.Flex`, **2 OCPU** | yes | `____` | ☐ |
| **12 GB RAM** | yes | `____` | ☐ |
| Public IPv4 assigned | yes | `____` | ☐ |
| Instance OCID | yes | `____` | ☐ |
| Region / Availability Domain | yes | `____` | ☐ |
| OS (Ubuntu 22.04 per the reusable recipe) | yes | `____` | ☐ |
| SSH/admin access confirmed | yes | `____` | ☐ |

**The 1 GB E2 micro (`140.245.10.153`) is excluded** (DL-0153) and must not be substituted — it
runs 13 other agents and is the wrong class for this workload.

> If A1 capacity is unavailable ("Out of host capacity"), try another Availability Domain or
> region. It is a known free-tier quirk, not an account problem.

## 2. DNS checklist — OWNER INPUT

| Item | Value | Status |
|---|---|---|
| Hostname (e.g. `voice.example.com`) | `____` | ☐ |
| A record → public IPv4 | `____` | ☐ |
| Propagation confirmed (`dig +short <host>` returns the IP) | `____` | ☐ |

## 3. HTTPS checklist — OWNER INPUT

A browser **will not grant a microphone** outside a secure context. HTTPS is not optional.

| Item | Value | Status |
|---|---|---|
| Certificate method (Let's Encrypt `certbot` per the reusable recipe) | `____` | ☐ |
| HTTPS endpoint | `https://____` | ☐ |
| Certificate valid (not self-signed) | `____` | ☐ |
| Browser secure-context verified (padlock, no warning) | `____` | ☐ |
| **TLS termination wired to the app** | `____` | ☐ **OWNER ACTION REQUIRED** |

> **No TLS wiring exists in this repository for the first-party app.** `certbot` produces the
> certificate; nothing connects it to the application. The two shapes that fit the Constitution's
> *"one cloud VM + systemd"* Stage-2 profile are (a) `uvicorn --ssl-keyfile/--ssl-certfile`
> directly, or (b) a TLS-terminating reverse proxy. **A reverse-proxy architecture is not mandated
> by any existing deployment document**, so choosing one is an owner decision — pick the simpler
> one that works and record it here.

## 4. Network checklist — OWNER INPUT

| Item | Required | Status |
|---|---|---|
| Oracle VCN **Security List / NSG** ingress configured | yes | ☐ |
| **Host iptables** configured (*the #1 Oracle gotcha — both layers are required*) | yes | ☐ |
| TCP **443** (HTTPS) open at both layers | yes | ☐ |
| TCP **80** open (only if using ACME HTTP-01) | if ACME | ☐ |
| **UDP ephemeral range** open at both layers (see §0(b)) | yes | ☐ |
| `net.ipv4.ip_local_port_range` narrowed first (recommended) | optional | ☐ |
| NAT present between the public IPv4 and the interface? | record | ☐ |
| Reverse proxy in path? | record | ☐ |
| Load balancer in path? | record | ☐ |

**Do not open 3478 / 5349 / 49152–49200.** Those are coturn ports from the superseded runbook.
**No TURN is deployed.**

## 5. ICE checklist — ADR-0054 is authoritative

| Item | Value | Status |
|---|---|---|
| `VOXVOICE_ICE_STUN_URLS` | `____` (empty = explicitly none) | ☐ |
| STUN intentionally unset? | `____` | ☐ |
| Public **host** candidates expected? (public IPv4 on the interface) | `____` | ☐ |
| NAT-mapped public IPv4 → STUN required? | `____` | ☐ |
| TURN required? | `____` | ☐ |

**How to decide, on the host, before the browser test:**

```bash
ip -4 addr show            # is the PUBLIC IPv4 on an interface, or a private 10.x/172.16.x?
```

* **Public IPv4 on the interface** → leave `VOXVOICE_ICE_STUN_URLS` empty. Host candidates are
  routable; nothing is contacted.
* **Private address only** (the common OCI 1:1 NAT shape) → the only host candidate is
  unroutable and the call **will negotiate and then carry no media**. Set
  `VOXVOICE_ICE_STUN_URLS` to a STUN server you accept a dependency on.
* **Still no connectivity** → record **"TURN is a deployment prerequisite"**. TURN is *not*
  implemented; a `turn:` URL is **refused at startup**, deliberately, so nobody believes they
  have relaying they do not have.

The library default is never inherited — `iceServers` is always passed explicitly (ADR-0054).

## 6. Provider credential checklist — presence only

| Credential | Required | Status now |
|---|---|---|
| `VOXVOICE_SECRET_DEEPGRAM_API_KEY` | yes | **ABSENT** |
| `VOXVOICE_SECRET_GROQ_API_KEY` | yes | **ABSENT** |
| `VOXVOICE_SECRET_CARTESIA_API_KEY` | yes | **ABSENT** |
| `VOXVOICE_TTS_VOICE_ID` | yes (configuration, not a credential) | **ABSENT** |

Set them **in the host's service environment only** — never in the repository, a commit, a log, a
screenshot or this document. They are resolved solely through the ADR-0049 `CredentialProvider`;
provider plugins never read the environment themselves.

**There is no fallback.** A missing credential is a **startup failure**, verified:

```
ValueError: the deepgram stt profile requires the 'deepgram_api_key' credential,
which is not configured; refusing to fall back to the local transcriber
```

That property is what makes this handoff trustworthy: the system cannot accidentally run a fake
live test.

## 7. Browser / operator checklist — OWNER INPUT

| Item | Value | Status |
|---|---|---|
| Browser available (Chrome or Safari) | `____` | ☐ |
| Microphone present and permitted | `____` | ☐ |
| **Operator identified by name** | `____` | ☐ |
| Operator can speak the smoke utterance | `____` | ☐ |
| Browser can reach the HTTPS endpoint | `____` | ☐ |

**A human is mandatory.** No automation, synthetic microphone, prerecorded audio or scripted peer
may stand in — §10 of the live milestone forbids it, and evidence produced that way would be
worthless for `E11-15`.

---

## 8. Deployment checklist — actual repository commands

Commands below are **verified to exist in this repository**. Anything that does not exist is
marked **OWNER ACTION REQUIRED** rather than invented.

### 8.1 Prepare the host

Follow **`platform/ORACLE_DEPLOY.md` Phases 1–5 only** (VM creation, both-layer port opening, DNS,
`certbot`). **Stop before Phase 6** — it deploys Dograh.

`platform/scripts/oracle_setup.sh` may be mined for its host-firewall, public-IP-detection and
`certbot` blocks. **Do not run it whole**: it installs Docker, Dograh and coturn.

Adjust the port list to §4 (443 + ephemeral UDP; *not* the coturn ports).

### 8.2 Install prerequisites

```bash
# Python 3.12+ and uv on the host — OWNER ACTION REQUIRED (no repo script installs these)
curl -LsSf https://astral.sh/uv/install.sh | sh      # uv's own documented installer
```

### 8.3 Sync the repository and verify the lock

```bash
cd <repo>/platform
uv sync --locked --extra dev
uv lock --check          # must resolve 53 packages, unchanged
```

### 8.4 Configure environment

Copy the names from `platform/.env.example` — it documents every setting. Minimum for live:

```bash
RUNTIME_TRANSPORT=webrtc
VOXVOICE_TTS_VOICE_ID=<voice id>
VOXVOICE_ICE_STUN_URLS=            # per §5
WEBRTC_ALLOWED_ORIGINS=            # empty if the client is served same-origin
APP_ENV=prod
DATABASE_URL=<durable database>
```

### 8.5 Configure credentials

The three `VOXVOICE_SECRET_*` values, in the service environment only (§6).

### 8.6 Start the application

```bash
uv run uvicorn "api.webrtc.app:build_webrtc_app" --factory --host 0.0.0.0 --port 8000
```

Note this is **`api.webrtc.app`**, not `api.main:app`. The default application deliberately has no
signalling surface, no CORS and the loopback transport.

**Service supervision: OWNER ACTION REQUIRED.** No systemd unit exists in this repository.
Constitution Part B names systemd as the Stage-2 shape; the unit must be written and recorded here.

### 8.7 Provision an org and an agent

```bash
uv run python -m api.bootstrap "<org name>"       # prints org_id and api_key ONCE
```

Then create an agent (the org API key is required):

```bash
curl -sS -X POST https://<host>/v1/agents \
  -H "x-api-key: <org api key>" -H 'content-type: application/json' \
  -d '{"name":"Smoke","system_prompt":"Reply in one short sentence.","first_message":"<optional>"}'
```

Record the returned `id` — the browser client needs it.

### 8.8 Verify health

```bash
curl -sS https://<host>/health
```

**What green proves, and what it does not** — see §9.

---

## 9. Health check — what it actually proves

`GET /health` returns:

```json
{"ok": true, "db": true, "transport": "webrtc",
 "webrtc_signalling": true, "ice_policy": "..."}
```

| Question | Answered? |
|---|---|
| Application running | **yes** — it responded |
| Database reachable | **yes** — `db: true` |
| WebRTC capability configured | **yes** — `transport: "webrtc"`, `webrtc_signalling: true` |
| ICE policy in force | **yes** — `ice_policy` states it verbatim |
| **Provider configuration present** | **yes, by inference** — see below |
| Providers actually reachable | **NO** |
| Media path works | **NO** |
| A conversation would succeed | **NO** |

**The credential inference is sound and worth understanding.** The WebRTC application refuses to
start without all three credentials and the voice id — the composition root raises during
lifespan, so uvicorn never serves. Therefore *a `/health` that answers at all with
`transport: "webrtc"` proves the credentials resolved at startup.* **No new health endpoint is
needed**, and none was added.

**Green health is not a working call.** It says nothing about Deepgram accepting the key, Cartesia
returning audio, or a single UDP packet crossing. Only the browser smoke proves those.

---

## 10. Live smoke procedure — human-operated

1. Open **Chrome or Safari** on a machine with a working microphone.
2. Load `https://<host>/v1/webrtc/client`. Confirm the **padlock** — no secure context, no mic.
3. Enter the **org API key** and the **agent id** (§8.7). Typed by the operator; never pasted into
   source, logged or screenshotted.
4. Click **Connect** and **grant microphone permission** when the browser asks.
5. Wait for the status line to read **`connected`** (the page reports peer state as it changes).
6. **Speak the smoke utterance**, then stop and stay silent so endpointing can fire:

   > **Smoke-test utterance:** *"Hello, can you hear me? Please reply with one short sentence."*
   >
   > No exact utterance is documented anywhere in the repository — the SPEC's Hinglish phrases are
   > a Phase-2 evaluation concept, not a pipeline smoke. This line is **defined here as the
   > smoke-test utterance**: short, deterministic, ends cleanly, and invites a brief reply.

7. **Verify assistant audio is physically audible** in the browser.
8. Verify the event chain (§11).
9. Click **Hang up**.
10. Verify cleanup (§12).

**No browser automation.** This is explicitly human-operated.

---

## 11. Evidence checklist

For each item: what is expected, what makes it PASS, what is safe to record, and what must never
be recorded.

| Event | PASS condition | Safe evidence | Never record |
|---|---|---|---|
| `transport.connected` | emitted once after negotiation | timestamp, session id | SDP body |
| `transport.media_ready` | emitted **only after the first real inbound frame** | timestamp, delta from connected | audio |
| `speech_start` | emitted when the operator begins speaking | timestamp | audio |
| `speech_end` | emitted at true end-of-speech (backdated, ADR-0047 ME1) | timestamp | audio |
| `stt_final` | a final transcript arrives from **real Deepgram** | timestamp, `is_final` flag, character **count** | **transcript text** |
| `llm_first_token` | first streamed token from **real Groq** | timestamp | **model output** |
| `tts_first_audio` | first audio chunk from **real Cartesia** | timestamp, encoding, sample rate | audio bytes |
| `first_audio_out` | first frame handed to the transport | timestamp | audio bytes |
| `transport.disconnected` | emitted once on hangup, **not** `failed` | timestamp, reason | — |
| `metric.turn` | emitted if telemetry is wired | the stage map, `voice_to_voice_ms` | — |

**Never capture:** transcript text, audio, model output, credential values, authorization headers,
full SDP, or any environment dump. **Do not add EventTypes, a second event bus, or a second
telemetry producer** — Epic 10's existing path (M11.12) is the only one.

---

## 12. Cleanup checklist

Verify after hangup, using lifecycle signals rather than sleeps:

☐ browser peer closed ☐ transport closed ☐ STT stream closed ☐ LLM stream closed
☐ TTS stream closed ☐ VAD worker released ☐ Playback Engine terminated
☐ subscriptions released ☐ **rendezvous released** (not left open) ☐ session ended exactly once
☐ no orphaned runtime task

The rendezvous releases through the binding controller on `POST /v1/sessions/{id}/end`, which the
browser client calls on **Hang up**.

---

## 13. R-66 / ADR-0053 — the decision still owed

**ADR-0053 is PROPOSED and must not be self-ratified.** It proposes deriving the `stt` stage from
`speech_end → stt_final` instead of `endpoint_decided → stt_final`, because audio-driven
endpointing (ADR-0047 ME1) makes the recognizer finish *during* the hold, producing a negative
interval that ME6 drops.

**The decision the owner still owes:** *does the proposed `speech_end → stt_final` measurement
become the authoritative reading of the §A8 STT budget?*

* **While it remains PROPOSED** — the applicable semantics stay `endpoint_decided → stt_final`,
  the `stt` stage keeps reporting no number, and **`E11-12` remains INCONCLUSIVE**. A live call
  does not change this.
* **If ratified** — the 150 ms budget is unchanged (SM3), no timestamp moves (SM2), and
  `voice_to_voice_ms` keeps its meaning. **SM5 states plainly that ratification alone does not
  unblock `E11-12`** — a live measurement against a real recognizer is still required.

This document does **not** pre-decide it.

---

## 14. Certification unblock matrix

| Item | Current | Required before it can change |
|---|---|---|
| **E11-12** | INCONCLUSIVE | An **approved measurement definition** (§13) **and** qualifying live evidence against a real recognizer |
| **E11-15** | NOT EXECUTED | Stage-2 host **+** real browser **+** real providers **+** one scripted spoken call |
| **R-33** | OPEN | Real external peer/media evidence |
| **R-34** | OPEN | **Actual browser execution** of the intended path |
| **R-54** | OPEN | Real Deepgram behaviour observed |
| **R-58** | OPEN | Real Cartesia execution |
| **R-63** | OPEN | Real WebRTC **with** real providers |
| **R-66** | OPEN | The measurement decision (§13) — **not** merely a transcript arriving |
| **R-68** | OPEN | Remains a **deployment limitation** unless a genuinely multi-provider deployment exists. One live call with one provider per capability proves the mechanism, **not** deployed failover |
| **R-69** | OPEN | Remains a **durability limitation** unless in-session recovery semantics are actually exercised. A successful conversation proves nothing about durable-event recovery |
| **R-71** | **CLOSED** | — (ADR-0054; reopen only if new evidence invalidates it) |

**Nothing here closes because the handoff is complete.**

---

## 15. The live gate — all fifteen must be true before M11.16.9 starts

1. ☐ Oracle A1 exists (2 OCPU / 12 GB)
2. ☐ Public IPv4 confirmed
3. ☐ DNS resolves
4. ☐ HTTPS works, certificate valid
5. ☐ UDP path configured at **both** layers
6. ☐ ICE policy explicitly configured (§5)
7. ☐ Deepgram credential present
8. ☐ Groq credential present
9. ☐ Cartesia credential present
10. ☐ `VOXVOICE_TTS_VOICE_ID` present
11. ☐ Application deployed at the expected revision
12. ☐ `/health` passes with `transport: "webrtc"`
13. ☐ Browser reaches the page over HTTPS
14. ☐ Human operator available
15. ☐ Microphone permission grantable

**If any item is false: DO NOT START LIVE EXECUTION.** The correct state is `BLOCKED`.
