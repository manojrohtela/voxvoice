# Deploy Dograh (remote mode) on a 2nd Oracle Cloud VM

Goal: a public-IP host where WebRTC voice actually works, isolated from your
VoxHire prod box. Uses Dograh's official `remote` profile (nginx HTTPS + coturn).
Dograh images are multi-arch, so the free **ARM A1** shape is fine.

**You need:** an SSH keypair, and a **DNS name** you control (e.g.
`voice.yourdomain.com`) — Dograh remote mode requires HTTPS, and the browser
needs a secure context for the mic.

---

## Phase 1 — Create the VM (Oracle Console)

Compute → **Instances** → **Create instance**:
- **Image:** Canonical Ubuntu 22.04.
- **Shape:** Ampere **VM.Standard.A1.Flex** (ARM) — **2 OCPU / 12 GB** (well within the free 4 OCPU / 24 GB; leaves headroom for VoxHire).
- **Networking:** assign a **public IPv4**.
- **SSH keys:** paste your public key.
- Create, then note the **public IP** (call it `SERVER_IP`).

> If you hit "Out of host capacity" on A1, try another Availability Domain or region — it's a known free-tier capacity quirk, not your account.

## Phase 2 — Open ports (BOTH layers — this is the #1 Oracle gotcha)

**(a) Cloud firewall** — VCN → your subnet's **Security List** (or an NSG) → add **Ingress** rules (Source `0.0.0.0/0`):

| Port(s) | Proto | Why |
|---|---|---|
| 80 | TCP | Let's Encrypt HTTP challenge |
| 443 | TCP | HTTPS (UI + API + signaling WS) |
| 3478 | TCP & UDP | coturn (TURN/STUN) |
| 5349 | TCP & UDP | coturn over TLS |
| 49152–49200 | UDP | coturn media relay range |

**(b) Host firewall** — Oracle's Ubuntu image ships restrictive iptables. SSH in and open the same ports:
```bash
sudo iptables -I INPUT -p tcp -m multiport --dports 80,443,3478,5349 -j ACCEPT
sudo iptables -I INPUT -p udp -m multiport --dports 3478,5349 -j ACCEPT
sudo iptables -I INPUT -p udp --dport 49152:49200 -j ACCEPT
sudo netfilter-persistent save        # persist across reboots
```

## Phase 3 — DNS
Create an **A record**: `voice.yourdomain.com` → `SERVER_IP`. Wait for it to resolve (`dig +short voice.yourdomain.com`).

## Phase 4 — Install Docker (on the VM)
```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker
```

## Phase 5 — TLS certificate (Let's Encrypt)
```bash
sudo apt-get update && sudo apt-get install -y certbot
sudo certbot certonly --standalone -d voice.yourdomain.com   # needs port 80 open (Phase 2)
```
You'll wire these into Dograh's `certs/` in the next phase.

## Phase 6 — Deploy Dograh (remote profile)
```bash
git clone https://github.com/dograh-hq/dograh.git && cd dograh
mkdir -p certs
sudo cp /etc/letsencrypt/live/voice.yourdomain.com/fullchain.pem certs/local.crt
sudo cp /etc/letsencrypt/live/voice.yourdomain.com/privkey.pem   certs/local.key
sudo chown $USER: certs/local.*

# .env — every value below is REQUIRED by Dograh's remote validator, and the
# equalities are enforced (BACKEND_API_ENDPOINT==PUBLIC_BASE_URL, TURN_HOST==PUBLIC_HOST, etc.)
cat > .env <<EOF
ENVIRONMENT=production
OSS_JWT_SECRET=$(openssl rand -hex 32)
SERVER_IP=<SERVER_IP>
PUBLIC_HOST=voice.yourdomain.com
PUBLIC_BASE_URL=https://voice.yourdomain.com
BACKEND_API_ENDPOINT=https://voice.yourdomain.com
MINIO_PUBLIC_ENDPOINT=https://voice.yourdomain.com
TURN_HOST=voice.yourdomain.com
TURN_SECRET=$(openssl rand -hex 24)
FASTAPI_WORKERS=1
ENABLE_TELEMETRY=false
EOF

docker compose --profile remote up -d
```
nginx now serves everything on **https://voice.yourdomain.com** (UI + `/api/v1` + signaling WS, same-origin), and coturn relays via `SERVER_IP` — reachable by both the browser and the in-container bot, so **voice connects**.

Check: `curl https://voice.yourdomain.com/api/v1/health` → `turn_enabled: true`.

## Phase 7 — Create a Dograh API key
Open `https://voice.yourdomain.com` → sign up → **Settings → API keys** → create. Copy the `dgr_…` key.

## Phase 8 — Point VoxVoice at it
In `platform/.env`:
```bash
ENGINE=dograh
DOGRAH_BASE_URL=https://voice.yourdomain.com
DOGRAH_UI_BASE_URL=https://voice.yourdomain.com
DOGRAH_API_KEY=dgr_...
DATABASE_URL=...        # Supabase/Postgres for prod (or sqlite to start)
```
Run `uvicorn api.main:app`. Create an agent (`POST /v1/agents`), open `/webclient`, start a session, click **Start Voice Call**, allow mic — real voice over the public TURN. Transcripts flow via the background poller.

---

### Notes
- **Quick test without DNS:** set `PUBLIC_HOST`/`TURN_HOST` to `SERVER_IP` and use a self-signed cert. The browser will warn (click through); mic/WebRTC usually works once accepted, but a real LE cert is more reliable — recommended for any demo.
- **Resources:** the full stack (postgres+redis+minio+api+ui+coturn+nginx) runs comfortably in 2 OCPU / 12 GB.
- **Cert renewal:** add a cron/systemd-timer to `certbot renew` and re-copy into `certs/` + `docker compose restart nginx`.
- This is the same model as your VoxHire Oracle deploy — just a separate instance so the two never contend.
