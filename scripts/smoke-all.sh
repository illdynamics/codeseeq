#!/usr/bin/env bash
set -Eeuo pipefail

CONTAINER="${CONTAINER:-podman}"
IMAGE="${IMAGE:-codeseeq:dev}"
MODEL="${MODEL:-deepseek-v4-flash}"
PROMPT="${PROMPT:-Return exactly: codeseeq-ok}"
ENV_CLEAN=(env -u CODESEEQ_CODEX_HOME -u CODESEEQ_APPROVAL_POLICY -u CODESEEQ_SANDBOX_MODE -u CODESEEQ_OPENRESPONSES_HOST -u CODESEEQ_OPENRESPONSES_PORT -u CODESEEQ_OPENRESPONSES_URL)

echo "[smoke-all] container runtime: $CONTAINER"
if ! command -v "$CONTAINER" >/dev/null 2>&1; then
  echo "[smoke-all] FAIL: $CONTAINER not found" >&2
  exit 1
fi

if [[ -f .env ]]; then
  echo "[smoke-all] loading .env (read-only)"
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
else
  echo "[smoke-all] .env not found; live tests may be skipped"
fi

key_state() {
  if [[ -n "${1:-}" ]]; then
    printf 'present'
  else
    printf 'missing'
  fi
}

echo "[smoke-all] key presence: DEEPSEEK_API_KEY=$(key_state "${DEEPSEEK_API_KEY:-}") BRAVE_API_KEY=$(key_state "${BRAVE_API_KEY:-}") UNSTRUCTURED_API_KEY=$(key_state "${UNSTRUCTURED_API_KEY:-}")"

echo "[smoke-all] step: inspect-openresponses"
make inspect-openresponses

echo "[smoke-all] step: build"
make build

echo "[smoke-all] step: models"
make models

echo "[smoke-all] step: container config/doctor checks"
./scripts/smoke-openresponses-container.sh

if [[ -n "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "[smoke-all] step: ping"
  make ping

  echo "[smoke-all] step: ping-stream"
  make ping-stream

  echo "[smoke-all] step: prompt"
  "${ENV_CLEAN[@]}" make prompt PROMPT="$PROMPT"

  echo "[smoke-all] step: codex container smoke"
  ./scripts/smoke-codex-container.sh

  echo "[smoke-all] step: host cli smoke"
  ./scripts/smoke-host-cli.sh
else
  echo "[smoke-all] SKIP live deepseek/codex checks: DEEPSEEK_API_KEY missing"
fi

echo "[smoke-all] step: doctor"
"${ENV_CLEAN[@]}" make doctor

if [[ -n "${BRAVE_API_KEY:-}" && -n "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "[smoke-all] step: ping-web"
  make ping-web
  CONTAINER="$CONTAINER" IMAGE="$IMAGE" CODESEEQ_MODEL="$MODEL" ./codeseeq ping-web >/dev/null
  ./scripts/smoke-openresponses-web-search.sh
else
  echo "[smoke-all] SKIP web search smoke"
fi

if [[ -n "${UNSTRUCTURED_API_KEY:-}" && -n "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "[smoke-all] step: ping-docs"
  make ping-docs
  CONTAINER="$CONTAINER" IMAGE="$IMAGE" CODESEEQ_MODEL="$MODEL" ./codeseeq ping-docs >/dev/null
  ./scripts/smoke-openresponses-doc-input.sh
else
  echo "[smoke-all] SKIP doc parsing smoke"
fi

if [[ -n "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "[smoke-all] step: stream sanity"
  ./scripts/smoke-openresponses-stream.sh
fi

echo "[smoke-all] step: model alias normalization"
for m in deepseek-v4-flash deepseek-v4-flash-thinking deepseek-v4-pro deepseek-v4-pro-thinking; do
  out="$("$CONTAINER" run --rm -e CODESEEQ_MODEL="$m" "$IMAGE" doctor)"
  grep -q "^Model (logical): $m$" <<<"$out"
done

echo "[smoke-all] step: grep wrong openresponses references in operational files"
if rg -n "masaic|openresponses/openresponses|docker.io/masaicai/open-responses" \
  README.md docs/architecture.md docs/security.md Dockerfile Makefile bin codeseeq config >/dev/null; then
  echo "[smoke-all] FAIL: found old/wrong OpenResponses references" >&2
  exit 1
fi

# Compose must not be a supported runtime path.
if [[ -f docker-compose.yml ]]; then
  echo "[smoke-all] FAIL: docker-compose.yml exists" >&2
  exit 1
fi

if rg -n "docker compose up|docker-compose up|compose-first|fallback compose|two-container fallback" \
  README.md docs/architecture.md docs/security.md Makefile codeseeq >/dev/null; then
  echo "[smoke-all] FAIL: found compose/two-container operational guidance" >&2
  exit 1
fi

echo "[smoke-all] step: grep .codex operational usage"
if rg -n "/home/codeseeq/\.codex|CODESEEQ_CODEX_HOME=.*/\.codex" \
  README.md docs/architecture.md docs/security.md Dockerfile Makefile bin codeseeq config >/dev/null; then
  echo "[smoke-all] FAIL: found .codex in supported operational paths" >&2
  exit 1
fi

echo "[smoke-all] PASS"
