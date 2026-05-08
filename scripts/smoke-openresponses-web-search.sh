#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/runtime.sh
source "${script_dir}/runtime.sh"
CONTAINER="$(codeseeq_detect_container)"
IMAGE="${IMAGE:-codeseeq:dev}"
MODEL="${MODEL:-deepseek-v4-flash}"

if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "[smoke-openresponses-web] SKIP: DEEPSEEK_API_KEY missing"
  exit 0
fi

if [[ -z "${BRAVE_API_KEY:-}" ]]; then
  echo "[smoke-openresponses-web] SKIP: BRAVE_API_KEY missing"
  exit 0
fi

echo "[smoke-openresponses-web] run ping-web"
out="$($CONTAINER run --rm \
  -e DEEPSEEK_API_KEY \
  -e BRAVE_API_KEY \
  -e CODESEEQ_MODEL="$MODEL" \
  "$IMAGE" ping-web)"

grep -q 'PONG-WEB: success' <<<"$out"
echo "[smoke-openresponses-web] PASS"
