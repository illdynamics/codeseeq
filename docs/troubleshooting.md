# Troubleshooting

Current version: `0.2.5`

## `./codeseeq` is not executable

```bash
chmod +x ./codeseeq
```

## Image missing when running `./codeseeq`

The host wrapper auto-builds by default. Manual build:

```bash
podman build -t codeseeq:dev .
```

## Podman missing

Install Podman. CodeSeeq does not silently switch to Docker.

Optional escape hatch only:

```bash
CONTAINER=docker ./codeseeq doctor
```

## `.env` missing

Live smokes requiring provider keys will skip/fail. Use `.env.example` as a template.

## `.env` not loaded before tests

Load safely:

```bash
set -a
source .env
set +a
```

## `.env` accidentally modified

Restore from your own secret management source and keep automation read-only. Do not run formatting or rewrite scripts against `.env`.

## `DEEPSEEK_API_KEY` missing

Required for `run`, `ping`, `ping-stream`, and prompt execution.

## `BRAVE_API_KEY` missing when using web search

`ping-web` requires it. Without it, web tool path is disabled.

## `UNSTRUCTURED_API_KEY` missing when using doc parsing

`ping-docs` requires it. Without it, doc parsing path is disabled.

## `ping-web` fails

Check:

- `BRAVE_API_KEY` is set
- outbound network works
- Brave endpoint not rate-limited for your key

Then retry:

```bash
./codeseeq ping-web
```

## `ping-docs` fails

Check:

- `UNSTRUCTURED_API_KEY` is set
- outbound network works
- Unstructured account quota/limits

Then retry:

```bash
./codeseeq ping-docs
```

## OpenResponses npm/npx install failed

Retry image build with network access. Confirm package resolves:

```bash
podman run --rm codeseeq:dev open-responses --help
```

## OpenResponses `setup` failed or interactive

Expected for upstream CLI: it is compose-oriented and interactive by design. CodeSeeq runtime does not depend on `open-responses setup/start`.

## `./open-responses` missing or wrong remote

Expected remote:

```bash
git -C open-responses remote -v
# should include https://github.com/open-responses/open-responses.git
```

## Wrong/old OpenResponses implementation still referenced

Run:

```bash
rg -n "masaic|openresponses/openresponses|julepai/open-responses|docker-compose|openresponses:6644" README.md docs Dockerfile Makefile bin scripts codeseeq
```

Update operational docs/scripts to only supported runtime paths.

## OpenResponses CLI install/start failure

`open-responses start` expects compose setup and Docker services. In this repository, single-container mode uses `/usr/local/bin/codeseeq-bridge.py` as `CODESEEQ_OPENRESPONSES_CMD`.

## OpenResponses does not expose expected tools

In this runtime, web/doc smoke paths are implemented by bridge-backed external calls. Validate keys and run `ping-web` / `ping-docs`.

## OpenResponses start command cannot be detected

Set explicit command:

```bash
CODESEEQ_OPENRESPONSES_CMD=/usr/local/bin/codeseeq-bridge.py ./codeseeq doctor
```

## Wrong OpenResponses port

Set both port and URL:

```bash
CODESEEQ_OPENRESPONSES_PORT=8080 \
CODESEEQ_OPENRESPONSES_URL=http://127.0.0.1:8080/v1 \
./codeseeq doctor
```

## OpenResponses health endpoint missing

CodeSeeq expects `${CODESEEQ_OPENRESPONSES_URL%/v1}/health`. Use `doctor` to verify health status.

## Streaming/chunking smoke fails

Run:

```bash
./codeseeq ping-stream
```

If it fails, verify:

- DeepSeek key validity
- bridge health
- no proxy/network interruption

## Raw DSML appears in Codex output

This should be fixed in `0.2.5` for standard DSML, split
`<____DSML____...>` blocks, and fullwidth-pipe `<｜｜DSML｜｜...>` blocks.

Check the installed command is current:

```bash
codeseeq config
cat ~/.config/codeseeq/VERSION
```

Then reinstall from the repo if needed:

```bash
./codeseeq install
```

## `OutputTextDelta without active item`

Current bridge streams include `output_index` metadata expected by recent
Codex builds. If this appears after upgrading, rebuild and reinstall the
container snapshot:

```bash
make build
./codeseeq install
```

## Codex asks for OpenAI login

Inspect config:

```bash
./codeseeq config
```

Expected provider fields:

- `model_provider = "codeseeq"`
- `wire_api = "responses"`
- `env_key = "DEEPSEEK_API_KEY"`
- `requires_openai_auth = false`

## CodeSeeq using `.codex` instead of `.codeseeq`

Expected config path:

- `/home/codeseeq/.codeseeq/config.toml`

Override explicitly if needed:

```bash
CODESEEQ_CODEX_HOME=/home/codeseeq/.codeseeq ./codeseeq doctor
```

## Codex hits wrong endpoint

Check `base_url` in `./codeseeq config` output. It must be local bridge `/v1` URL.

## Unsupported `wire_api`

Must be `responses`. `chat` is rejected by current Codex provider parsing.

## Non-DeepSeek model rejected

Supported logical models only:

- `deepseek-v4-flash`
- `deepseek-v4-flash-thinking`
- `deepseek-v4-pro`
- `deepseek-v4-pro-thinking`

## Model unsupported by bridge

Set one of the supported models above or provider-form aliases:

- `deepseek@deepseek-v4-flash`
- `deepseek@deepseek-v4-pro`

## Official model limits unknown/changed

Re-check official DeepSeek docs and update `config/model-catalog.json`. Do not guess values.

## `/model` menu not fully restricted

CodeSeeq sets a DeepSeek-only catalog via `model_catalog_json`. If a Codex release still shows additional entries, wrapper-level validation remains authoritative and non-DeepSeek requests are rejected.

## Thinking mode and tool loops fail

DeepSeek V4 thinking mode requires preserving `reasoning_content` across tool-call turns. If upstream behavior changes, tool-call loops may 400 until compatibility logic is adjusted.

## Podman SELinux bind mount errors

Use `:Z` on bind mounts. `./codeseeq` already does this for Podman.

## Rootless Podman networking issues

Set explicit localhost bridge vars and retry:

```bash
CODESEEQ_OPENRESPONSES_HOST=127.0.0.1 \
CODESEEQ_OPENRESPONSES_PORT=8080 \
./codeseeq doctor
```

## `danger-full-access` rejected by Codex version

Current CodeSeeq maps `danger-full-access` launches to Codex's
`--dangerously-bypass-approvals-and-sandbox` switch and avoids the conflicting
`--ask-for-approval` flag. If this still fails, check the installed Codex
version with `codeseeq doctor` and rerun `make build`.

## Requested behavior differs from `./codex` source

Prioritize source-verified behavior from local `./codex` over stale docs.

## Local docs are insufficient

Use upstream sources:

- https://github.com/open-responses/open-responses
- https://docs.julep.ai/responses/quickstart
- https://github.com/openai/codex.git
- https://developers.openai.com/codex
- https://api-docs.deepseek.com/
