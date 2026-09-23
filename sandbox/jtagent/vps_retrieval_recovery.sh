#!/usr/bin/env bash
# Paste this entire script into the Vultr web console for jayti-agent-db (139.84.165.81).
# Diagnoses and restarts jayti-retrieval on 127.0.0.1:8444 (nginx /retrieve/ upstream).
# Safe to re-run. Does NOT print secrets from /etc/jayti/*.env.
set -euo pipefail

SERVICE="jayti-retrieval"
PORT=8444
RETRIEVAL_DIR="/opt/jayti/retrieval"
HUB_VENV="/opt/jayti/hub/venv/bin/python"
RETRIEVAL_ENV="/etc/jayti/retrieval.env"
HUB_ENV="/etc/jayti/hub.env"
UNIT="/etc/systemd/system/jayti-retrieval.service"

redact_env() {
  sed -E 's/(JAYTI_PG_DSN=postgresql:\/\/[^:]+:)[^@]+/\1***/; s/(JAYTI_BOOTSTRAP_KEY=).*/\1***/'
}

section() { echo; echo "=== $* ==="; }

section "Host"
hostname -f || hostname
date -u +"%Y-%m-%dT%H:%M:%SZ"

section "Port ${PORT} before restart"
if ss -ltn "sport = :${PORT}" 2>/dev/null | grep -q ":${PORT}"; then
  echo "LISTEN on :${PORT}"
  ss -ltnp "sport = :${PORT}" 2>/dev/null || true
else
  echo "NOT listening on :${PORT}"
fi

section "Related services"
for u in jayti-retrieval jayti-hub eka-retrieval eka-agent nginx postgresql; do
  if systemctl list-unit-files "${u}.service" &>/dev/null; then
    systemctl is-active "${u}.service" 2>/dev/null || echo "${u}: inactive/missing"
    systemctl is-enabled "${u}.service" 2>/dev/null || true
  fi
done

section "Unit file"
if [[ -f "${UNIT}" ]]; then
  grep -E '^(Description|EnvironmentFile|ExecStart|WorkingDirectory|User)=' "${UNIT}" || true
else
  echo "MISSING ${UNIT}"
fi

section "Deploy paths"
for p in "${RETRIEVAL_DIR}/retrieval_server.py" \
         "${RETRIEVAL_DIR}/faiss_index/faiss_index.bin" \
         "${RETRIEVAL_DIR}/faiss_index/doc_offsets.bin" \
         "${RETRIEVAL_DIR}/faiss_index/doc_store.jsonl" \
         "${HUB_VENV}" \
         "${RETRIEVAL_ENV}" \
         "${HUB_ENV}"; do
  if [[ -e "${p}" ]]; then
    ls -lh "${p}"
  else
    echo "MISSING ${p}"
  fi
done

section "Env files (redacted)"
for f in "${RETRIEVAL_ENV}" "${HUB_ENV}"; do
  if [[ -f "${f}" ]]; then
    echo "--- ${f} ---"
    redact_env < "${f}"
  else
    echo "MISSING ${f}"
  fi
done

section "Ensure retrieval.env exists"
if [[ ! -f "${RETRIEVAL_ENV}" && -f "${HUB_ENV}" ]]; then
  echo "Creating ${RETRIEVAL_ENV} from ${HUB_ENV} (same PG DSN + bootstrap key)"
  install -d -m 700 /etc/jayti
  {
    grep -E '^JAYTI_PG_DSN=' "${HUB_ENV}" || true
    grep -E '^JAYTI_BOOTSTRAP_KEY=' "${HUB_ENV}" || true
    echo "JAYTI_RETRIEVAL_DIR=${RETRIEVAL_DIR}"
    echo "JAYTI_HOST=127.0.0.1"
    echo "JAYTI_PORT=${PORT}"
  } > "${RETRIEVAL_ENV}"
  chmod 600 "${RETRIEVAL_ENV}"
fi

section "Python import smoke test"
if [[ -x "${HUB_VENV}" && -f "${RETRIEVAL_DIR}/retrieval_server.py" ]]; then
  (
    cd "${RETRIEVAL_DIR}"
    set -a
    # shellcheck disable=SC1090
    [[ -f "${RETRIEVAL_ENV}" ]] && source "${RETRIEVAL_ENV}"
    set +a
    "${HUB_VENV}" - <<'PY'
import importlib.util
spec = importlib.util.spec_from_file_location("retrieval_server", "retrieval_server.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print("import_ok app=", getattr(mod, "app", None) is not None)
PY
  ) || echo "IMPORT FAILED — see traceback above"
else
  echo "SKIP import smoke (missing venv or retrieval_server.py)"
fi

section "Recent jayti-retrieval logs"
if systemctl list-unit-files "${SERVICE}.service" &>/dev/null; then
  journalctl -u "${SERVICE}.service" -n 80 --no-pager || true
else
  echo "Service unit not installed"
fi

section "Restart ${SERVICE}"
if [[ ! -f "${UNIT}" ]]; then
  echo "Cannot restart — install ${UNIT} from repo server/jayti-retrieval/jayti-retrieval.service first"
else
  systemctl daemon-reload
  systemctl enable "${SERVICE}.service" 2>/dev/null || true
  systemctl restart "${SERVICE}.service" || systemctl start "${SERVICE}.service"
  sleep 2
  systemctl status "${SERVICE}.service" --no-pager -l || true
fi

section "Port ${PORT} after restart"
if ss -ltn "sport = :${PORT}" 2>/dev/null | grep -q ":${PORT}"; then
  echo "LISTEN on :${PORT}"
  ss -ltnp "sport = :${PORT}" 2>/dev/null || true
else
  echo "STILL NOT listening on :${PORT}"
  journalctl -u "${SERVICE}.service" -n 40 --no-pager || true
fi

section "Local health checks"
set +e
curl -sS -o /tmp/jayti_ret_healthz.json -w "localhost:${PORT}/healthz HTTP %{http_code}\n" \
  "http://127.0.0.1:${PORT}/healthz"
head -c 200 /tmp/jayti_ret_healthz.json 2>/dev/null; echo
curl -sS -o /dev/null -w "nginx /retrieve/healthz HTTP %{http_code}\n" \
  -H "Host: agent.jaytipargal.tech" "https://127.0.0.1/retrieve/healthz" -k
curl -sS -o /dev/null -w "hub /healthz HTTP %{http_code}\n" \
  -H "Host: agent.jaytipargal.tech" "https://127.0.0.1/healthz" -k
curl -sS -o /dev/null -w "eka /retrieval/health HTTP %{http_code}\n" \
  -H "Host: agent.jaytipargal.tech" "https://127.0.0.1/retrieval/health" -k
set -e

section "If still failing"
cat <<'HINT'
Common fixes on this VPS:
  1. pip deps in hub venv: asyncpg argon2-cffi faiss-cpu sentence-transformers fastapi uvicorn numpy
  2. Copy retrieval_server.py to /opt/jayti/retrieval/ and FAISS index under faiss_index/
  3. Share JAYTI_PG_DSN from /etc/jayti/hub.env into /etc/jayti/retrieval.env
  4. Check OOM: dmesg -T | tail -20 | grep -i kill
  5. nginx upstream is 127.0.0.1:8444 — service must bind there (not 8443/8100)
HINT

echo
echo "Done. Expect HTTP 200 from http://127.0.0.1:${PORT}/healthz"
