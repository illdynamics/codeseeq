#!/usr/bin/env bash
set -Eeuo pipefail

log() {
  printf '[codeseeq-install] %s\n' "$*" >&2
}

die() {
  printf '[codeseeq-install:error] %s\n' "$*" >&2
  exit 1
}

resolve_self_path() {
  local source="$1"
  while [[ -h "$source" ]]; do
    local dir
    dir="$(cd -P "$(dirname "$source")" && pwd)"
    source="$(readlink "$source")"
    [[ "$source" == /* ]] || source="${dir}/${source}"
  done
  printf '%s\n' "$(cd -P "$(dirname "$source")" && pwd)/$(basename "$source")"
}

SELF_PATH="$(resolve_self_path "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd -P "$(dirname "$SELF_PATH")" && pwd)"
SOURCE_ROOT="$(cd -P "${SCRIPT_DIR}/.." && pwd)"

: "${CODESEEQ_INSTALL_DIR:=${HOME}/.config/codeseeq}"
: "${CODESEEQ_BIN_DIR:=${HOME}/bin}"

case "${CODESEEQ_INSTALL_DIR}/" in
  "${SOURCE_ROOT}/"*)
    die "CODESEEQ_INSTALL_DIR cannot be inside source repo: ${CODESEEQ_INSTALL_DIR}"
    ;;
esac

case "${CODESEEQ_BIN_DIR}/" in
  "${SOURCE_ROOT}/"*)
    die "CODESEEQ_BIN_DIR cannot be inside source repo: ${CODESEEQ_BIN_DIR}"
    ;;
esac

mkdir -p "$CODESEEQ_INSTALL_DIR" "$CODESEEQ_BIN_DIR"

if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete \
    --exclude '.git/' \
    --exclude '.codeseeq/' \
    --exclude 'system-prompt.md' \
    --exclude '.env' \
    --exclude '.env.*' \
    --exclude '.tmp-*/' \
    --exclude '.DS_Store' \
    --exclude '__pycache__/' \
    --exclude '.pytest_cache/' \
    --exclude 'node_modules/' \
    "$SOURCE_ROOT/" "$CODESEEQ_INSTALL_DIR/"
else
  tar -C "$SOURCE_ROOT" \
    --exclude '.git' \
    --exclude '.codeseeq' \
    --exclude 'system-prompt.md' \
    --exclude '.env' \
    --exclude '.env.*' \
    --exclude '.tmp-*' \
    --exclude '.DS_Store' \
    --exclude '__pycache__' \
    --exclude '.pytest_cache' \
    --exclude 'node_modules' \
    -cf - . | tar -C "$CODESEEQ_INSTALL_DIR" -xf -
fi

chmod +x "$CODESEEQ_INSTALL_DIR/codeseeq" "$CODESEEQ_INSTALL_DIR/scripts/install-local.sh"

launcher="${CODESEEQ_BIN_DIR}/codeseeq"
cat > "$launcher" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
exec "${CODESEEQ_INSTALL_DIR}/codeseeq" "\$@"
EOF
chmod +x "$launcher"


# CodeSeeq privacy hardening: do not auto-install @openai/codex@latest
# Pinned version: manually install if needed
if command -v codex >/dev/null 2>&1; then
  log "codex CLI found: $(command -v codex)"
else
  log "codex CLI not found. CodeSeeq uses a pinned Codex version (0.130.0)."
  log "Install manually if needed: npm install -g @openai/codex@0.130.0"
  log "Set CODESEEQ_ALLOW_LATEST_RELEASE=true to allow latest version fetching."
fi

# --- Install Python bridge deps if python3 available ---
if [[ -f "${CODESEEQ_INSTALL_DIR}/requirements-bridge.txt" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    if python3 -c 'import fastapi, uvicorn, httpx' >/dev/null 2>&1; then
      log "Python bridge deps: OK"
    else
      log "installing Python bridge deps ..."
      python3 -m pip install --quiet -r "${CODESEEQ_INSTALL_DIR}/requirements-bridge.txt" ||         log "WARN: pip install failed; install manually: python3 -m pip install -r ${CODESEEQ_INSTALL_DIR}/requirements-bridge.txt"
    fi
  else
    log "WARN: python3 not found; install manually for host/process mode: python3 -m pip install -r requirements-bridge.txt"
  fi
fi

log "installed repo snapshot to ${CODESEEQ_INSTALL_DIR}"
log "installed launcher to ${launcher}"

case ":${PATH}:" in
  *":${CODESEEQ_BIN_DIR}:"*) ;;
  *)
    log "add ${CODESEEQ_BIN_DIR} to PATH if needed"
    ;;
esac
