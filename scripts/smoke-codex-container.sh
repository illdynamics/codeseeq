#!/usr/bin/env bash
set -Eeuo pipefail

CONTAINER="${CONTAINER:-podman}"
IMAGE="${IMAGE:-codeseeq:dev}"

models=(
  deepseek-v4-flash
  deepseek-v4-flash-thinking
  deepseek-v4-pro
  deepseek-v4-pro-thinking
)

echo "[smoke-codex-container] verify model normalization"
for m in "${models[@]}"; do
  out="$($CONTAINER run --rm -e CODESEEQ_MODEL="$m" "$IMAGE" doctor)"
  grep -q "^Model (logical): $m$" <<<"$out"
  case "$m" in
    deepseek-v4-flash|deepseek-v4-flash-thinking)
      grep -q '^Model (provider): deepseek@deepseek-v4-flash$' <<<"$out"
      ;;
    deepseek-v4-pro|deepseek-v4-pro-thinking)
      grep -q '^Model (provider): deepseek@deepseek-v4-pro$' <<<"$out"
      ;;
  esac
  case "$m" in
    *-thinking)
      grep -q '^Thinking: true$' <<<"$out"
      ;;
    *)
      grep -q '^Thinking: false$' <<<"$out"
      ;;
  esac
done

echo "[smoke-codex-container] verify .codeseeq isolation"
$CONTAINER run --rm --entrypoint bash "$IMAGE" -lc 'test ! -e /home/codeseeq/.codex'

if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "[smoke-codex-container] SKIP live Codex prompt: DEEPSEEK_API_KEY missing"
  exit 0
fi

echo "[smoke-codex-container] run direct Codex prompt"
prompt_out="$($CONTAINER run --rm \
  -e DEEPSEEK_API_KEY \
  "$IMAGE" \
  "Return exactly: codeseeq-ok")"

grep -qi 'codeseeq-ok' <<<"$prompt_out"

echo "[smoke-codex-container] PASS"
