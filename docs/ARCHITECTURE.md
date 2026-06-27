# Architecture

Current version: `v0.3.1`

## Runtime Modes

CodeSeeq supports three runtime modes that control where the Codex CLI itself runs.
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
  -> /usr/local/bin/codeseeq-entrypoint
  -> Codex inside the container, cwd=/workspace
  -> local bridge inside the same container
  -> http://127.0.0.1:8080/v1/responses
  -> DeepSeek API
```

Default Codex settings: `approval_policy = "on-request"`, `sandbox_mode = "workspace-write"`.

### Host Runtime

```text
host ./codeseeq -y/--yolo/--sandbox danger-full-access ...
  -> bridge starts as process or container sidecar
  -> local host Codex, cwd=current host checkout
  -> isolated CODEX_HOME=$PWD/.codeseeq
  -> DeepSeek API through the bridge
```

Host runtime runs Codex directly on the host checkout without container sandboxing.
Codex approval and sandbox settings are configured normally through the generated
config unless the operator explicitly requests danger/yolo bypass. The
`--dangerously-bypass-approvals-and-sandbox` flag is only passed to Codex when
the operator uses `-y`, `--yolo`, or `--sandbox danger-full-access`.

## Bridge Modes

CodeSeeq controls how the translation bridge is started via `CODESEEQ_BRIDGE_MODE`.

| Mode | Behavior |
|------|----------|
| `process` | Start `bin/codeseeq-bridge.py` as a direct child process. No Docker/Podman required. |
| `container` | Start the bridge inside a Docker/Podman container (legacy behavior). |
| `external` | Assume the bridge is already running. Use `CODESEEQ_BRIDGE_BASE_URL`. |
| `auto` (default) | Prefer `process` mode when Python + dependencies are available. Fall back to `container`. |

### Process Mode Diagram

```text
wrapper (./codeseeq)
  -> python3 bin/codeseeq-bridge.py   (host-native child process)
  -> Codex                             (host or container)
  -> DeepSeek API
```

Process mode is the recommended path for host runtime. It removes the bridge
sidecar container entirely. Use when you want to avoid Docker-in-Docker or are
already running inside a container.

### External Mode

```text
./codeseeq --bridge-mode external --bridge-url http://192.168.1.10:8080/v1
```

Points CodeSeeq at a pre-existing bridge. The launcher runs a health check
against the configured URL and proceeds without starting any local bridge.

### Bridge Reuse

Set `CODESEEQ_BRIDGE_REUSE=1` to reuse an existing healthy bridge at the
configured port instead of starting a new one. If no healthy bridge is found,
a new one is started.

## Container Runtime

Podman is preferred. Docker is a compatible fallback. Compose is not supported.

Selection order in host scripts:

1. `CONTAINER=podman|docker`, if set.
2. `podman`
3. `docker`

`Makefile` uses:

```make
CONTAINER ?= podman
```

so `make CONTAINER=docker build` is supported.

Podman bind mounts use `:Z` by default for SELinux. Docker bind mounts omit `:Z` by default. `CODESEEQ_VOLUME_SUFFIX` can override the suffix.

## Workspace Mapping

Safe/container mode keeps the actual Codex working directory as:

```text
/workspace
```

The host wrapper passes the launch directory as `CODESEEQ_HOST_WORKDIR`. Before Codex starts, the entrypoint prints a stderr-only banner:

```text
CodeSeeq workspace:
  host: /home/user/project
  container: /workspace
```

This display does not change where Codex writes. Writes still go to `/workspace`, which is the mounted host checkout.

## Codex Configuration

Safe/container mode:

- `CODEX_HOME=/home/codeseeq/.codeseeq`
- config path `/home/codeseeq/.codeseeq/config.toml`
- default `approval_policy = "on-request"`
- default `sandbox_mode = "workspace-write"`

Host runtime mode:

- `CODEX_HOME=$PWD/.codeseeq` unless `CODESEEQ_HOST_CODEX_HOME` overrides it.
- config path `$PWD/.codeseeq/config.toml`
- local `codex` is invoked normally with the generated config's approval and
  sandbox settings. The `--dangerously-bypass-approvals-and-sandbox` flag is
  only added when the operator explicitly requests danger/yolo bypass.
- bridge runs as process or container sidecar, bound to `127.0.0.1`.
- each invocation starts its own bridge on the first free port starting at
  `CODESEEQ_BRIDGE_PORT` or falls back to auto-selection.

No supported path uses the user's real `~/.codex`.

## Persistent System Prompt

The persistent system prompt source of truth is:

```text
~/.config/codeseeq/system-prompt.md
```

The wrapper manages it through:

```bash
./codeseeq system add "..."
./codeseeq system add -f prompts/system.md
./codeseeq system view
./codeseeq system remove
```

Injection mechanism:

```text
codex-config-developer_instructions
```

CodeSeeq writes the stored prompt into generated Codex config as `developer_instructions`. Codex then sends it as a developer instruction, while preserving the selected model's normal base instructions. The bridge maps Codex `developer` messages to DeepSeek-compatible `system` messages.

`doctor` and `config` report presence, path, bytes, lines, and mechanism without printing the full prompt. `system view/show/cat` are the explicit content-printing commands.

## Prompt File Flow

`run -f/--file` reads a host prompt file and uses its exact content as the Codex non-interactive prompt.

Safe/container mode:

1. Host wrapper validates the file.
2. Host wrapper copies it to `.codeseeq/tmp/`.
3. The copied file is available in the container under `/workspace/.codeseeq/tmp/`, and `CODESEEQ_RUN_PROMPT_FILE`/`--file` point at that managed copy.
4. Entrypoint invokes `codex exec --skip-git-repo-check -` and feeds the file on stdin.

Host runtime mode:

1. Host wrapper validates the file.
2. Local Codex receives the file content on stdin through `codex exec --skip-git-repo-check -`.

This avoids large shell arguments and preserves markdown/code fences/newlines.

## Codex-Compatible CLI

CodeSeeq adds subcommands such as `run`, `system`, `build`, `install`, `doctor`,
`models`, `config`, `ping`, `shell`, `smoke`, and `package`, but it does not
claim Codex-owned flags.

- `./codeseeq "prompt"` maps to non-interactive `codex exec`.
- `./codeseeq run "prompt"` maps to non-interactive `codex exec`.
- `./codeseeq run -f task.md` feeds task-file contents on stdin.
- `-p PROFILE`, `--profile PROFILE`, and `--profile=PROFILE` are Codex profile flags.
- `--model`, `--sandbox`, `--ask-for-approval`, `--cd`, `--help`, `--version`,
  and other Codex flags are preserved or translated only where CodeSeeq must
  normalize DeepSeek model aliases or choose safe vs host runtime mode.
- `--prompt` is not a CodeSeeq direct-prompt flag.

Direct non-interactive prompt execution uses the Codex CLI's `exec` command.
The installed Codex CLI reports `codex exec [PROMPT]` and `codex exec -` for
stdin prompts.

## Components

1. **`./codeseeq`** — Host wrapper. Detects Podman/Docker. Handles
   install/build/smoke/system/package commands. Chooses container vs host
   runtime mode. Manages bridge lifecycle (process, container, or external
   mode). Rewrites safe-mode `run -f` prompt files into `.codeseeq/tmp/`.

2. **`bin/codeseeq-entrypoint`** — In-container dispatcher. Generates Codex
   config. Starts the local bridge. Runs Codex interactive, prompt, run-file,
   diagnostics, and passthrough paths.

3. **`bin/codeseeq-bridge.py`** — FastAPI bridge implementing `/v1/responses`
   (the OpenAI Responses API wire format). Converts Codex Responses requests
   to DeepSeek Chat Completions. Normalizes model aliases, streaming events,
   function/tool calls, and diagnostic web/doc paths.

4. **`@openai/codex`** — Installed in the image for safe mode. Used from the
   host in host runtime mode if local `codex` exists.

5. **`requirements-bridge.txt`** — Python dependencies for the bridge (FastAPI,
   Uvicorn, httpx). Install with: `python3 -m pip install -r requirements-bridge.txt`

6. **`config/model-catalog.json`** — DeepSeek model definitions used by the
   wrapper and bridge for model validation and catalog injection.

7. **`config/codex-model-catalog.json`** — Codex-compatible model catalog
   injected into Codex config via `model_catalog_json` for TUI model selection.

## Interactive Menu Compatibility

CodeSeeq does not fork Codex's interactive menu. Model, sandbox, approval, and
settings toggles use upstream Codex behavior against the generated CodeSeeq
config and isolated `CODEX_HOME`.

`model_catalog_json` points Codex at the CodeSeeq DeepSeek catalog. If a Codex
release still surfaces extra model choices, wrapper/bridge validation remains
authoritative and non-DeepSeek models fail clearly.

## Bridge API Format

The CodeSeeq bridge implements the OpenAI Responses API wire format
(`/v1/responses`). Codex is configured with `wire_api = "responses"` in its
generated `config.toml`, which tells Codex to speak the Responses protocol.
The bridge translates between that protocol and DeepSeek's Chat Completions API.

The upstream `open-responses` npm package is **not** used or required.
CodeSeeq's `Dockerfile` does not install it. The actual runtime bridge is
entirely `bin/codeseeq-bridge.py`.

## CI / Release Pipeline

CodeSeeq uses a single GitHub Actions workflow (`.github/workflows/ci.yml`)
that runs on every push and pull request. On version tag pushes (`v*`) it also
creates a GitHub Release.

Jobs:

1. **static** — Shell syntax checks, shellcheck, executable permissions,
   secret scanning, git whitespace, bridge Python syntax.
2. **project** — Bridge extraction tests, project checks, config generation
   validation, version consistency.
3. **bridge-smoke** — Bridge process smoke tests, package build, package
   validation, package hygiene.
4. **docker** — Docker image build, all container smoke tests (help, models,
   doctor, config, bridge server, version).
5. **🚀 Release** (tag pushes only) — Gated behind `needs: [static, project,
   bridge-smoke, docker]` and `if: startsWith(github.ref, 'refs/tags/v')`.
   Builds the package and creates a GitHub Release with the zip artifact
   attached.

## Packaging Model

Official release archives are created by `scripts/package.sh`, `./codeseeq package`, or `make package`. The checker can validate either a generated archive or an arbitrary uploaded zip:

```bash
./scripts/package.sh --check
./scripts/package.sh --check-archive /mnt/data/codeseeq.zip
```

Archives fail validation if they include local secrets/state such as `.env`,
`.git/`, `.codeseeq/`, `dist/`, nested zips, `workspace/`, `__MACOSX/`,
`.DS_Store`, `node_modules/`, `__pycache__/`, `*.pyc`, `logs/`, or `*.log`.
`.env.example` is intentionally included.
