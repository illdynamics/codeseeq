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
tokens used
1,530,954
[codeseeq] stopping owned bridge process (pid=35070)

