# CodeSeeq Qwibus Local Model — Full Fix Task

## Context

You are running on a MacBook M1 Max 32GB RAM.

CodeSeeq is installed locally. The `codeseeq` binary resolves `CODESEEQ_INSTALL_ROOT`
to the directory containing itself (i.e. the real path of the `codeseeq` script).

The local MLX server runs via `~/bin/ai`:
```bash
lightning-mlx serve \
  /Users/wicked/Qoding/ai/Qwen3.6-27B-Fable-Fusion-711-MTPLX-4bit \
  --mtp-num-draft-tokens 2 \
  --served-model-name qwibus-qwikk \
  --served-model-name qwibus-qmplx \
  --host 127.0.0.1 \
  --port 1337
```

The aliases in use are:
```bash
qwf  = CODESEEQ_ALLOW_UPSTREAM_CODEX_SERVICES=true CODESEEQ_RUNTIME_MODE=host codeseeq -m qwibus-qwikk -y run
qwft = CODESEEQ_ALLOW_UPSTREAM_CODEX_SERVICES=true CODESEEQ_RUNTIME_MODE=host codeseeq -m qwibus-qmplx -y run
```

---

## Known Problems To Fix

### Problem 1 — Bridge runs on pure built-in defaults (no catalog loaded)

The bridge (`codeseeq-bridge.py`) loads `model-catalog.json` at Python import time via
`_apply_catalog_overrides()`. It reads the path from env var `CODESEEQ_MODEL_CATALOG_JSON`,
defaulting to `/etc/codeseeq/model-catalog.json` which does NOT exist on macOS host.

Result: bridge ignores the `model-catalog.json` entirely and uses its built-in hardcoded
defaults — `context_window: 16384`, `max_output_tokens: 4096`, no `system_prompt`.

**Fix:** `CODESEEQ_MODEL_CATALOG_JSON` must be set to the actual path of `model-catalog.json`
inside the CodeSeeq install root (i.e. `<CODESEEQ_INSTALL_ROOT>/config/model-catalog.json`).

### Problem 2 — No `~/.codeseeq/config.toml` exists

`write_host_config()` writes `~/.codeseeq/config.toml` only when a prompt is run.
But `CODESEEQ_CONTEXT_WINDOW` and `CODESEEQ_MAX_OUTPUT_TOKENS` in that config.toml
default to `1000000` and `384000` — values meant for DeepSeek, not a local 32GB model.

For `qwibus-qwikk` these must be overridden to match the model's actual capacity:
- `CODESEEQ_CONTEXT_WINDOW=32768` (safe headroom for 32GB M1 Max with 27B 4-bit model)
- `CODESEEQ_MAX_OUTPUT_TOKENS=4096`

For `qwibus-qmplx`:
- `CODESEEQ_CONTEXT_WINDOW=32768`
- `CODESEEQ_MAX_OUTPUT_TOKENS=8192`

### Problem 3 — `model-catalog.json` has wrong `context_window` for qwibus-qwikk

Current value: `16384`. With 10 tool schemas + system prompt this is dangerously tight.
The model can safely handle 32768 on 32GB RAM with a 27B 4-bit quantised model.

### Problem 4 — `codex-model-catalog.json` `base_instructions` field is a dead-end

The `base_instructions` field in `codex-model-catalog.json` is consumed by Codex CLI UI only.
The bridge never reads it. The actual mechanism to prepend text to every request is
`system_prompt` in `model-catalog.json`, which the bridge inserts as a `{"role":"system"}`
message prepended to the messages array.

The Qwen3 model family requires `/no_think` prepended to suppress chain-of-thought leaking
into output when `enable_thinking: false`. This must go in `model-catalog.json` as
`"system_prompt": "/no_think"` for `qwibus-qwikk` only (qmplx uses thinking mode).

### Problem 5 — Double system message in requests

MLX logs show `roles=['system', 'system', 'user', 'user']`. Two system messages are being
sent. Investigate whether CodeSeeq's bridge is injecting a system prompt AND the Codex CLI
config's `developer_instructions` field is also being populated. Ensure only one system
message reaches the model for qwibus models.

### Problem 6 — Tools should still work when needed

Do NOT disable tools entirely. The `tools=10` is high but Codex tools are how the agent
does useful work (bash, file read/write, etc.). The fix is giving the model enough context
headroom to handle them, not stripping them. With `context_window: 32768` and a lean
`system_prompt: "/no_think"`, there is sufficient space.

---

## What To Do

### Step 1 — Find the real install root

```bash
realpath $(which codeseeq)
# Note the directory — this is CODESEEQ_INSTALL_ROOT
# Catalogs live at: <INSTALL_ROOT>/config/model-catalog.json
#                   <INSTALL_ROOT>/config/codex-model-catalog.json
```

### Step 2 — Update `model-catalog.json`

In `<INSTALL_ROOT>/config/model-catalog.json`, make these changes to the qwibus entries:

**qwibus-qwikk** — set:
```json
"context_window": 32768,
"max_output_tokens": 4096,
"system_prompt": "/no_think"
```

**qwibus-qmplx** — set:
```json
"context_window": 32768,
"max_output_tokens": 8192,
"system_prompt": ""
```

All other fields remain unchanged.

### Step 3 — Update `codex-model-catalog.json`

In `<INSTALL_ROOT>/config/codex-model-catalog.json`, update the qwibus entries:

**qwibus@qwibus-qwikk** — set:
```json
"context_window": 32768,
"max_context_window": 128000,
"truncation_policy": { "mode": "tokens", "limit": 32768 }
```

**qwibus@qwibus-qmplx** — set:
```json
"context_window": 32768,
"max_context_window": 128000,
"truncation_policy": { "mode": "tokens", "limit": 32768 }
```

### Step 4 — Update the shell aliases

In `~/.zshrc` (or wherever aliases live), replace the `qwf` and `qwft` aliases.

First, determine `INSTALL_ROOT` from Step 1. Then set aliases to:

```bash
# CodeSeeq local qwibus aliases
export CODESEEQ_INSTALL_ROOT="<INSTALL_ROOT_FROM_STEP_1>"

alias qwf='CODESEEQ_ALLOW_UPSTREAM_CODEX_SERVICES=true \
  CODESEEQ_RUNTIME_MODE=host \
  CODESEEQ_MODEL_CATALOG_JSON="${CODESEEQ_INSTALL_ROOT}/config/model-catalog.json" \
  CODESEEQ_CONTEXT_WINDOW=32768 \
  CODESEEQ_MAX_OUTPUT_TOKENS=4096 \
  codeseeq -m qwibus-qwikk -y run'

alias qwft='CODESEEQ_ALLOW_UPSTREAM_CODEX_SERVICES=true \
  CODESEEQ_RUNTIME_MODE=host \
  CODESEEQ_MODEL_CATALOG_JSON="${CODESEEQ_INSTALL_ROOT}/config/model-catalog.json" \
  CODESEEQ_CONTEXT_WINDOW=32768 \
  CODESEEQ_MAX_OUTPUT_TOKENS=8192 \
  codeseeq -m qwibus-qmplx -y run'
```

### Step 5 — Verify the fix

After sourcing the updated shell config (`source ~/.zshrc`), run:

```bash
# 1. Check the bridge receives the catalog
CODESEEQ_MODEL_CATALOG_JSON="<INSTALL_ROOT>/config/model-catalog.json" \
  python3 -c "
import os, json
os.environ['CODESEEQ_MODEL_CATALOG_JSON'] = '<INSTALL_ROOT>/config/model-catalog.json'
import sys; sys.path.insert(0, '<INSTALL_ROOT>/bin')
# Just validate JSON loads correctly
with open(os.environ['CODESEEQ_MODEL_CATALOG_JSON']) as f:
    cat = json.load(f)
for m in cat['models']:
    if 'qwibus' in m['id']:
        print(m['id'], '→ context_window:', m['context_window'], '| system_prompt:', repr(m.get('system_prompt','')))
"

# 2. Make sure the MLX server is running, then do a quick smoke test
qwf "say hi and confirm you are working"

# 3. Check the MLX server log — you should now see:
#    - prompt_tokens significantly lower than before
#    - roles=['system', 'user'] (single system message only)
#    - NO chain-of-thought leaking into the response content
```

### Step 6 — Investigate double system message

If after the above you still see `roles=['system', 'system', ...]` in the MLX logs:

```bash
# Check if a CodeSeeq system prompt is installed
codeseeq system view

# If it shows content, and you don't want it, remove it:
codeseeq system remove

# Also check ~/.codeseeq/config.toml after a run for developer_instructions field
cat ~/.codeseeq/config.toml | grep -A2 developer_instructions
```

The double system message is caused by EITHER:
- A persistent CodeSeeq system prompt (`~/.config/codeseeq/system-prompt.md` exists)
- The bridge prepending `system_prompt` from catalog AND Codex injecting its own

If the system prompt file exists and is unwanted, remove it. If it is wanted, it will merge
with `/no_think` — in that case prepend `/no_think\n` to the system-prompt.md content
instead of relying on the catalog `system_prompt` field, and clear `system_prompt` in the
catalog back to `""`.

---

## Expected End State

- `qwf "say hi"` completes successfully with a clean response (no CoT leaking)
- MLX server log shows `prompt_tokens` well under 5000 for simple prompts
- MLX server log shows `roles=['system', 'user']` (single system message)
- Tools remain active and functional for file/bash operations
- `qwft` works with thinking mode enabled on qmplx
- Both aliases have `CODESEEQ_MODEL_CATALOG_JSON` set so bridge loads real config
