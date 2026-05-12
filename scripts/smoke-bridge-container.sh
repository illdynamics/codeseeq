#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/runtime.sh
source "${script_dir}/runtime.sh"
CONTAINER="$(codeseeq_detect_container)"
IMAGE="${IMAGE:-codeseeq:dev}"

if ! command -v "$CONTAINER" >/dev/null 2>&1; then
  echo "[smoke-bridge-container] container runtime missing: $CONTAINER" >&2
  exit 1
fi

echo "[smoke-bridge-container] verify models command"
models_out="$($CONTAINER run --rm "$IMAGE" models)"
grep -q 'deepseek-v4-flash' <<<"$models_out"
grep -q 'deepseek-v4-pro' <<<"$models_out"

echo "[smoke-bridge-container] verify config output"
config_out="$($CONTAINER run --rm "$IMAGE" config)"
grep -q '^CODEX_HOME=/home/codeseeq/.codeseeq$' <<<"$config_out"
grep -q '^model_provider = "codeseeq"$' <<<"$config_out"
grep -q '^wire_api = "responses"$' <<<"$config_out"
grep -q '^env_key = "DEEPSEEK_API_KEY"$' <<<"$config_out"
grep -q '^base_url = "http://127.0.0.1:8080/v1"$' <<<"$config_out"
grep -q '^approval_policy = "on-request"$' <<<"$config_out"
grep -q '^sandbox_mode = "workspace-write"$' <<<"$config_out"

echo "[smoke-bridge-container] verify doctor output"
doctor_out="$($CONTAINER run --rm "$IMAGE" doctor)"
grep -q '^CodeSeeq Doctor' <<<"$doctor_out"
grep -q '^OpenResponses URL: http://127.0.0.1:8080/v1$' <<<"$doctor_out"
grep -q '^OpenResponses startup command: /usr/local/bin/codeseeq-bridge.py$' <<<"$doctor_out"
grep -Eq '^DeepSeek provider key: (present|missing)$' <<<"$doctor_out"
grep -q '^System prompt injection: codex-config-developer_instructions$' <<<"$doctor_out"

echo "[smoke-bridge-container] verify bridge server can start inside container"
bridge_pid_file="$(mktemp)"
cleanup_bridge() { kill "$(cat "$bridge_pid_file" 2>/dev/null)" 2>/dev/null || true; rm -f "$bridge_pid_file"; }
trap cleanup_bridge EXIT

bridge_port=19081
"$CONTAINER" run --rm -d \
  -p "${bridge_port}:${bridge_port}" \
  -e CODESEEQ_BRIDGE_PORT="${bridge_port}" \
  --entrypoint /usr/local/bin/codeseeq-bridge.py \
  "$IMAGE" > "$bridge_pid_file"

for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:${bridge_port}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

echo "[smoke-bridge-container] bridge /health"
if ! curl -sf "http://127.0.0.1:${bridge_port}/health"; then
  echo "[smoke-bridge-container] FAIL: bridge /health endpoint did not respond"
  exit 1
fi
echo

echo "[smoke-bridge-container] bridge /v1/models"
models_json="$(curl -sf "http://127.0.0.1:${bridge_port}/v1/models")"
if ! echo "$models_json" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['object']=='list'; assert any(m['id']=='deepseek-v4-flash' for m in d['data'])"; then
  echo "[smoke-bridge-container] FAIL: bridge /v1/models missing expected model"
  exit 1
fi
echo "$models_json"

echo "[smoke-bridge-container] PASS"
