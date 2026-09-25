#!/usr/bin/env bash
# Install the jtagent runtime venv on the Vultr VPS.
#
# The unit file (server/jayti-agent/jtagent.service) runs uvicorn from
# /opt/jayti/agent/venv, but nothing in the repo created that venv or installed
# the ML stack the GPT-2 + PeftModel path needs. This does both, idempotently.
#
# Run from the Vultr web console as root:
#   bash sandbox/jtagent/vps_install_agent.sh
set -euo pipefail

AGENT_DIR="${JTAGENT_AGENT_DIR:-/opt/jayti/agent}"
VENV="${AGENT_DIR}/venv"
REPO="${JTAGENT_REPO_DIR:-/opt/jayti/src/jayti}"

echo "[jtagent] agent dir: ${AGENT_DIR}"
mkdir -p "${AGENT_DIR}" "${AGENT_DIR}/adapters" "${AGENT_DIR}/hf-cache"

# System deps: python venv + rclone (for the Drive adapter sync).
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -qq || true
  apt-get install -y -qq python3-venv python3-pip rclone || true
fi

if [ ! -x "${VENV}/bin/python" ]; then
  python3 -m venv "${VENV}"
fi
# CPU-only torch keeps the small VPS light; the 7-8B model runs on the VivoBook,
# not here — this venv only serves the retrieval-grounded GPT-2 responder.
"${VENV}/bin/pip" install --upgrade pip -q
"${VENV}/bin/pip" install -q "fastapi" "uvicorn[standard]" "httpx"
# CPU-only torch from the CPU wheel index (must be its own args, not one string);
# fall back to the default wheel if that index is unreachable.
"${VENV}/bin/pip" install -q torch --index-url https://download.pytorch.org/whl/cpu \
  || "${VENV}/bin/pip" install -q torch
"${VENV}/bin/pip" install -q "transformers>=4.44" "peft>=0.12" "accelerate>=0.33" "huggingface_hub"

# Copy the server + sync helper next to the venv if the repo is present.
if [ -f "${REPO}/server/jayti-agent/jtagent_server.py" ]; then
  install -m 0644 "${REPO}/server/jayti-agent/jtagent_server.py" "${AGENT_DIR}/jtagent_server.py"
fi
if [ -f "${REPO}/sandbox/jtagent/vps_agent_sync.sh" ]; then
  install -m 0755 "${REPO}/sandbox/jtagent/vps_agent_sync.sh" "${AGENT_DIR}/vps_agent_sync.sh"
fi

echo "[jtagent] venv ready: ${VENV}"
echo "[jtagent] next: install server/jayti-agent/jtagent.service, then"
echo "          systemctl daemon-reload && systemctl enable --now jtagent"
