#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
cd "$repo_root"

failures=0

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
  docs/architecture.md \
  docs/security.md \
  docs/troubleshooting.md \
  codeseeq-current-state.md \
  codeseeq-desired-state.md \
  codeseeq-blueprint.md \
  codeseeq-deep-research.md \
  codeseeq-bridge-report.md \
  codeseeq-bridge.report.md; do
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

note "checking default danger-full-access launch args"
if ! PATH="${fakebin}:$PATH" \
  CODESEEQ_TEST_CODEX_ARGS="${tmp_check_dir}/codex-default-danger-args.out" \
  CODESEEQ_CODEX_HOME="${tmp_check_dir}/.codeseeq-default-danger-launch" \
  CODESEEQ_WORKDIR="${tmp_check_dir}/workspace-default-danger-launch" \
  CODESEEQ_RUNTIME_DIR="${tmp_check_dir}/run-default-danger-launch" \
  CODESEEQ_LOG_DIR="${tmp_check_dir}/log-default-danger-launch" \
  CODESEEQ_APPROVAL_POLICY="never" \
  CODESEEQ_SANDBOX_MODE="danger-full-access" \
  DEEPSEEK_API_KEY="dummy-test-key" \
  ./bin/codeseeq-entrypoint "Return exactly: danger-test"; then
  fail "codeseeq-entrypoint default danger-full-access launch failed"
else
  if ! grep -Fxq -- "--dangerously-bypass-approvals-and-sandbox" "${tmp_check_dir}/codex-default-danger-args.out"; then
    fail "default danger-full-access launch args missing bypass flag"
  fi
  if grep -Fxq -- "--ask-for-approval" "${tmp_check_dir}/codex-default-danger-args.out"; then
    fail "default danger-full-access launch args included conflicting --ask-for-approval"
  fi
fi

note "checking .env ignore rules"
if ! rg -n '^\.env$' .gitignore >/dev/null 2>&1; then
  fail ".env is not ignored"
fi

if (( failures > 0 )); then
  printf '[check] FAILED with %d issue(s).\n' "$failures" >&2
  exit 1
fi

note "all checks passed"
