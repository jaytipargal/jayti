#!/usr/bin/env bash
# Complete jayti-agent-db Hub recovery: schema, register, sandbox seed ingest.
# Paste into the Vultr web console (jayti-agent-db / 139.84.165.81):
#   cat << 'EOF' | bash
#   ...this file...
#   EOF
#
# Uses http://127.0.0.1:8443 only (public HTTPS may be down).
# Never echoes JAYTI_BOOTSTRAP_KEY or the DB password.
# Prints device keys once between BEGIN OUTPUT / END OUTPUT — copy immediately.
set +x
set -u

HUB=http://127.0.0.1:8443
HUB_ENV=/etc/jayti/hub.env
DEVDIR=/etc/jayti/devices
SANDBOX_ID=jtagent_sandbox
WIN_ID=windows_pc_abcom
SANDBOX_KEY=
WIN_KEY=
REGISTER_SANDBOX_HTTP=
REGISTER_WIN_HTTP=
INGEST_HTTP=
BEFORE_DEVICES=
BEFORE_KEYS=
BEFORE_ITEMS=
AFTER_DEVICES=
AFTER_KEYS=
AFTER_ITEMS=
AFTER_UNPROCESSED=
STATUS_JSON=
BOOTSTRAP_KEY=

print_output() {
  printf '%s\n' \
    '===== BEGIN OUTPUT =====' \
    "before_devices=${BEFORE_DEVICES:-}" \
    "before_keys=${BEFORE_KEYS:-}" \
    "before_items=${BEFORE_ITEMS:-}" \
    "register_sandbox_http=${REGISTER_SANDBOX_HTTP:-}" \
    "register_windows_http=${REGISTER_WIN_HTTP:-}" \
    "ingest_http=${INGEST_HTTP:-}" \
    "after_devices=${AFTER_DEVICES:-}" \
    "after_keys=${AFTER_KEYS:-}" \
    "after_items=${AFTER_ITEMS:-}" \
    "after_unprocessed=${AFTER_UNPROCESSED:-}" \
    "status_json=${STATUS_JSON:-}" \
    "sandbox_device_id=${SANDBOX_ID}" \
    "sandbox_device_key=${SANDBOX_KEY:-}" \
    "windows_device_id=${WIN_ID}" \
    "windows_device_key=${WIN_KEY:-}" \
    "windows_env_hint=save the windows key as %USERPROFILE%\\.eka_agent\\device.env with EKA_VPS_URL=https://agent.jaytipargal.tech" \
    '===== END OUTPUT ====='
}

cleanup() {
  unset BOOTSTRAP_KEY || true
  rm -f /tmp/jtagent_rec_*.json /tmp/jtagent_rec_*.hdr /tmp/jtagent_rec_*.py 2>/dev/null || true
}

trap 'rc=$?; cleanup || true; print_output || true; exit $rc' EXIT

count_sql() {
  sudo -u postgres psql -d eka_agent -At -c "$1" 2>/dev/null | tr -d '[:space:]' || true
}

BEFORE_DEVICES="$(count_sql 'SELECT count(*) FROM device_registry')"
BEFORE_KEYS="$(count_sql 'SELECT count(*) FROM api_keys')"
BEFORE_ITEMS="$(count_sql 'SELECT count(*) FROM ingestion_queue')"

sudo -u postgres psql -v ON_ERROR_STOP=1 -d eka_agent <<'SQL'
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
ALTER TABLE device_registry ADD COLUMN IF NOT EXISTS apps JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE device_registry ADD COLUMN IF NOT EXISTS credentials JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE device_registry ADD COLUMN IF NOT EXISTS agent_version VARCHAR(50);
ALTER TABLE device_registry ADD COLUMN IF NOT EXISTS location VARCHAR(100);
ALTER TABLE device_registry ADD COLUMN IF NOT EXISTS last_seen TIMESTAMPTZ;
ALTER TABLE device_registry ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE device_registry ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE device_registry ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS key_prefix VARCHAR(16);
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS issued_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ;
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMPTZ;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO jayti_writer;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO jayti_writer;
SQL

systemctl start jayti-hub nginx || true
sleep 1

[[ -r "$HUB_ENV" ]] || { echo "error: cannot read $HUB_ENV" >&2; exit 1; }
BOOTSTRAP_KEY="$(sed -n 's/^JAYTI_BOOTSTRAP_KEY=//p' "$HUB_ENV")"
BOOTSTRAP_KEY="${BOOTSTRAP_KEY%%$'\n'*}"
BOOTSTRAP_KEY="${BOOTSTRAP_KEY%$'\r'}"
[[ -n "$BOOTSTRAP_KEY" ]] || { echo "error: no JAYTI_BOOTSTRAP_KEY" >&2; exit 1; }

install -d -m 700 "$DEVDIR"
AUTH_HDR=/tmp/jtagent_rec_auth.hdr
printf 'Authorization: Bearer %s\n' "$BOOTSTRAP_KEY" > "$AUTH_HDR"
unset BOOTSTRAP_KEY

register_one() {
  local id="$1" name="$2" dtype="$3" osname="$4" loc="$5" outkey_var="$6" outhttp_var="$7"
  local body=/tmp/jtagent_rec_reg_body.json
  local resp=/tmp/jtagent_rec_reg_resp.json
  python3 - "$body" "$id" "$name" "$dtype" "$osname" "$loc" <<'PY'
import json, sys
path, device_id, name, dtype, osname, loc = sys.argv[1:7]
json.dump({
    "device_id": device_id,
    "device_name": name,
    "device_type": dtype,
    "os": osname,
    "agent_version": "jtagent",
    "location": loc,
}, open(path, "w", encoding="utf-8"), separators=(",", ":"))
PY
  local http
  http="$(curl --silent --show-error --http1.1 --ipv4 --connect-timeout 15 --max-time 40 \
    -o "$resp" -w '%{http_code}' \
    -X POST "${HUB}/register_device" \
    -H 'Content-Type: application/json' \
    -H @"${AUTH_HDR}" \
    --data-binary @"${body}" || true)"
  eval "${outhttp_var}=${http}"
  local key
  key="$(python3 - "$resp" "$id" <<'PY'
import json, sys
raw = open(sys.argv[1], encoding="utf-8", errors="replace").read()
try:
    body = json.loads(raw)
except Exception:
    body = {}
key = body.get("api_key") or ""
if not isinstance(key, str) or not key.startswith("jt_"):
    key = ""
if body.get("device_id") not in (None, sys.argv[2]):
    key = ""
print(key)
PY
)"
  eval "${outkey_var}=${key}"
  if [[ -n "$key" ]]; then
    umask 077
    cat > "${DEVDIR}/${id}.env" <<ENVEOF
EKA_DEVICE_ID=${id}
EKA_DEVICE_KEY=${key}
EKA_VPS_URL=https://agent.jaytipargal.tech
ENVEOF
    chmod 600 "${DEVDIR}/${id}.env"
  fi
}

register_one "$SANDBOX_ID" "$SANDBOX_ID" sandbox linux sandbox SANDBOX_KEY REGISTER_SANDBOX_HTTP
register_one "$WIN_ID" "$WIN_ID" laptop windows G4G-LAPTOP WIN_KEY REGISTER_WIN_HTTP

# Seed ingest via bootstrap Bearer so it works even if a prior key was lost.
ING_BODY=/tmp/jtagent_rec_ing.json
python3 - "$ING_BODY" "$SANDBOX_ID" <<'PY'
import json, sys
from datetime import datetime, timezone
path, device_id = sys.argv[1], sys.argv[2]
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
chunks = [
    {"input": "Who is the sandbox personal agent?", "output": "jtagent, Hugging Face user jtagent in org go4garage01.", "category": "identity", "title": "agent-name"},
    {"input": "Which Hub model and dataset does jtagent use?", "output": json.dumps({"model": "go4garage01/jt-agent-model", "dataset": "go4garage01/jt-agent-data", "note": "Full shards stay on the Hub; Colab trains GPT-2 LoRA."}), "category": "hub", "title": "hf-repos"},
    {"input": "How does a device push data into Jayti Hub?", "output": "python eka_agent_push.py --device <name> with EKA_DEVICE_ID and EKA_DEVICE_KEY from device.env.", "category": "pipeline", "title": "push"},
]
items = []
for chunk in chunks:
    items.append({"data_type": chunk["category"], "content": chunk, "device_time": now, "source": "jtagent_sandbox_seed"})
json.dump({"device": device_id, "items": items}, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
PY

INGEST_HTTP="$(curl --silent --show-error --http1.1 --ipv4 --connect-timeout 15 --max-time 40 \
  -o /tmp/jtagent_rec_ing_resp.json -w '%{http_code}' \
  -X POST "${HUB}/ingest" \
  -H 'Content-Type: application/json' \
  -H @"${AUTH_HDR}" \
  --data-binary @"${ING_BODY}" || true)"

rm -f -- "$AUTH_HDR"

STATUS_JSON="$(curl --silent --show-error --http1.1 --ipv4 --connect-timeout 10 --max-time 20 \
  "${HUB}/status" || true)"
STATUS_JSON="$(printf '%s' "$STATUS_JSON" | tr -d '\n')"

AFTER_DEVICES="$(count_sql 'SELECT count(*) FROM device_registry')"
AFTER_KEYS="$(count_sql 'SELECT count(*) FROM api_keys')"
AFTER_ITEMS="$(count_sql 'SELECT count(*) FROM ingestion_queue')"
AFTER_UNPROCESSED="$(count_sql "SELECT count(*) FROM ingestion_queue WHERE status = 'new'")"

rc=0
if [[ -z "${AFTER_DEVICES}" || "${AFTER_DEVICES}" == "0" || -z "${AFTER_ITEMS}" || "${AFTER_ITEMS}" == "0" ]]; then
  rc=1
fi
exit "$rc"
