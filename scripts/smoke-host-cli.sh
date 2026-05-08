#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/runtime.sh
source "${script_dir}/runtime.sh"
CONTAINER="$(codeseeq_detect_container)"
IMAGE="${IMAGE:-codeseeq:dev}"
MODEL="${MODEL:-deepseek-v4-flash}"

if [[ ! -x ./codeseeq ]]; then
  echo "[smoke-host-cli] ./codeseeq is missing or not executable" >&2
  exit 1
fi

echo "[smoke-host-cli] run ./codeseeq models"
models_out="$(CONTAINER="$CONTAINER" IMAGE="$IMAGE" CODESEEQ_MODEL="$MODEL" ./codeseeq models)"
grep -q '"id": "deepseek-v4-flash"' <<<"$models_out"
grep -q '"id": "deepseek-v4-pro-thinking"' <<<"$models_out"

echo "[smoke-host-cli] run ./codeseeq doctor"
doctor_out="$(CONTAINER="$CONTAINER" IMAGE="$IMAGE" CODESEEQ_MODEL="$MODEL" ./codeseeq doctor)"
grep -q '^CodeSeeq Doctor' <<<"$doctor_out"

if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "[smoke-host-cli] SKIP live run: DEEPSEEK_API_KEY missing"
  exit 0
fi

echo "[smoke-host-cli] run ./codeseeq bare prompt"
bare_out="$(CONTAINER="$CONTAINER" IMAGE="$IMAGE" CODESEEQ_MODEL="$MODEL" ./codeseeq "Return exactly: codeseeq-ok")"
grep -qi 'codeseeq-ok' <<<"$bare_out"

echo "[smoke-host-cli] run ./codeseeq run"
run_out="$(CONTAINER="$CONTAINER" IMAGE="$IMAGE" CODESEEQ_MODEL="$MODEL" ./codeseeq run "Return exactly: codeseeq-ok")"
grep -qi 'codeseeq-ok' <<<"$run_out"

echo "[smoke-host-cli] PASS"
