# VoxVoice — Deploy runbook (getting a real voice call working)

## TL;DR
Everything in this repo is built and verified. The **only** thing that does not
work on a local Mac is an actual spoken WebRTC call, because Docker Desktop on
macOS cannot relay WebRTC media between the host browser and the in-container
Dograh bot (there is no TURN relay IP both can reach). On any host with a
**public IP**, voice works. This runbook covers that deploy.

> Proven locally: platform API, Agent→Dograh workflow (node graph, live-validated),
> sessions, signaling WebSocket, OSS auth, and Dograh's STT/LLM/TTS pipeline all
> work. Only the media relay fails on macOS Docker — an environment limit, not a bug.

---

## 1. Run Dograh in REMOTE mode (public-IP host)

On a Linux VM with a public IP and a DNS name (e.g. `voice.example.com`):

```bash
# Get the full repo (the quickstart 2-file download omits the TURN/init scripts)
git clone https://github.com/dograh-hq/dograh.git && cd dograh

# .env — the values that make WebRTC work on a public host:
cat > .env <<EOF
ENVIRONMENT=remote
OSS_JWT_SECRET=$(openssl rand -hex 32)
PUBLIC_HOST=voice.example.com            # your DNS name
PUBLIC_BASE_URL=https://voice.example.com
BACKEND_API_ENDPOINT=https://voice.example.com
TURN_HOST=<PUBLIC_IP>                     # the server's public IP — reachable by
                                          # BOTH the browser and the bot
TURN_SECRET=$(openssl rand -hex 24)
FORCE_TURN_RELAY=false                    # direct host candidates also work here
EOF

# Bring up everything incl. nginx (HTTPS) + coturn:
docker compose --profile remote up -d
```

Open UDP/TCP **3478**, **5349**, and **49152–49200/udp** (coturn) plus **443**
on the firewall/security-group. nginx terminates HTTPS and proxies `/api/v1`
(including the signaling WebSocket) same-origin, so none of the local
work-arounds below are needed.

Create a Dograh API key (UI → Settings → API keys, or `POST /api/v1/user/api-keys`).

## 2. Point the VoxVoice platform at it

```bash
# platform/.env
ENGINE=dograh
DATABASE_URL=...                          # Supabase/Postgres in prod
DOGRAH_BASE_URL=https://voice.example.com
DOGRAH_UI_BASE_URL=https://voice.example.com
DOGRAH_API_KEY=dgr_...                    # from step 1
```

Run the API (`uvicorn api.main:app`). A consumer then does exactly what
`/webclient` does: `POST /v1/sessions` → gets `web.embed_script` → the browser
mounts the widget → real voice call over the public TURN server. Transcripts
flow via the background poller.

---

## Appendix A — Local dev (no voice) on macOS

For everything *except* a live spoken call, you can run Dograh locally:

```bash
# Full repo (needed for the local-turn init scripts)
git clone https://github.com/dograh-hq/dograh.git && cd dograh
# If host :8000 is taken, remap the api service to "8010:8000" in docker-compose.yaml
printf 'OSS_JWT_SECRET=%s\nBACKEND_API_ENDPOINT=http://localhost:8010\nENABLE_TELEMETRY=false\n' \
  "$(openssl rand -hex 32)" > .env
docker compose up -d                      # UI :3010, API :8010
```

You can exercise the full platform → Dograh path (create workflows, runs, embed
tokens, transcripts) — that all works. The browser **voice** call will reach
"Establishing Connection…" and time out (the macOS Docker TURN limitation).

### Same-origin proxy (only if you want the dashboard/UI WS to connect locally)
Next.js can't proxy the signaling WebSocket in local mode, so put a tiny nginx
in front that routes **only** `/api/v1/*` to the api (with WS upgrade) and
everything else (incl. the UI's own `/api/config/*`) to the UI:

```nginx
map $http_upgrade $connection_upgrade { default upgrade; '' close; }
server {
  listen 80;
  location /api/v1/ {
    proxy_pass http://api:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
    proxy_set_header Host $host;
    proxy_read_timeout 3600s;
  }
  location / { proxy_pass http://ui:3010; proxy_set_header Host $host; }
}
```
```bash
docker run -d --name vox-proxy --network dograh_app-network -p 8080:80 \
  -v "$PWD/vox-proxy.conf:/etc/nginx/conf.d/default.conf:ro" nginx:alpine
```
(Routing `/api/*` wholesale to the backend makes the UI fall back to Stack Auth
and error — only `/api/v1/*` belongs to the backend.)

## Appendix B — Why local voice can't work on macOS Docker
The bot runs in the api container; the browser runs on the host. WebRTC needs a
TURN relay IP reachable by **both**. On Docker Desktop for Mac:
- the container can reach `coturn` / `host.docker.internal` but **not** the host LAN IP;
- the browser can reach the host LAN IP but **not** `host.docker.internal`.

coturn has a single `external-ip`, so no relay address satisfies both. Linux
solves this with host networking; Docker Desktop for Mac has no equivalent.
A public-IP host removes the asymmetry entirely (§1).
