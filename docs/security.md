# Security

Current version: `0.2.5`

## Runtime Secrets

- `DEEPSEEK_API_KEY`, `BRAVE_API_KEY`, and `UNSTRUCTURED_API_KEY` are runtime-only.
- Secrets are not baked into the image.
- Secrets are not written to `/home/codeseeq/.codeseeq/config.toml`.
- Do not run with `set -x` when loading `.env`.

## `.env` Handling

Use read-only loading pattern for live tests:

```bash
set -a
source .env
set +a
```

Do not modify `.env` from scripts.

## Config Isolation

- Default `CODESEEQ_CODEX_HOME=/home/codeseeq/.codeseeq`
- `CODEX_HOME` is exported from that value before running Codex.
- Supported CodeSeeq paths do not use host `~/.codex`.

## Authentication Model

- No `codex login` flow is required.
- Provider config uses `env_key = "DEEPSEEK_API_KEY"`.
- `requires_openai_auth = false` in generated provider config.
- `OPENAI_API_KEY` may be exported as compatibility alias to `DEEPSEEK_API_KEY` for OpenAI-shaped tooling, not as OpenAI authentication.

## Network Scope

- Bridge binds to `127.0.0.1` by default inside container.
- Examples mount only current project path into `/workspace`.
- Podman bind mounts use `:Z` for SELinux compatibility.

## Safety Posture

Default mode is intentionally dangerous/autonomous:

- `approval_policy = "never"`
- `sandbox_mode = "danger-full-access"`

This mode can execute commands and modify files without additional prompts.

`--yolo` / `-y` is a launch-time shortcut for the same unsafe Codex bypass
path. It adds Codex's `--dangerously-bypass-approvals-and-sandbox` switch and
does not rewrite generated config.
