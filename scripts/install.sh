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
    --exclude '.env' \
    --exclude '.env.*' \
    --exclude '.tmp-*' \
    --exclude '.DS_Store' \
    --exclude '__pycache__' \
    --exclude '.pytest_cache' \
    --exclude 'node_modules' \
    -cf - . | tar -C "$CODESEEQ_INSTALL_DIR" -xf -
fi

chmod +x "$CODESEEQ_INSTALL_DIR/codeseeq" "$CODESEEQ_INSTALL_DIR/scripts/install.sh"

launcher="${CODESEEQ_BIN_DIR}/codeseeq"
cat > "$launcher" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
exec "${CODESEEQ_INSTALL_DIR}/codeseeq" "\$@"
EOF
chmod +x "$launcher"

log "installed repo snapshot to ${CODESEEQ_INSTALL_DIR}"
log "installed launcher to ${launcher}"

case ":${PATH}:" in
  *":${CODESEEQ_BIN_DIR}:"*) ;;
  *)
    log "add ${CODESEEQ_BIN_DIR} to PATH if needed"
    ;;
esac
