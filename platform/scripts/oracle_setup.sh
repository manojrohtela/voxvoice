#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# VoxVoice / Dograh — one-shot remote-mode setup for a fresh Ubuntu VM.
#
# Run this ON the Oracle VM after you've done the console-only steps:
#   1) created the VM (public IP),
#   2) opened the CLOUD firewall (VCN Security List): 80,443,3478,5349 tcp+udp,
#      49152-49200/udp,
#   3) pointed a DNS A record at the VM's public IP.
#
# Usage:
#   sudo ./oracle_setup.sh voice.yourdomain.com you@email.com
#
# It does: host firewall + Docker + Let's Encrypt cert + Dograh remote bring-up
# + health/TURN verification. Idempotent enough to re-run.
# ---------------------------------------------------------------------------
set -euo pipefail

DOMAIN="${1:?usage: oracle_setup.sh <domain> <email>}"
EMAIL="${2:?usage: oracle_setup.sh <domain> <email>}"
DOGRAH_DIR="${DOGRAH_DIR:-/opt/dograh}"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

# Public IP (Oracle metadata first, then fallback to an external echo service).
SERVER_IP="$(curl -fsS -H 'Authorization: Bearer Oracle' \
  http://169.254.169.254/opc/v2/vnics/ 2>/dev/null | grep -oE '"publicIp"\s*:\s*"[0-9.]+"' | grep -oE '[0-9.]+' | head -1 || true)"
[ -z "$SERVER_IP" ] && SERVER_IP="$(curl -fsS https://api.ipify.org || true)"
[ -z "$SERVER_IP" ] && { echo "Could not detect public IP; set SERVER_IP env and re-run."; exit 1; }
log "Public IP: $SERVER_IP   Domain: $DOMAIN"

# Sanity: does DNS already point at us? (warn, don't fail — propagation lag)
RESOLVED="$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1 || true)"
[ "$RESOLVED" != "$SERVER_IP" ] && \
  echo "WARNING: $DOMAIN resolves to '${RESOLVED:-nothing}', not $SERVER_IP. Cert issuance will fail until DNS is correct."

# --- Host firewall (cloud firewall is separate; you did that in the console) --
log "Opening host firewall ports"
iptables -C INPUT -p tcp -m multiport --dports 80,443,3478,5349 -j ACCEPT 2>/dev/null || \
  iptables -I INPUT -p tcp -m multiport --dports 80,443,3478,5349 -j ACCEPT
iptables -C INPUT -p udp -m multiport --dports 3478,5349 -j ACCEPT 2>/dev/null || \
  iptables -I INPUT -p udp -m multiport --dports 3478,5349 -j ACCEPT
iptables -C INPUT -p udp --dport 49152:49200 -j ACCEPT 2>/dev/null || \
  iptables -I INPUT -p udp --dport 49152:49200 -j ACCEPT
command -v netfilter-persistent >/dev/null 2>&1 && netfilter-persistent save || \
  (apt-get update -y && apt-get install -y iptables-persistent && netfilter-persistent save)

# --- Docker ----------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  log "Installing Docker"; curl -fsSL https://get.docker.com | sh
fi

# --- TLS cert (Let's Encrypt, standalone on :80) ---------------------------
log "Obtaining TLS certificate for $DOMAIN"
command -v certbot >/dev/null 2>&1 || { apt-get update -y && apt-get install -y certbot; }
if [ ! -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
  certbot certonly --standalone --non-interactive --agree-tos -m "$EMAIL" -d "$DOMAIN"
fi

# --- Dograh (remote profile) ------------------------------------------------
log "Setting up Dograh in $DOGRAH_DIR"
if [ ! -d "$DOGRAH_DIR/.git" ]; then
  git clone https://github.com/dograh-hq/dograh.git "$DOGRAH_DIR"
fi
cd "$DOGRAH_DIR"
mkdir -p certs
cp "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" certs/local.crt
cp "/etc/letsencrypt/live/$DOMAIN/privkey.pem"   certs/local.key

# Preserve secrets across re-runs.
if [ -f .env ] && grep -q OSS_JWT_SECRET .env; then
  log ".env exists — keeping existing secrets"
else
  cat > .env <<EOF
ENVIRONMENT=production
OSS_JWT_SECRET=$(openssl rand -hex 32)
SERVER_IP=$SERVER_IP
PUBLIC_HOST=$DOMAIN
PUBLIC_BASE_URL=https://$DOMAIN
BACKEND_API_ENDPOINT=https://$DOMAIN
MINIO_PUBLIC_ENDPOINT=https://$DOMAIN
TURN_HOST=$DOMAIN
TURN_SECRET=$(openssl rand -hex 24)
FASTAPI_WORKERS=1
ENABLE_TELEMETRY=false
EOF
fi

log "Bringing up Dograh (remote profile)"
docker compose --profile remote up -d

# --- Verify ----------------------------------------------------------------
log "Waiting for health (up to ~90s)"
for i in $(seq 1 18); do
  H="$(curl -fsS "https://$DOMAIN/api/v1/health" 2>/dev/null || true)"
  if echo "$H" | grep -q '"status":"ok"'; then
    echo "$H"
    echo "$H" | grep -q '"turn_enabled":true' && log "turn_enabled: true ✓" || \
      echo "WARNING: turn_enabled is not true — check TURN_HOST/SERVER_IP and firewall."
    break
  fi
  sleep 5
done

cat <<DONE

────────────────────────────────────────────────────────────
Dograh is up at:  https://$DOMAIN
Next:
  1) Open https://$DOMAIN -> sign up -> Settings -> API keys -> create (dgr_…)
  2) On your VoxVoice host, set platform/.env:
       ENGINE=dograh
       DOGRAH_BASE_URL=https://$DOMAIN
       DOGRAH_UI_BASE_URL=https://$DOMAIN
       DOGRAH_API_KEY=dgr_...
  3) Run the API, open /webclient, start a session, click Start Voice Call.
────────────────────────────────────────────────────────────
DONE
