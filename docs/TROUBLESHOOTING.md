# Troubleshooting

Current version: `v0.4.5`

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

Expected provider fields:

- `model_provider = "codeseeq"`
- `wire_api = "responses"`
- `env_key = "DEEPSEEK_API_KEY"`
- `requires_openai_auth = false`

Also verify `CODEX_HOME` is `.codeseeq`, not `~/.codex`.

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

If you prefer to keep using `gguf@` while reusing an existing server, set
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
