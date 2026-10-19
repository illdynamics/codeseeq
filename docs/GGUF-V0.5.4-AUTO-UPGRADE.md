# CodeSeeq v0.5.4 — `gguf@auto` drop-in upgrade

## What changes

`gguf@auto` attaches to **an already-running** llama.cpp / OpenAI-compatible
GGUF server. It reads `GET /v1/models`, uses the server-advertised loaded model
ID when sending `POST /v1/chat/completions`, and rediscovers the ID on every
request so switching your loaded model does not require updating CodeSeeq.

- No GGUF file path needed.
- No manual upstream model name needed.
- No second llama-server is spawned.
- With no endpoint configured, CodeSeeq checks previously configured gateway
  base URLs and local ports 8888, 8080, 8000, 1234, 5000, 11434 (plus
  `CODESEEQ_GGUF_PORT` if set).
- With `GGUF_BASE_URL` configured, **only that URL** is used. This supports a
  LAN/ZeroTier server and avoids silently falling back to the wrong model.
- A server advertising more than one loaded model is ambiguous and rejected;
  use a single-model endpoint or explicit `gguf@/path/model.gguf`.
- Optionally authenticate using `GGUF_API_KEY`; the key is sent on discovery
  and chat requests.

> The 131072/65536/0.6/0.95/20/0 sampling parameters remain defaults for
> CodeSeeq's **requests**. They do not reconfigure an external server's
> KV cache, context window or llama.cpp startup options. Start your server with
> the context capacity and output limit its memory can actually support.

## Reinstall on your Mac (keep configuration)

1. Stop existing CodeSeeq CLI sessions, but **leave llama-server running**.
2. Make a private backup of your current user config:

   ```bash
   mkdir -p "$HOME/codeseeq-backup-v0.5.4"
   cp -p "$HOME/.config/codeseeq/config.json" "$HOME/codeseeq-backup-v0.5.4/" 2>/dev/null || true
   cp -p "$HOME/.config/codeseeq/system-prompt.md" "$HOME/codeseeq-backup-v0.5.4/" 2>/dev/null || true
   cp -p "$HOME/.config/codeseeq/gguf-models.local.json" "$HOME/codeseeq-backup-v0.5.4/" 2>/dev/null || true
   ```

3. Extract the downloaded `codeseeq-v0.5.4.zip` into a **new** directory,
   separate from the live installation (the ZIP has repository files at root):

   ```bash
   mkdir -p "$HOME/Downloads/codeseeq-v0.5.4"
   unzip -oq "$HOME/Downloads/codeseeq-v0.5.4.zip" -d "$HOME/Downloads/codeseeq-v0.5.4"
   cd "$HOME/Downloads/codeseeq-v0.5.4"
   ./codeseeq install
   ```

4. Run the new launcher explicitly if `~/bin` is not in PATH:

   ```bash
   export PATH="$HOME/bin:$PATH"
   cat "$HOME/.config/codeseeq/VERSION"   # expected v0.5.4
   codeseeq config
   ```

5. In the interactive wizard, select **GGUF**. At the model prompt accept
   `auto` (Enter). Leave endpoint URL empty for local autodiscovery, or enter
   your existing server's URL **only if it uses a nonstandard port or remote
   address**. The wizard saves `CODESEEQ_MODEL=gguf@auto` for subsequent runs.

6. Check the model advertised by the existing server, if you know its port:

   ```bash
   curl -fsS http://127.0.0.1:8888/v1/models
   ```

7. Smoke-test CodeSeeq:

   ```bash
   codeseeq -m gguf@auto -y 'Reply with gguf-auto-ok only.'
   ```

### Non-interactive one-time choice

If your server is already on a recognized local port, you can skip the wizard:

```bash
codeseeq --model gguf@auto -y 'Reply with gguf-auto-ok only.'
```

For a remote or nonstandard endpoint:

```bash
GGUF_BASE_URL=http://127.0.0.1:9999/v1 codeseeq -m gguf@auto -y 'Hello'
```

To persist this selection without changing other user settings:

```bash
python3 - <<'PY'
import json
from pathlib import Path
p = Path.home() / '.config/codeseeq/config.json'
config = json.loads(p.read_text()) if p.is_file() else {}
config['CODESEEQ_MODEL'] = 'gguf@auto'
config['CODESEEQ_PROVIDER'] = 'gguf'
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps(config, indent=2) + '\n')
p.chmod(0o600)
PY
```

**Troubleshooting:** If the discovery step fails, check the server's actual
`/v1/models` output and whether it returns exactly one active model. If the
server is on a different port/host, set `GGUF_BASE_URL=http://HOST:PORT/v1`.
If it requires authentication, set `GGUF_API_KEY` securely in the environment
or private config. Only trusted endpoints should receive your prompts.

## Release/CI

`./scripts/check.sh`, `python3 scripts/test-bridge-gguf-auto.py`,
`python3 scripts/test-bridge-gguf.py`, and
`./scripts/test-install-preservation.sh` are the local checks. GitHub Actions
runs these alongside its static, project, bridge-smoke and Docker jobs.
After committing the repo to GitHub, push `main` and verify CI there before
pushing tag `v0.5.4`, which must match `VERSION`.
