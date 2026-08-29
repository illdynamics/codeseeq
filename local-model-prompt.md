# Task: Add GGUF local-model loading to CodeSeeq

## Goal

Extend CodeSeeq so an operator can run Codex against a **local GGUF model**
instead of DeepSeek (or any hosted provider), selecting the model **by its full
path to a `.gguf` file**. Examples of the target UX:

```bash
# via flag
codeseeq --model /absolute/path/to/llama-3.2-3b-instruct-q4_k_m.gguf "prompt"

# via env
CODESEEQ_MODEL=/absolute/path/to/model.gguf codeseeq run "prompt"

# explicit provider-prefixed slug (equivalent)
CODESEEQ_MODEL='gguf@/absolute/path/to/model.gguf' codeseeq

# via the config wizard (add a "gguf" provider that prompts for a path)
codeseeq config
```

When a `.gguf` path is selected, CodeSeeq must: (1) launch a local
OpenAI-compatible inference server pointed at that file, (2) route the existing
chat-completions translation to it, and (3) tear it down cleanly. Everything
else in CodeSeeq (config generation, tool calls, streaming, the bridge) must
keep working unchanged.

Implement this **completely and solidly**: correct behavior, no regressions,
robust lifecycle/error handling, new tests, and docs. Run the full check suite
and make it green before you finish.

---

## Context: what you are modifying

CodeSeeq (this repo, `v0.4.2`) is "Codex wired to DeepSeek / other providers".
Three components matter:

1. **`./codeseeq`** — bash host wrapper (3223 lines). Handles install/build/
   smoke/system/package, the `config` wizard (`cmd_config`, ~line 2365), model
   normalization (`normalize_model`, ~line 533), provider→API-key mapping
   (`provider_key_env_for_model`, ~line 797), host config generation
   (`write_host_config`, ~line 912), and env forwarding to the bridge
   (`codeseeq_bridge_env_names`, ~line 1040).

2. **`bin/codeseeq-entrypoint`** — bash in-container dispatcher (1334 lines).
   Mirrors the wrapper's logic inside the container: `normalize_model`
   (~line 282), `provider_key_env_for_model` (~line 595), `write_config`
   (~line 511), `write_merged_codex_catalog` (~line 436).

3. **`bin/codeseeq-bridge.py`** — FastAPI bridge (4397 lines). Implements
   `/v1/responses` (the OpenAI Responses wire format Codex speaks), `/v1/models`,
   `/health`, and `/v1/images/generations`. It translates Codex → provider API.

### How model selection works today

- `CODESEEQ_MODEL` (default `deepseek-v4-flash`) is the **logical** model.
- Slugs use `provider@model`. Bare aliases (`deepseek-v4-flash`, `qwibus-qwikk`)
  are normalized to `provider@model`. `normalize_model` (in both the wrapper and
  the entrypoint) maps the logical model to `PROVIDER_MODEL`, which is written
  into Codex's generated `config.toml` as `model = "<PROVIDER_MODEL>"`.
- The bridge (`bin/codeseeq-bridge.py::normalize_model`, ~line 1739) receives
  that model string on every `/v1/responses` request and resolves it to a
  `ModelSpec` (class at ~line 289) carrying `provider`, `base_url`, `chat_url`,
  `temperature`, `top_p`, `top_k`, `context_window`, `max_output_tokens`,
  `timeout_seconds`, `system_prompt`, and `deepseek_model` (the upstream model
  name actually sent to the provider).
- Built-in specs are built in `_build_model_specs` (~line 504), overlaid by the
  human catalog in `_apply_catalog_overrides` (~line 934), then stored in
  `MODEL_SPECS` (~line 1045).
- `normalize_model` also accepts **arbitrary** `provider@model` slugs for any
  known provider (the `elif "@" in raw:` branch, ~line 1772). This is how
  `local@llama-4-maverick` works today.
- Provider routing lives in `resolve_provider_for_slug` (~line 443), and the
  keyless/local exception lives in `require_provider_key` (~line 479).

### The existing `local` provider (do not break it)

CodeSeeq already has a `local` provider for **an already-running**
OpenAI-compatible gateway (Ollama / LM Studio / vLLM / llama.cpp):

- slug `local@<model>`, provider constant `PROVIDER_LOCAL = "local"` (~line 373)
- default base URL `http://127.0.0.1:1337`, chat endpoint derived as
  `{base}/v1/chat/completions` (`_derive_chat_url`, ~line 419)
- keyless by default, optional `LOCAL_API_KEY`
- the config wizard (`cmd_config`) already prompts for a local model name +
  optional key + base URL.

Your new GGUF support is a **separate provider** (`gguf`) that *spawns its own*
server from a file path, then reuses the same OpenAI-compatible chat-completions
translation path that `local`/DeepSeek use. Keep `local` exactly as-is.

---

## Design decisions (required — implement these, don't redesign)

### D1. Use llama.cpp `llama-server` as the inference engine

Launch the `llama-server` binary (from the llama.cpp project) as a **child
process** of the bridge. `llama-server` serves an OpenAI-compatible HTTP API
(`/v1/chat/completions`, streaming, and — when the model ships a tool-calling
chat template — tool calls). This is the most solid, widely-supported way to
serve a GGUF, and it reuses CodeSeeq's existing chat-completions translation
with almost no new wire-format code.

- **Discovery order** for the binary:
  1. `CODESEEQ_GGUF_LLAMA_SERVER_PATH` (explicit path to `llama-server`)
  2. `llama-server` on `PATH`
  3. fall back to `llama-server` found next to the repo (e.g. the installed
     `bin/` dir), then fail with a clear, actionable error.
- If no binary is found, fail **at request time** with a clean 502 (non-stream)
  or `response.failed` SSE event (stream) whose message says:
  `gguf provider requires llama.cpp llama-server. Set CODESEEQ_GGUF_LLAMA_SERVER_PATH
  or install llama.cpp (see https://github.com/ggml-org/llama.cpp).`
- Do **not** hardcode a binary into the repo. Do not vendor llama.cpp source.
- (Alternative, only if you must avoid a subprocess: `llama-cpp-python` in the
  bridge process. Do **not** choose this unless instructed — prefer D1.)

### D2. A dedicated `gguf` provider, keyless

Add `PROVIDER_GGUF = "gguf"` alongside the other provider constants. Treat it as
keyless like `local` (no required API key; ignore/omit Authorization unless an
optional `GGUF_API_KEY`/`--api-key` is configured). Route its chat endpoint as
`{base}/v1/chat/completions`.

### D3. Model selection by path

Accept **all three** of these and normalize them to the same internal
representation:

1. bare path: `CODESEEQ_MODEL=/abs/path/model.gguf` or `--model /abs/path/model.gguf`
2. slug: `gguf@/abs/path/model.gguf`
3. relative path (resolve against the current working directory to an absolute
   path before using it).

Detection rule (implement in both bash `normalize_model` copies and the bridge
`normalize_model`): a value is a GGUF selection if it ends in `.gguf`
(case-insensitive) **or** it has a `gguf@` prefix. Do not let a bare
non-`.gguf` path through silently.

### D4. Lifecycle: lazy start + reuse + clean teardown

- The bridge starts the `llama-server` for a given absolute GGUF path on the
  **first** `/v1/responses` request that resolves to it, and reuses that server
  for subsequent requests (one server per distinct path per bridge process).
- Health-check the server (`GET http://127.0.0.1:<port>/health`) with a
  startup timeout (default `CODESEEQ_GGUF_STARTUP_TIMEOUT_SECONDS=300`), then
  begin proxying. If it does not become healthy in time, kill it and return a
  clean error.
- **Teardown**: terminate the child (and its process group, if you launch one)
  on bridge shutdown — reuse the existing parent-death watchdog in
  `main()`/`_install_parent_watchdog` (~line 4295) and add an `atexit`/signal
  handler that SIGTERMs the child, waits briefly, then SIGKILLs. The existing
  bridge already knows how to clean up after itself; mirror that pattern so no
  orphaned `llama-server` survives a hard-killed `codeseeq` run.

### D5. Auto-select a free localhost port

Use the same bind-probe technique already in the bridge (`_bind_socket`, ~line
4283): bind a socket to `127.0.0.1:0`, read the OS-assigned port, close it, and
pass that port to `llama-server --port <port>`. Guard the small TOCTOU window by
retrying a few times if the server reports the port is taken. Never bind the
server to a non-localhost address by default.

---

## Implementation plan (ordered, file by file)

Work in this order. Each step must leave the repo in a runnable, testable
state. Keep the wrapper (`codeseeq`) and entrypoint (`bin/codeseeq-entrypoint`)
in lockstep — they duplicate `normalize_model` / `provider_key_env_for_model`
on purpose, and there is a regression test that checks they agree with the
bridge.

### Step 0 — Read the exact current code before editing

Open and re-read these before touching anything (line numbers are current as of
v0.4.2 and may drift slightly):

- `bin/codeseeq-bridge.py`: `ModelSpec` (289), `PROVIDER_*` block (368–415),
  `_derive_chat_url` (419), `resolve_provider_for_slug` (443),
  `require_provider_key` (479), `_build_model_specs` (504), `normalize_model`
  (1739), `responses` (3409), `deepseek_payload` (2519), `_bind_socket`/main
  (4283–4397).
- `codeseeq`: `normalize_model` (533), `provider_key_env_for_model` (797),
  `codeseeq_bridge_env_names` (1040), `cmd_config` (2365),
  `config_provider_*` helpers (1968–2015), `config_provider_model_items`
  (2135), `config_write_json` (2192), `config_print_status` (2301).
- `bin/codeseeq-entrypoint`: `normalize_model` (282), `write_merged_codex_catalog`
  (436), `write_config` (511), `provider_key_env_for_model` (595),
  `require_deepseek_key` (622).
- `config/model-catalog.json`, `config/codex-model-catalog.json` (schema).
- `scripts/check.sh`, `scripts/test-bridge-env-names.py`,
  `scripts/test-bridge-chat-url.py` (existing regression tests that must stay green).

### Step 1 — Bridge: add the `gguf` provider plumbing

In `bin/codeseeq-bridge.py`:

1. Add `PROVIDER_GGUF = "gguf"` next to the other provider constants.
2. Add `PROVIDER_GGUF` to `OPENAI_COMPAT_PROVIDERS`.
3. `PROVIDER_API_KEY_ENV[PROVIDER_GGUF] = None` (keyless by default). This also
   makes `resolve_provider_for_slug` and the arbitrary-`provider@model` branch
   accept a `gguf@` owner. Optionally map `GGUF_API_KEY` instead if you want to
   support a `--api-key` server, but `None` is the required default.
4. `PROVIDER_BASE_URL_ENV[PROVIDER_GGUF] = ("GGUF_BASE_URL", "LOCAL_BASE_URL",
   "OPENAI_BASE_URL", "CODESEEQ_BASE_URL")` and
   `PROVIDER_DEFAULT_BASE_URL[PROVIDER_GGUF] = "http://127.0.0.1:1"` (a
   placeholder — the real base is set dynamically by the server manager).
5. `_derive_chat_url`: add a `gguf` branch returning `{base}/v1/chat/completions`
   (same as local). Do **not** fall through to DeepSeek's `/chat/completions`.
6. Extend `ModelSpec` with one new field: `gguf_path: Optional[str] = None`.
   - Add it to `__slots__` and the `__init__` signature.
   - Update **every** `ModelSpec(...)` construction site (there are ~5: the
     `_spec` helper, the two arbitrary-slug branches in `normalize_model`, the
     final copy in `normalize_model`, and the `CODESEEQ_PROVIDER` override copy)
     to pass `gguf_path=...` (usually `None`, or the resolved path for GGUF).

### Step 2 — Bridge: GGUF path detection + spec resolution

In `bin/codeseeq-bridge.py::normalize_model` (1739), add a **dedicated GGUF
branch** at the top of the resolution logic (before the generic `provider@model`
branch) so you can attach the `gguf_path`:

- Normalize input: if `raw` ends with `.gguf` (case-insensitive) treat it as a
  path; if it starts with `gguf@`, strip that prefix and use the remainder as
  the path; else it is not a GGUF selection.
- Resolve to an **absolute** path (`os.path.abspath(os.path.expanduser(p))`).
- Validate it exists and is readable (`os.path.isfile`). If not, raise the same
  `ValueError`/`HTTPException(400)` style the rest of `normalize_model` uses,
  with a message like `gguf model file not found: <path>`.
- Build a `ModelSpec` with:
  - `slug = f"gguf@{abs_path}"`
  - `deepseek_model = <basename without .gguf>` (this is the value sent to
    llama-server as the `model` field; also pass it as `--alias`)
  - `provider = PROVIDER_GGUF`, `api_key_env = None`
  - `gguf_path = abs_path`
  - `base_url`/`chat_url` = a placeholder (the real URL is injected at request
    time by the server manager; see Step 3)
  - sampling/context defaults from env with sensible GGUF defaults
    (`context_window` default 8192 or a `CODESEEQ_GGUF_CONTEXT_WINDOW` value,
    `max_output_tokens` default from `CODESEEQ_GGUF_MAX_OUTPUT_TOKENS` or 2048,
    `temperature` 0.7 default, `timeout_seconds` from `CODESEEQ_GGUF_TIMEOUT_SECONDS`
    default 600).
- Make `CODESEEQ_THINKING` and `CODESEEQ_<KEY>_ENABLE_THINKING` still respected
  (thinking is passed through as `enable_thinking` if the model supports it; do
  not crash if the model has no thinking template — `llama-server` will ignore
  or the bridge's DSML fallback will still handle tool calls).

### Step 3 — Bridge: GGUF server manager

Add a new module section (near the top-level utilities, after the provider
block) with a small `GGUFServerManager`:

- Maintains a `dict[str, GGUFServer]` keyed by absolute GGUF path, where a
  `GGUFServer` holds: `path`, `port`, `process` (subprocess.Popen), `base_url`,
  `chat_url`.
- `async def ensure(path, ...) -> GGUFServer`:
  - returns the cached server if present and healthy,
  - otherwise picks a free port (bind-probe per D5),
  - builds the `llama-server` argv:
    `llama-server -m <abs_path> --host 127.0.0.1 --port <port> --alias <basename>
    -c <context_window> -ngl <CODESEEQ_GGUF_N_GPU_LAYERS or 0>
    -t <CODESEEQ_GGUF_THREADS or cpu_count> -np <CODESEEQ_GGUF_PARALLEL or 1>`
    (forward a bounded, documented set of `CODESEEQ_GGUF_*` env vars as flags;
    ignore/omit flags whose env is unset — llama-server has sane defaults),
  - launches with `stdout`/`stderr` to a log (reuse the bridge log directory
    convention or `tempfile`; never leak into the Responses stream),
  - polls `GET {base}/health` until healthy or `CODESEEQ_GGUF_STARTUP_TIMEOUT_SECONDS`
    elapses,
  - on timeout, terminates the child and raises a clean error.
- `def shutdown_all()`: SIGTERM each child, wait, SIGKILL stragglers. Hook this
  into (a) an `atexit` handler and (b) the existing parent-death watchdog
  (`_install_parent_watchdog`, ~4295) and (c) normal `main()` shutdown so a
  killed wrapper cannot leak an inference server.
- **Wire it into `responses()`** (3409): after `spec = normalize_model(model_in)`
  resolves, if `spec.provider == PROVIDER_GGUF`, call
  `server = await manager.ensure(spec.gguf_path)` and set
  `model_chat_url = server.chat_url` (and `spec.chat_url`/`spec.base_url` for
  any downstream code that reads them). All the existing
  chat-completions/non-stream/stream/DSML logic then works unchanged. The
  `log(...)` line already prints `chat_url` — it will now show the real local
  URL, which is correct.

### Step 4 — Bridge: endpoint + error surfacing

- `/health` (3373): keep reporting; optionally add `gguf` to the providers list
  and add a `gguf_binary` field (found/not-found) so `codeseeq doctor` can
  surface a missing `llama-server`.
- `/v1/models` (3394): do **not** hardcode a gguf path; optionally add a
  `gguf@local-model` placeholder entry so the provider is discoverable, but the
  real model is always the path the operator supplies.
- Error surfacing: missing binary / missing file / startup timeout must produce
  a clean 502 (non-stream) or `response.failed` SSE (stream) — mirror the
  existing `cannot reach upstream at <url>` handling in `responses()` (3480+).
  Never a raw 500 traceback.

### Step 5 — Wrapper `codeseeq`: accept the path and forward config

Mirror Steps 1–2 in bash:

1. `normalize_model` (533): add a `gguf` case. Accept `gguf@*`, bare `.gguf`
   paths, and (optionally) `*.gguf` relative paths; set
   `PROVIDER_MODEL="gguf@<abs path>"`, `CODESEEQ_PROVIDER="gguf"`, and compute
   thinking as `false` (or from `-thinking` if present). Resolve to an absolute
   path with a portable one-liner (Python3 `os.path.abspath` is acceptable; this
   repo already requires python3 for the wizard). Do not require the file to
   exist at *parse* time (validation happens in the bridge) — but do reject
   clearly-invalid values.
2. `provider_key_env_for_model` (797): return empty for `gguf@*` (keyless), and
   handle a `gguf` entry in the `CODESEEQ_PROVIDER` case.
3. `write_host_config` (912): add a `gguf@*) provider_label="CodeSeeq GGUF Bridge"`
   case; keep `model = "${PROVIDER_MODEL}"` so Codex sends `gguf@/abs/path` to
   the bridge. Keep `env_key` as `DEEPSEEK_API_KEY` fallback or blank — the
   bridge ignores auth for gguf, but `env_key` must be a non-breaking value
   (use `""` or `LOCAL_API_KEY`; verify Codex accepts an empty env_key).
4. `codeseeq_bridge_env_names` (1040): forward the new `CODESEEQ_GGUF_*` env
   vars (binary path, context window, max output tokens, n-gpu-layers, threads,
   parallel, startup timeout, timeout seconds, api key if used). Also forward
   the dynamic per-model key for the configured path the same way the current
   code forwards `CODESEEQ_LLAMA_4_MAVERICK_*` (reuse the existing dynamic-key
   block at ~1069).

### Step 6 — Wrapper `codeseeq`: config wizard

Add `gguf` as a provider option in `cmd_config` (2365) and its helpers:

- `config_provider_list` (1976): add `gguf`.
- `config_provider_label` (1980): `gguf -> 'GGUF (local llama.cpp)'`.
- `config_provider_key_env` (1992): return empty (keyless).
- `config_provider_base_url_env` / `config_provider_default_base_url`: empty
  (base URL is derived, not configured).
- In `cmd_config`, add a branch for `gguf` that uses `cfg_prompt` to ask for the
  absolute GGUF file path (e.g. `/path/to/model.gguf`) and writes
  `CODESEEQ_MODEL=gguf@<abs path>` + `CODESEEQ_PROVIDER=gguf`. Validate the
  path is non-empty and (optionally, if python3 available) that it exists.
- Ensure `config_write_json` (2192) and `config_print_status` (2301) handle the
  keyless gguf provider without writing an empty API-key key (the existing
  `LOCAL_API_KEY` guard shows the pattern to copy).

### Step 7 — Entrypoint `bin/codeseeq-entrypoint`: mirror

Make the in-container dispatcher behave identically to the wrapper for gguf:

- `normalize_model` (282): same gguf case.
- `provider_key_env_for_model` (595): keyless for gguf.
- `write_merged_codex_catalog` (436): ensure the merged Codex catalog includes
  the `gguf@<path>` slug (it already merges arbitrary `CODESEEQ_MODEL`, so just
  verify it doesn't choke on the `/` characters in the path; the `slug` field is
  a plain string so it should be fine, but test it).
- `write_config` (511): add the `gguf@*` provider-label case.
- `require_deepseek_key` (622): no key required for gguf.

### Step 8 — Catalogs (optional but recommended)

Add a minimal `gguf` provider to `config/model-catalog.json` (a `gguf` entry in
`providers` with empty `models` list, plus an optional `gguf@local-model`
placeholder model whose `provider` is `gguf`), and a matching `gguf@local-model`
entry in `config/codex-model-catalog.json`. Keep `check.sh`'s catalog assertions
satisfied (it asserts providers include `deepseek, anthropic, google, grok,
venice, local` and that every `provider_model` has a Codex-catalog slug).

### Step 9 — Dockerfile / install (optional, host-mode is primary)

GGUF inference is primarily a **host-runtime / process-bridge** feature (the
bridge runs as a host process and can read host files directly). For container
support you may, but are not required to, add an `llama.cpp` binary to the
Dockerfile and document that the GGUF directory must be bind-mounted. Do not let
container work block the host-mode feature; if you add it, make it opt-in and
documented.

---

## Robustness & edge cases (must handle)

- **Missing `llama-server` binary** → clean 502 / `response.failed` with the
  exact fix instructions (D1), and `codeseeq doctor` should flag it.
- **Missing/unreadable GGUF file** → clean 400/502 at request time (and an
  early, friendly error from the wrapper if easy), never a raw traceback.
- **Path normalization** → expand `~`, resolve relative→absolute, strip
  whitespace, reject empty. Handle paths with spaces (pass the path as a single
  argv element, never shell-interpolate).
- **Port collision / server failed to bind** → retry with a new free port a
  bounded number of times, then fail cleanly.
- **Slow model load** (large GGUF) → health-poll with a generous startup
  timeout; stream/return a clear "still loading" error if it times out, and
  kill the half-started server.
- **Concurrent `codeseeq` invocations** → each bridge process owns its own
  `llama-server` and port (the existing bind-probe + per-process model already
  guarantees this); do not share state across bridge processes.
- **Cleanup on SIGKILL of the wrapper** → the existing parent-death watchdog
  plus your `atexit`/signal handler must kill the `llama-server` child so no
  orphaned inference process or port leak remains.
- **Streaming** → llama-server streams SSE; the bridge's existing streaming path
  (3409+) must work end-to-end. Test a streamed response.
- **Tool calls** → many GGUF models lack a native tool-calling template; the
  bridge's existing DSML text-based tool-call extraction is the safety net.
  Verify both: (a) a model with native tool support, (b) DSML fallback. Do not
  regress `test-bridge-extraction.py`.
- **Backward compatibility** → `deepseek-v4-flash` default, all hosted
  providers, `local@*`, and `qwibus@*` must behave exactly as before. Do not
  change default `CODESEEQ_MODEL`.

## Environment variables (document all of these in `.env.example`)

| Variable | Purpose | Default |
|---|---|---|
| `CODESEEQ_GGUF_LLAMA_SERVER_PATH` | explicit path to `llama-server` | (PATH lookup) |
| `CODESEEQ_GGUF_CONTEXT_WINDOW` | `-c` context size | 8192 |
| `CODESEEQ_GGUF_MAX_OUTPUT_TOKENS` | max output tokens | 2048 |
| `CODESEEQ_GGUF_N_GPU_LAYERS` | `-ngl` offload layers | 0 |
| `CODESEEQ_GGUF_THREADS` | `-t` CPU threads | auto |
| `CODESEEQ_GGUF_PARALLEL` | `-np` parallel sequences | 1 |
| `CODESEEQ_GGUF_TIMEOUT_SECONDS` | per-request httpx timeout | 600 |
| `CODESEEQ_GGUF_STARTUP_TIMEOUT_SECONDS` | health-poll timeout | 300 |
| `CODESEEQ_GGUF_ENABLE_THINKING` | toggle thinking | false |
| `GGUF_BASE_URL` | (advanced) force an external gguf server URL | unset |
| `GGUF_API_KEY` | (optional) `--api-key` for the local server | unset |

Keep the `CODESEEQ_GGUF_*` names consistent across the wrapper, entrypoint, and
bridge (the `test-bridge-env-names.py` style of agreement applies).

---

## Testing & acceptance criteria

You are **done** only when all of the following pass locally:

1. `bash -n codeseeq && bash -n bin/codeseeq-entrypoint` — no syntax errors.
2. `python3 -m py_compile bin/codeseeq-bridge.py` — bridge compiles.
3. `python3 -c "import importlib.util; ...; exec_module(bridge)"` — bridge imports
   cleanly with the default (no-gguf) environment.
4. `./scripts/check.sh` — the full existing suite is green with **no new
   failures** (this includes the catalog, config-wizard, env-name-agreement,
   chat-url, extraction, Anthropic, and reap tests).
5. Add a new regression test `scripts/test-bridge-gguf.py` and wire it into
   `scripts/check.sh`. It must assert, **without requiring a real llama-server**:
   - `normalize_model("/tmp/foo/model.gguf")` (with the file stubbed to exist,
     or via monkeypatching the existence check) resolves to a spec with
     `provider == "gguf"`, `gguf_path` absolute, `api_key_env is None`, and a
     correct `deepseek_model` (basename without `.gguf`).
   - `normalize_model("gguf@/tmp/foo/model.gguf")` produces the same result as
     the bare path.
   - a non-`.gguf`, non-`gguf@` value still resolves exactly as before (no
     accidental gguf classification).
   - `_derive_chat_url("gguf", "http://127.0.0.1:9")` ==
     `http://127.0.0.1:9/v1/chat/completions`.
   - `resolve_provider_for_slug("gguf@x")` == `"gguf"`.
6. **Manual smoke (if you have llama.cpp + a small GGUF available):**
   - `CODESEEQ_MODEL=/path/to/model.gguf codeseeq ping` returns `codeseeq-ok`.
   - a streamed `ping-stream` works.
   - killing the `codeseeq` process leaves no orphaned `llama-server`
     (`pgrep -f llama-server` is empty).
   - run twice concurrently and confirm two distinct ports and no conflict.
   If llama.cpp is not available in your environment, state so in your final
   summary and rely on the deterministic tests above plus a mocked subprocess
   test (monkeypatch `subprocess.Popen` / the health poll) that proves the
   lifecycle logic.

7. **Documentation** (the check suite verifies the version string appears in
   these; keep them in sync):
   - `.env.example`: add the `CODESEEQ_GGUF_*` block.
   - `README.md`: a short "Run a local GGUF model" section with the three usage
     forms and the `llama-server` prerequisite.
   - `docs/ARCHITECTURE.md`: add a "GGUF / local llama.cpp" subsection under the
     runtime/provider explanation and update the provider list if it enumerates
     providers.
   - `RELEASE-NOTES.md`: add a v0.4.x entry describing the feature.
   - `docs/TROUBLESHOOTING.md`: add the two most common failures (missing
     `llama-server`, missing/unreadable GGUF path) and their fixes.

8. Do **not** bump `VERSION` unless the repo's convention says so; keep the
   version string consistent with whatever docs you touch.

## Definition of done

- GGUF selection via `--model <path>.gguf`, `CODESEEQ_MODEL=<path>.gguf`, and
  `gguf@<path>` all work end-to-end in host/process mode.
- The bridge spawns, health-checks, reuses, and cleanly tears down `llama-server`.
- Every hosted provider and the existing `local`/`qwibus` providers are
  unaffected.
- `./scripts/check.sh` and your new `scripts/test-bridge-gguf.py` pass.
- Docs and `.env.example` are updated.
- You summarize exactly which files you changed and why, and report the full
  check-suite output.
