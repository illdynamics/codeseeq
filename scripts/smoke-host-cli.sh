#!/usr/bin/env bash
set -Eeuo pipefail

CONTAINER="${CONTAINER:-podman}"
IMAGE="${IMAGE:-codeseeq:dev}"
MODEL="${MODEL:-deepseek-v4-flash}"

if [[ ! -x ./codeseeq ]]; then
  echo "[smoke-host-cli] ./codeseeq is missing or not executable" >&2
  exit 1
fi

echo "[smoke-host-cli] run ./codeseeq models"
models_out="$(CONTAINER="$CONTAINER" IMAGE="$IMAGE" CODESEEQ_MODEL="$MODEL" CODESEEQ_APPROVAL_POLICY=never CODESEEQ_SANDBOX_MODE=danger-full-access ./codeseeq models)"
grep -q '"id": "deepseek-v4-flash"' <<<"$models_out"
grep -q '"id": "deepseek-v4-pro-thinking"' <<<"$models_out"

echo "[smoke-host-cli] run ./codeseeq doctor"
doctor_out="$(CONTAINER="$CONTAINER" IMAGE="$IMAGE" CODESEEQ_MODEL="$MODEL" CODESEEQ_APPROVAL_POLICY=never CODESEEQ_SANDBOX_MODE=danger-full-access ./codeseeq doctor)"
grep -q '^CodeSeeq Doctor' <<<"$doctor_out"

if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "[smoke-host-cli] SKIP live run: DEEPSEEK_API_KEY missing"
  exit 0
fi

echo "[smoke-host-cli] run ./codeseeq run"
run_out="$(CONTAINER="$CONTAINER" IMAGE="$IMAGE" CODESEEQ_MODEL="$MODEL" CODESEEQ_APPROVAL_POLICY=never CODESEEQ_SANDBOX_MODE=danger-full-access ./codeseeq run "Return exactly: codeseeq-ok")"
grep -qi 'codeseeq-ok' <<<"$run_out"

echo "[smoke-host-cli] PASS"
