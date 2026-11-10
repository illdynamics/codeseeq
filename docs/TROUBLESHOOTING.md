# Troubleshooting

Current version: `v0.7.3`

## `./codeseeq` is not executable

```bash
chmod +x ./codeseeq
```

## No Container Runtime Found

Install Podman preferred, or Docker as a compatible fallback.

Selection order:

1. `CONTAINER`, if set.
2. `podman`
3. `docker`

Explicit override:

```bash
CONTAINER=podman ./codeseeq doctor
CONTAINER=docker ./codeseeq doctor
```

Docker Compose is not supported.

## Workspace Permission Warning (container runtime)

`codeseeq doctor` (or any container run) may print:

```text
[codeseeq:warn] workspace /workspace is not writable by 10001. host volume mapping might have permission issues.
[codeseeq:warn] write test failed in /workspace
```

The container runs as UID 10001 while the bind-mounted host workspace is owned
by your host user, so the container process cannot write files. CodeSeeq aligns
the container user with the host user automatically:

- **podman**: `--userns=keep-id` is the default. Disable/override with
  `CODESEEQ_PODMAN_USERNS` (empty value disables the mapping).
- **docker**: `--user <host-uid>:<host-gid>` is the default for non-root hosts
  (writable state dirs are relocated under the workspace). Disable/override
  with `CODESEEQ_DOCKER_USER`.

Manual fallbacks if you disabled the mapping or use a custom runtime:

```bash
sudo chown -R "$(id -u):$(id -g)" /path/to/workspace
# or
chmod -R u+rwX /path/to/workspace
```

## System Prompt Missing

`codeseeq doctor` reports `System prompt: missing` when no
`~/.config/codeseeq/system-prompt.md` exists. A bundled default
(`config/default-system-prompt.md`) is now seeded automatically on
`codeseeq install` and used as a fallback in both host and container runtime,
so `doctor` reports `present` and Codex always receives
`developer_instructions`. Set your own prompt with:

```bash
codeseeq system add "You are terse and practical."
codeseeq system view
codeseeq system remove
```

## Docker Fallback Surprises

If Docker is selected, CodeSeeq omits the Podman `:Z` bind-mount suffix. Override the mount suffix only when you know your runtime supports it:

```bash
CODESEEQ_VOLUME_SUFFIX=:Z CONTAINER=docker ./codeseeq run "say hi"
```

## Image Missing

The wrapper auto-builds by default. Manual build:

```bash
./codeseeq build
make build
make CONTAINER=docker build
```

Disable auto-build only when you want a hard failure:

```bash
CODESEEQ_AUTO_BUILD=false ./codeseeq models
```

## `codeseeq build` Looks for the Dockerfile in `~/.config/codeseeq`

If `codeseeq build` fails with something like:

```
Error: no Containerfile or Dockerfile specified or found in context directory,
~/.config/codeseeq: no such file or directory
```

…and `codeseeq install` fails with:

```
[codeseeq:error] CODESEEQ_INSTALL_DIR cannot be inside source repo: ~/.config/codeseeq
```

…then a stale `CODESEEQ_INSTALL_ROOT` in your environment is pointing the
wrapper at the *config/install* directory instead of the repo checkout you are
running from. The build context and the install source are derived from the
directory the running `codeseeq` script lives in (`SCRIPT_DIR`), never from an
overridable `CODESEEQ_INSTALL_ROOT`, so unset/remove that variable (or any
`CODESEEQ_BUILD_CONTEXT` pointing at the config dir):

```bash
unset CODESEEQ_INSTALL_ROOT CODESEEQ_BUILD_CONTEXT
./codeseeq build
./codeseeq install
```

`CODESEEQ_BUILD_CONTEXT` is still honored when you deliberately want to build
from a different checkout. `codeseeq install` copies the repo that the running
script came from (repo checkout or release zip) into `~/.config/codeseeq` and
writes the `~/bin/codeseeq` launcher; running `install` again from an
installed copy is refused to avoid copying the snapshot into itself.

## `.env` Handling

Load `.env` before live tests without modifying it:

```bash
set -a
source .env
set +a
```

Do not run formatters or rewrite scripts against `.env`.

## Package Checker Catches `.env`

Release archives must be produced by the package script and validated before upload:

```bash
./scripts/package.sh --check
./scripts/package.sh --check-archive /mnt/data/codeseeq.zip
```

If the checker reports `.env`, `.git/`, `.codeseeq/`, `__MACOSX/`, `.DS_Store`, nested `*.zip`, `workspace/`, `logs/`, or `__pycache__/`, discard the archive and rebuild it with `./scripts/package.sh`, `./codeseeq package`, or `make package`. Do not use Finder/macOS manual zips.

## `DEEPSEEK_API_KEY` Missing

Prompt execution and pings require it:

```bash
./codeseeq ping
./codeseeq run "Return exactly: codeseeq-ok"
```

## Host Path vs `/workspace`

Safe/container mode prints:

```text
CodeSeeq workspace:
  host: /path/to/project
  container: /workspace
```

This is expected. Codex writes to `/workspace` inside the container, which is your mounted host checkout. The banner goes to stderr so direct prompt stdout remains parseable.

Disable it:

```bash
CODESEEQ_WORKSPACE_BANNER=false ./codeseeq run "say hi"
```

## Default Mode Is Not Yolo

Default generated config should show:

```toml
approval_policy = "on-request"
sandbox_mode = "workspace-write"
```

Verify:

```bash
./codeseeq config
```

## Danger Host Mode Did Not Start

These forms should trigger host Codex mode:

```bash
./codeseeq -y "say hi"
./codeseeq --yolo "say hi"
./codeseeq --dangerously-bypass-approvals-and-sandbox "say hi"
./codeseeq --sandbox danger-full-access "say hi"
```

In that mode CodeSeeq starts a bridge (process or container), then runs local host `codex` with `CODEX_HOME=$PWD/.codeseeq`.

## Local Model Asks For Permission Instead Of Running The Command

Symptoms: with `sandbox_mode = "danger-full-access"` and
`approval_policy = "never"` the model stops and says something like
"request_user_input is unavailable in Default mode" / "I cannot execute
privileged actions", and offers to retry SSH or `sudo` only with approval.
Another model (for example Apex) running the same command works.

This is model behavior amplified by the bridge, not a missing permission:

- **`request_user_input` is a Plan-mode-only tool.** Codex v0.154+ rejects
  it in Default mode with
  `request_user_input is unavailable in Default mode`. Models that reach for
  it to ask "should I proceed?" therefore dead-end.
- **`approval_policy = "never"` forbids escalation.** In never mode Codex
  rejects `sandbox_permissions: "require_escalated"` with
  `you cannot request additional permissions unless the approval policy is
  OnRequest`, even under `danger-full-access`. Approvals disabled means "run
  it or fail"; it does not mean "auto-approve an escalation request".
- **Steering matters more than capabilities.** Some models (Apex-class) just
  run the command; others (small local LFM/GGUF models) interpret "elevated
  privileges" as needing an approval dialog.

Since v0.7.2 the bridge:

1. Adds an explicit permission-model rule to the tool-steering system prompt:
   never ask for approval via `request_user_input` or plain text, never use
   `sandbox_permissions` escalation under `never`, run `exec_command` as-is
   and report exact errors.
2. Blocks any `request_user_input` call the model still emits before it
   reaches Codex, and returns actionable feedback instead of the fatal
   mode error (set `CODESEEQ_BRIDGE_BLOCK_DEFAULT_MODE_INPUT=false` to
   restore the old forward-and-let-Codex-reject behavior).

What to check if a local model still refuses:

```bash
./codeseeq --print-config | grep -E 'approval_policy|sandbox_mode'
./codeseeq --yolo "ssh root@10.20.30.16 whoami"
```

- Prefer `--yolo` (danger host mode) when you actually need host-level SSH,
  `sudo`, or `-o StrictHostKeyChecking` style commands; container runtimes
  can still isolate networking/keys.
- Verify the SSH failure itself (`Permission denied (publickey)`) is real:
  `ssh -vvv -i ~/.ssh/your_key root@host` — a wrong key fails for every model.
- If a model keeps refusing, it is not a sandbox problem: run it with more
  explicit instructions or switch models. Apex-class models follow the
  permission rule more reliably than small LFM/GGUF models.

## Local Model Emits An XML Tool Block As Text

Some local/GGUF models do not emit a native `tool_calls` field. Instead they
print a DSML block, sometimes with an extra wrapper:

```xml
<tool_calls>
  <tool_calls>
    <exec_command><cmd>ssh root@host whoami</cmd></exec_command>
  </tool_calls>
</tool_calls>
```

Before v0.7.3 the bridge's block matcher consumed the outer wrapper and found
zero calls, so CodeSeeQ rendered the XML as the answer and stopped. That looked
like the model "claiming" it installed or opened something when nothing ran.

Since v0.7.3 the bridge:

1. extracts tool calls from nested `<tool_calls>` / `<function_calls>`
   wrappers (including one wrapper per call);
2. decodes entity-escaped (`&lt;tool_calls&gt;`) and unicode-escaped
   (`\u003ctool_calls\u003e`) DSML before extraction, in streaming and
   non-streaming responses;
3. unwraps structured arguments of the form
   `{"result": "<tool_calls>..."}` into the inner executable call.

If output still looks text-only, check the bridge log for
`parsed dsml tool calls` / `streaming dsml tool call`, or run:

```bash
scripts/test-bridge-extraction.py
```

A real SSH failure (`Permission denied (publickey)`) is separate: fix the key
path/agent first, then retry.

## Local Codex Missing In Danger Mode

Install Codex locally:

```bash
npm install -g @openai/codex@0.130.0
```

CodeSeeq will not silently fall back to container Codex for danger-full-access, because that would not be host full access.

## Bridge Port Conflict

Host mode starts a bridge for each CodeSeeq invocation. When
`CODESEEQ_BRIDGE_PORT` is unset, the bridge performs a **real bind** starting at
`CODESEEQ_OPENRESPONSES_PORT` (default `8080`) and increments up to
`CODESEEQ_OPENRESPONSES_PORT_SCAN_LIMIT`, writing the chosen port to
`CODESEEQ_BRIDGE_PORT_FILE`. This is bind-based (not a connect probe), so
parallel invocations no longer race for a specific port.

Pin an exact port:

```bash
CODESEEQ_BRIDGE_PORT=18080 ./codeseeq -y "say hi"
```

Change the auto-select starting port:

```bash
CODESEEQ_OPENRESPONSES_PORT=18080 ./codeseeq -y "say hi"
```

If startup fails with `no free bridge port found in range 8080-8179`, every
port in the auto-select range is already taken by a **leaked bridge process**
or a **leaked bridge container**. This happens when a `codeseeq` owner was
killed hard (SIGKILL / pipeline timeout, e.g. an agent-call teardown) before
its EXIT trap could stop the bridge; the orphaned bridge keeps its port
forever. Since v0.4.1 the process bridge self-terminates when its parent
disappears, and CodeSeeq reaps stale orphaned bridge processes on startup;
since v0.4.2 it also reaps orphaned standalone bridge containers
(`codeseeq-bridge-<port>-<owner-pid>` whose owner PID is gone) before picking
a new port, so the range recovers automatically. Reaping is disabled entirely
by `CODESEEQ_KEEP_BRIDGE_CONTAINER=true` (for users who intentionally keep a
bridge for external-mode reuse). The reaper derives its own PID portably
(`$BASHPID` when available, otherwise the parent PID of a child `sh`), so it
also runs correctly on macOS bash 3.2 where `$BASHPID` is unset. To reclaim
ports from older versions
immediately:

```bash
pkill -f codeseeq-bridge.py   # stop all leftover bridge processes
podman ps --filter name=codeseeq-bridge -q | xargs -r podman rm -f   # leftover containers
ss -tlnp | grep -E ':80[0-9]{2}|:81[0-7][0-9]'   # verify ports are free
```

If startup fails, inspect the bridge container name printed in the startup log,
or list bridge containers:

```bash
podman ps --filter name=codeseeq-bridge
docker ps --filter name=codeseeq-bridge
```

For process mode, check the bridge log:

```bash
cat ~/.config/codeseeq/log/bridge.log
```

## Codex Asks For OpenAI Login

Inspect config:

```bash
./codeseeq config
```

Expected provider fields (bridge providers - the default):

- `model_provider = "codeseeq"`
- `wire_api = "responses"`
- `env_key = "DEEPSEEK_API_KEY"`
- `requires_openai_auth = false`

Also verify `CODEX_HOME` is `.codeseeq`, not `~/.codex`.

If you intentionally want to use your **ChatGPT Plus / Pro / Team account**
(no API key), select the `chatgpt` provider instead:

```bash
codeseeq config        # provider: ChatGPT (Plus/Pro/Team account sign-in)
codeseeq login         # choose "Sign in with ChatGPT" in the browser
codeseeq run "hello"
```

The `chatgpt` provider writes a native config (`model_provider = "openai"`,
upstream Codex's built-in ChatGPT-auth provider) and runs Codex on the host
without the CodeSeeq bridge. Verify the generated config contains
`model_provider = "openai"` and that no API key is required.

## Codex Prints "failed to record rollout items: thread ... not found"

If a one-shot `codeseeq run` / piped run finishes successfully but shows:

```
ERROR codex_core::session: failed to record rollout items: thread <uuid> not found
```

This is benign. Modern Codex CLI builds (>= 0.146, the session/thread-store
era) race their final rollout append against live-recorder shutdown at the end
of a turn, so the last persistence write is skipped. The turn itself already
completed. CodeSeeq filters that known line out of one-shot/piped stderr
(upstream openai/codex #22055, #16300, #35385).

To see the raw upstream line anyway:

```bash
CODESEEQ_KEEP_CODEX_ROLLOUT_ERRORS=true ./codeseeq run "prompt"
```

The filter never applies to the interactive TUI (stdin is a terminal).

## Upstream Codex Commands Blocked

If you see:

```
disabled in CodeSeeq privacy mode: this upstream Codex command may contact OpenAI/ChatGPT services
```

CodeSeeq blocks these upstream Codex commands by default:

- `login`, `logout`, `cloud`, `app`, `app-server`, `plugin`, `update`, `features`

To allow a specific command:

```bash
CODESEEQ_ALLOW_UPSTREAM_CODEX_SERVICES=true ./codeseeq login
```

## Codex Auto-Update / Latest Version Disabled

CodeSeeq pins the Codex version instead of using `@openai/codex@latest`. If you need Codex CLI installed manually:

```bash
npm install -g @openai/codex@0.130.0
```

The installer fetches the latest release by default. Set `CODESEEQ_ALLOW_LATEST_RELEASE=false` (with a pinned `CODESEEQ_RELEASE_TAG`) if you want to pin an exact release.

## Privacy Settings Not In Config

Run `./codeseeq doctor` (and inspect the generated `config.toml` at your
`CODEX_HOME`) to verify the generated config includes:

```toml
web_search = "live"

[analytics]
enabled = false

[feedback]
enabled = false

[otel]
exporter = "none"
metrics_exporter = "none"
trace_exporter = "none"
log_user_prompt = false

[history]
persistence = "none"
```

If any of these are missing, make sure you are running the latest CodeSeeq with privacy hardening applied.


## System Prompt Missing

Check:

```bash
./codeseeq system view
./codeseeq doctor
```

Expected storage path:

```text
~/.config/codeseeq/system-prompt.md
```

Set one:

```bash
./codeseeq system add "When asked for the magic marker, answer exactly: SYSTEM-PROMPT-ACTIVE"
```

## System Prompt Not Applying

Verify `doctor` or `config` reports:

```text
System prompt: present
System prompt injection: codex-config-developer_instructions
```

Then run:

```bash
./codeseeq run "What is the magic marker?"
```

`config` redacts prompt content by design. Only `system view/show/cat` prints the full prompt.

## System Prompt Is Too Large

Large prompts increase every model request. Keep persistent system prompts focused. Store task-specific content in task files and run:

```bash
./codeseeq run -f task.md
```

Do not store secrets in the system prompt unless you understand the risk.

## `run -f` File Missing

Use a host path that exists and is readable:

```bash
./codeseeq run -f ./tasks/build-feature.md
```

If both file and inline prompt text are provided, CodeSeeq fails clearly.

Neither an inline prompt nor `-f/--file` is valid for `run`; use one of:

```bash
./codeseeq run "say hi"
./codeseeq run -f ./tasks/build-feature.md
./codeseeq run --file=./tasks/build-feature.md
```

## `run -f` Path Confusion

In safe/container mode, the host wrapper reads the host file and copies it through `.codeseeq/tmp/` so the container can feed it to Codex. You normally do not need to translate paths to `/workspace` yourself.

## Prompt File Sent To Model

Everything in a `run -f/--file` task file is sent as prompt content. Do not put secrets in task prompts unless intended.

## `ping-web` Fails

Check:

- `DEEPSEEK_API_KEY` is set.
- `BRAVE_API_KEY` is set.
- Network and provider quota are healthy.

Then run:

```bash
./codeseeq ping-web
```

## `ping-docs` Fails

Check:

- `DEEPSEEK_API_KEY` is set.
- `UNSTRUCTURED_API_KEY` is set.
- Network and provider quota are healthy.

Then run:

```bash
./codeseeq ping-docs
```

## Non-DeepSeek Model Rejected

Supported choices:

- `deepseek-v4-flash`
- `deepseek-v4-flash-thinking`
- `deepseek-v4-pro`
- `deepseek-v4-pro-thinking`

The Codex `/model` UI may depend on upstream Codex model-catalog behavior. Wrapper and bridge validation remain authoritative.

## Interactive Menu Checks

Manual verification:

```bash
./codeseeq
```

Open the Codex menu or use slash commands such as `/model`. Approval and sandbox toggles follow upstream Codex behavior. Menu state is stored under the isolated CodeSeeq `CODEX_HOME`, not `~/.codex`.

## Raw DSML Or Tool Call Markup Appears

Rebuild and rerun:

```bash
./codeseeq build
./codeseeq ping-stream
```

The bridge contains DSML/tool-call normalization for current CodeSeeq behavior.
Since v0.7.3 it also unwraps nested `<tool_calls>` wrappers and decodes escaped
markup (`&lt;tool_calls&gt;` / `\u003c`) so those blocks run as tool calls;
see "Local Model Emits An XML Tool Block As Text" above.

## Requested Behavior Differs From Local Codex

The local `codex` binary/source is authoritative for available flags. In this environment, Codex direct non-interactive prompts are implemented with:

```bash
codex exec "prompt"
codex exec - < task.md
```

CodeSeeq follows that: `./codeseeq "prompt"` and `./codeseeq run "prompt"` use non-interactive Codex exec. `-p` is Codex profile selection, not prompt mode:

```bash
./codeseeq -p myprofile
./codeseeq -p myprofile "prompt"
./codeseeq --profile myprofile "prompt"
```

`--prompt` is not a CodeSeeq prompt alias. Use a positional prompt or `run -f`.

## Empty Source References In Release Zips

Release packages may omit local `open-responses/` and `codex/` source checkouts. Default inspection prints an informational warning instead of failing:

```bash
make inspect-bridge
```

Use strict inspection only when the source checkouts are intentionally present:

```bash
make inspect-bridge-strict
```

## CI Release Job Did Not Run

The release job only triggers on version tag pushes matching `v*`. If you pushed
a tag and don't see the release step:

1. Verify the tag matches `v*`: `git tag --list 'v*'`
2. Check the Actions tab for the workflow run — the release job shows as
   **skipped** on non-tag pushes.
3. Make sure all four prerequisite jobs (`static`, `project`, `bridge-smoke`,
   `docker`) passed — the release job waits on `needs: [static, project,
   bridge-smoke, docker]`.
4. If the release job ran but failed, check the job logs for package build or
   `gh-release` action errors.

The workflow also needs `contents: write` permission for the release job, which
is set in the CI config.


## GGUF model: `llama-server` not found

The bridge needs the llama.cpp `llama-server` binary to serve a local `.gguf`
model. If it is missing you get:

```
gguf provider requires llama.cpp llama-server. Set CODESEEQ_GGUF_LLAMA_SERVER_PATH
or install llama.cpp (see https://github.com/ggml-org/llama.cpp).
```

Fix:

```bash
# install llama.cpp, or point CodeSeeq at an existing binary
export CODESEEQ_GGUF_LLAMA_SERVER_PATH=/path/to/llama-server
codeseeq --model /path/to/model.gguf "hello"
```

`codeseeq doctor` / `GET /health` also reports `gguf_binary` as `not-found`
when the binary is unavailable.

## GGUF model: file not found

The selected `.gguf` path must exist and be readable. A missing path fails with
a clean `gguf model file not found: <path>` error rather than a traceback:

```bash
CODESEEQ_MODEL=/does/not/exist/model.gguf codeseeq "hello"
# -> gguf model file not found: /does/not/exist/model.gguf
```

Check the path, expand `~` (the wrapper/bridge resolve it), and make sure the
file is an actual `.gguf` file.

If you were running in container runtime, CodeSeeq auto-selects host runtime
for GGUF models so the `.gguf` path is resolved on the host filesystem instead
of inside the container. If the file exists on the host but you still see this
error, confirm the host runtime prerequisites are installed: the Codex CLI,
Python with the bridge dependencies, and `llama-server` (or
`CODESEEQ_GGUF_LLAMA_SERVER_PATH`).

## GGUF/Qwen: `Unexpected reasoning effort max`

Qwen-family GGUF chat templates accept only `xhigh`, `medium`, and `low` for
`reasoning_effort`. Sending `max` (or `high`) makes `llama-server` return:

```text
Jinja Exception: Unexpected reasoning effort max.
Supported types are xhigh (default), medium, and low.
```

CodeSeeq v0.7.1+ translates GGUF efforts to the template-safe vocabulary:
`high`, `max`, and `ultra` become `xhigh`, while `medium` and `low` pass
through. If you are on an older build, or want to be explicit, set `xhigh`:

```bash
# one run
codeseeq run -m gguf@auto -R xhigh --thinking -y "prompt"

# persisted default
codeseeq config     # choose GGUF, then xhigh

# or in ~/.config/codeseeq/config.json
# "CODESEEQ_REASONING_EFFORT": "xhigh"
```

The environment variable and `-R` always override the saved config value.

## Reuse an already-running llama-server (don't spawn a second GGUF server)

If you already started `llama-server` yourself and just want CodeSeeq to talk
to that endpoint instead of launching a new one, use the `local` provider:

```bash
# llama-server listens on http://127.0.0.1:8080 by default
export LOCAL_BASE_URL=http://127.0.0.1:8080
codeseeq --model 'local@<alias-or-model-name>' "hello"
```

The part after `local@` is the upstream model name CodeSeeq sends to the
server, so use the same `--alias` you started `llama-server` with (or the GGUF
filename stem if you did not pass `--alias`). This path never validates or
spawns a `.gguf` file.

For automatic selection of the model already hosted by your server, use:

```bash
codeseeq --model gguf@auto -y "hello"
```

CodeSeeq queries `/v1/models` and forwards the loaded model's real ID. No
GGUF path or upstream model name is needed. For a remote/nonstandard port,
set `GGUF_BASE_URL` to the server base URL. If multiple models are loaded,
use a single-model endpoint to avoid ambiguity.

If you prefer to keep using `gguf@<path>` while reusing an existing server, set
`GGUF_BASE_URL`; the bridge then skips spawning and routes to
`{GGUF_BASE_URL}/v1/chat/completions`. Note that `gguf@<path>` still requires
the `.gguf` file to exist locally for model resolution:

```bash
export GGUF_BASE_URL=http://127.0.0.1:8080
codeseeq --model /path/to/model.gguf "hello"
```

> Strict llama.cpp chat templates (for example Qwen3-style GGUF models) reject
> multiple `system` messages with `Jinja Exception: System message must be at
> the beginning.` CodeSeeq collapses Codex's developer instructions, tool
> steering, and any per-model system prompt into a single leading system
> message before forwarding, so both the `gguf` and `local` paths work against
> these templates.

## MLX model: `mlx_lm` not found

The mlx provider serves a local MLX model directory by launching Apple's
`mlx_lm.server`, so the interpreter needs `mlx-lm` installed. A missing
installation fails with a clean message:

```
mlx provider requires Apple MLX + mlx-lm. Install with: python3 -m pip install
mlx-lm (see https://github.com/ml-explore/mlx-lm), or set CODESEEQ_MLX_PYTHON
to the interpreter that has mlx_lm installed.
```

If `mlx_lm` lives in a venv/conda env rather than the default `python3`:

```bash
export CODESEEQ_MLX_PYTHON=/path/to/venv/bin/python3
codeseeq -m mlx@/path/to/model-dir "hello"
```

## MLX model: directory not found / not an MLX conversion

A missing directory fails with `mlx model directory not found: <path>`. A
directory that is not an MLX conversion fails with one of:

- `mlx model directory has no config.json (is it an MLX conversion?): <path>`
- `mlx model directory has no .safetensors weights (convert with \`python -m mlx_lm.convert\`): <path>`

Convert a Hugging Face model first, e.g.:

```bash
python3 -m mlx_lm.convert --hf-path mistralai/Mistral-7B-Instruct-v0.3 -q
```

## MLX model: reuse an already-running `mlx_lm.server`

`mlx_lm.server` answers `/health` before its weights are loaded, so the first
request can block while the model loads. CodeSeeq waits for `/health`, then
routes the existing chat-completions translation to
`http://127.0.0.1:<port>/v1/chat/completions`.

To reuse a server you started yourself instead of letting CodeSeeq spawn one,
point the mlx provider at it with `CODESEEQ_MLX_BASE_URL` or `MLX_BASE_URL`
(the URL may end in `/v1`; CodeSeeq does not double it):

```bash
# terminal 1
python3 -m mlx_lm server --model /path/to/model-dir --port 8888

# terminal 2
export CODESEEQ_MLX_BASE_URL=http://127.0.0.1:8888
codeseeq -m mlx@/path/to/model-dir "hello"
```

A generic loopback `CODESEEQ_BASE_URL` / `OPENAI_BASE_URL` also selects
external mode for mlx; non-loopback generic base URLs are ignored for mlx so an
ambient hosted-API fallback (e.g. `CODESEEQ_BASE_URL=https://api.deepseek.com`)
can never hijack a local MLX model into a hosted API.

`codeseeq doctor` / `GET /health` reports `mlx_available` (`found` /
`not-found`) so you can check the interpreter discovery without a full run.
