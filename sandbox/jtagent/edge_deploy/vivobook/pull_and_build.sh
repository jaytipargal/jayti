#!/usr/bin/env bash
# VivoBook (Linux boot / WSL): pull the newest jtagent GGUF from the TAN Drive
# folder and (re)build the local Ollama model.
#
# Prereqs: rclone remote 'jtagent_tan' (rclone config reconnect jtagent_tan),
# ollama installed. The GGUF is produced by sandbox/jtagent/export_gguf.py and
# pushed to Drive under jtagent/gguf/ by drive_sync.
set -euo pipefail

REMOTE="${JTAGENT_RCLONE_REMOTE:-jtagent_tan}"
FOLDER_ID="${JTAGENT_TAN_FOLDER_ID:-1ondyw5YrwXpE6jV48nYpRlg4Z1QkZWUB}"
DEST="${JTAGENT_GGUF_DIR:-$HOME/jtagent/gguf}"
MODEL_NAME="${JTAGENT_OLLAMA_NAME:-jtagent}"
HERE="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "${DEST}"
echo "[edge] pulling GGUF from ${REMOTE}:jtagent/gguf"
rclone copy "${REMOTE}:jtagent/gguf" "${DEST}" \
  --drive-root-folder-id "${FOLDER_ID}" --include '*.gguf' -v

GGUF="$(find "${DEST}" -name '*.gguf' -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-)"
if [ -z "${GGUF}" ]; then
  echo "[edge] no GGUF found under ${DEST}" >&2
  exit 1
fi
echo "[edge] newest GGUF: ${GGUF}"

# Materialize a Modelfile pointing at the pulled GGUF.
MODELFILE="${DEST}/Modelfile"
sed "s#^FROM .*#FROM ${GGUF}#" "${HERE}/Modelfile.template" > "${MODELFILE}"

ollama create "${MODEL_NAME}" -f "${MODELFILE}"
echo "[edge] built ollama model '${MODEL_NAME}'. Try: ollama run ${MODEL_NAME}"
