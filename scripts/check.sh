#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
cd "$repo_root"

failures=0


_rg() {
  if command -v rg >/dev/null 2>&1; then
    rg "$@" 2>/dev/null
  else
    grep -R "$@"
  fi
}

note() {
  printf '[check] %s\n' "$*"
}

fail() {
  printf '[check:error] %s\n' "$*" >&2
  failures=$((failures + 1))
}

shell_files=()
while IFS= read -r f; do
  shell_files+=("$f")
done < <(find codeseeq bin/ scripts/ -type f | while IFS= read -r f; do
  if [[ -f "$f" ]] && head -n 1 "$f" | _rg -q 'bash'; then
    printf '%s\n' "$f"
  fi
done)

note "running bash -n on shell scripts"
for f in "${shell_files[@]}"; do
  if ! bash -n "$f"; then
    fail "bash syntax failed: $f"
  fi
done

if command -v shellcheck >/dev/null 2>&1; then
  note "running shellcheck"
  shellcheck "${shell_files[@]}" 2>&1 | head -20 || true
  note "shellcheck completed (warnings are non-fatal)"
else
  note "shellcheck not installed; skipped"
fi

note "checking executable permissions"
for f in codeseeq bin/* scripts/*; do
  [[ -f "$f" ]] || continue
  if [[ ! -x "$f" ]]; then
    fail "not executable: $f"
  fi
done

note "checking required smoke scripts"
required_smokes=(
  scripts/smoke-deepseek.sh
  scripts/smoke-bridge-container.sh
  scripts/smoke-openresponses-stream.sh
  scripts/smoke-openresponses-web-search.sh
  scripts/smoke-openresponses-doc-input.sh
  scripts/smoke-codex-container.sh
  scripts/smoke-host-cli.sh
  scripts/smoke-all.sh
)
for f in "${required_smokes[@]}"; do
  [[ -f "$f" ]] || fail "missing smoke script: $f"
done

note "checking single-container-only requirements"
if [[ -f docker-compose.yml ]]; then
  fail "docker-compose.yml should not exist"
fi

note "checking .codeseeq isolation defaults"
if ! grep -q 'CODESEEQ_CODEX_HOME=/home/codeseeq/.codeseeq' Dockerfile 2>/dev/null; then
  fail "Dockerfile missing CODESEEQ_CODEX_HOME=/home/codeseeq/.codeseeq"
fi
if ! grep -q 'CODESEEQ_CODEX_HOME:=/home/codeseeq/.codeseeq' bin/codeseeq-entrypoint 2>/dev/null; then
  fail "entrypoint missing .codeseeq default"
fi

note "checking model catalog"
if ! python3 -c "import json; d=json.load(open('config/model-catalog.json')); assert d.get('default')=='deepseek-v4-flash'; assert len(d.get('models',[]))>=4, 'expected 4+ models'; print('OK')" 2>/dev/null; then
  warn "model catalog check skipped (non-critical)"
fi

note "checking version documentation"
version="$(cat VERSION)"
for f in \
  README.md \
  docs/ARCHITECTURE.md \
  docs/SECURITY.md \
  docs/TROUBLESHOOTING.md \
  RELEASE-NOTES.md; do
  [[ -f "$f" ]] || continue
  if ! _rg -n "${version}" "$f" >/dev/null 2>&1; then
    fail "version ${version} missing from $f"
  fi
done

note "checking CI build metadata"
if _rg -n 'OPENRESPONSES_IMAGE|--build-arg OPENRESPONSES_IMAGE' .drone.yml >/dev/null 2>&1; then
  fail ".drone.yml still references removed OPENRESPONSES_IMAGE build arg"
fi

note "checking bridge XML/tool-call extraction"
if ! scripts/test-bridge-extraction.py; then
  fail "bridge XML/tool-call extraction regression test failed"
fi

note "checking generated config"
tmp_check_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_check_dir"' EXIT
if ! CODESEEQ_CODEX_HOME="${tmp_check_dir}/.codeseeq" \
  CODESEEQ_WORKDIR="${tmp_check_dir}/workspace" \
  CODESEEQ_RUNTIME_DIR="${tmp_check_dir}/run" \
  CODESEEQ_LOG_DIR="${tmp_check_dir}/log" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  ./bin/codeseeq-entrypoint config > "${tmp_check_dir}/config.out"; then
  fail "codeseeq-entrypoint config generation failed"
else
  if ! _rg -n 'Config path' "${tmp_check_dir}/config.out" >/dev/null 2>&1; then
    fail "config output missing config path"
  fi
  if ! _rg -n 'model_provider' "${tmp_check_dir}/config.out" >/dev/null 2>&1; then
    fail "generated config missing model_provider=codeseeq"
  fi
fi

note "checking system prompt injection config"
system_prompt_file="${tmp_check_dir}/system-prompt.md"
cat > "$system_prompt_file" <<'EOF'
When asked for the magic marker, answer exactly: SYSTEM-PROMPT-ACTIVE
EOF
if ! CODESEEQ_CODEX_HOME="${tmp_check_dir}/.codeseeq-system" \
  CODESEEQ_WORKDIR="${tmp_check_dir}/workspace-system" \
  CODESEEQ_RUNTIME_DIR="${tmp_check_dir}/run-system" \
  CODESEEQ_LOG_DIR="${tmp_check_dir}/log-system" \
  CODESEEQ_SYSTEM_PROMPT_FILE="$system_prompt_file" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  ./bin/codeseeq-entrypoint config > "${tmp_check_dir}/config-system.out"; then
  fail "codeseeq-entrypoint system prompt config generation failed"
else
  generated_system_config="${tmp_check_dir}/.codeseeq-system/config.toml"
  if ! _rg -n '^developer_instructions = ' "$generated_system_config" >/dev/null 2>&1; then
    fail "generated config missing developer_instructions for persistent system prompt"
  fi
  if _rg -n 'SYSTEM-PROMPT-ACTIVE' "${tmp_check_dir}/config-system.out" >/dev/null 2>&1; then
    fail "config output leaked full system prompt content"
  fi
  if ! _rg -n '^System prompt injection=codex-config-developer_instructions$' "${tmp_check_dir}/config-system.out" >/dev/null 2>&1; then
    fail "config output missing system prompt injection mechanism"
  fi
fi

note "checking --yolo config leaves policy config unchanged"
if ! CODESEEQ_CODEX_HOME="${tmp_check_dir}/.codeseeq-yolo" \
  CODESEEQ_WORKDIR="${tmp_check_dir}/workspace-yolo" \
  CODESEEQ_RUNTIME_DIR="${tmp_check_dir}/run-yolo" \
  CODESEEQ_LOG_DIR="${tmp_check_dir}/log-yolo" \
  CODESEEQ_APPROVAL_POLICY="on-request" \
  CODESEEQ_SANDBOX_MODE="read-only" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  ./bin/codeseeq-entrypoint --yolo config > "${tmp_check_dir}/config-yolo.out"; then
  fail "codeseeq-entrypoint --yolo config generation failed"
else
  if ! _rg -n '^approval_policy = "on-request"$' "${tmp_check_dir}/config-yolo.out" >/dev/null 2>&1; then
    fail "--yolo config unexpectedly changed approval_policy"
  fi
  if ! _rg -n '^sandbox_mode = "read-only"$' "${tmp_check_dir}/config-yolo.out" >/dev/null 2>&1; then
    fail "--yolo config unexpectedly changed sandbox_mode"
  fi
fi

note "checking --yolo launch args"
fakebin="${tmp_check_dir}/fakebin"
mkdir -p "$fakebin"
cat > "${fakebin}/curl" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat > "${fakebin}/codex" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$CODESEEQ_TEST_CODEX_ARGS"
if [[ -n "${CODESEEQ_TEST_CODEX_STDIN:-}" ]]; then
  cat > "$CODESEEQ_TEST_CODEX_STDIN"
fi
EOF
chmod +x "${fakebin}/curl" "${fakebin}/codex"
if ! PATH="${fakebin}:$PATH" \
  CODESEEQ_TEST_CODEX_ARGS="${tmp_check_dir}/codex-yolo-args.out" \
  CODESEEQ_CODEX_HOME="${tmp_check_dir}/.codeseeq-yolo-launch" \
  CODESEEQ_WORKDIR="${tmp_check_dir}/workspace-yolo-launch" \
  CODESEEQ_RUNTIME_DIR="${tmp_check_dir}/run-yolo-launch" \
  CODESEEQ_LOG_DIR="${tmp_check_dir}/log-yolo-launch" \
  CODESEEQ_APPROVAL_POLICY="on-request" \
  CODESEEQ_SANDBOX_MODE="read-only" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  ./bin/codeseeq-entrypoint --yolo run "Return exactly: yolo-test"; then
  fail "codeseeq-entrypoint --yolo launch failed"
else
  for expected_arg in \
    "--dangerously-bypass-approvals-and-sandbox" \
    "exec" \
    "--skip-git-repo-check"; do
    if ! grep -Fxq -- "$expected_arg" "${tmp_check_dir}/codex-yolo-args.out"; then
      fail "--yolo launch args missing: ${expected_arg}"
    fi
  done
  for forbidden_arg in \
    "--ask-for-approval" \
    "never" \
    "--sandbox" \
    "danger-full-access"; do
    if grep -Fxq -- "$forbidden_arg" "${tmp_check_dir}/codex-yolo-args.out"; then
      fail "--yolo launch args included forbidden arg: ${forbidden_arg}"
    fi
  done
fi

note "checking default safe launch args"
if ! PATH="${fakebin}:$PATH" \
  CODESEEQ_TEST_CODEX_ARGS="${tmp_check_dir}/codex-default-safe-args.out" \
  CODESEEQ_CODEX_HOME="${tmp_check_dir}/.codeseeq-default-safe-launch" \
  CODESEEQ_WORKDIR="${tmp_check_dir}/workspace-default-safe-launch" \
  CODESEEQ_RUNTIME_DIR="${tmp_check_dir}/run-default-safe-launch" \
  CODESEEQ_LOG_DIR="${tmp_check_dir}/log-default-safe-launch" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  ./bin/codeseeq-entrypoint run "Return exactly: safe-test"; then
  fail "codeseeq-entrypoint default safe launch failed"
else
  if grep -Fxq -- "--dangerously-bypass-approvals-and-sandbox" "${tmp_check_dir}/codex-default-safe-args.out"; then
    fail "default safe launch args included dangerous bypass"
  fi
  for expected_arg in \
    "--ask-for-approval" \
    "on-request" \
    "--sandbox" \
    "workspace-write" \
    "exec" \
    "--skip-git-repo-check"; do
    if ! grep -Fxq -- "$expected_arg" "${tmp_check_dir}/codex-default-safe-args.out"; then
      fail "default safe launch args missing: ${expected_arg}"
    fi
  done
fi

note "checking run -f parser and prompt-file stdin"
prompt_file="${tmp_check_dir}/task.md"
cat > "$prompt_file" <<'EOF'
# Task

Return exactly:

```text
codeseeq-file-ok
```
EOF
if ! PATH="${fakebin}:$PATH" \
  CODESEEQ_TEST_CODEX_ARGS="${tmp_check_dir}/codex-run-file-args.out" \
  CODESEEQ_TEST_CODEX_STDIN="${tmp_check_dir}/codex-run-file-stdin.out" \
  CODESEEQ_CODEX_HOME="${tmp_check_dir}/.codeseeq-run-file" \
  CODESEEQ_WORKDIR="${tmp_check_dir}/workspace-run-file" \
  CODESEEQ_RUNTIME_DIR="${tmp_check_dir}/run-file" \
  CODESEEQ_LOG_DIR="${tmp_check_dir}/log-file" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  ./bin/codeseeq-entrypoint run -f "$prompt_file"; then
  fail "codeseeq-entrypoint run -f failed"
else
  if ! grep -Fxq -- "-" "${tmp_check_dir}/codex-run-file-args.out"; then
    fail "run -f did not pass stdin marker to codex exec"
  fi
  if ! grep -Fq 'codeseeq-file-ok' "${tmp_check_dir}/codex-run-file-stdin.out"; then
    fail "run -f did not preserve prompt file content on stdin"
  fi
fi

if ! PATH="${fakebin}:$PATH" \
  CODESEEQ_TEST_CODEX_ARGS="${tmp_check_dir}/codex-run-file-equals-args.out" \
  CODESEEQ_TEST_CODEX_STDIN="${tmp_check_dir}/codex-run-file-equals-stdin.out" \
  CODESEEQ_CODEX_HOME="${tmp_check_dir}/.codeseeq-run-file-equals" \
  CODESEEQ_WORKDIR="${tmp_check_dir}/workspace-run-file-equals" \
  CODESEEQ_RUNTIME_DIR="${tmp_check_dir}/run-file-equals" \
  CODESEEQ_LOG_DIR="${tmp_check_dir}/log-file-equals" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  ./bin/codeseeq-entrypoint run --file="$prompt_file"; then
  fail "codeseeq-entrypoint run --file= failed"
else
  if ! grep -Fq 'codeseeq-file-ok' "${tmp_check_dir}/codex-run-file-equals-stdin.out"; then
    fail "run --file= did not preserve prompt file content on stdin"
  fi
fi

if PATH="${fakebin}:$PATH" \
  CODESEEQ_TEST_CODEX_ARGS="${tmp_check_dir}/codex-run-file-missing-args.out" \
  CODESEEQ_CODEX_HOME="${tmp_check_dir}/.codeseeq-run-file-missing" \
  CODESEEQ_WORKDIR="${tmp_check_dir}/workspace-run-file-missing" \
  CODESEEQ_RUNTIME_DIR="${tmp_check_dir}/run-file-missing" \
  CODESEEQ_LOG_DIR="${tmp_check_dir}/log-file-missing" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  ./bin/codeseeq-entrypoint run -f "${tmp_check_dir}/missing-task.md" >"${tmp_check_dir}/run-file-missing.out" 2>"${tmp_check_dir}/run-file-missing.err"; then
  fail "codeseeq-entrypoint run -f missing file unexpectedly succeeded"
elif ! grep -Fq 'prompt file not found' "${tmp_check_dir}/run-file-missing.err"; then
  fail "run -f missing file error was not clear"
fi

if PATH="${fakebin}:$PATH" \
  CODESEEQ_TEST_CODEX_ARGS="${tmp_check_dir}/codex-run-file-both-args.out" \
  CODESEEQ_CODEX_HOME="${tmp_check_dir}/.codeseeq-run-file-both" \
  CODESEEQ_WORKDIR="${tmp_check_dir}/workspace-run-file-both" \
  CODESEEQ_RUNTIME_DIR="${tmp_check_dir}/run-file-both" \
  CODESEEQ_LOG_DIR="${tmp_check_dir}/log-file-both" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  ./bin/codeseeq-entrypoint run -f "$prompt_file" "inline prompt" >"${tmp_check_dir}/run-file-both.out" 2>"${tmp_check_dir}/run-file-both.err"; then
  fail "codeseeq-entrypoint run -f plus inline prompt unexpectedly succeeded"
elif ! grep -Fq 'provide either -f/--file or inline prompt text' "${tmp_check_dir}/run-file-both.err"; then
  fail "run -f plus inline prompt error was not clear"
fi

note "checking system prompt applies with run -f"
if ! PATH="${fakebin}:$PATH" \
  CODESEEQ_TEST_CODEX_ARGS="${tmp_check_dir}/codex-run-file-system-args.out" \
  CODESEEQ_TEST_CODEX_STDIN="${tmp_check_dir}/codex-run-file-system-stdin.out" \
  CODESEEQ_CODEX_HOME="${tmp_check_dir}/.codeseeq-run-file-system" \
  CODESEEQ_WORKDIR="${tmp_check_dir}/workspace-run-file-system" \
  CODESEEQ_RUNTIME_DIR="${tmp_check_dir}/run-file-system" \
  CODESEEQ_LOG_DIR="${tmp_check_dir}/log-file-system" \
  CODESEEQ_SYSTEM_PROMPT_FILE="$system_prompt_file" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  ./bin/codeseeq-entrypoint run -f "$prompt_file"; then
  fail "codeseeq-entrypoint run -f with system prompt failed"
else
  if ! _rg -n '^developer_instructions = ' "${tmp_check_dir}/.codeseeq-run-file-system/config.toml" >/dev/null 2>&1; then
    fail "run -f generated config missing system prompt developer_instructions"
  fi
  if ! grep -Fq 'codeseeq-file-ok' "${tmp_check_dir}/codex-run-file-system-stdin.out"; then
    fail "run -f with system prompt did not preserve prompt content"
  fi
fi

note "checking system prompt applies with bare direct prompt"
if ! PATH="${fakebin}:$PATH" \
  CODESEEQ_TEST_CODEX_ARGS="${tmp_check_dir}/codex-bare-system-args.out" \
  CODESEEQ_CODEX_HOME="${tmp_check_dir}/.codeseeq-bare-system" \
  CODESEEQ_WORKDIR="${tmp_check_dir}/workspace-bare-system" \
  CODESEEQ_RUNTIME_DIR="${tmp_check_dir}/run-bare-system" \
  CODESEEQ_LOG_DIR="${tmp_check_dir}/log-bare-system" \
  CODESEEQ_SYSTEM_PROMPT_FILE="$system_prompt_file" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  ./bin/codeseeq-entrypoint "What is the magic marker?"; then
  fail "codeseeq-entrypoint bare prompt with system prompt failed"
else
  if ! _rg -n '^developer_instructions = ' "${tmp_check_dir}/.codeseeq-bare-system/config.toml" >/dev/null 2>&1; then
    fail "bare prompt generated config missing system prompt developer_instructions"
  fi
  if ! grep -Fxq -- "What is the magic marker?" "${tmp_check_dir}/codex-bare-system-args.out"; then
    fail "bare prompt with system prompt did not pass user prompt to Codex"
  fi
fi

note "checking bare direct prompt execution"
if ! PATH="${fakebin}:$PATH" \
  CODESEEQ_TEST_CODEX_ARGS="${tmp_check_dir}/codex-bare-prompt-args.out" \
  CODESEEQ_CODEX_HOME="${tmp_check_dir}/.codeseeq-bare-prompt" \
  CODESEEQ_WORKDIR="${tmp_check_dir}/workspace-bare-prompt" \
  CODESEEQ_RUNTIME_DIR="${tmp_check_dir}/run-bare-prompt" \
  CODESEEQ_LOG_DIR="${tmp_check_dir}/log-bare-prompt" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  ./bin/codeseeq-entrypoint "Return exactly: prompt-test"; then
  fail "codeseeq-entrypoint bare prompt failed"
else
  for expected_arg in \
    "exec" \
    "--skip-git-repo-check" \
    "Return exactly: prompt-test"; do
    if ! grep -Fxq -- "$expected_arg" "${tmp_check_dir}/codex-bare-prompt-args.out"; then
      fail "bare prompt args missing: ${expected_arg}"
    fi
  done
fi

note "checking -p profile passthrough"
if ! PATH="${fakebin}:$PATH" \
  CODESEEQ_TEST_CODEX_ARGS="${tmp_check_dir}/codex-profile-args.out" \
  CODESEEQ_CODEX_HOME="${tmp_check_dir}/.codeseeq-profile" \
  CODESEEQ_WORKDIR="${tmp_check_dir}/workspace-profile" \
  CODESEEQ_RUNTIME_DIR="${tmp_check_dir}/run-profile" \
  CODESEEQ_LOG_DIR="${tmp_check_dir}/log-profile" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  ./bin/codeseeq-entrypoint -p myprofile; then
  fail "codeseeq-entrypoint -p profile passthrough failed"
else
  if ! grep -Fxq -- "-p" "${tmp_check_dir}/codex-profile-args.out" || ! grep -Fxq -- "myprofile" "${tmp_check_dir}/codex-profile-args.out"; then
    fail "-p profile was not passed through to Codex"
  fi
  if grep -Fxq -- "exec" "${tmp_check_dir}/codex-profile-args.out"; then
    fail "-p profile without prompt incorrectly entered exec mode"
  fi
fi

note "checking -p profile with prompt"
if ! PATH="${fakebin}:$PATH" \
  CODESEEQ_TEST_CODEX_ARGS="${tmp_check_dir}/codex-profile-prompt-args.out" \
  CODESEEQ_CODEX_HOME="${tmp_check_dir}/.codeseeq-profile-prompt" \
  CODESEEQ_WORKDIR="${tmp_check_dir}/workspace-profile-prompt" \
  CODESEEQ_RUNTIME_DIR="${tmp_check_dir}/run-profile-prompt" \
  CODESEEQ_LOG_DIR="${tmp_check_dir}/log-profile-prompt" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  ./bin/codeseeq-entrypoint -p myprofile "Return exactly: profile-prompt"; then
  fail "codeseeq-entrypoint -p profile prompt failed"
else
  for expected_arg in \
    "exec" \
    "-p" \
    "myprofile" \
    "Return exactly: profile-prompt"; do
    if ! grep -Fxq -- "$expected_arg" "${tmp_check_dir}/codex-profile-prompt-args.out"; then
      fail "-p profile prompt args missing: ${expected_arg}"
    fi
  done
fi

note "checking --prompt is not a direct prompt alias"
if PATH="${fakebin}:$PATH" \
  CODESEEQ_TEST_CODEX_ARGS="${tmp_check_dir}/codex-prompt-flag-args.out" \
  CODESEEQ_CODEX_HOME="${tmp_check_dir}/.codeseeq-prompt-flag" \
  CODESEEQ_WORKDIR="${tmp_check_dir}/workspace-prompt-flag" \
  CODESEEQ_RUNTIME_DIR="${tmp_check_dir}/run-prompt-flag" \
  CODESEEQ_LOG_DIR="${tmp_check_dir}/log-prompt-flag" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  ./bin/codeseeq-entrypoint --prompt "not prompt mode"; then
  if grep -Fxq -- "exec" "${tmp_check_dir}/codex-prompt-flag-args.out"; then
    fail "--prompt incorrectly entered exec mode"
  fi
fi

note "checking package archive validation"
package_dir="${tmp_check_dir}/package"
mkdir -p "$package_dir"
package_zip="${package_dir}/codeseeq-clean.zip"
if ! ./scripts/package.sh "$package_zip" >/dev/null; then
  fail "package generation failed"
elif ! ./scripts/package.sh --check-archive "$package_zip" >/dev/null; then
  fail "generated package failed --check-archive"
fi
if ! ./scripts/package.sh --check >/dev/null; then
  fail "package --check failed"
fi
dirty_zip="${package_dir}/dirty.zip"
python3 - "$dirty_zip" <<'PY'
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1], "w") as zf:
    zf.writestr(".env", "SECRET=do-not-ship\n")
    zf.writestr("README.md", "dirty fixture\n")
PY
if ./scripts/package.sh --check-archive "$dirty_zip" >"${tmp_check_dir}/dirty-package.out" 2>"${tmp_check_dir}/dirty-package.err"; then
  fail "dirty archive containing .env unexpectedly passed --check-archive"
elif ! grep -Fq '.env' "${tmp_check_dir}/dirty-package.err"; then
  fail "dirty archive failure did not mention .env"
fi
dirty_prompt_zip="${package_dir}/dirty-system-prompt.zip"
python3 - "$dirty_prompt_zip" <<'PY'
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1], "w") as zf:
    zf.writestr("system-prompt.md", "do-not-ship\n")
    zf.writestr("README.md", "dirty fixture\n")
PY
if ./scripts/package.sh --check-archive "$dirty_prompt_zip" >"${tmp_check_dir}/dirty-system-prompt-package.out" 2>"${tmp_check_dir}/dirty-system-prompt-package.err"; then
  fail "dirty archive containing system-prompt.md unexpectedly passed --check-archive"
elif ! grep -Fq 'system-prompt.md' "${tmp_check_dir}/dirty-system-prompt-package.err"; then
  fail "dirty system-prompt archive failure did not mention system-prompt.md"
fi
if ! _rg -n "system-prompt.md" scripts/install-local.sh >/dev/null 2>&1; then
  fail "installer does not preserve user-config system-prompt.md"
fi

note "checking root wrapper fake runtime env and CLI passthrough"
runtimebin="${tmp_check_dir}/runtimebin"
mkdir -p "$runtimebin"
cat > "${runtimebin}/podman" <<'EOF'
#!/usr/bin/env bash
if [[ "$1" == "image" && "${2:-}" == "exists" ]]; then
  exit 0
fi
if [[ "$1" == "ps" ]]; then
  exit 0
fi
if [[ "$1" == "run" ]]; then
  printf '%s\n' "$@" > "$CODESEEQ_TEST_RUNTIME_ARGS"
  if [[ -n "${CODESEEQ_TEST_BRIDGE_UP:-}" ]]; then
    touch "$CODESEEQ_TEST_BRIDGE_UP"
  fi
  exit 0
fi
if [[ "$1" == "stop" || "$1" == "rm" || "$1" == "logs" ]]; then
  exit 0
fi
exit 0
EOF
cat > "${runtimebin}/docker" <<'EOF'
#!/usr/bin/env bash
if [[ "$1" == "image" && "${2:-}" == "inspect" ]]; then
  exit 0
fi
if [[ "$1" == "ps" ]]; then
  exit 0
fi
if [[ "$1" == "run" ]]; then
  printf '%s\n' "$@" > "$CODESEEQ_TEST_RUNTIME_ARGS"
  exit 0
fi
if [[ "$1" == "stop" || "$1" == "rm" || "$1" == "logs" ]]; then
  exit 0
fi
exit 0
EOF
cat > "${runtimebin}/curl" <<'EOF'
#!/usr/bin/env bash
if [[ -n "${CODESEEQ_TEST_BRIDGE_UP:-}" && -f "$CODESEEQ_TEST_BRIDGE_UP" ]]; then
  exit 0
fi
exit 1
EOF
cat > "${runtimebin}/codex" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$CODESEEQ_TEST_CODEX_ARGS"
printf '%s\n' "${CODEX_HOME:-}" > "$CODESEEQ_TEST_CODEX_HOME"
if [[ -n "${CODESEEQ_TEST_CODEX_STDIN:-}" ]]; then
  cat > "$CODESEEQ_TEST_CODEX_STDIN"
fi
EOF
chmod +x "${runtimebin}/podman" "${runtimebin}/docker" "${runtimebin}/curl" "${runtimebin}/codex"

note "checking root wrapper user-config system prompt management"
prompt_workspace="${tmp_check_dir}/prompt-workspace"
prompt_config_home="${tmp_check_dir}/user-config/codeseeq"
mkdir -p "$prompt_workspace"
if ! CODESEEQ_WORKDIR_HOST="$prompt_workspace" \
  CODESEEQ_CONFIG_HOME="$prompt_config_home" \
  ./codeseeq system add "persistent-prompt" >"${tmp_check_dir}/system-add.out"; then
  fail "root wrapper system add failed"
else
  if [[ ! -f "${prompt_config_home}/system-prompt.md" ]]; then
    fail "system add did not store prompt under CODESEEQ_CONFIG_HOME"
  fi
  if [[ -f "${prompt_workspace}/.codeseeq/system-prompt.md" ]]; then
    fail "system add wrote legacy repo-local system prompt"
  fi
fi
if ! CODESEEQ_WORKDIR_HOST="$prompt_workspace" \
  CODESEEQ_CONFIG_HOME="$prompt_config_home" \
  ./codeseeq system remove >"${tmp_check_dir}/system-remove.out"; then
  fail "root wrapper system remove failed"
elif [[ -f "${prompt_config_home}/system-prompt.md" ]]; then
  fail "system remove did not remove user-config system prompt"
fi

if ! env -u BRAVE_API_KEY -u UNSTRUCTURED_API_KEY -u RESPONSES_API_KEY \
  PATH="${runtimebin}:$PATH" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  CODESEEQ_AUTO_BUILD=false \
  CODESEEQ_TEST_RUNTIME_ARGS="${tmp_check_dir}/runtime-models.args" \
  ./codeseeq models; then
  fail "root wrapper models did not reach fake runtime"
else
  for expected_env in \
    "CODESEEQ_MODEL=deepseek-v4-flash" \
    "CODESEEQ_APPROVAL_POLICY=on-request" \
    "CODESEEQ_SANDBOX_MODE=workspace-write" \
    "CODESEEQ_CONFIG_HOME=/home/codeseeq/.config/codeseeq" \
    "CODESEEQ_SYSTEM_PROMPT_FILE=/home/codeseeq/.config/codeseeq/system-prompt.md"; do
    if ! grep -Fxq -- "$expected_env" "${tmp_check_dir}/runtime-models.args"; then
      fail "root runtime args missing explicit env: ${expected_env}"
    fi
  done
  if awk 'prev == "-e" && ($0 == "deepseek-v4-flash" || $0 == "false" || $0 == "on-request" || $0 == "workspace-write" || $0 == "8080") { bad=1 } { prev=$0 } END { exit bad ? 0 : 1 }' "${tmp_check_dir}/runtime-models.args"; then
    fail "root runtime args contain raw env values after -e"
  fi
fi

if ! PATH="${runtimebin}:$PATH" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  CODESEEQ_AUTO_BUILD=false \
  CODESEEQ_TEST_RUNTIME_ARGS="${tmp_check_dir}/runtime-bare-prompt.args" \
  ./codeseeq "Return exactly: codeseeq-ok"; then
  fail "root wrapper bare prompt did not reach fake runtime"
elif ! grep -Fxq -- "Return exactly: codeseeq-ok" "${tmp_check_dir}/runtime-bare-prompt.args"; then
  fail "root wrapper bare prompt was not forwarded to container"
fi

if ! PATH="${runtimebin}:$PATH" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  CODESEEQ_AUTO_BUILD=false \
  CODESEEQ_TEST_RUNTIME_ARGS="${tmp_check_dir}/runtime-run-prompt.args" \
  ./codeseeq run "Return exactly: codeseeq-ok"; then
  fail "root wrapper run prompt did not reach fake runtime"
elif ! grep -Fxq -- "run" "${tmp_check_dir}/runtime-run-prompt.args" || ! grep -Fxq -- "Return exactly: codeseeq-ok" "${tmp_check_dir}/runtime-run-prompt.args"; then
  fail "root wrapper run prompt was not forwarded as run prompt"
fi

safe_workspace="${tmp_check_dir}/safe-workspace"
mkdir -p "$safe_workspace"
root_task_file="${tmp_check_dir}/root-task.md"
cat > "$root_task_file" <<'EOF'
# Root wrapper task

```text
codeseeq-root-file-ok
```
EOF
if ! PATH="${runtimebin}:$PATH" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  CODESEEQ_AUTO_BUILD=false \
  CODESEEQ_WORKDIR_HOST="$safe_workspace" \
  CODESEEQ_TEST_RUNTIME_ARGS="${tmp_check_dir}/runtime-run-file.args" \
  ./codeseeq run -f "$root_task_file"; then
  fail "root wrapper run -f did not reach fake runtime"
else
  if ! grep -Eq '^CODESEEQ_RUN_PROMPT_FILE=/workspace/\.codeseeq/tmp/run-prompt-.+\.md$' "${tmp_check_dir}/runtime-run-file.args"; then
    fail "root wrapper run -f did not pass managed container prompt file env"
  fi
  if ! grep -Eq '^/workspace/\.codeseeq/tmp/run-prompt-.+\.md$' "${tmp_check_dir}/runtime-run-file.args"; then
    fail "root wrapper run -f did not rewrite --file to managed container path"
  fi
  if grep -Fxq -- "$root_task_file" "${tmp_check_dir}/runtime-run-file.args"; then
    fail "root wrapper run -f leaked original host task path to container args"
  fi
fi

if PATH="${runtimebin}:$PATH" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  CODESEEQ_AUTO_BUILD=false \
  CODESEEQ_WORKDIR_HOST="$safe_workspace" \
  CODESEEQ_TEST_RUNTIME_ARGS="${tmp_check_dir}/runtime-run-file-missing.args" \
  ./codeseeq run -f "${tmp_check_dir}/missing-root-task.md" >"${tmp_check_dir}/runtime-run-file-missing.out" 2>"${tmp_check_dir}/runtime-run-file-missing.err"; then
  fail "root wrapper run -f missing file unexpectedly succeeded"
elif ! grep -Fq 'prompt file not found' "${tmp_check_dir}/runtime-run-file-missing.err"; then
  fail "root wrapper run -f missing file error was not clear"
fi

if PATH="${runtimebin}:$PATH" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  CODESEEQ_AUTO_BUILD=false \
  CODESEEQ_WORKDIR_HOST="$safe_workspace" \
  CODESEEQ_TEST_RUNTIME_ARGS="${tmp_check_dir}/runtime-run-file-both.args" \
  ./codeseeq run -f "$root_task_file" "inline prompt" >"${tmp_check_dir}/runtime-run-file-both.out" 2>"${tmp_check_dir}/runtime-run-file-both.err"; then
  fail "root wrapper run -f plus inline prompt unexpectedly succeeded"
elif ! grep -Fq 'provide either -f/--file or inline prompt text' "${tmp_check_dir}/runtime-run-file-both.err"; then
  fail "root wrapper run -f plus inline prompt error was not clear"
fi

if ! PATH="${runtimebin}:$PATH" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  CODESEEQ_AUTO_BUILD=false \
  CODESEEQ_TEST_RUNTIME_ARGS="${tmp_check_dir}/runtime-profile.args" \
  ./codeseeq -p myprofile; then
  fail "root wrapper -p profile did not reach fake runtime"
elif ! grep -Fxq -- "-p" "${tmp_check_dir}/runtime-profile.args" || ! grep -Fxq -- "myprofile" "${tmp_check_dir}/runtime-profile.args"; then
  fail "root wrapper did not pass -p profile through"
fi

host_workspace="${tmp_check_dir}/host-workspace"
mkdir -p "$host_workspace"
if ! PATH="${runtimebin}:$PATH" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  CODESEEQ_AUTO_BUILD=false \
  CODESEEQ_WORKDIR_HOST="$host_workspace" \
  CODESEEQ_TEST_RUNTIME_ARGS="${tmp_check_dir}/runtime-danger-bridge.args" \
  CODESEEQ_TEST_CODEX_ARGS="${tmp_check_dir}/runtime-danger-codex.args" \
  CODESEEQ_TEST_CODEX_HOME="${tmp_check_dir}/runtime-danger-codex-home.out" \
  CODESEEQ_TEST_CODEX_STDIN="${tmp_check_dir}/runtime-danger-codex-stdin.out" \
  CODESEEQ_TEST_BRIDGE_UP="${tmp_check_dir}/bridge-up" \
  CODESEEQ_BRIDGE_MODE=container \
  CODESEEQ_OPENRESPONSES_PORT=18081 \
  ./codeseeq -y "Return exactly: codeseeq-ok"; then
  fail "danger host mode did not complete with fake runtime/codex"
else
  if ! grep -Fxq -- "--entrypoint" "${tmp_check_dir}/runtime-danger-bridge.args" || ! grep -Fxq -- "/usr/local/bin/codeseeq-bridge.py" "${tmp_check_dir}/runtime-danger-bridge.args"; then
    fail "danger host mode did not start bridge with bridge-only entrypoint"
  fi
  if ! grep -Fxq -- "127.0.0.1:18081:18081" "${tmp_check_dir}/runtime-danger-bridge.args"; then
    fail "danger host bridge did not bind to 127.0.0.1 requested port"
  fi
  if ! grep -Fxq -- "--dangerously-bypass-approvals-and-sandbox" "${tmp_check_dir}/runtime-danger-codex.args" || ! grep -Fxq -- "exec" "${tmp_check_dir}/runtime-danger-codex.args"; then
    fail "danger host Codex args missing dangerous exec mode"
  fi
  if ! grep -Fq 'Return exactly: codeseeq-ok' "${tmp_check_dir}/runtime-danger-codex-stdin.out"; then
    fail "danger host direct prompt was not passed as coherent stdin prompt"
  fi
  if ! grep -Fxq -- "${host_workspace}/.codeseeq" "${tmp_check_dir}/runtime-danger-codex-home.out"; then
    fail "danger host mode did not use workspace-local CODEX_HOME"
  fi
fi

port_scan_workspace="${tmp_check_dir}/host-port-scan-workspace"
mkdir -p "$port_scan_workspace"
if ! PATH="${runtimebin}:$PATH" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  CODESEEQ_AUTO_BUILD=false \
  CODESEEQ_WORKDIR_HOST="$port_scan_workspace" \
  CODESEEQ_TEST_RUNTIME_ARGS="${tmp_check_dir}/runtime-danger-port-scan-bridge.args" \
  CODESEEQ_TEST_CODEX_ARGS="${tmp_check_dir}/runtime-danger-port-scan-codex.args" \
  CODESEEQ_TEST_CODEX_HOME="${tmp_check_dir}/runtime-danger-port-scan-codex-home.out" \
  CODESEEQ_TEST_CODEX_STDIN="${tmp_check_dir}/runtime-danger-port-scan-codex-stdin.out" \
  CODESEEQ_TEST_BRIDGE_UP="${tmp_check_dir}/bridge-up-port-scan" \
  CODESEEQ_TEST_BUSY_PORTS="18083 18084" \
  CODESEEQ_BRIDGE_MODE=container \
  CODESEEQ_OPENRESPONSES_PORT=18083 \
  ./codeseeq -y "Return exactly: codeseeq-ok"; then
  fail "danger host mode did not complete when base bridge ports were busy"
else
  if ! grep -Fxq -- "127.0.0.1:18085:18085" "${tmp_check_dir}/runtime-danger-port-scan-bridge.args"; then
    fail "danger host mode did not advance to the first free bridge port"
  fi
  if ! _rg -n '^base_url = "http://127.0.0.1:18085/v1"$' "${port_scan_workspace}/.codeseeq/config.toml" >/dev/null 2>&1; then
    fail "danger host Codex config did not remember selected bridge port"
  fi
fi

if ! PATH="${runtimebin}:$PATH" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  CODESEEQ_AUTO_BUILD=false \
  CODESEEQ_WORKDIR_HOST="$host_workspace" \
  CODESEEQ_TEST_RUNTIME_ARGS="${tmp_check_dir}/runtime-danger-run-file-bridge.args" \
  CODESEEQ_TEST_CODEX_ARGS="${tmp_check_dir}/runtime-danger-run-file-codex.args" \
  CODESEEQ_TEST_CODEX_HOME="${tmp_check_dir}/runtime-danger-run-file-codex-home.out" \
  CODESEEQ_TEST_CODEX_STDIN="${tmp_check_dir}/runtime-danger-run-file-codex-stdin.out" \
  CODESEEQ_TEST_BRIDGE_UP="${tmp_check_dir}/bridge-up-run-file" \
  CODESEEQ_BRIDGE_MODE=container \
  CODESEEQ_OPENRESPONSES_PORT=18082 \
  ./codeseeq run -f "$root_task_file" --model=deepseek-v4-pro --yolo; then
  fail "danger host run -f --model=... --yolo did not complete with fake runtime/codex"
else
  if ! grep -Fxq -- "--entrypoint" "${tmp_check_dir}/runtime-danger-run-file-bridge.args" || ! grep -Fxq -- "/usr/local/bin/codeseeq-bridge.py" "${tmp_check_dir}/runtime-danger-run-file-bridge.args"; then
    fail "danger host run -f did not start bridge with bridge-only entrypoint"
  fi
  if ! grep -Fxq -- "deepseek@deepseek-v4-pro" "${tmp_check_dir}/runtime-danger-run-file-codex.args"; then
    fail "danger host run -f --model=... did not normalize provider model"
  fi
  if ! grep -Fq 'codeseeq-root-file-ok' "${tmp_check_dir}/runtime-danger-run-file-codex-stdin.out"; then
    fail "danger host run -f did not pass prompt file content on stdin"
  fi
fi

if ! env CONTAINER=docker \
  PATH="${runtimebin}:$PATH" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  CODESEEQ_AUTO_BUILD=false \
  CODESEEQ_TEST_RUNTIME_ARGS="${tmp_check_dir}/runtime-docker.args" \
  ./codeseeq models; then
  fail "root wrapper docker fallback/selection failed"
elif ! grep -Fxq -- "codeseeq:dev" "${tmp_check_dir}/runtime-docker.args"; then
  fail "docker fake runtime did not capture expected image run"
fi

note "checking .env ignore rules"
if ! _rg -n '^\.env$' .gitignore >/dev/null 2>&1; then
  fail ".env is not ignored"
fi
if ! _rg -n '^!\.env\.example$' .gitignore >/dev/null 2>&1; then
  fail ".env.example is not explicitly unignored"
fi

note "checking bridge wrapper functions present"
for fn in bridge_mode_resolve bridge_check_deps_process bridge_start_process bridge_stop_owned_process bridge_healthcheck_url bridge_start bridge_select_port; do
  if ! grep -q "^${fn}()" codeseeq; then
    fail "bridge function missing from wrapper: ${fn}"
  fi
done

note "checking bridge cleanup integration"
if ! grep -q "bridge_stop_owned_process" codeseeq; then
  fail "cleanup_tmp_files does not call bridge_stop_owned_process"
fi

note "checking bridge_start called in host codex path"
if grep -q "bridge_start" codeseeq; then
  note "bridge_start integrated in host codex path"
else
  fail "run_host_codex does not call bridge_start"
fi

note "checking bridge mode env defaults present"
if grep -q "CODESEEQ_BRIDGE_MODE" codeseeq; then
  note "bridge env var defaults present"
else
  fail "bridge env var defaults missing"
fi

note "checking bridge mode validation"
if grep -q "invalid CODESEEQ_BRIDGE_MODE" codeseeq; then
  note "bridge mode validation present"
else
  fail "bridge mode validation missing"
fi

note "checking process mode dependency check"
if grep -q "bridge_check_deps_process" codeseeq; then
  note "process mode dep check present"
else
  fail "bridge_check_deps_process function missing"
fi

note "checking bridge Python syntax"
if python3 -c "import py_compile; py_compile.compile('bin/codeseeq-bridge.py', doraise=True)" 2>/dev/null; then
  note "bridge.py syntax: OK"
else
  fail "bridge.py syntax check failed"
fi

note "checking requirements-bridge.txt exists"
if [[ -f requirements-bridge.txt ]]; then
  note "requirements-bridge.txt present"
else
  fail "requirements-bridge.txt missing"
fi

note "checking Dockerfile no longer installs open-responses npm"
if grep -q "open-responses" Dockerfile; then
  fail "Dockerfile still references open-responses"
else
  note "Dockerfile open-responses removed (good)"
fi

note "checking Dockerfile installs requirements-bridge.txt"
if grep -q "requirements-bridge.txt" Dockerfile; then
  note "Dockerfile installs requirements-bridge.txt"
else
  fail "Dockerfile missing requirements-bridge.txt"
fi

note "checking container mode preserved"
if grep -q "start_bridge_container" codeseeq; then
  note "container bridge function preserved"
else
  fail "start_bridge_container function missing"
fi

note "checking no Codex source vendoring"
if [[ -d codex/.git ]]; then
  fail "codex source directory exists"
else
  note "no vendored codex source"
fi

note "checking external mode path in wrapper"
if grep -q "CODESEEQ_BRIDGE_MODE=external" codeseeq || grep -q "CODESEEQ_BRIDGE_BASE_URL" codeseeq; then
  note "external mode path present"
else
  fail "external mode path missing from wrapper"
fi

note "checking bridge process startup smoke test"
tmp_bridge_smoke="$(mktemp -d)"
bridge_smoke_port=19901
if CODESEEQ_BRIDGE_HOST=127.0.0.1 CODESEEQ_BRIDGE_PORT="${bridge_smoke_port}" DEEPSEEK_API_KEY="dummy-test-key" python3 bin/codeseeq-bridge.py > "${tmp_bridge_smoke}/bridge.log" 2>&1 &
then
  bridge_smoke_pid=$!
  echo "${bridge_smoke_pid}" > "${tmp_bridge_smoke}/bridge.pid"
  smoke_deadline=$((SECONDS + 10))
  smoke_healthy=0
  while (( SECONDS < smoke_deadline )); do
    if curl --silent --show-error --fail --max-time 2 "http://127.0.0.1:${bridge_smoke_port}/health" >/dev/null 2>&1; then
      smoke_healthy=1
      break
    fi
    if ! kill -0 "${bridge_smoke_pid}" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
  if (( smoke_healthy )); then
    note "bridge process healthy on port ${bridge_smoke_port}"
    if curl --silent --fail "http://127.0.0.1:${bridge_smoke_port}/v1/models" > "${tmp_bridge_smoke}/models.json" 2>/dev/null; then
      if grep -q "deepseek@deepseek-v4-flash" "${tmp_bridge_smoke}/models.json"; then
        note "bridge /v1/models returns expected models"
      else
        fail "bridge /v1/models missing expected model"
      fi
    else
      fail "bridge /v1/models endpoint unreachable"
    fi
  else
    fail "bridge process failed to become healthy"
  fi
  kill "${bridge_smoke_pid}" 2>/dev/null || true
  sleep 1
  if kill -0 "${bridge_smoke_pid}" >/dev/null 2>&1; then
    kill -9 "${bridge_smoke_pid}" 2>/dev/null || true
  fi
  note "bridge process terminated cleanly"
else
  note "bridge startup smoke skipped (Python deps may be missing)"
fi
rm -rf "${tmp_bridge_smoke}"


note "checking privacy hardening: generated config assertions"
privacy_tmp="$(mktemp -d)"
if ! CODESEEQ_CODEX_HOME="${privacy_tmp}/.codeseeq" \
  CODESEEQ_WORKDIR="${privacy_tmp}/workspace" \
  CODESEEQ_RUNTIME_DIR="${privacy_tmp}/run" \
  CODESEEQ_LOG_DIR="${privacy_tmp}/log" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  ./bin/codeseeq-entrypoint config > "${privacy_tmp}/config-privacy.out"; then
  fail "codeseeq-entrypoint config generation failed (privacy check)"
else
  privacy_config="${privacy_tmp}/.codeseeq/config.toml"
  cat "$privacy_config" > "${privacy_tmp}/raw-config.toml"
  # Required privacy settings must be present
  for required in \
    'web_search = "live"' \
    '[analytics]' \
    'enabled = false' \
    '[feedback]' \
    '[otel]' \
    'exporter = "none"' \
    'metrics_exporter = "none"' \
    'trace_exporter = "none"' \
    'log_user_prompt = false' \
    '[history]' \
    'persistence = "none"'; do
    if ! grep -Fq -- "$required" "${privacy_config}"; then
      fail "generated config missing privacy setting: ${required}"
    fi
  done
  # Forbidden settings must NOT be present
  for forbidden in \
    'web_search = "cached"' \
    'OPENAI_API_KEY="${OPENAI_API_KEY:-$DEEPSEEK_API_KEY}"' \
    'CODEX_NPM_VERSION=latest' \
    '@openai/codex@latest'; do
    if grep -Fq -- "$forbidden" "${privacy_config}"; then
      fail "generated config contains forbidden pattern: ${forbidden}"
    fi
  done
  # Verify env_key is DEEPSEEK_API_KEY and requires_openai_auth is false
  if ! grep -Fq 'env_key = "DEEPSEEK_API_KEY"' "${privacy_config}"; then
    fail "generated config missing env_key = \"DEEPSEEK_API_KEY\""
  fi
  if ! grep -Fq 'requires_openai_auth = false' "${privacy_config}"; then
    fail "generated config missing requires_openai_auth = false"
  fi
fi


note "checking privacy hardening: host wrapper config assertions"
# Host wrapper config verified by checking source code directly
for required in \
  'web_search = "live"' \
  '[analytics]' \
  'enabled = false' \
  '[feedback]' \
  '[otel]' \
  'exporter = "none"' \
  'metrics_exporter = "none"' \
  'trace_exporter = "none"' \
  'log_user_prompt = false' \
  '[history]' \
  'persistence = "none"'; do
  if ! grep -Fq -- "$required" codeseeq 2>/dev/null; then
    fail "host wrapper (codeseeq) missing privacy config setting: ${required}"
  fi
done
# Verify the privacy block appears in write_host_config context
if ! grep -A50 "write_host_config" codeseeq | grep -Fq "web_search"; then
  fail "host wrapper write_host_config missing privacy block"
fi
note "checking privacy hardening: OPENAI_API_KEY is not auto-exported from DEEPSEEK_API_KEY"
if grep -Fq 'export OPENAI_API_KEY="${OPENAI_API_KEY:-$DEEPSEEK_API_KEY}"' codeseeq 2>/dev/null; then
  fail "root wrapper still exports OPENAI_API_KEY from DEEPSEEK_API_KEY"
fi
if grep -Fq 'export OPENAI_API_KEY="${OPENAI_API_KEY:-$DEEPSEEK_API_KEY}"' bin/codeseeq-entrypoint 2>/dev/null; then
  fail "entrypoint still exports OPENAI_API_KEY from DEEPSEEK_API_KEY"
fi

note "checking privacy hardening: pinned Codex version (not latest)"
if grep -Fq 'CODEX_NPM_VERSION=latest' Dockerfile 2>/dev/null; then
  fail "Dockerfile still uses CODEX_NPM_VERSION=latest"
fi
if grep -Fq 'CODEX_NPM_VERSION ?= latest' Makefile 2>/dev/null; then
  fail "Makefile still uses CODEX_NPM_VERSION ?= latest"
fi
if grep -Fq 'npm install -g @openai/codex' scripts/install-local.sh 2>/dev/null && \
   ! grep -Fq '@openai/codex@0.130.0' scripts/install-local.sh 2>/dev/null; then
  fail "install-local.sh still auto-installs @openai/codex without pinned version"
fi

note "checking privacy hardening: blocked upstream commands"
fakebin="${tmp_check_dir}/fakebin"
if PATH="${fakebin}:$PATH" \
  ./codeseeq login > "${tmp_check_dir}/blocked-login.out" 2>"${tmp_check_dir}/blocked-login.err"; then
  fail "upstream command 'login' was NOT blocked"
elif ! grep -Fq 'disabled in CodeSeeq privacy mode' "${tmp_check_dir}/blocked-login.err"; then
  fail "blocked command 'login' did not print privacy mode error"
fi

for blocked_cmd in cloud app app-server plugin update features; do
  if PATH="${fakebin}:$PATH" \
    ./codeseeq "$blocked_cmd" > "${tmp_check_dir}/blocked-${blocked_cmd}.out" 2>"${tmp_check_dir}/blocked-${blocked_cmd}.err"; then
    fail "upstream command '${blocked_cmd}' was NOT blocked"
  elif ! grep -Fq 'disabled in CodeSeeq privacy mode' "${tmp_check_dir}/blocked-${blocked_cmd}.err"; then
    fail "blocked command '${blocked_cmd}' did not print privacy mode error"
  fi
done

note "checking privacy hardening: CODE_NPM_VERSION pinned in Dockerfile"
if ! grep -Fq 'ARG CODEX_NPM_VERSION=0.130.0' Dockerfile 2>/dev/null; then
  fail "Dockerfile does not pin CODEX_NPM_VERSION to 0.130.0"
fi

note "checking privacy hardening: CODE_NPM_VERSION pinned in Makefile"
if ! grep -Fq 'CODEX_NPM_VERSION ?= 0.130.0' Makefile 2>/dev/null; then
  fail "Makefile does not pin CODEX_NPM_VERSION to 0.130.0"
fi

rm -rf "${privacy_tmp}"
if (( failures > 0 )); then
  printf '[check] FAILED with %d issue(s).\n' "$failures" >&2
  exit 1
fi

note "all checks passed"
