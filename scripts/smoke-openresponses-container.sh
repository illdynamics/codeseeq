#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/runtime.sh
source "${script_dir}/runtime.sh"
CONTAINER="$(codeseeq_detect_container)"
IMAGE="${IMAGE:-codeseeq:dev}"

if ! command -v "$CONTAINER" >/dev/null 2>&1; then
  echo "[smoke-openresponses-container] container runtime missing: $CONTAINER" >&2
  exit 1
fi

echo "[smoke-openresponses-container] verify open-responses package is installed"
"$CONTAINER" run --rm --entrypoint npm "$IMAGE" list -g --depth=0 open-responses >/dev/null

echo "[smoke-openresponses-container] verify open-responses binary launch (best-effort)"
if ! "$CONTAINER" run --rm --entrypoint open-responses "$IMAGE" --help >/dev/null 2>&1; then
  echo "[smoke-openresponses-container] WARN: open-responses binary not runnable on this architecture; runtime does not depend on it in single-container mode"
fi

echo "[smoke-openresponses-container] verify models command"
models_out="$($CONTAINER run --rm "$IMAGE" models)"
grep -q '"id": "deepseek-v4-flash"' <<<"$models_out"
grep -q '"id": "deepseek-v4-pro-thinking"' <<<"$models_out"

echo "[smoke-openresponses-container] verify generated config isolation"
config_out="$($CONTAINER run --rm "$IMAGE" config)"
grep -q '^CODEX_HOME=/home/codeseeq/.codeseeq$' <<<"$config_out"
grep -q '^model_provider = "codeseeq"$' <<<"$config_out"
grep -q '^wire_api = "responses"$' <<<"$config_out"
grep -q '^env_key = "DEEPSEEK_API_KEY"$' <<<"$config_out"
grep -q '^base_url = "http://127.0.0.1:8080/v1"$' <<<"$config_out"
grep -q '^approval_policy = "on-request"$' <<<"$config_out"
grep -q '^sandbox_mode = "workspace-write"$' <<<"$config_out"

echo "[smoke-openresponses-container] verify doctor output"
doctor_out="$($CONTAINER run --rm "$IMAGE" doctor)"
grep -q '^CodeSeeq Doctor' <<<"$doctor_out"
grep -q '^OpenResponses URL: http://127.0.0.1:8080/v1$' <<<"$doctor_out"
grep -q '^OpenResponses startup command: /usr/local/bin/codeseeq-bridge.py$' <<<"$doctor_out"
grep -Eq '^DeepSeek provider key: (present|missing)$' <<<"$doctor_out"
grep -q '^System prompt injection: codex-config-developer_instructions$' <<<"$doctor_out"

echo "[smoke-openresponses-container] PASS"
