## Unreleased

### Fixed
- **Hide benign upstream Codex rollout-persistence teardown errors in one-shot
  runs.** Modern Codex CLI builds (>= 0.146, the session/thread-store era) can
  print
  `ERROR codex_core::session: failed to record rollout items: thread <uuid> not found`
  when a turn finishes while session persistence is shutting down (the live
  thread recorder is already removed, so the final rollout append races
  teardown). The turn itself succeeded; only tail persistence is skipped.
  `codeseeq run`/piped `codex` passthrough now filter that known-benign line
  from user-facing stderr (upstream openai/codex #22055, #16300, #35385).
  Set `CODESEEQ_KEEP_CODEX_ROLLOUT_ERRORS=true` to see the raw upstream line.
- **File-write guidance for LLM providers.** The bundled system prompt now
  instructs agents to use a QUOTED heredoc delimiter (`cat <<'EOF'`) when a
  file's content contains backticks/`$`/backslashes (Markdown, code, JSON,
  LaTeX), and to verify every created file with `ls -l` + `cat`/`head` before
  claiming success. Unquoted `<<EOF` makes the shell run backticks and
  `$(...)` inside the content as commands, corrupting the write - the root
  cause of "agent said it created the file but no file exists" failures with
  weaker local models.

## v0.4.8 - 2026-09-05

### Added
- **ChatGPT account sign-in provider (`chatgpt@<model>` / `CODESEEQ_PROVIDER=chatgpt`).**
  CodeSeeq can now drive OpenAI Codex with a **ChatGPT Plus / Pro / Team
  account** - the upstream `codex login` -> "Sign in with ChatGPT" OAuth flow -
  instead of an `OPENAI_API_KEY` or any other provider API key. Selecting the
  new `chatgpt` provider makes CodeSeeq bypass the local bridge entirely and
  generate a native Codex config (`model_provider = "openai"`, upstream
  Codex's built-in ChatGPT-auth provider), so requests go straight to
  OpenAI's ChatGPT backend (`https://chatgpt.com/backend-api/codex`) with the
  ChatGPT session stored in `CODEX_HOME/auth.json`.
  - Setup: `codeseeq config` (choose "ChatGPT (Plus/Pro/Team account
    sign-in)", pick e.g. `chatgpt@gpt-5-codex`), then `codeseeq login` and
    choose **Sign in with ChatGPT** in the browser. No API key is stored or
    required. Log out with `codeseeq logout`.
  - Usage: `codeseeq -m chatgpt@gpt-5-codex "prompt"`,
    `codeseeq run -f task.md`, interactive TUI, etc. Host runtime is forced
    automatically (the login session must persist next to the workspace
    CODEX_HOME; container CODEX_HOME is ephemeral with `--rm`), matching the
    GGUF/MLX host-forcing behavior.
  - Both catalogs (`config/model-catalog.json`,
    `config/codex-model-catalog.json`) and the `codeseeq config` wizard gained
    the `chatgpt` provider with the ChatGPT Codex model family
    (`gpt-5-codex`, `gpt-5.1-codex`, `gpt-5.2-codex`, `gpt-5.3-codex`).
  - **Privacy default preserved:** upstream `login` / `logout` stay blocked
    for every non-chatgpt provider. They are auto-allowed only while the
    `chatgpt` provider is active (or with
    `CODESEEQ_ALLOW_UPSTREAM_CODEX_SERVICES=true` as before). `cloud`, `app`,
    `app-server`, `plugin`, `update`, `features` remain blocked regardless.
  - `./codeseeq login` / `logout` now run upstream Codex against the isolated
    host `CODEX_HOME` (`<workdir>/.codeseeq`) so the ChatGPT session persists
    for the bridge-free chatgpt path; container mode is not used for auth.

### Changed
- `codeseeq config`, `codeseeq doctor`, host diagnostics and generated
  configs understand the keyless `chatgpt` provider (no API-key screen; auth
  state reported as ChatGPT account sign-in).
- CodeSeeq keeps its no-OpenAI default: unless the operator explicitly picks
  the `chatgpt` provider, nothing contacts OpenAI/ChatGPT services.

## v0.4.7 - 2026-09-04

### Added
- **MLX local-model provider (`mlx@<path-to-model-directory>`).** CodeSeeq can
  now run Codex against local Apple MLX model directories (MLX conversions with
  `config.json` + `*.safetensors`), not just GGUF files. The bridge lazily
  launches Apple's `mlx_lm.server` on a free loopback port (or a pinned
  `CODESEEQ_MLX_PORT`), health-checks it, reuses one server per directory, and
  tears it down on shutdown / parent-death - the same lifecycle llama.cpp has
  for GGUF. No API key is required, and host runtime + host process bridge are
  forced automatically (like GGUF) so the directory resolves on the host.
  Example:
  `CODESEEQ_RUNTIME_MODE=host codeseeq -m mlx@~/Qoding/ai/My-Model-mlx-4bit -y "prompt"`
- **MLX tuning knobs mirror the GGUF knobs.** Per-model values in
  `config/mlx-models.json` win over `CODESEEQ_MLX_*` env vars, which win over
  the model's own `config.json` (`max_position_embeddings` /
  `model_max_length`, including nested `text_config` for multimodal
  conversions), which win over built-in defaults. Supported per-model keys:
  `context_window`, `max_output_tokens`, `port`, `timeout_seconds`,
  `temperature`, `top_p`, `top_k`, `enable_thinking`, `server_args`. Global
  `CODESEEQ_TEMPERATURE` is honored as a generic sampling fallback.
- **Reuse an already-running MLX server.** Set `CODESEEQ_MLX_BASE_URL` or
  `MLX_BASE_URL` (or a loopback `OPENAI_BASE_URL` / `CODESEEQ_BASE_URL`, with
  or without a trailing `/v1`) to route the mlx provider to an existing
  OpenAI-compatible server instead of spawning one:
  `CODESEEQ_BASE_URL=http://127.0.0.1:8888/v1 CODESEEQ_RUNTIME_MODE=host codeseeq -m mlx@<dir> -y "prompt"`
- **`codeseeq config` provider + catalogs.** The config wizard now offers an
  "MLX (local Apple MLX)" provider that prompts for a model directory (and an
  optional external-server base URL), and both `model-catalog.json` /
  `codex-model-catalog.json` gained an `mlx` provider and `mlx@local-model`
  placeholder so the merged Codex catalog stays in sync with the resolved
  context window at runtime.

### Changed
- GGUF / MLX external base-URL derivation no longer doubles a trailing `/v1`
  (e.g. `http://127.0.0.1:8888/v1` now maps to
  `http://127.0.0.1:8888/v1/chat/completions`).
- Bridge `/health` reports `mlx_available` and exposes `mlx` in `providers`;
  `/v1/models` lists the `mlx@local-model` placeholder.

## v0.4.6 - 2026-09-01

### Fixed
- **Codex CLI JSON parse crash on `max` reasoning level** (critical boot fix).
  `codex-model-catalog.json` used `"level": "max"` for DeepSeek V4 Flash Thinking
  and DeepSeek V4 Pro Thinking entries. Codex CLI v0.130.0's Rust parser only
  accepts the `ReasoningLevel` enum variants `none | minimal | low | medium | high | xhigh`;
  `max` is not a valid variant, causing an immediate fatal parse error on every
  launch when either of those models was referenced. Fixed by renaming the catalog
  token to `"level": "xhigh"` (the Codex ceiling) while keeping `"effort": "max"`
  so the bridge still sends the correct `reasoning_effort: max` value to the
  DeepSeek API.
- **`normalize_deepseek_reasoning_effort`: `xhigh` now maps to `"max"`** (not `"high"`).
  The previous mapping collapsed Codex's `xhigh` token to DeepSeek's `high` effort,
  silently downgrading any request for maximum reasoning. Now `xhigh` correctly
  maps to `max` — the top DeepSeek effort tier — making `-R max` / `-R xhigh`
  behave identically and deliver full reasoning power. The `ultra` alias
  (Codex `model_reasoning_effort` ceiling) is unchanged and also maps to `max`.

## v0.4.5 - 2026-08-28

### Added
- **DeepSeek reasoning-effort control (`-R` / `CODESEEQ_REASONING_EFFORT`).**
  A new `-R low|high|max` (also `--reasoning` / `--reasoning-effort`) command-line
  switch sets DeepSeek's `reasoning_effort` for the run and overrides any
  config-file default. `codeseeq config` now records a default reasoning effort
  for DeepSeek (written as `CODESEEQ_REASONING_EFFORT` in the JSON config), and
  `codeseeq config status` displays it. The bridge only forwards
  `reasoning_effort` for DeepSeek models (flash, pro, and their `-thinking`
  variants) and leaves other providers untouched.
- **Correct DeepSeek `reasoning_effort` mapping.** Per the DeepSeek API docs the
  accepted levels are `low`, `high` and `max` (default `high`). `medium` and
  `xhigh` are compatibility aliases for `high`; the legacy `minimal` value is
  treated as `low`. The previous mapping (which collapsed `low` to `high` and
  promoted `xhigh` to `max`) is fixed.

### Changed
- `CODESEEQ_REASONING_EFFORT` is now forwarded through the bridge launcher,
  in-container entrypoint, and Codex container environment so the value set via
  `-R` or config actually reaches the DeepSeek provider.

## v0.4.4 - 2026-08-28

### Added
- **GGUF llama-server fixed port (`CODESEEQ_GGUF_PORT`).** The GGUF bridge can
  now pin the llama-server loopback port via `--port` (e.g. `8888`) instead of
  auto-selecting a free port. The llama-server tuning flags `-c` (context),
  `-ngl` (GPU layers), `-np` (parallel) and `--port` are all settable via
  environment variables or JSON config keys (precedence: env > JSON config >
  default): `CODESEEQ_GGUF_CONTEXT_WINDOW`, `CODESEEQ_GGUF_N_GPU_LAYERS`,
  `CODESEEQ_GGUF_PARALLEL`, and `CODESEEQ_GGUF_PORT`. `.env.example` documents
  the 1:1 flag mapping.

## v0.4.3 - 2026-08-28

### Added
- **Local GGUF model support (`gguf` provider).** Select a model by its full
  `.gguf` path via `--model /path/to/model.gguf`, `CODESEEQ_MODEL=/path/to/model.gguf`,
  or the explicit `gguf@/path/to/model.gguf` slug. The bridge lazily launches
  llama.cpp `llama-server` on a free loopback port, health-checks `/health`,
  reuses one server per path per bridge process, and tears it down via `atexit`
  + the parent-death watchdog so a killed wrapper cannot leak an inference
  process. `codeseeq config` gains a "GGUF (local llama.cpp)" provider that
  prompts for the model path.
- **GGUF tuning knobs.** `CODESEEQ_GGUF_CONTEXT_WINDOW`,
  `CODESEEQ_GGUF_MAX_OUTPUT_TOKENS`, `CODESEEQ_GGUF_N_GPU_LAYERS`,
  `CODESEEQ_GGUF_THREADS`, `CODESEEQ_GGUF_PARALLEL`,
  `CODESEEQ_GGUF_TIMEOUT_SECONDS`, `CODESEEQ_GGUF_STARTUP_TIMEOUT_SECONDS`,
  `CODESEEQ_GGUF_ENABLE_THINKING`, `CODESEEQ_GGUF_LLAMA_SERVER_PATH`, and the
  optional `GGUF_BASE_URL` / `GGUF_API_KEY` are documented in `.env.example`.

## v0.4.2 - 2026-08-19

### Added
- **`codeseeq config` interactive setup wizard.** A three-screen TUI that walks
  you through provider selection (`anthropic`, `google`, `grok`, `deepseek`,
  `venice`, `local`) → model selection → API key, then writes
  `~/.config/codeseeq/config.json` so CodeSeeq is ready to use. API providers
  get a live model list from the catalog; the local provider accepts any typed
  model name. The API-key screen is required for hosted providers and can be
  left empty for local gateways (an optional `LOCAL_API_KEY` is stored and
  honoured by the bridge when set; the local gateway base URL is also
  configurable). `codeseeq config status` prints the current configuration
  without revealing keys.
- **Multi-provider bridge.** The bridge now routes requests to DeepSeek
  (OpenAI-compatible), Anthropic Claude (native Messages API, including
  extended thinking and tool use), Google Gemini (OpenAI-compatible endpoint),
  Grok/xAI, Venice.ai, and arbitrary local OpenAI-compatible gateways. Model
  slugs use the `provider@model` form (`anthropic@claude-sonnet-4`,
  `local@llama-4-maverick`); unknown `<provider>@<model>` names are accepted and
  routed to the provider's base URL (overridable via `ANTHROPIC_BASE_URL`,
  `GOOGLE_BASE_URL`, `GROK_BASE_URL`, `VENICE_BASE_URL`, `LOCAL_BASE_URL`).
- **Provider-aware model catalogs.** `config/model-catalog.json` and
  `config/codex-model-catalog.json` now include Anthropic, Google, Grok, Venice
  and local models; the generated Codex catalog is merged with the configured
  model so Codex accepts arbitrary locally-typed model names.
- **`CODESEEQ_ALLOW_LATEST_RELEASE` is now real.** The one-line installer
  honors it: `false` requires an explicit pinned `CODESEEQ_RELEASE_TAG`;
  default remains auto-fetch of the latest release (docs updated to match).
- **Installer/package exclude dev artifacts.** `oops.md`, `codeseeq-*-fix.md`,
  `venice-image.md`, `package.json`, `package-lock.json` and `.qq*` are no
  longer shipped in install snapshots or release zips.

### Fixed
- **Anthropic tool-loop translation.** Assistant `tool_calls` from previous
  turns are now forwarded to the Messages API as `tool_use` content blocks, so
  `tool_result` blocks can reference them. Previously the tool calls were
  dropped and Anthropic rejected the multi-turn request (every second agentic
  turn 400'd for Claude models).
- **Anthropic extended-thinking constraints.** `temperature` / `top_p` are no
  longer sent alongside `thinking` (Anthropic rejects them), `max_tokens` is
  raised to at least the thinking budget, and a specific `tool_choice` is
  downgraded to `"auto"` while thinking is enabled. `claude-*-thinking`
  variants now produce valid requests.
- **Per-model env overrides reach the bridge.** The wrapper now forwards
  `CODESEEQ_<MODEL>_*` overrides (e.g. `CODESEEQ_CLAUDE_SONNET_4_BASE_URL`)
  matching the bridge's `provider@model` slug key scheme; the old
  provider-prefixed names (`CODESEEQ_ANTHROPIC_CLAUDE_SONNET_4_*`) were
  forwarded but ignored.
- **Provider base-URL overrides now affect the chat endpoint.** Setting
  `ANTHROPIC_BASE_URL`, `GOOGLE_BASE_URL`, `GROK_BASE_URL`, `VENICE_BASE_URL`,
  `LOCAL_BASE_URL` or a per-model `CODESEEQ_<MODEL>_BASE_URL` re-points the
  actual chat request (Anthropic -> `/v1/messages`, others ->
  `/chat/completions`), not just the displayed base URL.
- **`CODESEEQ_PROVIDER` override is honored end-to-end.** The bridge routes to
  the overridden provider (key env + base URL) and the wrapper/entrypoint
  require the matching key, so the two can never disagree. `/health` reports
  the effective provider of the configured model.
- **`codeseeq build` / `codeseeq install` no longer trust a stale `CODESEEQ_INSTALL_ROOT`.**
  The build context and the install source are now always derived from the
  running script's own directory (`SCRIPT_DIR`), so a `CODESEEQ_INSTALL_ROOT`
  that points at `~/.config/codeseeq` (e.g. left over from an older shell or
  harness) can no longer make `codeseeq build` hunt for the Dockerfile in the
  config dir or make `codeseeq install` die with "CODESEEQ_INSTALL_DIR cannot
  be inside source repo". `CODESEEQ_BUILD_CONTEXT` remains the explicit
  override for the image build context.
- **Config wizard jq fallback lists DeepSeek models.** Without python3 the
  numbered model menu now includes the DeepSeek models (the previous jq query
  only used the `providers.<p>.models` list, which is empty for deepseek).
- **Config wizard never drops unrelated config keys.** Re-running `codeseeq
  config` (e.g. switching providers) previously read the freshly-created temp
  file instead of the existing `config.json`, silently discarding every
  unrelated key (`BRAVE_API_KEY`, `UNSTRUCTURED_API_URL`, ...). The merge now
  reads the existing file and only clears stale provider keys.
- **Config wizard model list falls back gracefully.** A python3/jq failure no
  longer yields an empty model menu ("no models found ..."); the wizard now
  falls through to the next available source (python3 → jq → static list).
- **Check suite sanitizes the new config env vars.** The deterministic-env
  sanitization now also clears `CODESEEQ_CONFIG_HOME`, `CODESEEQ_CONFIG_JSON`
  and the new `LOCAL_API_KEY`, so the config-wizard checks cannot be skewed by
  an ambient environment.
- **Docs/examples pinned to v0.4.2.** README pinned-install examples and the
  install.sh error hint now reference v0.4.2, and the `.env.example` documents
  the per-model key for `local@<model>` correctly
  (`CODESEEQ_LLAMA_4_MAVERICK_BASE_URL`, not `CODESEEQ_LOCAL_BASE_URL`).
- **Local gateway chat endpoint now correct under `LOCAL_BASE_URL`.** The
  bridge previously derived the chat URL as `<base>/chat/completions` whenever
  a provider base-URL override was set, so a wizard-configured local provider
  (`codeseeq config` → local → typed model → `LOCAL_BASE_URL`) called
  `<base>/chat/completions` instead of `<base>/v1/chat/completions` — a 404 on
  Ollama/LM Studio/vLLM. Chat endpoints are now derived per provider
  (local `/v1/chat/completions`, grok `/v1/chat/completions`, venice
  `/api/v1/chat/completions`, google `/v1beta/openai/chat/completions`,
  anthropic `/v1/messages`, deepseek `/chat/completions`) for both catalog
  models and arbitrary `<provider>@<model>` slugs, including under
  `CODESEEQ_PROVIDER` overrides.
- **Per-model overrides now work for arbitrary `local@<model>` slugs.** The
  bridge's generic `<provider>@<model>` path (any model typed in the wizard)
  now honours `CODESEEQ_<MODEL>_*` overrides
  (`CODESEEQ_LLAMA_4_MAVERICK_BASE_URL`, `_CHAT_URL`, `_TEMPERATURE`,
  `_TOP_P`, `_TOP_K`, `_MAX_OUTPUT_TOKENS`, `_TIMEOUT_SECONDS`,
  `_ENABLE_THINKING`, `_SYSTEM_PROMPT`) exactly like catalog models, and the
  wrapper forwards them into containers/standalone bridges for the configured
  model.
- **Config wizard keeps an env-only API key on empty Enter.** Re-running
  `codeseeq config` with the provider key set only in the environment (which
  always overrides the config at runtime) previously failed with "API key is
  required" when Enter was pressed; the env key is now treated as the existing
  key and stored.
- **Bridge returns 502 (not 500) when an upstream is unreachable.** A dead
  local gateway (or any connect failure to the provider) now yields a clean
  `502 cannot reach upstream at <url>` response — and a `response.failed`
  SSE event on streaming requests — instead of an unhandled 500 traceback.
- **Orphaned bridge containers are reaped on startup.** A SIGKILLed wrapper
  in container-bridge mode previously leaked the standalone bridge container,
  which held its host-port mapping forever and exhausted the auto-select range
  exactly like the orphaned processes v0.4.1 fixed. `start_bridge_container`
  now removes `codeseeq-bridge-<port>-<owner-pid>` containers whose owner PID
  is gone (never touching live owners, and disabled entirely by
  `CODESEEQ_KEEP_BRIDGE_CONTAINER=true`).
- **macOS bash 3.2 compatibility for bridge reaping.** The startup reaper that
  removes orphaned `codeseeq-bridge-<port>-<owner-pid>` containers referenced
  `$BASHPID`, which the bash 3.2 shipped with macOS does not define; under `set
  -u` the wrapper aborted with "BASHPID: unbound variable" before any reaping
  could happen (and the same crash risk existed in the process-bridge reaper).
  The reaper now derives the current shell PID portably — `BASHPID` when
  available, otherwise the PPID of a child `sh` (verified equivalent to
  `BASHPID` on bash 4+) — so orphaned-bridge cleanup works on macOS and other
  older-bash systems.

### Changed
- **Provider keys are per-provider.** `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`,
  `GROK_API_KEY`, `VENICE_API_KEY` and `DEEPSEEK_API_KEY` are each validated
  only when their provider is active; local gateways need no key.
- **README/doc URLs point at `illdynamics/codeseeq`.** The one-liner, clone and
  releases links in the README no longer 404 (the historical `codeseeq/codeseeq`
  org did not exist).

### Known limitations
- **Anthropic extended thinking + tool use across turns.** Anthropic requires
  the assistant `thinking` signature to be passed back on every subsequent turn
  when extended thinking and tools are combined. The stateless bridge cannot
  persist signatures, so multi-turn agentic runs with `claude-*-thinking`
  variants can fail at the API; single-turn thinking works. Use the
  non-thinking Claude variants for multi-turn tool-driven work.

## v0.4.1 - 2026-08-18

### Fixed
- **Bridge processes can no longer be orphaned (port leaks).** Each
  `codeseeq run` starts its own bridge process and relies on a bash EXIT
  trap to stop it. When the owning process was killed hard (SIGKILL /
  process-group teardown, e.g. an agent-call timeout in a pipeline such as
  QonQrete), that trap could not run and the bridge was reparented to PID 1,
  holding its port forever. After enough runs the auto-select range
  (default 8080-8179) filled up and every new run failed with
  "no free bridge port found in range 8080-8179". The bridge now runs a
  parent-death watchdog thread that shuts it down and releases the port the
  moment its parent process disappears — even under SIGKILL.
- **Stale bridges are reaped on startup.** Before starting a new process
  bridge, CodeSeeq scans for `codeseeq-bridge.py` processes that (a) write
  to the same bridge log and (b) are orphaned (parent already gone), and
  terminates them, so ports leaked by older versions or by hard-killed runs
  are reclaimed automatically. Bridges whose owner is still alive (including
  `CODESEEQ_BRIDGE_REUSE=1` setups) are never touched.

## v0.3.9 - 2026-08-17

### Fixed
- **Parallel `codeseeq` now binds ports reliably.** The bridge no longer relies
  on a connect-probe (TOCTOU race) to pick a port. When `CODESEEQ_BRIDGE_PORT`
  is unset it performs a real `bind()` starting at `CODESEEQ_OPENRESPONSES_PORT`
  and increments up to `CODESEEQ_OPENRESPONSES_PORT_SCAN_LIMIT`, writing the
  chosen port to `CODESEEQ_BRIDGE_PORT_FILE`. Multiple simultaneous invocations
  now obtain distinct ports without crashing on "address already in use".
- **Decoupled container bridge ports.** The standalone bridge container now
  binds a fixed internal port (`CODESEEQ_BRIDGE_CONTAINER_PORT`, default `8080`)
  and is mapped to the auto-selected host port, eliminating the
  host-port == container-port collision.
- **`CODESEEQ_STREAM_IDLE_TIMEOUT_MS` is now actually enforced.** The bridge
  applies it as an idle (between-chunk) read timeout on streaming responses, so
  a stalled upstream stream or client can no longer hang a uvicorn task
  indefinitely (a timeout now yields a `response.failed` event).
- **Per-model env overrides now work for every model.** The bridge previously
  generated per-model env keys from the full `provider@model` slug
  (e.g. `CODESEEQ_QWIBUS_QWIBUS_QWIKK_BASE_URL` instead of the documented
  `CODESEEQ_QWIBUS_QWIKK_BASE_URL`), so qwibus (and other) per-model overrides
  were silently ignored. The provider prefix is now stripped, and explicit
  env/config values correctly take precedence over the catalog.
- **Container runtime no longer drops qwibus/provider config.** The env
  collection now forwards generic base-URL overrides and all per-model
  `CODESEEQ_<MODEL>_*` knobs into the Codex container, so qwibus endpoints can
  be configured in container mode.
- **Removed dead session-tracking code** (`_active_sessions`,
  `_session_is_known`, `_prune_expired_sessions`, `CODESEEQ_SESSION_TTL_SECONDS`)
  which had no callers.

### Added
- **JSON configuration alternative.** Every environment variable can now be
  supplied through a JSON config file (keys are the literal env-var names).
  Precedence is environment variable > JSON config > built-in default. Config
  path is `CODESEEQ_CONFIG_JSON` (default `~/.config/codeseeq/config.json`,
  or `/home/codeseeq/.config/codeseeq/config.json` in the container).
- **Catalog clarification.** `config/model-catalog.json` (bridge) and
  `config/codex-model-catalog.json` (Codex TUI) are documented as two distinct,
  both-used catalogs; neither is redundant.

### Changed
- **Health endpoint** reports version `0.3.9`.
- **Version bump to v0.3.9.** All docs, configs, and internal version strings
  updated.

---

## v0.3.8 - 2026-08-17

### Added
- **Two new qwibus models.** `qwibus-qwikk` (quick, no reasoning) and
  `qwibus-qmplx` (heavy reasoning/thinking) run against a local
  OpenAI-compatible gateway (default `http://127.0.0.1:1337`) and require no
  API key.
- **Per-model endpoint + sampling configuration.** The bridge now supports
  per-model `base_url`, `chat_url`, `temperature`, `top_p`, `top_k`,
  `context_window`, `max_output_tokens`, `timeout_seconds`, `enable_thinking`,
  and `system_prompt`. Defaults are sourced from `config/model-catalog.json`
  (single source of truth), with per-model `CODESEEQ_<KEY>_*` env overrides and
  generic `OPENAI_BASE_URL` / `DEEPSEEK_BASE_URL` / `CODESEEQ_BASE_URL`
  fallbacks.
- **DeepSeek per-model defaults.** `deepseek-v4-flash` timeout 60s,
  `deepseek-v4-pro` 120s, `deepseek-v4-flash-thinking` 600s, and
  `deepseek-v4-pro-thinking` 1200s, all with 1M context and 384K max output.
  `enable_thinking` is `true` on the two thinking variants.

### Changed
- **Health endpoint** reports version `0.3.8`.
- **Version bump to v0.3.8.** All docs, configs, and internal version strings
  updated.

---

## v0.3.7 - 2026-07-14

### Changed
- **Venice image backend auto-detection.** When `VENICE_API_KEY` is set,
  CodeSeeq automatically enables the Venice backend without requiring
  `CODESEEQ_IMAGE_BACKEND=venice`. The launcher, entrypoint, and bridge
  all auto-detect the key and activate Venice. Explicit `CODESEEQ_IMAGE_BACKEND`
  still works as an override.
- **Default image output to current directory.** `bin/codeseeq-venice-image.py`
  now saves generated images to the current working directory instead of a
  `codeseeq-images/` subdirectory.
- **`ping-image` no longer requires explicit `CODESEEQ_IMAGE_BACKEND=venice`.**
  Setting `VENICE_API_KEY` is sufficient; auto-detection handles the rest.

### Removed
- **OpenAI `image_gen` skill deleted.** The `.codeseeq/skills/.system/imagegen/`
  skill (OpenAI `image_gen` tool, GPT image models, `OPENAI_API_KEY` dependency)
  has been completely removed. CodeSeeq uses Venice.ai exclusively for image
  generation. No OpenAI API keys or endpoints are ever contacted for images.

### Changed
- **Health endpoint** now reports version `0.3.7`, shows effective image backend
  (including auto-detected `venice`), and Venice API key state.
- **Version bump to v0.3.7.** All docs, configs, and internal version strings
  updated.

---

## v0.3.6 - 2026-07-14

### Added
- **Venice.ai image generation backend.** New `CODESEEQ_IMAGE_BACKEND` configuration
  option (default `none`). Set to `venice` to enable image generation via the
  Venice.ai API using `VENICE_API_KEY`.
  - Supports all Venice `/image/generate` parameters: model selection (`auto` or
    specific models like `z-image-turbo`, `gpt-image-2`, `nano-banana-pro`),
    aspect ratio, resolution (1K/2K/4K), format (jpeg/png/webp), variants (1–4),
    safe mode, watermark control, negative prompts, seed, and CFG scale.
  - New standalone script: `bin/codeseeq-venice-image.py` for direct CLI usage
    without Codex.
  - New bridge endpoint: `POST /v1/images/generations` for OpenAI-compatible
    image generation proxied through Venice.
  - New `ping-image` diagnostic command.
  - New `--image-backend` and `--venice-image-model` CLI flags.
  - Doctor output now includes image backend status and Venice API key state.
  - Health endpoint now reports `image_backend` status.
- **Comprehensive environment variable configuration** for all Venice image
  parameters: `CODESEEQ_VENICE_IMAGE_MODEL`, `CODESEEQ_VENICE_IMAGE_ASPECT_RATIO`,
  `CODESEEQ_VENICE_IMAGE_RESOLUTION`, `CODESEEQ_VENICE_IMAGE_FORMAT`,
  `CODESEEQ_VENICE_IMAGE_VARIANTS`, `CODESEEQ_VENICE_IMAGE_SAFE_MODE`,
  `CODESEEQ_VENICE_IMAGE_HIDE_WATERMARK`, `CODESEEQ_VENICE_IMAGE_SEED`,
  `CODESEEQ_VENICE_IMAGE_RETURN_BINARY`, `CODESEEQ_VENICE_IMAGE_NEGATIVE_PROMPT`,
  `CODESEEQ_VENICE_IMAGE_CFG_SCALE`, `CODESEEQ_VENICE_IMAGE_STEPS`.

### Changed
- **Version bump to v0.3.6.**
- **Health endpoint** now reports `image_backend` status and Venice configuration.
- **Dockerfile** now includes `bin/codeseeq-venice-image.py`.
- All documentation updated with image backend section and configuration reference.

---

## v0.3.5 - 2026-07-08

### Changed

- **Version bump to v0.3.5.**

## v0.3.4 - 2026-07-07

### Changed

- **Version bump to v0.3.4.**

## v0.3.3 - 2026-07-07

### Added
- **`CODESEEQ_REASONING_EFFORT` environment variable.** Controls the
  reasoning effort level forwarded to DeepSeek for thinking-enabled
  models. Accepted values: `minimal`, `low`, `medium`, `high`, `xhigh`,
  `max`. The bridge maps `low`/`medium` → `high` and `xhigh` → `max`
  to match DeepSeek's supported levels.
- **Reasoning summary wiring in the launcher.** When thinking is enabled,
  the `codeseeq` launcher now exports `MODEL_SUPPORTS_REASONING_SUMMARIES=true`
  and `MODEL_REASONING_SUMMARY=auto`, corresponding to the updated model
  catalog entries. When thinking is disabled, both are set to `false`/`none`.

### Fixed
- **Bridge reasoning-effort resilience.** The bridge now handles missing or
  non-dict `reasoning` fields in thinking-enabled requests without crashing.
  When `reasoning` is absent, falls back to the `CODESEEQ_REASONING_EFFORT`
  environment variable, defaulting to an empty string (conservative). Also
  adds a truthiness guard so `effort=""` does not trigger the mapping logic.
- **Thinking-model catalog entries updated.** `deepseek-v4-flash-thinking`
  and `deepseek-v4-pro-thinking` now correctly declare
  `supports_reasoning_summaries: true` with `reasoning_summary: "auto"` and
  `supported_reasoning_levels: [{level: "high"}, {level: "max"}]`. This
  fixes Codex treating these models as non-thinking-capable.

### Changed
- **Model catalog formatting.** `config/codex-model-catalog.json` entries
  reformatted for consistency (expanded inline-objects to multi-line).

---

## v0.3.2 - 2026-06-29

### Fixed
- **`tty: true` forced on all shell/exec tool calls.** The bridge now
  unconditionally sets `tty: true` on every `shell`, `exec_command`, `bash`,
  and related tool call before forwarding to Codex. This keeps stdin open
  for subsequent `write_stdin` calls, preventing "stdin is closed for this
  session" errors when the model pipes input to a running command. Non-
  interactive commands work fine with `tty: true` — they just complete
  normally.
- **`update_plan` explanation stripped before Codex forwarding.** Codex's
  Rust tool router rejects `explanation` at the top level of `update_plan`
  arguments as an unknown field (its struct doesn't include it despite the
  prompt mentioning it). The bridge now strips `explanation` from
  normalized arguments before forwarding, so Codex can parse the call
  without "unknown field explanation, expected step or status" errors.
- **Tool steering instruction updated.** The injected steering message now
  documents that `exec_command` always requires `tty: true`.

---

## v0.3.1 - 2026-06-27

### Added
- **Uncensored mode (`-U` / `--uncensored-mode`).** Loads the Dolfino (Dirty D)
  uncensored system prompt from `config/uncensored.md` for unrestricted interactions
  with no content filtering or moralizing guardrails. Also settable via
  `CODESEEQ_UNCENSORED_MODE=true` environment variable. The flag is safe to use
  alongside any other CodeSeeq flags and subcommands.
- **Privacy hardening (default-on).** Every generated Codex config now includes:
  - `web_search = "live"` — live web search enabled
  - `[analytics] enabled = false` — analytics disabled
  - `[feedback] enabled = false` — feedback disabled
  - `[otel] exporter/metrics_exporter/trace_exporter = "none"` — all OpenTelemetry
    pipelines disabled
  - `[otel] log_user_prompt = false` — raw prompt content not logged
  - `[history] persistence = "none"` — history persistence disabled
- **Upstream Codex command blocking.** Commands that contact OpenAI/ChatGPT services
  (`login`, `logout`, `cloud`, `app`, `app-server`, `plugin`, `update`, `features`,
  `remote-control`) are blocked by default with a clear error message. Override via
  `CODESEEQ_ALLOW_UPSTREAM_CODEX_SERVICES=true`.
- **`OPENAI_API_KEY` no longer auto-populated from `DEEPSEEK_API_KEY`.** Direct
  DeepSeek-only auth; no implicit key aliasing for OpenAI-shaped tooling.
- **Codex version pinned to `0.130.0`.** Both Dockerfile (`ARG CODEX_NPM_VERSION`)
  and Makefile (`CODEX_NPM_VERSION`) default to a pinned release instead of `latest`.
  Installer no longer auto-fetches `@openai/codex@latest` without
  `CODESEEQ_ALLOW_LATEST_RELEASE=true`.

### Fixed
- **Bash 3.2 compatibility for empty array expansion.** The `_CODESEEQ_KEPT_ARGS`
  array expansion at end of flag pre-parse loop now uses the `${array[@]+"${array[@]}"}`
  pattern to avoid "unbound variable" errors on macOS (bash 3.2) and other older
  shells. Fixes `./codeseeq --uncensored-mode` and other invocations that consume
  CodeSeeq-specific flags without passing any remaining arguments to Codex.
- **Uncensored mode recursion guard.** The uncensored mode code now sets
  `CODESEEQ_UNCENSORED_DONE=true` and unsets `CODESEEQ_UNCENSORED_MODE` in the
  child process to prevent infinite recursion when `"$SELF_PATH" system add -f`
  re-enters the launcher with the same env var set.

### Changed
- **Installer no longer auto-installs `@openai/codex`.** The `install` subcommand
  no longer runs `npm install -g @openai/codex`. Users install Codex manually with
  `npm install -g @openai/codex@0.130.0` (pinned version shown in the error message).
- **Host runtime Codex version check prints pinned version.**
  `run_host_codex()` now tells users to install `@openai/codex@0.130.0` instead of
  the unversioned package.
- **`check.sh` extended.** New assertions validate privacy hardening config content,
  blocked upstream commands, pinned `CODEX_NPM_VERSION` in both Dockerfile and
  Makefile, and that `OPENAI_API_KEY` is not auto-exported from `DEEPSEEK_API_KEY`.

---

## v0.2.9 - 2026-05-12

### Fixed
- **Flags after `run` now parsed before runtime dispatch.**  
  `./codeseeq run --runtime-mode host --bridge-mode process "hello"` correctly routes to host runtime.
- **Host diagnostics no longer require container.**  
  `models`, `doctor`, and `config` work without Docker/Podman in host runtime mode.
- **Process bridge cleanup verified.** Owned bridges stopped on all exit paths (EXIT/INT/TERM/HUP).
- **Prompt temp files no longer leak.** `TMP_FILES` properly tracked after command substitution.
- **External mode supports `/v1/` trailing slash.**
- **0.0.0.0 bridge host now writes `127.0.0.1` in client config** (warning printed).
- **`make bridge-process-smoke` no longer leaks processes.** POSIX-safe script with cleanup trap.
- **Container bridge smoke updated** with correct model IDs and bridge host binding.
- **Doctor output cleaned up** — "Bridge URL" instead of "OpenResponses", runtime/bridge mode fields.
- **Package hygiene strengthened** — `make clean-artifacts`, `make package-check`, docs warn against manual zips.
- **No Codex source modified.** No upstream `open-responses` runtime dependency.

### Added
- **Host-native bridge process mode.** `CODESEEQ_BRIDGE_MODE=process` starts
  `bin/codeseeq-bridge.py` as a direct child process on the host with no
  Docker or Podman required. All four bridge modes are supported: `process`,
  `container`, `external`, and `auto` (default, prefers process when Python
  dependencies are available, falling back to container).
- **Host runtime mode.** `CODESEEQ_RUNTIME_MODE=host` launches Codex directly
  on the host alongside a process bridge, without going through the container
  runtime at all. `container` and `auto` (default) remain available.
- **External bridge mode.** `CODESEEQ_BRIDGE_MODE=external` with
  `CODESEEQ_BRIDGE_BASE_URL` lets CodeSeeq talk to a pre-existing bridge
  without starting anything locally.
- **Bridge reuse.** `CODESEEQ_BRIDGE_REUSE=1` causes CodeSeeq to check for a
  healthy bridge at the configured port and reuse it instead of starting a
  new one.
- **CLI flags for bridge configuration.** `--bridge-mode`, `--bridge-url`,
  and `--bridge-port` are now accepted on the `codeseeq` launcher and the
  `run` subcommand so bridge mode can be set per-invocation without
  environment variables.
- **Unified CI release pipeline.** Release step merged into `ci.yml` — runs
  only on tag pushes (`v*`) after all checks pass.

### Changed
- **Dockerfile no longer pulls `open-responses` npm dependency.** The
  upstream `open-responses` package was removed from the container image
  build. The actual runtime bridge is entirely `bin/codeseeq-bridge.py`.
- **Bridge mode architecture rewrite.** The launcher now has a unified
  `bridge_start()` abstraction that selects between process, container,
  external, and auto modes with consistent health-check and cleanup
  behavior. Process-mode owned bridges are stopped on launcher exit.

### Notes
- No Codex source was modified. The bridge remains a drop-in
  Responses-compatible API that Codex talks to exactly as it would talk to
  any OpenAI-compatible provider.
- `wire_api = "responses"` in the generated Codex config stays because Codex
  expects that value; it does not mean the upstream `open-responses` package
  is used.

---

## 0.2.8 - 2026-05-12

### Fixed
- Version bump and release process improvements.

---

## 0.2.7 - 2026-05-08

### Added
- **`codeseeq nuke` subcommand.** Uninstalls all CodeSeeq user artifacts — the
  installed snapshot at `~/.config/codeseeq`, the launcher at `~/bin/codeseeq`,
  and any leftover `~/.codeseeq` state — with a confirmation prompt before
  removal. The local repo checkout and container images are left untouched.

### Changed
- **README.md description rewritten.** The first paragraph now describes
  CodeSeeq as a "drop-in launcher and CLI wrapper" rather than a "command
  switch", better reflecting its role as a full launcher/substitute that
  manages container runtime, bridge lifecycle, configuration, and DeepSeek
  model wiring.
- **CodeSeeq splash image.** The `codeseeq.jpg` image was added to the README
  between the introductory paragraph and the version/release-notes section.
- **Version bumped to `0.2.7`.** Updated `VERSION`, `README.md`, and all
  doc-version references in `docs/ARCHITECTURE.md`, `docs/SECURITY.md`, and
  `docs/TROUBLESHOOTING.md`.

### Fixed
- **Typo `--sanbox` in README.md and doc examples.** The documented alias
  examples in `README.md`, `docs/TROUBLESHOOTING.md`, and `docs/SECURITY.md`
  were using `--sanbox` instead of the correct `--sandbox`. Fixed to show the
  proper flag spelling while keeping `--sanbox` as an accepted internal alias.

---

## 0.2.6 - 2026-05-08

### Added
- **Container-launcher rewrite.** The `./codeseeq` launcher was substantially
  rewritten with robust configuration defaults, helper functions (`warn`,
  `bool_normalize`), and expanded environment-variable plumbing for
  `CODESEEQ_MODEL`, `CODESEEQ_THINKING`, `CODESEEQ_APPROVAL_POLICY`,
  `CODESEEQ_SANDBOX_MODE`, `CODESEEQ_OPENRESPONSES_PORT`,
  `CODESEEQ_OPENRESPONSES_URL`, `CODESEEQ_CONTEXT_WINDOW`,
  `CODESEEQ_HOST_CODEX_HOME`, and `CODESEEQ_SYSTEM_PROMPT_FILE`.
- **System prompt injection.** New `CODESEEQ_SYSTEM_PROMPT_FILE` (default
  `${WORKDIR}/.codeseeq/system-prompt.md`) is read and injected into Codex's
  TOML config as a quoted string. Helper functions (`system_prompt_present`,
  `system_prompt_state`, `system_prompt_bytes`, `system_prompt_lines`) report
  prompt state at startup.
- **Workspace banner.** Entrypoint prints a summary banner showing the
  workspace path, version, model, approval policy, sandbox mode, key
  configuration hash, and system-prompt state on each launch.
- **`.env.example` template.** Documented all supported environment variables
  with their defaults, so users can copy `.env.example` to `.env` and customize.
- **Expanded smoke-test suite.** `scripts/smoke-all.sh` now runs container
  smoke tests and host-cli smoke tests. New `scripts/runtime.sh` checks for
  container and GPU host capabilities.
- **Bridge binary on `codeseeq-bridge.py`.** The bridge now lives at its own
  path (`bin/codeseeq-bridge.py`) in the container, launched side-by-side with
  Codex rather than being embedded.

### Changed
- **Launcher becomes dual-purpose.** `./codeseeq` now detects the `install`
  subcommand automatically (install logic is inlined in the launcher;
  `scripts/install-local.sh` is kept as a standalone equivalent). Running
  without subcommand starts the container with all configuration variables
  forwarded.
- **`CODESEEQ_WORKDIR_HOST` now resolves symlinks.** Uses `pwd -P` instead of
  plain `$PWD` so bind-mount paths are canonical.
- **Documentation overhaul.** `README.md`, `docs/ARCHITECTURE.md`,
  `docs/SECURITY.md`, and `docs/TROUBLESHOOTING.md` were rewritten with
  up-to-date configuration references, container-runtime instructions, and
  security/architecture guidance.
- **Scripts polished.** `scripts/check.sh` extended with bridge-extraction
  regression tests; `scripts/package.sh` streamlined; `scripts/install-local.sh`
  updated for the new launcher layout.

### Fixed
- **`codeseeq` binary made executable in-repo.** The root `codeseeq` file now
  has the executable bit set so it runs directly without `bash codeseeq`.

---

## 0.2.5 - 2026-05-07

### Fixed
- Fixed split display-mangled DSML such as `<____DSML____tool_calls>...`
  leaking into the Codex UI after a successful tool call. The streaming buffer
  now normalizes obfuscated DSML after chunk reassembly, so the block is either
  converted into a tool call or suppressed instead of being shown as assistant
  text.
- Added missing `output_index` metadata to streamed message/tool lifecycle
  events so current Codex builds keep output deltas attached to their active
  items instead of logging orphaned `OutputTextDelta` diagnostics.
- Fixed Responses top-level function tools being collected for steering but not
  forwarded to DeepSeek's nested Chat Completions `tools` shape. This keeps
  DeepSeek able to emit actual structured tool calls instead of plain bash
  snippets.
- Updated README, quickstart, state docs, bridge docs, and CI build metadata to
  reflect the current single-container local-bridge runtime.
- Added `workspace/` to `.gitignore` so the local repro clone does not break
  `git add .`.

---

## 0.2.4 - 2026-05-07

### Fixed
- Fixed regular `danger-full-access` launches emitting both `--ask-for-approval
  ...` and `--dangerously-bypass-approvals-and-sandbox`. Codex rejects that
  combination. The launcher now omits `--ask-for-approval` whenever it emits the
  bypass flag.

---

## 0.2.3 - 2026-05-07

### Changed
- `codeseeq --yolo` and `codeseeq -y` now only add Codex launch switches
  `--dangerously-bypass-approvals-and-sandbox` and, for `codex exec` paths,
  `--skip-git-repo-check`.
- Yolo mode no longer injects `--ask-for-approval never`, no longer injects
  `--sandbox danger-full-access`, and no longer rewrites
  `CODESEEQ_APPROVAL_POLICY` / `CODESEEQ_SANDBOX_MODE` config values.

---

## 0.2.2 - 2026-05-07

### Added
- `codeseeq --yolo` and `codeseeq -y` wrapper flags. They force
  `CODESEEQ_APPROVAL_POLICY=never` and `CODESEEQ_SANDBOX_MODE=danger-full-access`,
  and launch Codex with `--ask-for-approval never` plus `--sandbox danger-full-access`
  and `--dangerously-bypass-approvals-and-sandbox`.
- Direct `run`/prompt shortcuts keep using `codex exec --skip-git-repo-check`.
  `codeseeq --yolo codex exec ...` also injects `--skip-git-repo-check` when it is
  not already present.

---

## 0.2.1 - 2026-05-07

Malformed XML compatibility patch for DeepSeek tool-use output.

### Fixed
- Recognizes model-invented outer tool tags such as
  `<exec_command><command>...</command></exec_command>`, `<bash>...</bash>`,
  and `<tool_call name="...">...</tool_call>` as real Codex function calls
  instead of streaming them as assistant text.
- Extends streaming buffering to hold those malformed tags until the closing
  tag arrives, preventing visible XML leakage in the Codex UI.
- Normalizes common XML argument aliases against the registered Codex tool
  schema, including `command` -> `cmd` for `exec_command`/unified shell tools.
- Adds focused bridge extraction regression coverage and wires it into
  `scripts/check.sh`.

---

## 0.2.0 - 2026-05-07

DSML/tool-calling correctness pass for `bin/codeseeq-bridge.py`. The bridge now
properly streams tool calls to Codex CLI, normalizes display-mangled DSML in
history, and remaps emitted tool names onto whatever the client actually
registered. Drop-in replacement; no changes required to Codex, Dockerfile,
entrypoint, or scripts.

### Fixed
- **DSML leakage during streaming.** Raw `<function_calls>...</function_calls>`
  XML was being streamed verbatim to the Codex TUI before the post-stream
  extractor ran. Replaced with `StreamingDsmlBuffer` that detects DSML inline,
  emits only safe text deltas, and surfaces tool-call blocks as soon as their
  closing tag is seen. Buffer uses depth tracking so a nested `</invoke>` inside
  an outer `<function_calls>` wrapper does not terminate prematurely.
- **`call_id: None` on `response.output_item.added`.** The added event was fired
  before the tool name and call id were known, then back-filled. Now deferred
  until the call has a real name and id, so Codex never sees a partial item.
- **Wrong delta event for function tools.** Used
  `response.custom_tool_call_input.delta` for function-typed tools; modern Codex
  listens on `response.function_call_arguments.delta`. Both are now emitted
  (modern + legacy) so older Codex builds keep working.
- **Missing `response.function_call_arguments.done`.** Now emitted, in the proper
  place in the lifecycle.
- **Broken DSML extraction lifecycle.** Post-stream DSML extraction emitted only
  `output_item.done`. Now emits the full sequence: `output_item.added` ->
  `function_call_arguments.delta` -> `function_call_arguments.done` ->
  `output_item.done` (plus legacy `custom_tool_call_input.delta`).
- **Duplicate `response.completed`.** Deduplicated to a single emission.
- **Display-mangled DSML in history.** Codex's TUI obfuscates `<` to
  `<____DSML____` for safe display. When that text fed back as history, DeepSeek
  imitated the malformed format. Added `normalize_dsml_display()` applied to ALL
  inbound message content so the model only ever sees clean XML or, ideally,
  structured `tool_calls`.

### Added
- **Tool-name aliasing.** Flat `TOOL_NAME_ALIASES` map — emitted name -> ordered
  tuple of preferred replacements. `resolve_tool_name()` does exact ->
  case-insensitive -> alias-prefs (only those actually registered) -> fuzzy
  (`difflib`, cutoff 0.7) -> first preference fallback. Common variants covered:
  `bash`/`sh`/`execute_command`/`exec_command`/`run_command` -> `shell`;
  `write`/`write_file`/`create_file` -> `apply_patch`/`write_file`;
  `edit`/`patch`/`str_replace_editor` -> `str_replace`/`apply_patch`;
  `read_file`/`view_file`/`cat` -> `view`; etc. Toggle with env
  `CODESEEQ_BRIDGE_TOOL_ALIAS_FUZZY` (default on).
- **Tool-use steering system message.** When tools are present in the request, a
  small system message is injected telling the model to emit structured
  `tool_calls` rather than XML. Toggle via env `CODESEEQ_BRIDGE_TOOL_STEERING`
  (default on).
- **Stricter error handling for upstream stream.** `httpx.RemoteProtocolError`,
  `httpx.ReadError`, and `asyncio.CancelledError` are caught separately so the
  bridge logs and surfaces the right SSE error type rather than 500ing.

### Notes
- No schema changes to `/v1/responses`, `/v1/models`, or `/health`.
- No changes to Codex configuration, Dockerfile, container entrypoint, or smoke
  scripts; behavior is fully on the bridge side.
- `CODESEEQ_BRIDGE_DEBUG_LOG=1` continues to dump full request/response payloads
  to `/tmp/codeseeq-bridge.log` for diagnostics.

---

## 0.1.0 - 2026-05-07

- Initial public version of CodeSeeq single-container CLI workflow.
- Added root `./codeseeq` launcher and container entrypoint path.
- Added DeepSeek/OpenResponses bridge runtime wiring and smoke scripts.
- Added `VERSION` file with starting semantic version.
- Switched license from AGPL-3.0 to Apache 2.0 (updated `LICENSE`, `COPYRIGHT`,
  `README.md`).
