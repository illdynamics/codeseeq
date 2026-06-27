# Security

Current version: `v0.3.1`

## Runtime Secrets

- `DEEPSEEK_API_KEY`, `BRAVE_API_KEY`, and `UNSTRUCTURED_API_KEY` are runtime-only.
- Secrets are not baked into the image.
- Secrets are not written to generated Codex config.
- Release archives must not contain `.env`, `.env.*`, `*.env`, `.codeseeq/`, `.git/`, logs, nested zips, or local workspace state.
- Do not run with `set -x` when loading `.env`.

Load `.env` read-only for tests:

```bash
set -a
source .env
set +a
```

Do not modify `.env` from automation.

## Release Archives

Create release zips only with:

```bash
./scripts/package.sh
./codeseeq package
make package
```

Check any generated or uploaded archive before release:

```bash
./scripts/package.sh --check
./scripts/package.sh --check-archive /mnt/data/codeseeq.zip
```

Manual Finder/macOS zips are forbidden for releases because they can include
`__MACOSX`, `.DS_Store`, `.git/`, `.codeseeq/`, nested zips, and `.env`
secrets. `.env.example` is allowed.

## Default Safety Posture

Default CodeSeeq mode is safe/containerized:

- Codex runs inside the CodeSeeq container.
- Codex cwd is `/workspace`.
- `/workspace` is the mounted host checkout.
- `approval_policy = "on-request"`
- `sandbox_mode = "workspace-write"`

This is not a hard security boundary for all threat models, but it is no longer
the old yolo/default-danger behavior.

## Explicit Danger Mode

These forms opt into Codex's dangerous bypass:

```bash
./codeseeq -y ...
./codeseeq --yolo ...
./codeseeq --dangerously-bypass-approvals-and-sandbox ...
./codeseeq --sandbox danger-full-access ...
```

In danger mode:

- Codex runs locally on the host checkout.
- The bridge still runs in a Podman/Docker container (or as a process).
- Local Codex uses isolated `CODEX_HOME=$PWD/.codeseeq`.
- CodeSeeq does not use the user's real `~/.codex`.

Danger mode can run commands and modify files directly on the host. Use it only
when you intend that.

## YOLO Environment Variable

Setting `CODESEEQ_YOLO=true` in `.env` or your shell is equivalent to passing
`-y` on every invocation:

```bash
export CODESEEQ_YOLO=true
./codeseeq run "fix the tests"    # runs in danger mode automatically
```

## Container Runtime

Podman is preferred. Docker is supported as a compatible fallback. Docker
Compose is not supported.

Podman safe-mode bind mounts default to `:Z` for SELinux. Docker safe-mode
bind mounts default to no suffix. `CODESEEQ_VOLUME_SUFFIX` can override this.

## Config Isolation

Safe/container mode:

```text
CODEX_HOME=/home/codeseeq/.codeseeq
```

Danger host mode:

```text
CODEX_HOME=$PWD/.codeseeq
```

No supported path mounts or writes the user's normal `~/.codex`.

## Persistent System Prompt

System prompts are stored in user-level CodeSeeq config:

```text
~/.config/codeseeq/system-prompt.md
```

System prompts are not treated as secrets by default. They are sent to the
model as `developer_instructions` on normal CodeSeeq/Codex requests. Do not
place secrets in a system prompt unless you understand that risk.

`doctor` and `config` report prompt status/path/size/mechanism without printing
content. Only `system view/show/cat` prints the full prompt.

## Prompt Files

`run -f/--file` sends the full file content to the model as task prompt text.
Review task files before sending if they may contain secrets.

## Codex Profile Flags

`-p` and `--profile` are Codex profile-selection flags. They are not CodeSeeq
prompt shortcuts. Use `./codeseeq "prompt"`, `./codeseeq run "prompt"`, or
`./codeseeq run -f task.md` for direct prompt execution.

## Workspace Path Display

The safe-mode banner shows both host and container paths:

```text
CodeSeeq workspace:
  host: /path/to/project
  container: /workspace
```

This does not grant the container extra paths. It only explains where the
`/workspace` bind mount lands on the host.

## Authentication Model

- No `codex login` flow is required for CodeSeeq model requests.
- Generated provider config uses `env_key = "DEEPSEEK_API_KEY"`.
- Generated provider config uses `requires_openai_auth = false`.
- `OPENAI_API_KEY` is no longer auto-populated from `DEEPSEEK_API_KEY` for privacy hardening.

## Privacy Hardening

CodeSeeq applies privacy hardening by default in every generated Codex config:

```toml
web_search = "live"

[analytics]
enabled = false

[feedback]
enabled = false

[otel]
exporter = "none"
metrics_exporter = "none"
trace_exporter = "none"
log_user_prompt = false

[history]
persistence = "none"
```

Additional hardening beyond telemetry:

- **Upstream Codex commands blocked:** `login`, `logout`, `cloud`, `app`, `app-server`, `plugin`, `update`, `features`, and `remote-control` are blocked by default. Set `CODESEEQ_ALLOW_UPSTREAM_CODEX_SERVICES=true` to override.
- **Codex version pinned:** The Dockerfile and Makefile use a pinned `CODEX_NPM_VERSION` (default: `0.130.0`) instead of `latest`. Set `CODESEEQ_ALLOW_LATEST_RELEASE=true` to allow latest release fetching in the installer.
- **No OPENAI_API_KEY aliasing:** `DEEPSEEK_API_KEY` is used directly. It is not exported as `OPENAI_API_KEY`.
- **Network diagnostics guard:** Use `CODESEEQ_ALLOW_NETWORK_DIAGNOSTICS=true` to enable diagnostics that contact third-party services outside the normal model/web-search path.

## Network Scope

- Live web search is enabled and routed through the CodeSeeq/Brave bridge path.
- Model requests go exclusively to DeepSeek.
- Diagnostics that contact non-DeepSeek services require explicit opt-in.
- Safe-mode bridge binds to `127.0.0.1` inside the container.
- Danger host-mode bridge is published to the first free host port starting at
  `CODESEEQ_BRIDGE_PORT` or auto-selected.
- Examples mount only the current project path into `/workspace`.

## CI / Release Security

- Release artifacts are built by the GitHub Actions CI pipeline, not manually.
- The release job only runs on version tag pushes (`v*`) and only after all
  CI checks pass (`static`, `project`, `bridge-smoke`, `docker`).
- Release archives are validated by `scripts/package.sh --check-archive` inside
  the pipeline before upload.
- Manual release zips created outside the CI pipeline are not permitted.
