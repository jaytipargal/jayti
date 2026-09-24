#!/usr/bin/env bash
# Finish sandbox seed ingest when register succeeded but ingest failed.
# Run on jayti-agent-db console:
#   export EKA_DEVICE_ID=jtagent_sandbox
#   export EKA_DEVICE_KEY='jt_PASTE_KEY_HERE'
#   bash /opt/jayti/sandbox/jtagent/vps_sandbox_ingest.sh
# Or paste this file via: cat << 'EOF' | bash
set -euo pipefail

HUB="${HUB:-https://agent.jaytipargal.tech}"
DEVICE_ID="${EKA_DEVICE_ID:-jtagent_sandbox}"
DEVICE_KEY="${EKA_DEVICE_KEY:-}"

[[ "$DEVICE_KEY" =~ ^jt_[0-9a-fA-F]+$ ]] || {
  echo "error: set EKA_DEVICE_KEY to the jt_ key from register output" >&2
  exit 1
}

ING_BODY="$(mktemp)"
ING_RESP="$(mktemp)"
KEY_HDR="$(mktemp)"
trap 'rm -f "$ING_BODY" "$ING_RESP" "$KEY_HDR"' EXIT

python3 -c "
import json, sys
from datetime import datetime, timezone
device_id = sys.argv[1]
path = sys.argv[2]
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

CURL=(curl --silent --show-error --http1.1 --ipv4 --connect-timeout 20 --max-time 90)
[[ -r /etc/ssl/certs/ca-certificates.crt ]] && CURL+=(--cacert /etc/ssl/certs/ca-certificates.crt)

INGEST_HTTP="$("${CURL[@]}" -o "$ING_RESP" -w '%{http_code}' \
  -X POST "${HUB}/ingest" \
  -H 'Content-Type: application/json' \
  -H @"${KEY_HDR}" \
  --data-binary @"${ING_BODY}")"

STATUS_HTTP="$("${CURL[@]}" -o /tmp/jtagent_status.json -w '%{http_code}' "${HUB}/status")"
ITEMS_TOTAL=""
ITEMS_UNPROCESSED=""
if [[ "$STATUS_HTTP" == "200" ]]; then
  read -r ITEMS_TOTAL ITEMS_UNPROCESSED < <(python3 -c "
import json
b=json.load(open('/tmp/jtagent_status.json'))
print(b.get('items_total',''), b.get('items_unprocessed',''))
")
fi

echo "ingest_http=${INGEST_HTTP}"
echo "status_http=${STATUS_HTTP}"
echo "items_total=${ITEMS_TOTAL}"
echo "items_unprocessed=${ITEMS_UNPROCESSED}"
[[ "$INGEST_HTTP" == "201" ]] || exit 1
