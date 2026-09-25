#!/usr/bin/env bash
# Pull the newest GPT-2 LoRA adapter from the TAN Drive folder onto the VPS and
# point the agent at it. Runs as ExecStartPre for jtagent.service (best-effort).
#
# The 7-8B GGUF stays on the VivoBook; this only syncs the small adapters the
# VPS's retrieval-grounded GPT-2 responder loads via JTAGENT_ADAPTER.
#
# Requires an rclone remote 'jtagent_tan' rooted at the TAN folder id
# (rclone config reconnect jtagent_tan once), same remote the Colab side uses.
set -euo pipefail

AGENT_DIR="${JTAGENT_AGENT_DIR:-/opt/jayti/agent}"
ADAPTERS_DIR="${AGENT_DIR}/adapters"
REMOTE="${JTAGENT_RCLONE_REMOTE:-jtagent_tan}"
FOLDER_ID="${JTAGENT_TAN_FOLDER_ID:-1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB}"
AGENT_ENV="${JTAGENT_AGENT_ENV:-/etc/jayti/agent.env}"

mkdir -p "${ADAPTERS_DIR}"

if ! command -v rclone >/dev/null 2>&1; then
  echo "[sync] rclone not installed; skipping adapter sync"
  exit 0
fi

echo "[sync] pulling adapters from ${REMOTE}:jtagent/adapters"
rclone copy "${REMOTE}:jtagent/adapters" "${ADAPTERS_DIR}" \
  --drive-root-folder-id "${FOLDER_ID}" \
  --create-empty-src-dirs -v || {
    echo "[sync] rclone copy failed; leaving existing adapters in place"
    exit 0
  }

# Newest adapter dir that actually contains a final/ adapter.
NEWEST="$(find "${ADAPTERS_DIR}" -type d -name final -printf '%T@ %p\n' 2>/dev/null \
  | sort -nr | head -1 | cut -d' ' -f2-)"

if [ -n "${NEWEST}" ] && [ -f "${NEWEST}/adapter_config.json" ]; then
  echo "[sync] newest adapter: ${NEWEST}"
  mkdir -p "$(dirname "${AGENT_ENV}")"
  touch "${AGENT_ENV}"
  # Replace or append JTAGENT_ADAPTER in the env file the unit reads.
  if grep -q '^JTAGENT_ADAPTER=' "${AGENT_ENV}" 2>/dev/null; then
    sed -i "s#^JTAGENT_ADAPTER=.*#JTAGENT_ADAPTER=${NEWEST}#" "${AGENT_ENV}"
  else
    echo "JTAGENT_ADAPTER=${NEWEST}" >> "${AGENT_ENV}"
  fi
else
  echo "[sync] no adapter with adapter_config.json found; agent will run base/evidence-only"
fi
