#!/usr/bin/env bash
# Install Jayti Hub + nginx on a fresh Ubuntu VPS (jayti-agent-db).
# Paste into Vultr console, or: bash /path/to/vps_install_hub.sh
# Never prints secrets from hub.env.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
SRC=/opt/jayti/src
HUB=/opt/jayti/hub
UNIT=/etc/systemd/system/jayti-hub.service
ENVF=/etc/jayti/hub.env
CERT=/etc/letsencrypt/live/agent.jaytipargal.tech
SITE=/etc/nginx/sites-available/jaytipargal-agent

echo "=== paths ==="
for d in /opt/jayti /opt/eka-agent /etc/nginx /etc/jayti; do
  if [[ -e "$d" ]]; then echo "EXISTS $d"; else echo "MISSING $d"; fi
done

finish() {
  echo "=== listeners ==="
  ss -ltnp 2>/dev/null | grep -E ':(22|80|443|8443)\b' || ss -ltn | grep -E ':(22|80|443|8443)\b' || true
  echo "=== healthz ==="
  curl -sS -m 10 -w '\nHTTP:%{http_code}\n' http://127.0.0.1/healthz || true
}

if [[ -f "$HUB/jayti_hub_server.py" && -f "$UNIT" ]]; then
  echo "Existing hub + unit found; starting (no wipe)"
  systemctl daemon-reload
  systemctl enable --now jayti-hub 2>/dev/null || systemctl start jayti-hub || true
  systemctl enable --now nginx 2>/dev/null || systemctl start nginx || true
  finish
  exit 0
fi

echo "=== apt ==="
apt-get update -y
apt-get install -y nginx postgresql postgresql-contrib python3-venv python3-pip git \
  certbot python3-certbot-nginx curl

systemctl enable --now postgresql

if [[ ! -d "$SRC/.git" ]]; then
  echo "=== clone ==="
  mkdir -p /opt/jayti
  git clone --depth 1 https://github.com/jaytipargal/jayti.git "$SRC"
fi

install -d -m 700 /etc/jayti
install -d -m 755 "$HUB"

# Postgres role/db (jayti_writer / eka_agent) - create only if missing
DB_USER=jayti_writer
DB_NAME=eka_agent
ROLE_EXISTS=$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" || true)
DB_EXISTS=$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" || true)

WRITE_ENV=0
if [[ ! -f "$ENVF" || "$ROLE_EXISTS" != "1" ]]; then
  DB_PASS=$(openssl rand -base64 24)
  BOOT_KEY=$(openssl rand -hex 32)
  WRITE_ENV=1
fi
if [[ "$ROLE_EXISTS" != "1" ]]; then
  sudo -u postgres psql -v ON_ERROR_STOP=1 -v pw="$DB_PASS" \
    -c "CREATE USER ${DB_USER} WITH ENCRYPTED PASSWORD :'pw'"
elif [[ "$WRITE_ENV" -eq 1 ]]; then
  sudo -u postgres psql -v ON_ERROR_STOP=1 -v pw="$DB_PASS" \
    -c "ALTER USER ${DB_USER} WITH ENCRYPTED PASSWORD :'pw'"
fi
if [[ "$DB_EXISTS" != "1" ]]; then
  sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}"
fi
sudo -u postgres psql -v ON_ERROR_STOP=1 -c \
  "GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER}"
sudo -u postgres psql -v ON_ERROR_STOP=1 -d "$DB_NAME" -c \
  "GRANT ALL ON SCHEMA public TO ${DB_USER}; GRANT CREATE ON SCHEMA public TO ${DB_USER}"
if [[ "$WRITE_ENV" -eq 1 ]]; then
  DB_PASS_ENC=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$DB_PASS")
  umask 077
  cat > "$ENVF" <<ENVEOF
JAYTI_PG_DSN=postgresql://${DB_USER}:${DB_PASS_ENC}@127.0.0.1:5432/${DB_NAME}
JAYTI_BOOTSTRAP_KEY=${BOOT_KEY}
JAYTI_HOST=127.0.0.1
JAYTI_PORT=8443
ENVEOF
  chmod 600 "$ENVF"
  echo "hub.env written"
  unset DB_PASS DB_PASS_ENC BOOT_KEY
fi

# Schema (idempotent) + api_keys used by hub auth
sudo -u postgres psql -v ON_ERROR_STOP=1 -d "$DB_NAME" <<'SQLEOF'
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE TABLE IF NOT EXISTS device_registry (
    device_id VARCHAR(50) PRIMARY KEY, device_name VARCHAR(100) NOT NULL,
    device_type VARCHAR(30) NOT NULL, os VARCHAR(30) NOT NULL, location VARCHAR(100),
    agent_version VARCHAR(50), last_seen TIMESTAMPTZ, is_active BOOLEAN NOT NULL DEFAULT true,
    apps JSONB NOT NULL DEFAULT '[]'::jsonb, credentials JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS ingestion_queue (
    id BIGSERIAL PRIMARY KEY, device VARCHAR(50) NOT NULL, source VARCHAR(200) NOT NULL,
    data_type VARCHAR(50) NOT NULL, content JSONB NOT NULL, content_hash VARCHAR(64) NOT NULL UNIQUE,
    device_time TIMESTAMPTZ NOT NULL, ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status VARCHAR(20) NOT NULL DEFAULT 'new', processed_at TIMESTAMPTZ,
    priority VARCHAR(5) DEFAULT NULL, hidden_data JSONB DEFAULT NULL);
CREATE TABLE IF NOT EXISTS mobility_map (
    email VARCHAR(100) PRIMARY KEY, owner VARCHAR(100) NOT NULL, email_type VARCHAR(30) NOT NULL,
    devices JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS audit_trail (
    log_id BIGSERIAL PRIMARY KEY, timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    agent_session VARCHAR(100), device VARCHAR(50), action_type VARCHAR(30) NOT NULL,
    action_detail TEXT, input_ref TEXT, output_ref TEXT, data_affected JSONB, priority VARCHAR(5),
    correlation_ids JSONB, integrity_hash VARCHAR(64), duration_ms INTEGER,
    status VARCHAR(15) NOT NULL, error TEXT);
CREATE TABLE IF NOT EXISTS correlations (
    correlation_id VARCHAR(80) PRIMARY KEY, type VARCHAR(30) NOT NULL, devices JSONB NOT NULL,
    evidence JSONB NOT NULL, relationship TEXT NOT NULL, confidence VARCHAR(10) NOT NULL,
    timestamp_correlation TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS training_status (
    batch_id VARCHAR(50) PRIMARY KEY, batch_date DATE NOT NULL,
    chunks_created INTEGER NOT NULL DEFAULT 0, duplicates INTEGER NOT NULL DEFAULT 0,
    p0_found INTEGER NOT NULL DEFAULT 0, p1_found INTEGER NOT NULL DEFAULT 0,
    lora_adapter VARCHAR(200), train_status VARCHAR(20) NOT NULL DEFAULT 'pending',
    trained_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS api_keys (
    key_id UUID PRIMARY KEY, device_id VARCHAR(50) NOT NULL REFERENCES device_registry(device_id),
    key_hash TEXT NOT NULL, key_prefix VARCHAR(16),
    issued_at TIMESTAMPTZ NOT NULL DEFAULT now(), expires_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ, last_used_at TIMESTAMPTZ);
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO jayti_writer;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO jayti_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO jayti_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO jayti_writer;
SQLEOF

echo "=== hub app ==="
cp "$SRC/server/jayti-hub/jayti_hub_server.py" "$HUB/"
cp "$SRC/server/jayti-hub/jayti-hub.service" "$UNIT"
if [[ ! -x "$HUB/venv/bin/python" ]]; then
  python3 -m venv "$HUB/venv"
fi
"$HUB/venv/bin/pip" install -q --upgrade pip
"$HUB/venv/bin/pip" install -q fastapi 'uvicorn[standard]' asyncpg argon2-cffi

echo "=== nginx ==="
install -d /etc/nginx/snippets /var/www/html
cp "$SRC/nginx/snippets/eka-agent-locations.conf" /etc/nginx/snippets/
if [[ ! -f /etc/nginx/conf.d/eka-agent-api-key.conf ]]; then
  API_HEX=$(openssl rand -hex 32)
  install -m 600 /dev/null /etc/nginx/conf.d/eka-agent-api-key.conf
  cat > /etc/nginx/conf.d/eka-agent-api-key.conf <<KEYEOF
map_hash_bucket_size 128;
map \$http_x_api_key \$eka_agent_key_ok {
    default 0;
    "${API_HEX}" 1;
}
KEYEOF
  chmod 600 /etc/nginx/conf.d/eka-agent-api-key.conf
  unset API_HEX
fi
rm -f /etc/nginx/sites-enabled/default

if [[ -f "$CERT/fullchain.pem" && -f "$CERT/privkey.pem" ]]; then
  cp "$SRC/nginx/sites/agent.jaytipargal.tech.conf" "$SITE"
else
  cat > "$SITE" <<'NGX'
server {
    listen 80;
    listen [::]:80;
    server_name agent.jaytipargal.tech _;
    location /.well-known/acme-challenge/ { root /var/www/html; }
    include snippets/eka-agent-locations.conf;
}
NGX
  echo "NOTE: TLS certs missing - HTTP:80 only; run: certbot --nginx -d agent.jaytipargal.tech"
fi
ln -sfn "$SITE" /etc/nginx/sites-enabled/jaytipargal-agent
nginx -t
certbot --nginx -d agent.jaytipargal.tech --non-interactive --agree-tos \
  --register-unsafely-without-email --redirect 2>/dev/null \
  || echo "NOTE: certbot skipped/failed; 443 still needs: certbot --nginx -d agent.jaytipargal.tech"

systemctl daemon-reload
systemctl enable --now jayti-hub nginx
finish
