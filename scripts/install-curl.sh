#!/usr/bin/env bash
# CodeSeeq one-line installer
# Usage: curl -fsSL https://raw.githubusercontent.com/<user>/codeseeq/main/scripts/install-curl.sh | bash
set -Eeuo pipefail

log()  { printf '[codeseeq-install] %s\n' "$*" >&2; }
die()  { printf '[codeseeq-install:error] %s\n' "$*" >&2; exit 1; }

REPO="${CODESEEQ_REPO:-codeseeq/codeseeq}"
RELEASE_TAG="${CODESEEQ_RELEASE_TAG:-v0.2.9}"
RELEASE_URL="${CODESEEQ_RELEASE_URL:-https://github.com/${REPO}/releases/download/${RELEASE_TAG}/codeseeq-${RELEASE_TAG}.zip}"
INSTALL_DIR="${CODESEEQ_INSTALL_DIR:-${HOME}/.config/codeseeq}"
BIN_DIR="${CODESEEQ_BIN_DIR:-${HOME}/bin}"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

log "CodeSeeq installer"
log "Release: ${RELEASE_TAG}"
log "Install dir: ${INSTALL_DIR}"

cd "$WORK_DIR"

# Download release zip
if command -v curl >/dev/null 2>&1; then
  log "Downloading ${RELEASE_URL} ..."
  curl -fsSL -o codeseeq.zip "$RELEASE_URL" || die "download failed: ${RELEASE_URL}"
elif command -v wget >/dev/null 2>&1; then
  log "Downloading ${RELEASE_URL} ..."
  wget -q -O codeseeq.zip "$RELEASE_URL" || die "download failed: ${RELEASE_URL}"
else
  die "curl or wget is required"
fi

# Verify it's a valid zip
if ! python3 -c "import zipfile; zipfile.ZipFile('codeseeq.zip')" 2>/dev/null; then
  if ! unzip -t codeseeq.zip >/dev/null 2>&1; then
    die "downloaded file is not a valid zip"
  fi
fi

# Extract
log "Extracting ..."
unzip -qo codeseeq.zip || die "extract failed"

# Find the codeseeq launcher (may be in a subdirectory)
if [[ -f codeseeq ]]; then
  : # root level
elif [[ -d codeseeq-* ]]; then
  cd codeseeq-*
else
  # Find first directory containing codeseeq
  CODESEEQ_DIR=$(find . -maxdepth 2 -name codeseeq -type f | head -1 | xargs dirname)
  if [[ -n "$CODESEEQ_DIR" && -d "$CODESEEQ_DIR" ]]; then
    cd "$CODESEEQ_DIR"
  fi
fi

if [[ ! -f codeseeq ]]; then
  die "codeseeq launcher not found in extracted archive"
fi

chmod +x codeseeq

# Run the local installer
log "Installing to ${INSTALL_DIR} ..."
CODESEEQ_INSTALL_DIR="$INSTALL_DIR" CODESEEQ_BIN_DIR="$BIN_DIR" ./codeseeq install

log ""
log "Done! CodeSeeq v${RELEASE_TAG} installed."
log ""
log "Add to your shell if not already in PATH:"
log "  export PATH=\"${BIN_DIR}:\$PATH\""
log ""
log "Set your API key:"
log "  export DEEPSEEK_API_KEY=sk-..."
log ""
log "Then run:"
log "  codeseeq -y \"hello\""
log "  codeseeq run \"inspect this repo\""
