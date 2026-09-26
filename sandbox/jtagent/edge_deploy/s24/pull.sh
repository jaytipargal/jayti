#!/data/data/com.termux/files/usr/bin/bash
# Samsung S24 (Termux): pull the newest jtagent GGUF from the TAN Drive folder via
# the rclone remote 'jtagent_tan' (authorize once, headless, with
# ../configure_rclone_sa.sh <sa-key.json>).
#
#   bash pull.sh                      # -> $JTAGENT_GGUF_DIR (default ~/jtagent/gguf)
#   bash termux-run.sh "$(bash pull.sh --print-newest)" "your prompt"
set -euo pipefail

REMOTE="${JTAGENT_RCLONE_REMOTE:-jtagent_tan}"
FOLDER_ID="${JTAGENT_TAN_FOLDER_ID:-1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB}"
DEST="${JTAGENT_GGUF_DIR:-$HOME/jtagent/gguf}"

newest() { find "${DEST}" -name '*.gguf' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -1 | cut -d' ' -f2-; }

if [ "${1:-}" = "--print-newest" ]; then
  newest
  exit 0
fi

mkdir -p "${DEST}"
echo "[s24] pulling GGUF from ${REMOTE}:jtagent/gguf -> ${DEST}"
rclone copy "${REMOTE}:jtagent/gguf" "${DEST}" \
  --drive-root-folder-id "${FOLDER_ID}" --include '*.gguf' -v

GGUF="$(newest)"
if [ -z "${GGUF}" ]; then
  echo "[s24] no GGUF found under ${DEST}" >&2
  exit 1
fi
echo "[s24] newest GGUF: ${GGUF}"
echo "[s24] run: bash termux-run.sh \"${GGUF}\" \"your prompt\""
