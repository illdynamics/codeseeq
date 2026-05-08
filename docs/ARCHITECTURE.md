# Architecture

Current version: `0.2.5`

## Runtime Modes

CodeSeeq has two runtime modes.

Default safe mode:

```text
host ./codeseeq
  -> podman/docker run codeseeq:dev
  -> /usr/local/bin/codeseeq-entrypoint
  -> Codex inside the container, cwd=/workspace
  -> local bridge inside the same container
  -> http://127.0.0.1:8080/v1/responses
  -> DeepSeek API
```

Explicit danger host mode:

```text
host ./codeseeq -y/--yolo/--sandbox danger-full-access ...
  -> podman/docker run bridge container only
  -> bridge published on http://127.0.0.1:<port>/v1
  -> local host codex, cwd=current host checkout
  -> isolated CODEX_HOME=$PWD/.codeseeq
  -> DeepSeek API through the bridge
```

Danger host mode is intentional: when the operator asks for Codex's dangerous full-access behavior, Codex runs directly on the host checkout instead of pretending a container sandbox is full host access.

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

Danger host mode:

- `CODEX_HOME=$PWD/.codeseeq` unless `CODESEEQ_HOST_CODEX_HOME` overrides it.
- config path `$PWD/.codeseeq/config.toml`
- local `codex` is invoked with `--dangerously-bypass-approvals-and-sandbox`.
- bridge remains containerized and bound to `127.0.0.1` on the host.

No supported path uses the user's real `~/.codex`.

## Persistent System Prompt

The persistent system prompt source of truth is:

```text
$PWD/.codeseeq/system-prompt.md
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

Danger host mode:

1. Host wrapper validates the file.
2. Local Codex receives the file content on stdin through `codex exec --skip-git-repo-check -`.

This avoids large shell arguments and preserves markdown/code fences/newlines.

## Codex-Compatible CLI

CodeSeeq adds subcommands such as `run`, `system`, `build`, `install`, `doctor`, `models`, `config`, `ping`, `shell`, `smoke`, and `package`, but it does not claim Codex-owned flags.

- `./codeseeq "prompt"` maps to non-interactive `codex exec`.
- `./codeseeq run "prompt"` maps to non-interactive `codex exec`.
- `./codeseeq run -f task.md` feeds task-file contents on stdin.
- `-p PROFILE`, `--profile PROFILE`, and `--profile=PROFILE` are Codex profile flags.
- `--model`, `--sandbox`, `--ask-for-approval`, `--cd`, `--help`, `--version`, and other Codex flags are preserved or translated only where CodeSeeq must normalize DeepSeek model aliases or choose safe vs danger runtime mode.
- `--prompt` is not a CodeSeeq direct-prompt flag.

Direct non-interactive prompt execution uses the Codex CLI's `exec` command. The installed Codex CLI reports `codex exec [PROMPT]` and `codex exec -` for stdin prompts.

## Components

1. `./codeseeq`
- Host wrapper.
- Detects Podman/Docker.
- Handles install/build/smoke/system/package commands.
- Chooses safe container mode vs danger host mode.
- Rewrites safe-mode `run -f` prompt files into `.codeseeq/tmp/`.

2. `bin/codeseeq-entrypoint`
- In-container dispatcher.
- Generates Codex config.
- Starts the local bridge.
- Runs Codex interactive, prompt, run-file, diagnostics, and passthrough paths.

3. `bin/codeseeq-bridge.py`
- FastAPI bridge implementing `/v1/responses`.
- Converts Codex Responses requests to DeepSeek Chat Completions.
- Normalizes model aliases, streaming events, function/tool calls, and diagnostic web/doc paths.

4. `@openai/codex`
- Installed in the image for safe mode.
- Used from the host in danger mode if local `codex` exists.

## Interactive Menu Compatibility

CodeSeeq does not fork Codex's interactive menu. Model, sandbox, approval, and settings toggles use upstream Codex behavior against the generated CodeSeeq config and isolated `CODEX_HOME`.

`model_catalog_json` points Codex at the CodeSeeq DeepSeek catalog. If a Codex release still surfaces extra model choices, wrapper/bridge validation remains authoritative and non-DeepSeek models fail clearly.

## OpenResponses Note

The upstream `open-responses` CLI remains Compose-oriented. CodeSeeq does not use Compose at runtime. The image includes the package for ecosystem compatibility, while the actual runtime bridge is `bin/codeseeq-bridge.py`.

## Packaging Model

Official release archives are created by `scripts/package.sh`, `./codeseeq package`, or `make package`. The checker can validate either a generated archive or an arbitrary uploaded zip:

```bash
./scripts/package.sh --check
./scripts/package.sh --check-archive /mnt/data/codeseeq.zip
```

Archives fail validation if they include local secrets/state such as `.env`, `.git/`, `.codeseeq/`, `dist/`, nested zips, `workspace/`, `__MACOSX/`, `.DS_Store`, `node_modules/`, `__pycache__/`, `*.pyc`, `logs/`, or `*.log`. `.env.example` is intentionally included.
