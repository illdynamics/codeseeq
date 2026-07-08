8s
Run ./scripts/check.sh
[check] running bash -n on shell scripts
bin/codeseeq-bridge.py: line 43: syntax error near unexpected token `('
bin/codeseeq-bridge.py: line 43: `app = FastAPI()'
[check:error] bash syntax failed: bin/codeseeq-bridge.py
bin/__pycache__/codeseeq-bridge.cpython-312.pyc: line 14: syntax error near unexpected token `.'
bin/__pycache__/codeseeq-bridge.cpython-312.pyc: line 14: `  (added -> function_call_arguments.delta -> .done -> output_item.done).'
[check:error] bash syntax failed: bin/__pycache__/codeseeq-bridge.cpython-312.pyc
scripts/test-bridge-extraction.py: line 13: syntax error near unexpected token `('
scripts/test-bridge-extraction.py: line 13: `def install_import_stubs() -> None:'
[check:error] bash syntax failed: scripts/test-bridge-extraction.py
scripts/test-bridge-translation.py: line 5: syntax error near unexpected token `('
scripts/test-bridge-translation.py: line 5: `class FakeDS(http.server.BaseHTTPRequestHandler):'
[check:error] bash syntax failed: scripts/test-bridge-translation.py
[check] running shellcheck

In codeseeq line 100:
    [[ -n "$f" ]] && rm -f -- "$f" 2>/dev/null || true
                  ^-- SC2015 (info): Note that A && B || C is not if-then-else. C may run when A is true.


In codeseeq line 1219:
  CODESEEQ_RUN_PROMPT_FILE="$container_prompt"
  ^----------------------^ SC2034 (warning): CODESEEQ_RUN_PROMPT_FILE appears unused. Verify use (or export if used externally).


In codeseeq line 1333:
  local raw=("$@")
        ^-^ SC2034 (warning): raw appears unused. Verify use (or export if used externally).


In codeseeq line 1489:
  local log_prefix="[codeseeq-install]"
        ^--------^ SC2034 (warning): log_prefix appears unused. Verify use (or export if used externally).

[check] shellcheck completed (warnings are non-fatal)
[check] checking executable permissions
[check] checking required smoke scripts
[check] checking single-container-only requirements
[check] checking .codeseeq isolation defaults
[check] checking model catalog
OK
[check] checking version documentation
[check] checking CI build metadata
[check] checking bridge XML/tool-call extraction
[codeseeq-bridge] permissive xml tool name remapped: 'exec_command' -> 'shell'
[codeseeq-bridge] dsml tool name remapped: 'bash' -> 'shell'
[test-bridge-extraction] PASS
[check] checking generated config
[codeseeq] workspace /tmp/tmp.gvHaBQtwSY/workspace is writable
[check] checking system prompt injection config
[codeseeq] workspace /tmp/tmp.gvHaBQtwSY/workspace-system is writable
[check] checking --yolo config leaves policy config unchanged
[codeseeq] workspace /tmp/tmp.gvHaBQtwSY/workspace-yolo is writable
[check] checking --yolo launch args
[codeseeq] workspace /tmp/tmp.gvHaBQtwSY/workspace-yolo-launch is writable
[check] checking default safe launch args
[codeseeq] workspace /tmp/tmp.gvHaBQtwSY/workspace-default-safe-launch is writable
[check] checking run -f parser and prompt-file stdin
[codeseeq] workspace /tmp/tmp.gvHaBQtwSY/workspace-run-file is writable
[codeseeq] workspace /tmp/tmp.gvHaBQtwSY/workspace-run-file-equals is writable
[check] checking system prompt applies with run -f
[codeseeq] workspace /tmp/tmp.gvHaBQtwSY/workspace-run-file-system is writable
[check] checking system prompt applies with bare direct prompt
[codeseeq] workspace /tmp/tmp.gvHaBQtwSY/workspace-bare-system is writable
[check] checking bare direct prompt execution
[codeseeq] workspace /tmp/tmp.gvHaBQtwSY/workspace-bare-prompt is writable
[check] checking -p profile passthrough
[codeseeq] workspace /tmp/tmp.gvHaBQtwSY/workspace-profile is writable
[check] checking -p profile with prompt
[codeseeq] workspace /tmp/tmp.gvHaBQtwSY/workspace-profile-prompt is writable
[check] checking --prompt is not a direct prompt alias
[codeseeq] workspace /tmp/tmp.gvHaBQtwSY/workspace-prompt-flag is writable
[check] checking package archive validation
[check] checking root wrapper fake runtime env and CLI passthrough
[check] checking root wrapper user-config system prompt management
[codeseeq] runtime_mode=auto cmd_arg=Return exactly: codeseeq-ok
[codeseeq] auto: container runtime available, will use container
[codeseeq] runtime_mode=auto cmd_arg=run
[codeseeq] auto: container runtime available, will use container
[codeseeq] runtime_mode=auto cmd_arg=run
[codeseeq] auto: container runtime available, will use container
[codeseeq] runtime_mode=auto cmd_arg=
[codeseeq] auto: container runtime available, will use container
[codeseeq] runtime_mode=auto cmd_arg=-y
[codeseeq] bridge mode: container
[codeseeq] starting bridge container codeseeq-bridge-18081-3090 with podman on http://127.0.0.1:18081/v1
[codeseeq] host mode: running local Codex with bridge at http://127.0.0.1:18081/v1
[codeseeq] runtime_mode=auto cmd_arg=-y
[codeseeq] bridge mode: container
[codeseeq] starting bridge container codeseeq-bridge-18085-3145 with podman on http://127.0.0.1:18085/v1
[codeseeq] host mode: running local Codex with bridge at http://127.0.0.1:18085/v1
[codeseeq] runtime_mode=auto cmd_arg=run
[codeseeq] bridge mode: container
[codeseeq] starting bridge container codeseeq-bridge-18082-3193 with podman on http://127.0.0.1:18082/v1
[codeseeq] host mode: running local Codex with bridge at http://127.0.0.1:18082/v1
[check] checking .env ignore rules
[check] checking bridge wrapper functions present
[check] checking bridge cleanup integration
[check] checking bridge_start called in host codex path
[check] bridge_start integrated in host codex path
[check] checking bridge mode env defaults present
[check] bridge env var defaults present
[check] checking bridge mode validation
[check] bridge mode validation present
[check] checking process mode dependency check
[check] process mode dep check present
[check] checking bridge Python syntax
[check] bridge.py syntax: OK
[check] checking requirements-bridge.txt exists
[check] requirements-bridge.txt present
[check] checking Dockerfile no longer installs open-responses npm
[check] Dockerfile open-responses removed (good)
[check] checking Dockerfile installs requirements-bridge.txt
[check] Dockerfile installs requirements-bridge.txt
[check] checking container mode preserved
[check] container bridge function preserved
[check] checking no Codex source vendoring
[check] no vendored codex source
[check] checking external mode path in wrapper
[check] external mode path present
[check] checking bridge process startup smoke test
[check] bridge process healthy on port 19901
[check] bridge /v1/models returns expected models
[check] bridge process terminated cleanly
[check] checking privacy hardening: generated config assertions
[codeseeq] workspace /tmp/tmp.Bm9PFZTX76/workspace is writable
[check] checking privacy hardening: host wrapper config assertions
[check] checking privacy hardening: OPENAI_API_KEY is not auto-exported from DEEPSEEK_API_KEY
[check] checking privacy hardening: pinned Codex version (not latest)
[check] checking privacy hardening: blocked upstream commands
[check] checking privacy hardening: CODE_NPM_VERSION pinned in Dockerfile
[check] checking privacy hardening: CODE_NPM_VERSION pinned in Makefile
[check] FAILED with 4 issue(s).
Error: Process completed with exit code 1.

fix this error, then run exactly this:
git add . ; git commit -am "fixes" ; git tag v0.3.5 ; git push origin v0.3.5
