#!/usr/bin/env bash
set -Eeuo pipefail

: "${DEEPSEEK_API_KEY:?DEEPSEEK_API_KEY required}"

token="codeseeq-deepseek-ok"
response="$(curl -fsS https://api.deepseek.com/chat/completions \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${DEEPSEEK_API_KEY}" \
  -d "{\"model\":\"deepseek-v4-flash\",\"messages\":[{\"role\":\"user\",\"content\":\"Return exactly: ${token}\"}],\"stream\":false}")"

if command -v jq >/dev/null 2>&1; then
  text="$(jq -r '.choices[0].message.content // empty' <<<"$response")"
else
  text="$response"
fi

if [[ "$text" != *"$token"* ]]; then
  printf '%s\n' "$response" >&2
  echo "[smoke-deepseek] token not found" >&2
  exit 1
fi

printf '%s\n' "$text"
