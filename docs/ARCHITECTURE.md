# Architecture

Current version: `0.2.5`

## Runtime

CodeSeeq supports one runtime path:

- one OCI image (`codeseeq:dev`)
- one container process tree
- Podman-first execution

No supported Docker Compose or two-container path remains.

## Flow

```text
host ./codeseeq
  -> podman run codeseeq:dev
  -> codeseeq-entrypoint
  -> local bridge (/usr/local/bin/codeseeq-bridge.py)
  -> http://127.0.0.1:8080/v1/responses
  -> DeepSeek API (https://api.deepseek.com/chat/completions)
```

Codex is pointed at the local bridge via custom model provider config with `wire_api = "responses"`.

## Components

1. `@openai/codex` CLI
- Installed in the image via npm.
- Runs interactive mode (`codex`) or direct prompt mode (`codex exec`).

2. `open-responses` CLI package
- Installed in the image for OpenResponses ecosystem parity.
- Current upstream CLI is compose-driven (`setup/start`), so it is not the runtime server in this single-container design.

3. `codeseeq-bridge.py`
- In-container FastAPI service implementing `/v1/responses`.
- Normalizes allowed DeepSeek model aliases.
- Forwards request content to DeepSeek Chat Completions.
- Emits Responses-compatible output and SSE stream events.
- Translates structured DeepSeek tool calls and DSML/XML fallback tool-call
  text into Codex-compatible `function_call` items.
- Converts Codex Responses top-level function tool specs into DeepSeek's nested
  Chat Completions function-tool shape.
- Normalizes display-mangled DSML from Codex history and emits stable
  `output_index` metadata in streaming lifecycle events.
- Includes bridge-level smoke endpoints for web/doc integration checks.

4. `codeseeq-entrypoint`
- Validates env.
- Generates isolated Codex config under `/home/codeseeq/.codeseeq/config.toml`.
- Starts the local bridge and waits for health.
- Dispatches `run`, `ping`, `ping-stream`, `ping-web`, `ping-docs`, `doctor`, `models`, `config`, `shell`.
- Supports `--yolo` / `-y`, which adds only Codex's `--dangerously-bypass-approvals-and-sandbox` launch switch; `run`/`exec` paths also add `--skip-git-repo-check`.

5. Host wrapper `./codeseeq`
- Podman-first launcher.
- Auto-builds image if missing.
- Mounts current project to `/workspace:Z`.

## Config Isolation

- Default `CODESEEQ_CODEX_HOME=/home/codeseeq/.codeseeq`
- Entry point exports `CODEX_HOME=$CODESEEQ_CODEX_HOME`
- No supported path uses host `~/.codex`

## Auth Model

- Required key: `DEEPSEEK_API_KEY`
- Optional keys: `BRAVE_API_KEY`, `UNSTRUCTURED_API_KEY`
- No `codex login` requirement
- No OpenAI auth requirement for provider

## Model Strategy

CodeSeeq exposes only four logical model choices:

- `deepseek-v4-flash`
- `deepseek-v4-flash-thinking`
- `deepseek-v4-pro`
- `deepseek-v4-pro-thinking`

Provider-facing model IDs are normalized to:

- `deepseek@deepseek-v4-flash`
- `deepseek@deepseek-v4-pro`

## OpenResponses Source of Truth

- local repo: `./open-responses`
- upstream: `https://github.com/open-responses/open-responses`
- docs: `https://docs.julep.ai/responses/quickstart`

Observed behavior in upstream source/docs: CLI setup/start is Docker Compose based. This is the reason CodeSeeq runtime uses an in-container bridge process for single-container operation.
