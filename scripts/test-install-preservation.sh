#!/usr/bin/env bash
# Regression: upgrading an installed snapshot must not delete a user's
# interactive provider/model config, local overrides, prompt, or .env secrets.
set -Eeuo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
home="$tmp/home"
install_dir="$home/.config/codeseeq"
mkdir -p "$install_dir" "$tmp/bin" "$tmp/fakebin"
printf '%s\n' '{"CODESEEQ_PROVIDER":"gguf","CODESEEQ_MODEL":"gguf@/tmp/example-model.gguf"}' > "$install_dir/config.json"
printf '%s\n' '{"models":{"example-model.gguf":{"temperature":0.2}}}' > "$install_dir/gguf-models.local.json"
printf '%s\n' 'important customized system prompt' > "$install_dir/system-prompt.md"
printf '%s\n' 'CODESEEQ_TEST_SECRET=do-not-remove' > "$install_dir/.env"
printf '#!/usr/bin/env bash\nexit 0\n' > "$tmp/fakebin/codex"
chmod +x "$tmp/fakebin/codex"
(
  cd "$repo_root"
  HOME="$home" \
  PATH="$tmp/fakebin:$PATH" \
  CODESEEQ_CONFIG_HOME="$install_dir" \
  CODESEEQ_INSTALL_DIR="$install_dir" \
  CODESEEQ_BIN_DIR="$tmp/bin" \
  ./codeseeq install > "$tmp/install.out" 2> "$tmp/install.err"
) || { cat "$tmp/install.err" >&2; exit 1; }
python3 - "$tmp" <<'PY'
from pathlib import Path
import json
import sys
root = Path(sys.argv[1])
p = root / "home/.config/codeseeq"
assert (p / "VERSION").read_text().strip() == "v0.5.4"
assert json.loads((p / "config.json").read_text())["CODESEEQ_PROVIDER"] == "gguf"
assert json.loads((p / "gguf-models.local.json").read_text())["models"]["example-model.gguf"]["temperature"] == 0.2
assert (p / "system-prompt.md").read_text().startswith("important customized")
assert (p / ".env").read_text().strip() == "CODESEEQ_TEST_SECRET=do-not-remove"
assert (root / "bin/codeseeq").is_file()
print("[install-preservation] PASS: upgraded to v0.5.4 and preserved user configuration")
PY
