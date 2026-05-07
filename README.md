# CodeSeeq

CodeSeeq is a Podman-first, single-container CLI that runs OpenAI Codex CLI against DeepSeek through a local Responses-compatible bridge inside the same container.

Current version: `0.2.5` (from [`VERSION`](./VERSION)).

Release notes: [`RELEASE-NOTES.md`](./RELEASE-NOTES.md)

## Quickstart

### 1. Prerequisites

- **Podman** installed
- **`DEEPSEEK_API_KEY`** available in your shell
- Optional: `BRAVE_API_KEY` for web search, `UNSTRUCTURED_API_KEY` for doc parsing

### 2. Install user-local command

From the CodeSeeq repo:

```bash
./codeseeq install
```

This installs:

- repo snapshot: `~/.config/codeseeq`
- launcher: `~/bin/codeseeq`

If needed, add `~/bin` to `PATH`.

### 3. Build image

```bash
codeseeq models
```

The first command auto-builds `codeseeq:dev` if missing. Manual build:

```bash
podman build -t codeseeq:dev .
```

### 4. Start interactive mode

```bash
codeseeq
```

`codeseeq` always mounts your current directory into `/workspace`, so coding happens in the directory you run it from.

### 5. Run one prompt

```bash
codeseeq run "Return exactly: codeseeq-ok"
```

Use `--yolo` / `-y` when you intentionally want Codex launched with
`--dangerously-bypass-approvals-and-sandbox`; `run`/`exec` paths also include
`--skip-git-repo-check`:

```bash
codeseeq --yolo run "create ./test.txt with hi"
```

### 6. Container-only usage

```bash
podman run --rm -it \
  -e DEEPSEEK_API_KEY="$DEEPSEEK_API_KEY" \
  -e BRAVE_API_KEY="$BRAVE_API_KEY" \
  -e UNSTRUCTURED_API_KEY="$UNSTRUCTURED_API_KEY" \
  -v "$PWD:/workspace:Z" \
  -w /workspace \
  codeseeq:dev
```

### 7. Health and diagnostics

```bash
codeseeq models
codeseeq doctor
codeseeq ping
codeseeq ping-stream
codeseeq ping-web
codeseeq ping-docs
```

### 8. Optional `.env` loading for local tests

```bash
set -a
source .env
set +a
```

Do not modify `.env` from automation.

---

## Status and Scope

- Supported runtime: single container only.
- Default runtime: Podman.
- Docker Compose: not supported.
- Required key: `DEEPSEEK_API_KEY`.
- Optional keys:
  - `BRAVE_API_KEY` for web search smoke/tool path.
  - `UNSTRUCTURED_API_KEY` for document parsing smoke/tool path.

## Architecture

```text
operator shell
  -> ./codeseeq
  -> podman run codeseeq:dev
  -> /usr/local/bin/codeseeq-entrypoint
  -> local bridge process (/usr/local/bin/codeseeq-bridge.py)
  -> /v1/responses on 127.0.0.1:8080
  -> DeepSeek Chat Completions API
```

Codex is configured with `wire_api = "responses"` and talks only to the local bridge URL.

See [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) for full architecture details.

## Why this runtime path

This repository is pinned to the OpenResponses ecosystem:

- Local reference: `./open-responses`
- Upstream: https://github.com/open-responses/open-responses
- Git URL: https://github.com/open-responses/open-responses.git
- Docs: https://docs.julep.ai/responses/quickstart

Current upstream OpenResponses CLI (`open-responses setup/start`) is Docker-Compose-oriented and requires compose-managed services. That conflicts with the required one-container runtime.

CodeSeeq therefore:

- installs official `open-responses` CLI package in the image for ecosystem compatibility/inspection,
- and runs a local in-container Responses-compatible bridge process for single-container execution.
- bridge normalizes DeepSeek streaming and DSML-style tool-call output into Responses-compatible events for Codex.
- bridge suppresses display-mangled DSML (`<____DSML____...>` and `<｜｜DSML｜｜...>`) and emits tool calls with Codex-compatible streaming lifecycle metadata.

## Local Project Context

- Blueprint: `./codeseeq-blueprint.md`
- Deep research: `./codeseeq-deep-research.md`
- Codex report: `./codeseeq-codex-report.md`
- Bridge report: `./codeseeq-bridge.report.md` (alias file), `./codeseeq-bridge-report.md`
- Codex source reference: `./codex`
- OpenResponses source reference: `./open-responses`

## Build

```bash
podman build -t codeseeq:dev .
```

## Install (User Local)

From this repository:

```bash
./codeseeq install
```

This installs a self-contained snapshot to `~/.config/codeseeq` and creates `~/bin/codeseeq`.
Run `codeseeq` from any project directory. CodeSeeq mounts your current directory into `/workspace`, so changes apply to the directory you launched from, not the CodeSeeq repo.

## Main Usage

Interactive:

```bash
codeseeq
```

Direct prompt:

```bash
codeseeq run "say hi"
```

Yolo mode adds only Codex's approval/sandbox bypass switch, and `run`/`exec`
paths also use `--skip-git-repo-check`:

```bash
codeseeq --yolo run "write ./test.txt with hi"
codeseeq -y
```

Container direct prompt:

```bash
podman run --rm -it \
  -e DEEPSEEK_API_KEY="$DEEPSEEK_API_KEY" \
  -e BRAVE_API_KEY="$BRAVE_API_KEY" \
  -e UNSTRUCTURED_API_KEY="$UNSTRUCTURED_API_KEY" \
  -v "$PWD:/workspace:Z" \
  -w /workspace \
  codeseeq:dev \
  "inspect this repo and summarize the architecture"
```

Container interactive:

```bash
podman run --rm -it \
  -e DEEPSEEK_API_KEY="$DEEPSEEK_API_KEY" \
  -e BRAVE_API_KEY="$BRAVE_API_KEY" \
  -e UNSTRUCTURED_API_KEY="$UNSTRUCTURED_API_KEY" \
  -v "$PWD:/workspace:Z" \
  -w /workspace \
  codeseeq:dev
```

## Commands

- `./codeseeq models`
- `./codeseeq doctor`
- `./codeseeq config`
- `./codeseeq ping`
- `./codeseeq ping-stream`
- `./codeseeq ping-web`
- `./codeseeq ping-docs`
- `./codeseeq shell`

Wrapper options:

- `--model`, `-m`
- `--thinking`
- `--no-thinking`
- `--yolo`, `-y`

## Environment

- `DEEPSEEK_API_KEY` required
- `BRAVE_API_KEY` optional
- `UNSTRUCTURED_API_KEY` optional
- `CODESEEQ_MODEL` default: `deepseek-v4-flash`
- `CODESEEQ_THINKING` default: derived from model alias
- `CODESEEQ_CODEX_HOME` default: `/home/codeseeq/.codeseeq`
- `CODESEEQ_APPROVAL_POLICY` default: `never`
- `CODESEEQ_SANDBOX_MODE` default: `danger-full-access`
- `CODESEEQ_STREAM` default: `true`
- `CODESEEQ_STREAM_IDLE_TIMEOUT_MS` default: `600000`
- `CODESEEQ_REQUEST_MAX_RETRIES` default: `2`
- `CODESEEQ_OPENRESPONSES_HOST` default: `127.0.0.1`
- `CODESEEQ_OPENRESPONSES_PORT` default: `8080`
- `CODESEEQ_OPENRESPONSES_URL` default: `http://127.0.0.1:8080/v1`
- `CODESEEQ_OPENRESPONSES_CMD` default: `/usr/local/bin/codeseeq-bridge.py`

## Supported Models (DeepSeek only)

- `deepseek-v4-flash` (default, thinking off)
- `deepseek-v4-flash-thinking`
- `deepseek-v4-pro`
- `deepseek-v4-pro-thinking`

Normalization also accepts:

- `deepseek@deepseek-v4-flash`
- `deepseek@deepseek-v4-pro`

Any non-DeepSeek model is rejected by the wrapper.

## Model Limits

From official DeepSeek docs:

- Context length: `1M`
- Max output: `384K`
- Streaming: supported
- Tool calls: supported
- Thinking toggle: `thinking.type = enabled|disabled`
- Reasoning effort: `high|max` (compat mapping: `low/medium -> high`, `xhigh -> max`)

Sources:

- https://api-docs.deepseek.com/
- https://api-docs.deepseek.com/quick_start/pricing
- https://api-docs.deepseek.com/guides/thinking_mode
- https://api-docs.deepseek.com/api/list-models
- https://api-docs.deepseek.com/news/news260424
- https://api-docs.deepseek.com/updates

## .env Test Loading

Use this exact safe load pattern before live tests:

```bash
set -a
source .env
set +a
```

Do not modify `.env` in scripts or automation.

## Isolation and Auth

- CodeSeeq uses `/home/codeseeq/.codeseeq` only.
- CodeSeeq does not use or mount host `~/.codex`.
- OpenAI login is not required.
- Secrets are runtime-only and are not written to config files.

## Dangerous Default Mode

Default operation is intentionally autonomous and unsafe:

- `approval_policy = "never"`
- `sandbox_mode = "danger-full-access"`

Codex can execute commands and modify files aggressively.

## Make Targets

- `make build`
- `make inspect-openresponses`
- `make models`
- `make doctor`
- `make ping`
- `make ping-stream`
- `make ping-web`
- `make ping-docs`
- `make prompt PROMPT="Return exactly: codeseeq-ok"`
- `make shell`
- `make smoke`

## Codex References

- Local source: `./codex`
- Upstream: https://github.com/openai/codex.git
- Docs:
  - https://developers.openai.com/codex
  - https://developers.openai.com/codex/cli
  - https://developers.openai.com/codex/config-basic
  - https://developers.openai.com/codex/config-reference
  - https://developers.openai.com/codex/cli/reference

## Troubleshooting, Architecture, Security

- [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md)
- [`docs/TROUBLESHOOTING.md`](./docs/TROUBLESHOOTING.md)
- [`docs/SECURITY.md`](./docs/SECURITY.md)

## License

This repository is licensed under the GNU Affero General Public License v3.0 or later (AGPL-3.0-or-later).

- Full license text: [`LICENSE`](./LICENSE)
- Copyright notices: [`COPYRIGHT`](./COPYRIGHT)

If you modify this software and offer it to users over a network, AGPL section 13 requires offering corresponding source to those users.
