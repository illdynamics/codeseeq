#!/usr/bin/env bash
set -Eeuo pipefail

# smoke-bridge-process.sh
# Start bin/codeseeq-bridge.py on a dynamic free port, smoke /health and
# /v1/models, then kill the process and verify no leak.

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
bridge_py="${repo_root}/bin/codeseeq-bridge.py"

log()  { printf '[smoke-bridge] %s\n' "$*" >&2; }
pass() { printf 'bridge-process-smoke: PASS\n'; exit 0; }
fail() { printf 'bridge-process-smoke: FAIL - %s\n' "$*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"
}

require_cmd python3
require_cmd curl

# --- 1) Pick a free port ----------------------------------------------------
log "selecting free port..."
BRIDGE_PORT="$(python3 -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.bind(('127.0.0.1', 0))
print(s.getsockname()[1])
s.close()
")"
log "  port=${BRIDGE_PORT}"

# --- 2) Start bridge -------------------------------------------------------
log "starting codeseeq-bridge..."
LOG_FILE="$(mktemp /tmp/bridge-smoke.XXXXXX.log)"
CODESEEQ_BRIDGE_PORT="${BRIDGE_PORT}" python3 "${bridge_py}" >"${LOG_FILE}" 2>&1 &
BRIDGE_PID=$!
log "  pid=${BRIDGE_PID}"

# --- 3) Install cleanup trap ------------------------------------------------
cleanup() {
  if kill -0 "${BRIDGE_PID}" 2>/dev/null; then
    kill "${BRIDGE_PID}" 2>/dev/null || true
    wait "${BRIDGE_PID}" 2>/dev/null || true
  fi
  rm -f "${LOG_FILE}"
}
trap 'cleanup' EXIT INT TERM

# --- 4) Poll /health --------------------------------------------------------
log "polling /health..."
healthy=0
for i in $(seq 1 20); do
  if curl -sf "http://127.0.0.1:${BRIDGE_PORT}/health" >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 0.3
done
if [[ "${healthy}" -ne 1 ]]; then
  fail "bridge did not become healthy within 6 seconds"
fi
log "  /health OK"

# --- 5) Curl /v1/models -----------------------------------------------------
log "curling /v1/models..."
if ! curl -sf "http://127.0.0.1:${BRIDGE_PORT}/v1/models" >/dev/null 2>&1; then
  fail "/v1/models returned non-2xx"
fi
log "  /v1/models OK"

# --- 6) Kill bridge and wait ------------------------------------------------
log "stopping bridge (pid=${BRIDGE_PID})..."
kill "${BRIDGE_PID}" 2>/dev/null || true
wait "${BRIDGE_PID}" 2>/dev/null || true

# --- 7) Verify no bridge process remains ------------------------------------
if kill -0 "${BRIDGE_PID}" 2>/dev/null; then
  fail "bridge pid ${BRIDGE_PID} still alive after kill"
fi

# --- 8) Remove log, pass ----------------------------------------------------
rm -f "${LOG_FILE}"
pass
