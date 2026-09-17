#!/bin/bash
# ============================================================
# Register a device with Jayti Hub and print its device.env
# ============================================================
# Run this ON THE VPS: the bootstrap key lives in /etc/jayti/hub.env and must
# never leave it. The per-device key is shown ONCE — the hub stores only an
# argon2 hash — so copy the printed block to the device immediately.
#
# Usage:
#   sudo bash eka_register_device.sh <device_id> [device_name] [device_type] [os]
#
# On the device, save the printed block as /etc/eka-agent/device.env (or
# ~/.eka_agent/device.env on Windows) with mode 600, then:
#   python eka_agent_push.py --device <device_id>
# ============================================================
set -euo pipefail

DEVICE_ID="${1:?usage: eka_register_device.sh <device_id> [device_name] [device_type] [os]}"
DEVICE_NAME="${2:-$DEVICE_ID}"
DEVICE_TYPE="${3:-laptop}"
DEVICE_OS="${4:-linux}"
HUB_URL="${HUB_URL:-http://127.0.0.1:8443}"
PUBLIC_URL="${PUBLIC_URL:-https://agent.jaytipargal.tech}"
HUB_ENV="${HUB_ENV:-/etc/jayti/hub.env}"

BOOTSTRAP_KEY="$(sed -n 's/^JAYTI_BOOTSTRAP_KEY=//p' "$HUB_ENV")"
[ -n "$BOOTSTRAP_KEY" ] || { echo "no JAYTI_BOOTSTRAP_KEY in $HUB_ENV" >&2; exit 1; }

RESPONSE="$(curl -sS -X POST "${HUB_URL}/register_device" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${BOOTSTRAP_KEY}" \
    -d "{\"device_id\":\"${DEVICE_ID}\",\"device_name\":\"${DEVICE_NAME}\",\"device_type\":\"${DEVICE_TYPE}\",\"os\":\"${DEVICE_OS}\"}")"

API_KEY="$(printf '%s' "$RESPONSE" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("api_key",""))' 2>/dev/null || true)"
if [ -z "$API_KEY" ]; then
    echo "registration failed: $RESPONSE" >&2
    exit 1
fi

cat <<EOF

=== device.env for ${DEVICE_ID} (shown once — copy it now) ===
EKA_DEVICE_ID=${DEVICE_ID}
EKA_DEVICE_KEY=${API_KEY}
EKA_VPS_URL=${PUBLIC_URL}
=== end ===

Install on the device:
  install -d -m 700 /etc/eka-agent   # Windows: mkdir %USERPROFILE%\\.eka_agent
  <paste the three lines above into /etc/eka-agent/device.env>
  chmod 600 /etc/eka-agent/device.env
EOF
