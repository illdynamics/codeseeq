# CodeSeeq

**Production-grade Codex CLI drop-in replacement routing to DeepSeek V4 models.**
Run codeseeq instead of codex. Same flags, same interactive behavior, same tool calls. But your prompts go to DeepSeek V4 models via your DEEPSEEK_API_KEY — no OpenAI account/API key needed.

<p align="center">
  <img src="./codeseeq.jpg" alt="CodeSeeq" width="80%">
</p>

Current version: `0.2.9` (from [`VERSION`](./VERSION)).

Release notes: [`RELEASE-NOTES.md`](./RELEASE-NOTES.md)

## Quickstart

### Prerequisites

- **DEEPSEEK_API_KEY** — set in your shell for model requests.
- Podman or Docker (optional — only needed for container mode).
- Python 3 + `pip install -r requirements-bridge.txt` (optional — only needed for host/process mode).

### Install

**Option A — curl one-liner (recommended)**

```bash
curl -fsSL https://raw.githubusercontent.com/codeseeq/codeseeq/main/scripts/install-curl.sh | bash
```

Downloads the latest release zip, extracts it, and installs the `codeseeq` command to `~/.config/codeseeq` with a launcher at `~/bin/codeseeq`.

**Option B — git clone**

```bash
git clone https://github.com/codeseeq/codeseeq.git
cd codeseeq
./codeseeq install
```

**Option C — download release zip manually**

Download `codeseeq-v0.2.9.zip` from [GitHub Releases](https://github.com/codeseeq/codeseeq/releases), then:

```bash
unzip codeseeq-v0.2.9.zip
cd codeseeq-v0.2.9  # or wherever it extracted
./codeseeq install
```

### Post-install

Make sure `~/bin` is in your `PATH`:

```bash
export PATH="$HOME/bin:$PATH"
```

Set your API key:

```bash
export DEEPSEEK_API_KEY=sk-...
```

### Use it

```bash
codeseeq -y "say hi"
codeseeq run "say hi"
codeseeq run -f task.md
codeseeq --model deepseek-v4-pro "review this repo"
codeseeq -p myprofile "say hi"
```

### Host-native mode (no Docker/Podman needed)

```bash
pip3 install -r ~/.config/codeseeq/requirements-bridge.txt
codeseeq --bridge-mode process -y "say hi"
```

### Uninstall

```bash
codeseeq nuke
```

## Runtime Model

CodeSeeq separates **where Codex runs** from **how the bridge is started**.

### Runtime Modes (where Codex runs)

Set via `CODESEEQ_RUNTIME_MODE`.

| Mode | Behavior |
|------|----------|
| `container` | Run Codex inside a Docker/Podman container. Safe/isolated default. |
| `host` | Run Codex directly on the host. No container isolation. |
| `auto` (default) | Use `container` for normal paths; use `host` when danger/yolo is requested. |

### Container Runtime (Safe Default)

```text
host ./codeseeq
  -> podman/docker run codeseeq:dev
  -> Codex inside the container
  -> local bridge inside the container
  -> DeepSeek
```

Default Codex settings:

- `approval_policy = "on-request"`
- `sandbox_mode = "workspace-write"`

### Host Runtime

Host runtime runs Codex directly on your host checkout. It does **not** provide
container isolation. Codex uses the normal approval and sandbox settings from
its generated config. The danger/yolo bypass is only applied when you explicitly
request it with `-y`, `--yolo`, `--dangerously-bypass-approvals-and-sandbox`, or
`--sandbox danger-full-access`.

```bash
# Host runtime with process bridge (no containers at all)
CODESEEQ_RUNTIME_MODE=host CODESEEQ_BRIDGE_MODE=process ./codeseeq run "hello"

# Danger/yolo mode: host Codex with bypass flag
./codeseeq -y "fix the tests"
./codeseeq --yolo "fix the tests"
```

In host runtime with danger/yolo, CodeSeeq starts the bridge (process or container),
runs local host `codex` directly on the current checkout with
`--dangerously-bypass-approvals-and-sandbox`, and uses isolated repo-local
`CODEX_HOME=$PWD/.codeseeq` — never the user's real `~/.codex`.

If local `codex` is missing, install it:

```bash
npm install -g @openai/codex
```

## How It Works

CodeSeeq does not fork or patch Codex. It launches the upstream Codex CLI with an
isolated generated `config.toml`. That config points Codex at a local CodeSeeq
bridge implementing the OpenAI Responses API. The bridge translates requests to
DeepSeek Chat Completions and converts responses back to the format Codex expects.

## Bridge Modes

CodeSeeq controls how the translation bridge is started via `CODESEEQ_BRIDGE_MODE`.

| Mode | Behavior |
|------|----------|
| `process` | Start `bin/codeseeq-bridge.py` as a direct child process on the host. No Docker/Podman required. |
| `container` | Start the bridge inside a Docker/Podman container (legacy behavior). |
| `external` | Assume the bridge is already running. Use `CODESEEQ_BRIDGE_BASE_URL`. |
| `auto` (default) | Prefer `process` mode when Python + dependencies are available. Fall back to `container`. |

### Process Mode (Recommended for Host Runtime)

```bash
# No container needed for the bridge
CODESEEQ_BRIDGE_MODE=process DEEPSEEK_API_KEY=sk-... ./codeseeq -y "inspect this repo"

# Or just rely on auto-detection when deps are installed
pip3 install -r requirements-bridge.txt
DEEPSEEK_API_KEY=sk-... ./codeseeq -y "review the code"

# Combined: host runtime + process bridge (zero containers)
CODESEEQ_RUNTIME_MODE=host CODESEEQ_BRIDGE_MODE=process DEEPSEEK_API_KEY=sk-... ./codeseeq run "hello"
```

Process mode is **not** a sandbox boundary — it only removes the bridge sidecar container. Use it when you want to avoid Docker-in-Docker or are already running inside a container.

### Container Mode (Legacy)

```bash
# Force old container-bridge behavior
CODESEEQ_BRIDGE_MODE=container DEEPSEEK_API_KEY=sk-... ./codeseeq -y "hello"
```

### External Mode

```bash
# Point at an already-running bridge
CODESEEQ_BRIDGE_MODE=external CODESEEQ_BRIDGE_BASE_URL=http://127.0.0.1:8080/v1 DEEPSEEK_API_KEY=sk-... ./codeseeq -y "hello"
```

### Bridge Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `CODESEEQ_BRIDGE_MODE` | `auto` | `auto`, `process`, `container`, or `external` |
| `CODESEEQ_BRIDGE_HOST` | `127.0.0.1` | Bridge listen address |
| `CODESEEQ_BRIDGE_PORT` | auto-select | Fixed bridge port (omit for auto) |
| `CODESEEQ_BRIDGE_BASE_URL` | — | Full bridge URL override (external mode) |
| `CODESEEQ_BRIDGE_LOG` | `~/.config/codeseeq/log/bridge.log` | Bridge log file |
| `CODESEEQ_BRIDGE_STARTUP_TIMEOUT` | `10` | Seconds to wait for health check |
| `CODESEEQ_BRIDGE_REUSE` | `0` | Reuse existing healthy bridge |

## Container Runtime

Podman is preferred. Docker is supported as a compatible fallback. Docker Compose is not supported.

Selection order:

1. `CONTAINER=...` override, if set.
2. `podman`
3. `docker`

Examples:

```bash
CONTAINER=podman ./codeseeq models
CONTAINER=docker ./codeseeq models
make CONTAINER=docker build
```

Podman bind mounts use `:Z` by default for SELinux. Docker uses no suffix by default. Override advanced mount behavior with:

```bash
CODESEEQ_VOLUME_SUFFIX=:z ./codeseeq run "say hi"
CODESEEQ_VOLUME_SUFFIX= ./codeseeq run "say hi"
```

## Workspace Paths

In container runtime, Codex works in `/workspace` inside the container. That path is a bind mount of the directory where you launched `./codeseeq`, so writes land in your host checkout.

Before Codex starts, CodeSeeq prints a stderr banner:

```text
CodeSeeq workspace:
  host: /home/user/project
  container: /workspace
```

The host path is only for operator clarity. Codex still writes to `/workspace` inside the container.

Disable the banner:

```bash
CODESEEQ_WORKSPACE_BANNER=false ./codeseeq run "say hi"
```

## Persistent System Prompt

CodeSeeq can store a user-level persistent system prompt:

```bash
./codeseeq system add "You are terse and practical."
./codeseeq system add -f prompts/system.md
./codeseeq system add --file prompts/system.md
./codeseeq system view
./codeseeq system remove
```

Aliases:

- `view`, `show`, `cat`
- `remove`, `rm`, `clear`

Storage path:

```text
~/.config/codeseeq/system-prompt.md
```

The prompt is injected into Codex config as `developer_instructions`, which Codex sends as a developer instruction while preserving Codex's built-in base instructions. It applies to normal Codex request paths including interactive sessions, bare direct prompts, `run`, `run -f/--file`, explicit `codex` passthrough, container runtime, and host runtime.

Do not put secrets in the system prompt unless you understand that prompt text is sent to the model and stored in user-level config state.

## Prompt Files

Run a task file directly:

```bash
./codeseeq run -f task.md
./codeseeq run --file task.md
./codeseeq run --file=task.md
./codeseeq run -f ./tasks/build-feature.md --model deepseek-v4-pro
./codeseeq run -f task.md --thinking
./codeseeq run -f task.md --yolo
```

`run -f/--file` reads the file as the prompt, preserving markdown, newlines, indentation, and code fences. Missing files fail clearly. Providing both a file and inline prompt text fails clearly.

Large prompt files are copied through `.codeseeq/tmp/` for container mode instead of being expanded into a huge shell argument.

## Commands

CodeSeeq-specific commands remain available:

```bash
./codeseeq build
./codeseeq install
./codeseeq nuke
./codeseeq doctor
./codeseeq models
./codeseeq config
./codeseeq ping
./codeseeq ping-stream
./codeseeq ping-web
./codeseeq ping-docs
./codeseeq shell
./codeseeq smoke
./codeseeq system --help
./codeseeq package
```

Explicit passthrough:

```bash
./codeseeq codex --help
./codeseeq codex exec "say hi"
```

Unknown non-CodeSeeq arguments are passed to Codex as much as possible. CodeSeeq does not use `-p` or `--prompt` as prompt aliases. `-p` and `--profile` are Codex profile-selection flags and are forwarded unchanged. Direct prompt execution is `./codeseeq "prompt"` or `./codeseeq run "prompt"`.

## Clean Packages

Release zips must be produced by the official package command only:

```bash
./scripts/package.sh
./codeseeq package
make package
```

Validate a generated or uploaded archive:

```bash
./scripts/package.sh --check
./scripts/package.sh --check-archive dist/codeseeq-YYYYMMDD-HHMMSS.zip
./scripts/package.sh --check-archive /mnt/data/codeseeq.zip
```

Do not create release zips manually in Finder or macOS Archive Utility. Manual zips can include `__MACOSX`, `.DS_Store`, `.git/`, `.codeseeq/`, nested zips, or `.env` files. `.env.example` is the only env-style file intended for release archives.

## Supported Models

- `deepseek-v4-flash` (default)
- `deepseek-v4-flash-thinking`
- `deepseek-v4-pro`
- `deepseek-v4-pro-thinking`

Provider-form aliases:

- `deepseek@deepseek-v4-flash`
- `deepseek@deepseek-v4-pro`

Non-DeepSeek models are rejected by the wrapper/bridge.

## Diagnostics

Load `.env` for local live tests without modifying it:

```bash
set -a
source .env
set +a
```

Then run:

```bash
./codeseeq doctor
./codeseeq config
./codeseeq ping
./codeseeq ping-stream
./codeseeq ping-web
./codeseeq ping-docs
```

`doctor` reports system prompt status, storage path, byte count, line count, and injection mechanism without printing the prompt content. `config` also redacts the full prompt content.

## Interactive Menu Notes

Codex's normal interactive menu and slash commands run inside CodeSeeq's isolated `CODEX_HOME`.

Manual check:

```bash
./codeseeq
```

Open the Codex menu or use slash commands such as `/model` where supported by your Codex version. Approval and sandbox toggles use upstream Codex behavior. The model menu is backed by CodeSeeq's DeepSeek catalog where Codex honors `model_catalog_json`; wrapper and bridge validation remain authoritative if upstream Codex shows additional models.

## Architecture and Security

- [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md)
- [`docs/TROUBLESHOOTING.md`](./docs/TROUBLESHOOTING.md)
- [`docs/SECURITY.md`](./docs/SECURITY.md)

Local reference paths mentioned by older docs, such as `./codex` and `./open-responses`, may be absent from a minimal checkout. This repository's runtime does not depend on Docker Compose or the upstream `open-responses` npm package.

## License

Licensed under the Apache License, Version 2.0 (Apache-2.0).

- Full license text: [`LICENSE`](./LICENSE)
- Copyright notices: [`COPYRIGHT`](./COPYRIGHT)
