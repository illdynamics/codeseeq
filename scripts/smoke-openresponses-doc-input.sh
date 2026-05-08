#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/runtime.sh
source "${script_dir}/runtime.sh"
CONTAINER="$(codeseeq_detect_container)"
IMAGE="${IMAGE:-codeseeq:dev}"
MODEL="${MODEL:-deepseek-v4-flash}"

if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "[smoke-openresponses-doc] SKIP: DEEPSEEK_API_KEY missing"
  exit 0
fi

if [[ -z "${UNSTRUCTURED_API_KEY:-}" ]]; then
  echo "[smoke-openresponses-doc] SKIP: UNSTRUCTURED_API_KEY missing"
  exit 0
fi

echo "[smoke-openresponses-doc] run ping-docs"
out="$($CONTAINER run --rm \
  -e DEEPSEEK_API_KEY \
  -e UNSTRUCTURED_API_KEY \
  -e CODESEEQ_MODEL="$MODEL" \
  "$IMAGE" ping-docs)"

grep -q 'PONG-DOCS: success' <<<"$out"
echo "[smoke-openresponses-doc] PASS"
