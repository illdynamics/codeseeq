# CodeSeeq

CodeSeeq is a drop-in launcher and CLI wrapper for OpenAI's Codex CLI, routing all model traffic through a local OpenResponses-compatible bridge to DeepSeek. Where you would normally type `codex ...`, you type `codeseeq ...` instead — it manages the container runtime, bridge lifecycle, configuration, and DeepSeek model wiring so you get a seamless Codex experience backed by DeepSeek.

<p align="center">
  <img src="./codeseeq.jpg" alt="CodeSeeq" width="80%">
</p>

Current version: `0.2.7` (from [`VERSION`](./VERSION)).

Release notes: [`RELEASE-NOTES.md`](./RELEASE-NOTES.md)

## Quickstart

Prerequisites:

- Podman preferred, or Docker as a compatible fallback.
- `DEEPSEEK_API_KEY` in your shell for model requests.
- Optional `BRAVE_API_KEY` for `ping-web`.
- Optional `UNSTRUCTURED_API_KEY` for `ping-docs`.

Build or auto-build the image:

```bash
./codeseeq models
# or
./codeseeq build
```

Use it like Codex:

```bash
./codeseeq
./codeseeq "say hi"
./codeseeq run "say hi"
./codeseeq run -f task.md
./codeseeq run --file tasks/feature.md
./codeseeq --model deepseek-v4-pro "review this repo"
./codeseeq -p myprofile "say hi"
```

Install the user-local command:

```bash
./codeseeq install
```

This installs a snapshot to `~/.config/codeseeq` and a launcher at `~/bin/codeseeq`.

Uninstall with:

```bash
./codeseeq nuke
```

## Runtime Model

Default mode is safe/containerized:

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

Danger mode is explicit:

```bash
./codeseeq -y "fix the tests"
./codeseeq --yolo "fix the tests"
./codeseeq --dangerously-bypass-approvals-and-sandbox "fix the tests"
./codeseeq --sandbox danger-full-access "fix the tests"
./codeseeq --sanbox danger-full-access "fix the tests"
```

In danger mode, CodeSeeq starts only the bridge in the selected container runtime and runs local host `codex` directly on the current checkout. It uses isolated repo-local `CODEX_HOME=$PWD/.codeseeq`, never the user's real `~/.codex`. Each danger-mode invocation gets its own bridge on the first free localhost port starting at `CODESEEQ_OPENRESPONSES_PORT` (default `8080`).

If local `codex` is missing, install it:

```bash
npm install -g @openai/codex
```

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

In safe/container mode, Codex works in `/workspace` inside the container. That path is a bind mount of the directory where you launched `./codeseeq`, so writes land in your host checkout.

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

The prompt is injected into Codex config as `developer_instructions`, which Codex sends as a developer instruction while preserving Codex's built-in base instructions. It applies to normal Codex request paths including interactive sessions, bare direct prompts, `run`, `run -f/--file`, explicit `codex` passthrough, safe container mode, and danger host mode.

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

Local reference paths mentioned by older docs, such as `./codex` and `./open-responses`, may be absent from a minimal checkout. This repository's runtime does not depend on Docker Compose.

## License

Licensed under the Apache License, Version 2.0 (Apache-2.0).

- Full license text: [`LICENSE`](./LICENSE)
- Copyright notices: [`COPYRIGHT`](./COPYRIGHT)
