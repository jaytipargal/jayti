#!/usr/bin/env bash
# Register jtagent_sandbox on Jayti Hub from the VPS console, seed 3 rows, print status.
# Paste into Vultr web console for jayti-agent-db (139.84.165.81):
#   cat << 'EOF' | bash
#   ...this file...
#   EOF
#
# Reads JAYTI_BOOTSTRAP_KEY from /etc/jayti/hub.env (never echoed).
# Prints device_key once between BEGIN OUTPUT / END OUTPUT — copy immediately.
set +x
set -euo pipefail

HUB="${HUB:-http://127.0.0.1:8443}"
HUB_ENV=/etc/jayti/hub.env
DEVICE_ID=jtagent_sandbox
DEVICE_KEY=
REGISTER_HTTP=
INGEST_HTTP=
ITEMS_TOTAL=
ITEMS_UNPROCESSED=
AUTH_HDR=
KEY_HDR=
REG_BODY=
REG_RESP=
ING_BODY=
ING_RESP=
STATUS_RESP=
KEY_OUT=
META_OUT=
COUNT_OUT=
BOOTSTRAP_KEY=

print_output() {
  printf '%s\n' \
    '===== BEGIN OUTPUT =====' \
    "device_id=${DEVICE_ID}" \
    "device_key=${DEVICE_KEY}" \
    "register_http=${REGISTER_HTTP}" \
    "ingest_http=${INGEST_HTTP}" \
    "items_total=${ITEMS_TOTAL}" \
    "items_unprocessed=${ITEMS_UNPROCESSED}" \
    '===== END OUTPUT ====='
}

cleanup() {
  local f
  for f in "$AUTH_HDR" "$KEY_HDR" "$REG_BODY" "$REG_RESP" "$ING_BODY" "$ING_RESP" "$STATUS_RESP" "$KEY_OUT" "$META_OUT" "$COUNT_OUT"; do
    if [[ -n "$f" ]]; then
      rm -f -- "$f" || true
    fi
  done
  unset BOOTSTRAP_KEY || true
}

on_exit() {
  local rc=$?
  cleanup || true
  print_output || true
  exit "$rc"
}
trap on_exit EXIT

command -v curl >/dev/null 2>&1 || { echo 'error: curl not found' >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo 'error: python3 not found' >&2; exit 1; }
[[ -r "$HUB_ENV" ]] || { echo 'error: cannot read /etc/jayti/hub.env' >&2; exit 1; }

BOOTSTRAP_KEY="$(sed -n 's/^JAYTI_BOOTSTRAP_KEY=//p' "$HUB_ENV")"
BOOTSTRAP_KEY="${BOOTSTRAP_KEY%%$'\n'*}"
BOOTSTRAP_KEY="${BOOTSTRAP_KEY%$'\r'}"
[[ -n "$BOOTSTRAP_KEY" ]] || { echo 'error: no JAYTI_BOOTSTRAP_KEY in /etc/jayti/hub.env' >&2; exit 1; }

umask 077
AUTH_HDR="$(mktemp)"
KEY_HDR="$(mktemp)"
REG_BODY="$(mktemp)"
REG_RESP="$(mktemp)"
ING_BODY="$(mktemp)"
ING_RESP="$(mktemp)"
STATUS_RESP="$(mktemp)"
KEY_OUT="$(mktemp)"
META_OUT="$(mktemp)"
COUNT_OUT="$(mktemp)"

printf 'Authorization: Bearer %s\n' "$BOOTSTRAP_KEY" > "$AUTH_HDR"
unset BOOTSTRAP_KEY

python3 - "$REG_BODY" "$DEVICE_ID" << 'PY'
import json, sys
path, device_id = sys.argv[1], sys.argv[2]
body = {
    "device_id": device_id,
    "device_name": device_id,
    "device_type": "sandbox",
    "os": "linux",
    "agent_version": "jtagent",
    "location": "sandbox",
}
with open(path, "w", encoding="utf-8") as fh:
    json.dump(body, fh, separators=(",", ":"))
PY

CURL=(
  curl --silent --show-error --http1.1 --tlsv1.2 --tls-max 1.2
  --curves X25519:prime256v1 --ipv4 --proto '=https' --proto-redir '=https'
  --max-redirs 0 --connect-timeout 20 --max-time 90
)
if [[ -r /etc/ssl/certs/ca-certificates.crt ]]; then
  CURL+=(--cacert /etc/ssl/certs/ca-certificates.crt)
fi

set +e
REGISTER_HTTP="$("${CURL[@]}" -o "$REG_RESP" -w '%{http_code}' \
  -X POST "${HUB}/register_device" \
  -H 'Content-Type: application/json' \
  -H @"${AUTH_HDR}" \
  --data-binary @"${REG_BODY}")"
reg_curl=$?
set -e
rm -f -- "$AUTH_HDR"
AUTH_HDR=
if [[ -z "$REGISTER_HTTP" ]]; then REGISTER_HTTP=000; fi
if [[ "$reg_curl" -ne 0 && "$REGISTER_HTTP" == "000" ]]; then
  echo "error: register transport failed (curl ${reg_curl})" >&2
fi

python3 - "$REG_RESP" "$DEVICE_ID" "$KEY_OUT" "$META_OUT" << 'PY'
import json, sys
resp_path, device_id, key_path, meta_path = sys.argv[1:5]
raw = open(resp_path, encoding="utf-8", errors="replace").read()
try:
    body = json.loads(raw)
except Exception:
    body = {}
if not isinstance(body, dict):
    body = {}
key = body.get("api_key") or ""
if not isinstance(key, str):
    key = ""
key = key.replace("\n", "").replace("\r", "")
returned_id = body.get("device_id")
if key and returned_id not in (None, device_id):
    key = ""
    sys.stderr.write("error: register response device_id was not jtagent_sandbox; key discarded\n")
detail = body.get("detail", "")
if not isinstance(detail, str):
    detail = json.dumps(detail, ensure_ascii=False)
detail = detail.replace("\n", " ").replace("\r", " ")[:200]
low = detail.lower()
if "jt_" in detail or "bearer " in low:
    detail = "detail suppressed"
existing = "1" if any(n in low for n in (
    "already exist", "already registered", "duplicate key",
    "unique constraint", "existing device", "device exists")) else "0"
open(key_path, "w", encoding="utf-8").write(key)
open(meta_path, "w", encoding="utf-8").write(existing + "\n" + detail + "\n")
PY

DEVICE_KEY="$(cat "$KEY_OUT")"
EXISTING="$(sed -n '1p' "$META_OUT")"
DETAIL="$(sed -n '2p' "$META_OUT")"
if [[ "$REGISTER_HTTP" == "409" ]]; then EXISTING=1; fi
if [[ ! "$DEVICE_KEY" =~ ^jt_[0-9a-fA-F]+$ ]]; then
  if [[ -n "$DEVICE_KEY" ]]; then
    echo 'error: register returned a token that is not a jt_ key; discarded' >&2
  fi
  DEVICE_KEY=
fi

if [[ -z "$DEVICE_KEY" ]]; then
  if [[ "$EXISTING" == "1" ]]; then
    echo 'error: jtagent_sandbox already registered; no re-issue route; other devices not touched' >&2
  elif [[ -n "${DETAIL:-}" ]]; then
    echo "error: register HTTP ${REGISTER_HTTP}: ${DETAIL}" >&2
  else
    echo "error: register HTTP ${REGISTER_HTTP}" >&2
  fi
else
  python3 -c "
import json, sys
from datetime import datetime, timezone
device_id, path = sys.argv[1], sys.argv[2]
now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
chunks = [
    {'input': 'Who is the sandbox personal agent?', 'output': 'jtagent, Hugging Face user jtagent in org go4garage01.', 'category': 'identity', 'title': 'agent-name'},
    {'input': 'Which Hub model and dataset does jtagent use?', 'output': json.dumps({'model': 'go4garage01/jt-agent-model', 'dataset': 'go4garage01/jt-agent-data', 'note': 'Full shards stay on the Hub; Colab trains GPT-2 LoRA.'}), 'category': 'hub', 'title': 'hf-repos'},
    {'input': 'How does a device push data into Jayti Hub?', 'output': 'python eka_agent_push.py --device <name> with EKA_DEVICE_ID and EKA_DEVICE_KEY from device.env.', 'category': 'pipeline', 'title': 'push'},
]
allowed = {'identity', 'hub', 'pipeline'}
items = []
for chunk in chunks:
    cat = chunk['category']
    if cat not in allowed:
        raise SystemExit('refusing non-sandbox seed category')
    items.append({'data_type': cat, 'content': chunk, 'device_time': now, 'source': 'jtagent_sandbox_seed'})
open(path, 'w', encoding='utf-8').write(json.dumps({'device': device_id, 'items': items}, ensure_ascii=False, separators=(',', ':')))
" "$DEVICE_ID" "$ING_BODY"
  printf 'X-Device-Id: %s\nX-Api-Key: %s\n' "$DEVICE_ID" "$DEVICE_KEY" > "$KEY_HDR"
  set +e
  INGEST_HTTP="$("${CURL[@]}" -o "$ING_RESP" -w '%{http_code}' \
    -X POST "${HUB}/ingest" \
    -H 'Content-Type: application/json' \
    -H @"${KEY_HDR}" \
    --data-binary @"${ING_BODY}")"
  ing_curl=$?
  set -e
  rm -f -- "$KEY_HDR"
  KEY_HDR=
  if [[ -z "$INGEST_HTTP" ]]; then INGEST_HTTP=000; fi
  if [[ "$ing_curl" -ne 0 && "$INGEST_HTTP" == "000" ]]; then
    echo "error: ingest transport failed (curl ${ing_curl})" >&2
  elif [[ "$INGEST_HTTP" != "201" ]]; then
    echo "error: ingest HTTP ${INGEST_HTTP}" >&2
  fi
fi

set +e
STATUS_HTTP="$("${CURL[@]}" -o "$STATUS_RESP" -w '%{http_code}' "${HUB}/status")"
status_curl=$?
set -e
if [[ -z "${STATUS_HTTP:-}" ]]; then STATUS_HTTP=000; fi
if [[ "$STATUS_HTTP" == "200" ]]; then
  python3 - "$STATUS_RESP" "$COUNT_OUT" << 'PY'
import json, sys
body = {}
try:
    body = json.loads(open(sys.argv[1], encoding="utf-8", errors="replace").read())
except Exception:
    body = {}
if not isinstance(body, dict):
    body = {}
def show(k):
    v = body.get(k)
    return str(v) if isinstance(v, (int, float, str)) else ""
open(sys.argv[2], "w", encoding="utf-8").write(show("items_total") + "\n" + show("items_unprocessed") + "\n")
PY
  ITEMS_TOTAL="$(sed -n '1p' "$COUNT_OUT")"
  ITEMS_UNPROCESSED="$(sed -n '2p' "$COUNT_OUT")"
else
  echo "error: status HTTP ${STATUS_HTTP}" >&2
fi

rc=0
if [[ -z "$DEVICE_KEY" || "$INGEST_HTTP" != "201" || -z "$ITEMS_TOTAL" || -z "$ITEMS_UNPROCESSED" ]]; then
  rc=1
fi
exit "$rc"
