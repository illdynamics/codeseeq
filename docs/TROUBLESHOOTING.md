# Troubleshooting

Current version: `0.2.5`

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
./codeseeq --sanbox danger-full-access "say hi"
```

In that mode CodeSeeq starts a bridge container, then runs local host `codex` with `CODEX_HOME=$PWD/.codeseeq`.

## Local Codex Missing In Danger Mode

Install Codex locally:

```bash
npm install -g @openai/codex
```

CodeSeeq will not silently fall back to container Codex for danger-full-access, because that would not be host full access.

## Bridge Port Conflict

Danger host mode publishes the bridge on:

```text
http://127.0.0.1:${CODESEEQ_OPENRESPONSES_PORT:-8080}/v1
```

Change the port:

```bash
CODESEEQ_OPENRESPONSES_PORT=18080 ./codeseeq -y "say hi"
```

If startup fails, inspect the bridge container logs:

```bash
podman logs codeseeq-bridge-8080
docker logs codeseeq-bridge-8080
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

## System Prompt Missing

Check:

```bash
./codeseeq system view
./codeseeq doctor
```

Expected storage path:

```text
$PWD/.codeseeq/system-prompt.md
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
make inspect-openresponses
```

Use strict inspection only when the source checkouts are intentionally present:

```bash
make inspect-openresponses-strict
```
