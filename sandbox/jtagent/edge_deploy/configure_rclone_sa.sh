#!/usr/bin/env bash
# Write the rclone remote 'jtagent_tan' (rooted at the TAN Drive folder) using a
# Google Cloud service-account key — headless, no browser OAuth. Works on Linux,
# WSL and Termux (S24). The service account's email must be shared on the TAN
# folder (reader is enough to pull artifacts).
#
#   bash configure_rclone_sa.sh /path/to/sa-key.json
#
# Env overrides: JTAGENT_RCLONE_REMOTE (jtagent_tan), JTAGENT_TAN_FOLDER_ID.
set -euo pipefail

KEY="${1:-${RCLONE_DRIVE_SERVICE_ACCOUNT_FILE:-}}"
REMOTE="${JTAGENT_RCLONE_REMOTE:-jtagent_tan}"
FOLDER_ID="${JTAGENT_TAN_FOLDER_ID:-1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB}"

if [ -z "${KEY}" ] || [ ! -f "${KEY}" ]; then
  echo "usage: $0 /path/to/sa-key.json  (a Google Cloud service-account JSON key)" >&2
  exit 1
fi
command -v rclone >/dev/null 2>&1 || { echo "rclone not installed (Termux: pkg install rclone)" >&2; exit 1; }

# Keep the key private; rclone reads it by absolute path.
chmod 600 "${KEY}" 2>/dev/null || true
KEY_ABS="$(cd "$(dirname "${KEY}")" && pwd)/$(basename "${KEY}")"

# Non-interactive: (re)create the remote; any old token/client fields are dropped.
rclone config delete "${REMOTE}" >/dev/null 2>&1 || true
rclone config create "${REMOTE}" drive \
  scope=drive root_folder_id="${FOLDER_ID}" \
  service_account_file="${KEY_ABS}" --non-interactive >/dev/null

echo "[rclone] remote '${REMOTE}' -> folder ${FOLDER_ID} via service account ${KEY_ABS}"
echo "[rclone] verifying (lists the TAN folder):"
rclone lsd "${REMOTE}:" --max-depth 1
