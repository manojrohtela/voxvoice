# Stage-2 Voice Host Runbook

| | |
|---|---|
| **Status** | Active v1.0 (M11.18) |
| **Date** | 2026-10-19 |
| **Subject** | Deploying the **voice runtime + console** on one cloud VM so a real spoken call works |
| **Supersedes** | `platform/DEPLOY.md`, `platform/ORACLE_DEPLOY.md`, `platform/scripts/oracle_setup.sh` (Dograh-era, removed at M11.18) |
| **Authority** | [ADR-0054](../adr/ADR-0054-ice-server-policy-is-explicit-at-the-composition-root.md) (ICE) · [ADR-0049](../adr/ADR-0049-provider-credential-injection.md) (credentials) · [pre-live handoff](../releases/epic11-pre-live-handoff.md) (the checklists this operationalises) |
| **Not this document** | [`operations/deployment-guide.md`](deployment-guide.md) covers the Epic 8 HTTP API (Postgres, `VOXVOICE_API_*`). It has nothing to do with voice. |

> **No secret belongs in this file.** Credentials are referred to by variable name only.

## 0. What this gets you, and what it does not

It gets a host where **a browser can hold a real spoken conversation** with an agent: HTTPS (so the
microphone is granted), WebRTC media that actually flows, real Deepgram/Groq/Cartesia, and the
console. That is the evidence `E11-15` requires.

It does **not** make this a production release. GOVERNANCE §10 gates remain, and `E11-12` is still
inconclusive. Treat this host as a **test host**.

## 1. The one decision that determines everything: NAT shape

**TURN is not implemented.** `providers/transport/ice.py` accepts `stun:`/`stuns:` and **refuses
`turn:`/`turns:` at startup**, deliberately, so nobody believes they have relaying they do not
have. So the host's network shape decides whether voice works at all.

Run this **first, on the host**, before configuring anything:

```bash
ip -4 addr show      # is the PUBLIC IPv4 on an interface, or a private 10.x / 172.16.x?
ip -6 addr show      # is there a routable (non-fe80::) IPv6 address?
```

| What you see | What to do |
|---|---|
| **Public IPv4 on the interface** (Hetzner, DigitalOcean, Vultr, Linode) | Leave `VOXVOICE_ICE_STUN_URLS` **empty**. Host candidates are routable; nothing is contacted. |
| **Private IPv4 only** (Oracle, AWS, GCP, Azure — 1:1 NAT) | Set `VOXVOICE_ICE_STUN_URLS`. OCI's 1:1 NAT is static and full-cone-like, so STUN is sufficient. |
| **Routable IPv6 present** | Even better — IPv6 sits directly on the NIC, so there is no NAT at all. Enable it *in addition* to IPv4+STUN and let ICE pick whichever pair connects. |

> **Enable IPv6 on the VCN at creation time.** On Oracle, assign the VCN an IPv6 CIDR *and* give
> the subnet one when you create them. Retrofitting IPv6 onto an existing VCN and subnet is
> possible but fiddly, and it is the kind of change you do not want to be making while debugging a
> call that connects and then carries no media. Decide it before the instance exists.
| **Neither works after all of the above** | Record **"TURN is a deployment prerequisite"** and stop. Do not fake it. |

**Why TURN might still be needed:** relaying is for **symmetric** NAT, and it is needed when
*either* peer is symmetric. The server side is fine on the shapes above. The **caller's** side may
not be — Indian mobile **CGNAT** is the realistic failure case. Test from desktop broadband first,
then from mobile; a mobile-only failure is your TURN evidence.

## 2. Host

The repo's choice is DL-0153: **Oracle Ampere A1 Flex, 2 OCPU / 12 GB, Ubuntu 22.04, public IPv4**.
The 1 GB E2 micro (`140.245.10.153`) is **excluded** — it runs 13 other agents and is the wrong
class. If A1 capacity is unavailable ("Out of host capacity"), try another AD or region; it is a
known free-tier quirk.

**Sizing reality check (measured at M11.18):** the whole dependency set is **169 MB** installed
(`av` 44 MB, sqlalchemy 18 MB, cryptography 13 MB) with **no torch, no ONNX, no GPU**, one
`uvicorn` process and a SQLite file. **2 vCPU / 2–4 GB is ample** for a first live call. The 12 GB
A1 is headroom, not a requirement — which means a ~€4/month Hetzner CX22 also fits, and has the
better NAT shape (§1). Changing provider away from Oracle contradicts DL-0153, so record a decision
if you do.

| Item | Value |
|---|---|
| Provider / shape | `____` |
| vCPU / RAM | `____` |
| Public IPv4 | `____` |
| Routable IPv6 | `____` |
| OS | `____` |
| Instance id / OCID | `____` |

## 3. Firewall — **four** layers, not two

This is where most of the lost time goes. Cloud firewall and host firewall are independent, **and
IPv6 has its own ruleset**: `ip6tables` is not `iptables`. Opening IPv4 does nothing for IPv6.

| Layer | IPv4 | IPv6 |
|---|---|---|
| Cloud (OCI Security List / NSG, or provider firewall) | ☐ | ☐ |
| Host (`iptables`) | ☐ | — |
| Host (`ip6tables`) | — | ☐ |

Open:

* **TCP 443** — HTTPS. Required.
* **TCP 80** — only if using ACME HTTP-01.
* **UDP ephemeral range** — the media path. Narrow it first so the rule is small:

```bash
sudo sysctl -w net.ipv4.ip_local_port_range="50000 50100"   # persist in /etc/sysctl.d/
```

**Oracle's Ubuntu images ship REJECT rules.** People open the Security List, see nothing work, and
lose an afternoon. The handoff calls this *"the #1 Oracle gotcha — both layers are required"*.

**Do not open 3478 / 5349 / 49152–49200.** Those were coturn ports from the removed Dograh runbook.
No TURN is deployed.

## 4. HTTPS — and the gap you must close yourself

**A browser will not grant a microphone outside a secure context.** HTTPS is not optional, and
**no TLS wiring exists in this repository**: `certbot` produces a certificate and nothing connects
it to the application. Pick one and record it here:

| Option | Shape |
|---|---|
| **(a) `uvicorn --ssl-keyfile/--ssl-certfile`** — *recommended* | Matches the Constitution's "one cloud VM + systemd" profile; fewest moving parts; no proxy to misconfigure |
| (b) TLS-terminating reverse proxy | More familiar, but no existing deployment document mandates it, and it adds a hop that can mangle SSE (the diagnostics stream) |

```
Hostname        ____        A record → public IPv4 ____    propagation confirmed ☐
                            AAAA record → IPv6 ____ (if §1 uses IPv6)
Certificate     ____        valid, not self-signed ☐       padlock, no warning ☐
TLS method      (a) uvicorn --ssl-*  — chosen 2026-10-19
```

### 4.1 Two things (a) needs, or it breaks silently

**Certbot's files are root-owned**, and the service must not run as root. **And `uvicorn` reads the
certificate once at startup** — it does not notice a renewal, so a renewed certificate is ignored
until the process restarts. Ninety days later the site serves an expired certificate, the browser
refuses the secure context, and the microphone stops being granted. Both problems are solved by one
deploy hook that publishes readable copies and restarts the service:

```bash
# /etc/letsencrypt/renewal-hooks/deploy/10-voxvoice.sh   (chmod 0755)
#!/usr/bin/env bash
set -euo pipefail
install -d -m 0750 -o root -g voxvoice /etc/voxvoice/tls
install -m 0640 -o root -g voxvoice "$RENEWED_LINEAGE/privkey.pem"   /etc/voxvoice/tls/privkey.pem
install -m 0644 -o root -g voxvoice "$RENEWED_LINEAGE/fullchain.pem" /etc/voxvoice/tls/fullchain.pem
systemctl restart voxvoice
```

Point `uvicorn` at `/etc/voxvoice/tls/*`, **not** at `/etc/letsencrypt/live/…`. Run the hook once
by hand (or `certbot renew --force-renewal`) so the files exist before first start, and verify
renewal end to end with:

```bash
sudo certbot renew --dry-run     # exercises the hook without consuming a rate limit
```

The restart drops any call in progress. On a test host that is the right trade; note it if this
host ever carries traffic you care about.

## 5. Environment

Exported in the **service's** environment only — never a committed file.

```bash
# Credentials (ADR-0049). A missing one is a STARTUP FAILURE, never a silent downgrade.
VOXVOICE_SECRET_DEEPGRAM_API_KEY=…
VOXVOICE_SECRET_GROQ_API_KEY=…
VOXVOICE_SECRET_CARTESIA_API_KEY=…
VOXVOICE_TTS_VOICE_ID=…          # configuration, not a credential; REQUIRED when transport=webrtc

# Runtime
RUNTIME_TRANSPORT=webrtc
DATABASE_URL=sqlite:////var/lib/voxvoice/voxvoice.db

# ICE — §1 decides this. Empty means "explicitly none" (ADR-0054); it is never inherited.
VOXVOICE_ICE_STUN_URLS=stun:stun.l.google.com:19302    # only if behind 1:1 NAT

# Console
VOXVOICE_CONSOLE_DIST=/opt/voxvoice/console/dist
VOXVOICE_CONSOLE_SIGNUP=closed     # first_run | open | closed
APP_ENV=prod                       # cookie_secure defaults to true whenever APP_ENV != dev
```

Startup refuses an incomplete configuration (`WebRTCSettings.validate`): an unknown transport, or
`transport=webrtc` without `VOXVOICE_TTS_VOICE_ID`, and a wildcard `WEBRTC_ALLOWED_ORIGINS` outside
`APP_ENV=dev`. **If the process starts, every credential was present** — that is the check; there is
no separate one.

Note `VOXVOICE_ICE_STUN_URLS` is **comma-separated**, and a `turn:` URL fails startup by design.

## 6. Install and run

```bash
# Build the console on the host (or ship dist/)
cd /opt/voxvoice/console && npm ci && npm run build

cd /opt/voxvoice/platform
uv sync --locked --extra dev
uv run python -c "import aiortc, av, deepgram, groq, cartesia, pysilero_vad; print('ok')"
```

Serve the **console app** — it wraps the WebRTC app unchanged and adds `/console/`:

`--host ::` binds both families (dual-stack) — needed for the IPv6 path in §1.

```ini
# /etc/systemd/system/voxvoice.service
[Unit]
Description=VoxVoice voice runtime + console
After=network-online.target
Wants=network-online.target

[Service]
User=voxvoice
Group=voxvoice
WorkingDirectory=/opt/voxvoice/platform
# 0640 root:voxvoice — this file holds the provider credentials (§5)
EnvironmentFile=/etc/voxvoice/voxvoice.env
ExecStart=/opt/voxvoice/platform/.venv/bin/uvicorn api.console.app:build_console_app --factory \
          --host :: --port 443 \
          --ssl-keyfile /etc/voxvoice/tls/privkey.pem \
          --ssl-certfile /etc/voxvoice/tls/fullchain.pem

# Bind 443 without being root. Without this the service either runs as root (don't) or
# fails with "Permission denied" on the port and the cause is not obvious from the log.
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=/var/lib/voxvoice
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
```

```bash
sudo useradd --system --home /opt/voxvoice --shell /usr/sbin/nologin voxvoice
sudo install -d -o voxvoice -g voxvoice /var/lib/voxvoice     # the SQLite file lives here
sudo systemctl daemon-reload && sudo systemctl enable --now voxvoice
journalctl -u voxvoice -f                                      # watch the startup refusals (§5)
```

`ProtectSystem=strict` makes the filesystem read-only except `ReadWritePaths`, so the database
directory must be listed or the first write fails. If you add anything else that writes, add it
there too.

Create the first account once, then set `VOXVOICE_CONSOLE_SIGNUP=closed`:

```bash
uv run python -m api.console.bootstrap_owner --email you@example.com --new-org "Your org"
```

## 7. Verify, in this order

```bash
curl -sS https://<host>/health
# {"ok":true,"db":true,"transport":"webrtc","webrtc_signalling":true,"ice_policy":"…"}
```

`transport: "webrtc"` confirms the real stack. **Green health does not mean a call will work** — it
says nothing about media flowing or the providers accepting your keys.

1. Open `https://<host>/console/`, sign in.
2. **Agents → New agent.** Language **`en`** (the recognizer declares only `en`; F-4). **Set
   Model explicitly** — see §8.
3. **Playground → Start call**, allow the microphone, wait for **Connected**.
4. Say *"Hello. Tell me briefly what you can do."* then **stop and stay quiet** — endpointing fires
   on trailing silence.
5. **Diagnostics panel** (M12.14) shows the runtime's own events live; it is the first place to look
   when something fails.
6. **Hang up.** Check the Conversation record: transcript, per-turn latency, timeline.

**Test from desktop broadband first, then from mobile** (§1, CGNAT).

## 8. Known traps, each one observed

| Trap | Symptom | Fix |
|---|---|---|
| **Stale default model** | Every turn cancelled, no reply; diagnostics show `llm: stream: ProviderProtocolError` | A vendor retires models without a release of ours. **Set the agent's Model field.** Check your account's catalogue first. `DEFAULT_MODEL` was `llama-3.3-70b-versatile` until it was retired (found by the first real call, 2026-10-08). |
| Agent language `en-IN` | Greeting plays, then the call drops | Use `en` (F-4). The console prevents this; the developer API's default does not. |
| Speakers instead of headphones | Agent interrupts itself | Headphones. It hears its own output. |
| IPv4 open, IPv6 silent | Works for some callers, not others | `ip6tables` is a separate ruleset (§3) |
| Connected, then silence | ICE negotiated, no media | NAT shape (§1) — add STUN, or record TURN as a prerequisite |
| Barge-in does nothing | Speaking over the agent is ignored | **Expected.** F-1: `allow_interruptions` never reaches the runtime on the product path. |

## 9. Teardown

```bash
sudo systemctl stop voxvoice && sudo systemctl disable voxvoice
```

The host carries no state worth keeping beyond the SQLite file; delete it, or back it up if it holds
evidence. Revoke the console account and rotate the provider keys if the host is being
decommissioned or was ever shared.

## 10. What this runbook deliberately leaves open

| Open item | Why |
|---|---|
| **TLS wiring** (§4) | Decided 2026-10-19: `uvicorn --ssl-*` with a certbot deploy hook. No repository artefact implements it — the unit and hook in §4.1/§6 are this document's, not code. |
| **TURN** | Not implemented. A `turn:` URL is refused at startup. If §1 exhausts, this is a real blocker, not a configuration step. |
| **Postgres** | Not an `E11` requirement and unexercised. SQLite is what is tested. |
| **Multi-worker / scale** | The rendezvous registry is in-process (R-E12-5). One worker. |
| **`E11-12`** | Still inconclusive. A live run produces the measurement; it does not by itself resolve ADR-0053, which is unratified. |
