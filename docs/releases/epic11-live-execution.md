# Epic 11 — Stage-2 Deployment & Real Browser Live Smoke (M11.16.7)

**Status:** Attempted 2026-09-20 · **Verdict: `BLOCKED — external provisioning required`**
**Related:** [`epic11-live-readiness.md`](epic11-live-readiness.md) (M11.16.6) · [`epic11-live-smoke.md`](epic11-live-smoke.md) (M11.16.5) · [ADR-0054](../adr/ADR-0054-ice-server-policy-is-explicit-at-the-composition-root.md) · [ADR-0049](../adr/ADR-0049-provider-credential-injection.md) · DL-0153, DL-0231

> **No deployment was performed and no conversation was executed.** This is the **third consecutive
> milestone** blocked on the **same six external prerequisites**. The gates were re-evaluated from
> scratch rather than carried forward, and the result is unchanged.

---

## 1. What this milestone could and could not do

M11.16.7's purpose was **execution**, not engineering: deploy the existing application to the
authorized Stage-2 host and have a human speak to it. Every step of that requires something this
repository cannot produce — a cloud instance, three vendor credentials, a DNS name, a certificate,
and a person at a microphone.

**Nothing was substituted.** No local provider, no scripted `aiortc` peer presented as a browser,
no injected audio, no automation standing in for the operator, and **not** the excluded 1 GB E2
micro.

## 2. Gates — re-evaluated, not carried forward

| Gate | Status | Evidence gathered this milestone |
|---|---|---|
| **Stage-2 A1 host** | **BLOCKED — not provisioned** | No `oci` CLI, no `~/.oci`, no Terraform, no Ansible, no Pulumi. **No authorized Oracle Cloud access exists**, so no instance could be created *or queried*. DL-0153 still records provisioning as *"NOT VERIFIED — owner input required"* with no IP, DNS name or instance id. |
| **Public IPv4 / DNS** | **BLOCKED** | Downstream. No address or name was invented. The only hosts known to this machine are `13.234.213.246` and `140.245.10.153` — the latter being the **excluded** E2 micro, which was not used. |
| **HTTPS** | **BLOCKED** | Downstream. No certificate architecture was invented. |
| **UDP media range** | **BLOCKED** | Downstream. No host firewall or Oracle security list exists to inspect. |
| **Deepgram** | **BLOCKED** | **ABSENT** via the ADR-0049 seam (presence only; no value read, printed or logged). |
| **Groq** | **BLOCKED** | **ABSENT**, same method. |
| **Cartesia** | **BLOCKED** | **ABSENT**, same method. |
| **`VOXVOICE_TTS_VOICE_ID`** | **BLOCKED** | **ABSENT**. |
| **Operator / browser** | **BLOCKED** | A browser *environment* exists (Chrome, Safari). **No human operator is present in this execution context**, and §10 requires a person to grant the microphone and speak. This is the one gate no engineering can ever close from here. |
| **Deployed application** | **BLOCKED** | Nothing is deployed anywhere. |

## 3. What *was* verified (local, read-only — not live evidence)

* The **ICE policy from ADR-0054 is in force**: the default is explicitly no ICE servers, and
  `VOXVOICE_ICE_STUN_URLS` is unset, so a deployment would gather host candidates only and contact
  nothing. Re-confirmed, and unchanged from M11.16.6.
* The **live profile still refuses to start rather than downgrading** — constructing it with no
  credential raises rather than falling back to the local transcriber. This is the property that
  makes "BLOCKED" trustworthy: the system cannot accidentally have run a fake live test.
* **Tree integrity**: 153 WebRTC/ICE tests pass; **zero production-code changes** were made by this
  milestone.
* **Security**: credential-literal scan over 1418 tracked files — identifier matches only,
  **0 credential values**. No transcript, audio, model output or SDP exists to persist.

## 4. Live execution

**NOT EXECUTED.** No `transport.connected`, `transport.media_ready`, `speech_start`, `speech_end`,
`stt_final`, `llm_first_token`, `tts_first_audio`, `first_audio_out`, `transport.disconnected` or
`metric.turn` was produced, because no session ran. No timeline is reported. No cleanup was
observed because nothing was started.

## 5. Certification and risk

`E11-12` **INCONCLUSIVE**, `E11-15` **NOT EXECUTED**, Epic 11 digest unchanged at `25c9f152…`
(14/16); the control catalogue was run for verification only and not modified. ADR-0053 remains
**PROPOSED** and its semantics were not applied.

**R-33, R-34, R-54, R-58, R-63, R-66, R-68, R-69 all remain OPEN** — every one of them requires
live evidence that does not exist. **R-71 remains CLOSED**; nothing here invalidates ADR-0054.

## 6. Recommendation

Three milestones (M11.16.5, M11.16.6, M11.16.7) have now been authorized whose completion depends
on the same six external items. M11.16.6 had engineering work of its own (R-71) and completed it;
**M11.16.7 had none, and therefore produced nothing but this record.**

**A fourth live-execution milestone should not be authorized until the prerequisites exist.** The
engineering side of the live path is finished: signalling, the browser client, the composition
path, the ICE policy, the refusal-to-fall-back property and the test coverage are all in place and
green. What remains is procurement and one person with a microphone.

## 7. Exact remaining prerequisites

1. **Provision the Oracle Ampere A1** (2 OCPU / 12 GB) with a public IPv4 — `ORACLE_DEPLOY.md`
   has the recipe; open ports at **both** the Oracle security list and host iptables.
2. **DNS + HTTPS** for that host.
3. **Inbound UDP** on the media range.
4. **`VOXVOICE_SECRET_DEEPGRAM_API_KEY`, `…_GROQ_API_KEY`, `…_CARTESIA_API_KEY`** — on the host
   environment only, never in the repository.
5. **`VOXVOICE_TTS_VOICE_ID`.**
6. **A human operator** at a browser.

Plus one decision, now explicit rather than accidental: if the host's public IPv4 is NAT-mapped,
set **`VOXVOICE_ICE_STUN_URLS`**; if direct connectivity then still fails, TURN becomes a
deployment prerequisite (and is still not implemented).
