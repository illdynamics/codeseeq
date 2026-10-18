# CodeSeeq v0.5.2 — GGUF configuration and safe live upgrade

CodeSeeq launches `llama-server` automatically in host/process mode when
`CODESEEQ_MODEL` points to a `.gguf` file. Version v0.5.2 changes **GGUF only**:

| Setting | Default | llama.cpp option |
| --- | ---: | --- |
| Context window (input + output) | `131072` | `-c 131072` |
| Maximum generated tokens **per request** | `65536` | bridge `max_tokens` cap; not a server CLI option |
| Temperature | `0.6` | `--temp 0.6` |
| Top-p | `0.95` | `--top-p 0.95` |
| Top-k | `20` | `--top-k 20` |
| Min-p | `0` | `--min-p 0` |
| Parallel slots | `1` | `-np 1` |

`0.6` is a reasonable starting point for modern instruction/reasoning GGUF
models. Lower temperature can make runs more repeatable, but **does not prove
higher accuracy**. If your specific model benchmarks better at 0.1–0.2, set
`CODESEEQ_GGUF_TEMPERATURE=0.1` in your user config, or a per-model JSON entry.

## Upgrade an existing installation (macOS/Linux)

1. **Stop existing CodeSeeq sessions** cleanly (`Ctrl+C` or `/exit`). Stop any
   separately managed `llama-server` only if you intend CodeSeeq to launch it.
2. **Backup your existing configuration** (avoid copying secrets into your git repo):

   ```bash
   mkdir -p "$HOME/codeseeq-backup-v0.5.1"
   cp -p "$HOME/.config/codeseeq/config.json" "$HOME/codeseeq-backup-v0.5.1/" 2>/dev/null || true
   cp -p "$HOME/.config/codeseeq/system-prompt.md" "$HOME/codeseeq-backup-v0.5.1/" 2>/dev/null || true
   cp -p "$HOME/.config/codeseeq/config/gguf-models.json" "$HOME/codeseeq-backup-v0.5.1/" 2>/dev/null || true
   ```

3. **Extract the downloaded ZIP** to any staging directory *outside* the
   installation directory, e.g. `~/Downloads/codeseeq-0.5.2`. The packaged
   release archive has `codeseeq`, `bin/`, `config/`, `scripts/` at its root.
4. **Install the local archive, not the old online release**:

   ```bash
   cd "$HOME/Downloads/codeseeq-0.5.2"
   chmod +x codeseeq
   ./codeseeq install
   ```

   This refreshes the default `~/.config/codeseeq` snapshot and `~/bin/codeseeq`
   launcher; you can set `CODESEEQ_INSTALL_DIR` / `CODESEEQ_BIN_DIR` for a
   nondefault live installation. v0.5.2's reinstall preserves top-level
   `config.json`, `.env`, `system-prompt.md`, `.codeseeq/`, and `*.local.json`.
   The bundled `config/gguf-models.json` is still updated as a release asset;
   restore any personal tweaks into a **separate** override file.
5. **Check the active executable and version**:

   ```bash
   export PATH="$HOME/bin:$PATH"
   command -v codeseeq
   codeseeq --version
   cat "$HOME/.config/codeseeq/VERSION"
   ```

6. **Keep your existing GGUF model path** if already configured, or run
   `codeseeq config` and choose GGUF, then enter the absolute path. To do a
   one-shot test without changing existing preferences, run:

   ```bash
   codeseeq --model "/ABSOLUTE/PATH/YOUR-MODEL.gguf" -y "Return exactly: gguf-ok"
   ```

7. **Verify your preferences**: inspect
   `~/.config/codeseeq/config.json` *locally* without sharing any API keys.
   Explicit `CODESEEQ_GGUF_*` values set by your shell or in config.json take
   precedence over the new defaults; per-model overrides take precedence over
   both. If you want the v0.5.2 defaults, remove old GGUF-specific values
   such as `8192`, `2048`, or your former sampler settings, or explicitly set:

   ```text
   CODESEEQ_GGUF_CONTEXT_WINDOW=131072
   CODESEEQ_GGUF_MAX_OUTPUT_TOKENS=65536
   CODESEEQ_GGUF_TEMPERATURE=0.6
   CODESEEQ_GGUF_TOP_P=0.95
   CODESEEQ_GGUF_TOP_K=20
   CODESEEQ_GGUF_MIN_P=0
   CODESEEQ_GGUF_PARALLEL=1
   ```

   Set these in `~/.config/codeseeq/config.json` using JSON key/value pairs,
   or export them in your shell. You may set
   `CODESEEQ_GGUF_N_GPU_LAYERS=99` on a compatible GPU-equipped machine to
   offload model layers; this is hardware-dependent and **not** changed by
   this release.

8. **Check for a separate upstream server**: if `GGUF_BASE_URL` is configured,
   CodeSeeq **reuses** the external server and cannot change its launch flags.
   Restart your separately managed llama-server with the above CLI options and
   `-c 131072`. CodeSeeq still applies sampling/output limits on API requests.

## Custom per-model override (persistent)

For advanced overrides, create e.g.
`~/.config/codeseeq/gguf-models.local.json`:

```json
{
  "models": {
    "your-model.gguf": {
      "context_window": 131072,
      "max_output_tokens": 65536,
      "temperature": 0.6,
      "top_p": 0.95,
      "top_k": 20,
      "min_p": 0,
      "parallel": 1
    }
  }
}
```

Set `CODESEEQ_GGUF_MODELS_JSON` to the absolute path of this file in your
`config.json` or shell. This file is preserved by the local installer. Entries
in your separate override take precedence over environment defaults.

## Hardware and model limitations

A model must actually support 131072 context tokens, and your hardware must
hold its KV cache. `65536` is an upper bound, **not** a reserved chunk of
output or a guaranteed generation length. The prompt, tool history, context
metadata, and generated output must fit into the **same** 131072 context. Very
long contexts can significantly raise memory use and latency (particularly on
32-GB Apple Silicon), and may require lower `CODESEEQ_GGUF_CONTEXT_WINDOW`,
smaller output cap, shorter prompts, or fewer GPU layers.

## Before publishing v0.5.2 on GitHub

Run `python3 scripts/test-bridge-gguf.py`, `./scripts/package.sh`, and
`./scripts/package.sh --check-archive /path/to/archive.zip` from the repo.
Push code to the `main` branch, wait for GitHub Actions CI to pass, **then**
create/push tag `v0.5.2`. The release workflow checks the tag matches
`VERSION` and publishes `codeseeq-v0.5.2.zip` automatically. A local ZIP is
not itself an already published GitHub release; do not use the one-line
internet installer until the release exists on GitHub.
