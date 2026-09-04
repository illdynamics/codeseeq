Global env (defaults in parens) — `codeseeq:174-186`, `.env.example:109-135`:

| Var | llama-server flag | Default |
|---|---|---|
| `CODESEEQ_GGUF_CONTEXT_WINDOW` | `-c` | 8192 |
| `CODESEEQ_GGUF_MAX_OUTPUT_TOKENS` | — | 2048 |
| `CODESEEQ_GGUF_N_GPU_LAYERS` | `-ngl` | 0 |
| `CODESEEQ_GGUF_THREADS` | `-t` | auto |
| `CODESEEQ_GGUF_PARALLEL` | `-np` | 1 |
| `CODESEEQ_GGUF_PORT` | `--port` | auto free port |
| `CODESEEQ_GGUF_TIMEOUT_SECONDS` | — | 600 |
| `CODESEEQ_GGUF_STARTUP_TIMEOUT_SECONDS` | — | 300 |
| `CODESEEQ_GGUF_ENABLE_THINKING` | — | false |
| `CODESEEQ_GGUF_LLAMA_SERVER_PATH` | binary | `llama-server` on PATH |
| `GGUF_API_KEY` | `--api-key` | unset |

**Per-model** overrides live in `config/gguf-models.json` (host) / `/etc/codeseeq/gguf-models.json` (container); point elsewhere with `CODESEEQ_GGUF_MODELS_JSON`. Keys can be absolute path, `~/` path, basename, or stem (without `.gguf`). Supported keys: `context_window`, `max_output_tokens`, `n_gpu_layers`, `threads`, `parallel`, `port`, `timeout_seconds`, `temperature`, `enable_thinking`. Precedence: **per-model JSON > env var > built-in default** (`bin/codeseeq-bridge.py:1075-1168`). Your repo already has a `config/gguf-models.json` with two Qwen models configured (e.g. `n_gpu_layers: "all"`, `context_window: 131072`).

### Notes / gotchas

- **Qwen3-style strict chat templates** reject multiple `system` messages (`Jinja Exception: System message must be at the beginning`). CodeSeeq collapses developer instructions + tool steering + system prompt into one leading system message, so both `gguf` and `local` paths work (`docs/TROUBLESHOOTING.md:558-563`).
- **Missing binary** → clean error: `gguf provider requires llama.cpp llama-server. Set CODESEEQ_GGUF_LLAMA_SERVER_PATH or install llama.cpp` (`bin/codeseeq-bridge.py:1171-1176`).
- **Missing file** → `gguf model file not found: <path>` (`bin/codeseeq-bridge.py:2150`).
- llama-server logs go to a temp file (`codeseeq-gguf-*.log`) if you need to debug startup.
- `qwibus` (`qwibus-qwikk` / `qwibus-qmplx`) is the legacy local-gateway provider (lightning-mlx); `local` is the general-purpose one.

If you want, I can also walk through adding your own models to `config/gguf-models.json` or wire up a `local@` alias config for your exact llama-server invocation — just say which model you're running.

## MLX local models (`mlx@<dir>`) — same lifecycle as GGUF

Global env (defaults in parens) — `codeseeq` MLX config block, `.env.example` MLX block:

| Var | mlx_lm.server meaning | Default |
|---|---|---|
| `CODESEEQ_MLX_CONTEXT_WINDOW` | context override | from the model `config.json` |
| `CODESEEQ_MLX_MAX_OUTPUT_TOKENS` | max output tokens | 2048 |
| `CODESEEQ_MLX_PORT` | `--port` | auto free port |
| `CODESEEQ_MLX_PYTHON` | interpreter that has `mlx_lm` | `python3` on PATH |
| `CODESEEQ_MLX_TIMEOUT_SECONDS` | — | 600 |
| `CODESEEQ_MLX_STARTUP_TIMEOUT_SECONDS` | health-poll | 600 |
| `CODESEEQ_MLX_ENABLE_THINKING` | — | false |
| `CODESEEQ_MLX_SERVER_ARGS` | extra server flags | unset |
| `CODESEEQ_MLX_BASE_URL` / `MLX_BASE_URL` | reuse an already-running server | unset |

**Per-model** overrides live in `config/mlx-models.json` (host) /
`/etc/codeseeq/mlx-models.json` (container); point elsewhere with
`CODESEEQ_MLX_MODELS_JSON`. Keys can be absolute path, `~/` path, basename, or
trailing component. Supported keys: `context_window`, `max_output_tokens`,
`port`, `timeout_seconds`, `temperature`, `top_p`, `top_k`,
`enable_thinking`, `server_args`. Precedence: **per-model JSON >
`CODESEEQ_MLX_*` env var > model `config.json` (`max_position_embeddings` /
`model_max_length`, incl. nested `text_config`) > built-in default**
(`bin/codeseeq-bridge.py` MLX section).

### MLX gotchas

- **Missing mlx-lm** → clean error: `mlx provider requires Apple MLX +
  mlx-lm. Install with: python3 -m pip install mlx-lm ... or set
  CODESEEQ_MLX_PYTHON` (`bin/codeseeq-bridge.py` MISSING_MLX_BINARY_MSG).
- **Missing/invalid directory** → `mlx model directory not found: <path>` /
  `mlx model directory has no config.json` / `no .safetensors weights`.
- mlx_lm.server is lazy: `/health` answers before the weights are loaded, so
  the first request can block while the model loads (raise
  `CODESEEQ_MLX_TIMEOUT_SECONDS` / `CODESEEQ_MLX_STARTUP_TIMEOUT_SECONDS` for
  very large models).
- Server logs go to a temp file (`codeseeq-mlx-*.log`) if you need to debug
  startup.
- External mode: `MLX_BASE_URL` / `CODESEEQ_MLX_BASE_URL` always mean "reuse a
  running server"; generic `OPENAI_BASE_URL` / `CODESEEQ_BASE_URL` only select
  external mode for mlx when they point at loopback (an ambient hosted-API
  base URL must never hijack a local model). A trailing `/v1` in the base URL
  is not doubled.
