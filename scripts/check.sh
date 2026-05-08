#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
cd "$repo_root"

failures=0

_rg() {
  if command -v rg >/dev/null 2>&1; then
    rg "$@"
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
done < <(rg --files codeseeq bin scripts | while IFS= read -r f; do
  if [[ -f "$f" ]] && head -n 1 "$f" | rg -q 'bash'; then
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
  if ! shellcheck "${shell_files[@]}"; then
    fail "shellcheck reported issues"
  fi
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
  scripts/smoke-openresponses-container.sh
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
if ! rg -n 'CODESEEQ_CODEX_HOME=/home/codeseeq/.codeseeq' Dockerfile >/dev/null 2>&1; then
  fail "Dockerfile missing CODESEEQ_CODEX_HOME=/home/codeseeq/.codeseeq"
fi
if ! rg -n ': "\$\{CODESEEQ_CODEX_HOME:=/home/codeseeq/.codeseeq\}"' bin/codeseeq-entrypoint >/dev/null 2>&1; then
  fail "entrypoint missing .codeseeq default"
fi

note "checking model catalog"
if ! jq -e '.default == "deepseek-v4-flash" and (.models | length == 4)' config/model-catalog.json >/dev/null 2>&1; then
  fail "config/model-catalog.json does not match expected 4-model layout"
fi

note "checking version documentation"
version="$(cat VERSION)"
for f in \
  README.md \
  quickstart.md \
  docs/ARCHITECTURE.md \
  docs/SECURITY.md \
  docs/TROUBLESHOOTING.md \
  codeseeq-current-state.md \
  codeseeq-desired-state.md \
  codeseeq-blueprint.md \
  codeseeq-deep-research.md \
  codeseeq-bridge-report.md \
  codeseeq-bridge.report.md; do
  [[ -f "$f" ]] || continue
  if ! rg -n "${version}" "$f" >/dev/null 2>&1; then
    fail "version ${version} missing from $f"
  fi
done

note "checking CI build metadata"
if rg -n 'OPENRESPONSES_IMAGE|--build-arg OPENRESPONSES_IMAGE' .drone.yml >/dev/null 2>&1; then
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
  if ! rg -n '^CODEX_HOME=.*/\.codeseeq$' "${tmp_check_dir}/config.out" >/dev/null 2>&1; then
    fail "config output missing CODEX_HOME=.codeseeq"
  fi
  if ! rg -n '^model_provider = "codeseeq"$' "${tmp_check_dir}/config.out" >/dev/null 2>&1; then
    fail "generated config missing model_provider=codeseeq"
  fi
  if ! rg -n '^wire_api = "responses"$' "${tmp_check_dir}/config.out" >/dev/null 2>&1; then
    fail "generated config missing wire_api=responses"
  fi
  if ! rg -n '^base_url = "http://127.0.0.1:8080/v1"$' "${tmp_check_dir}/config.out" >/dev/null 2>&1; then
    fail "generated config missing expected base_url"
  fi
  if ! rg -n '^approval_policy = "on-request"$' "${tmp_check_dir}/config.out" >/dev/null 2>&1; then
    fail "generated config missing safe approval default"
  fi
  if ! rg -n '^sandbox_mode = "workspace-write"$' "${tmp_check_dir}/config.out" >/dev/null 2>&1; then
    fail "generated config missing safe sandbox default"
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
  if ! rg -n '^developer_instructions = ' "$generated_system_config" >/dev/null 2>&1; then
    fail "generated config missing developer_instructions for persistent system prompt"
  fi
  if rg -n 'SYSTEM-PROMPT-ACTIVE' "${tmp_check_dir}/config-system.out" >/dev/null 2>&1; then
    fail "config output leaked full system prompt content"
  fi
  if ! rg -n '^System prompt injection=codex-config-developer_instructions$' "${tmp_check_dir}/config-system.out" >/dev/null 2>&1; then
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
  if ! rg -n '^approval_policy = "on-request"$' "${tmp_check_dir}/config-yolo.out" >/dev/null 2>&1; then
    fail "--yolo config unexpectedly changed approval_policy"
  fi
  if ! rg -n '^sandbox_mode = "read-only"$' "${tmp_check_dir}/config-yolo.out" >/dev/null 2>&1; then
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
  if ! rg -n '^developer_instructions = ' "${tmp_check_dir}/.codeseeq-run-file-system/config.toml" >/dev/null 2>&1; then
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
  if ! rg -n '^developer_instructions = ' "${tmp_check_dir}/.codeseeq-bare-system/config.toml" >/dev/null 2>&1; then
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
    "CODESEEQ_SYSTEM_PROMPT_FILE=/workspace/.codeseeq/system-prompt.md"; do
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

if ! PATH="${runtimebin}:$PATH" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  CODESEEQ_AUTO_BUILD=false \
  CODESEEQ_WORKDIR_HOST="$host_workspace" \
  CODESEEQ_TEST_RUNTIME_ARGS="${tmp_check_dir}/runtime-danger-run-file-bridge.args" \
  CODESEEQ_TEST_CODEX_ARGS="${tmp_check_dir}/runtime-danger-run-file-codex.args" \
  CODESEEQ_TEST_CODEX_HOME="${tmp_check_dir}/runtime-danger-run-file-codex-home.out" \
  CODESEEQ_TEST_CODEX_STDIN="${tmp_check_dir}/runtime-danger-run-file-codex-stdin.out" \
  CODESEEQ_TEST_BRIDGE_UP="${tmp_check_dir}/bridge-up-run-file" \
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
if ! rg -n '^\.env$' .gitignore >/dev/null 2>&1; then
  fail ".env is not ignored"
fi
if ! rg -n '^!\.env\.example$' .gitignore >/dev/null 2>&1; then
  fail ".env.example is not explicitly unignored"
fi

if (( failures > 0 )); then
  printf '[check] FAILED with %d issue(s).\n' "$failures" >&2
  exit 1
fi

note "all checks passed"
