# Security

Current version: `0.2.7`

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

Manual Finder/macOS zips are forbidden for releases because they can include `__MACOSX`, `.DS_Store`, `.git/`, `.codeseeq/`, nested zips, and `.env` secrets. `.env.example` is allowed.

## Default Safety Posture

Default CodeSeeq mode is safe/containerized:

- Codex runs inside the CodeSeeq container.
- Codex cwd is `/workspace`.
- `/workspace` is the mounted host checkout.
- `approval_policy = "on-request"`
- `sandbox_mode = "workspace-write"`

This is not a hard security boundary for all threat models, but it is no longer the old yolo/default-danger behavior.

## Explicit Danger Mode

These forms opt into Codex's dangerous bypass:

```bash
./codeseeq -y ...
./codeseeq --yolo ...
./codeseeq --dangerously-bypass-approvals-and-sandbox ...
./codeseeq --sandbox danger-full-access ...
./codeseeq --sandbox danger-full-access ...
```

In danger mode:

- Codex runs locally on the host checkout.
- The bridge still runs in a Podman/Docker container.
- Local Codex uses isolated `CODEX_HOME=$PWD/.codeseeq`.
- CodeSeeq does not use the user's real `~/.codex`.

Danger mode can run commands and modify files directly on the host. Use it only when you intend that.

## Container Runtime

Podman is preferred. Docker is supported as a compatible fallback. Docker Compose is not supported.

Podman safe-mode bind mounts default to `:Z` for SELinux. Docker safe-mode bind mounts default to no suffix. `CODESEEQ_VOLUME_SUFFIX` can override this.

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

System prompts are not treated as secrets by default. They are sent to the model as `developer_instructions` on normal CodeSeeq/Codex requests. Do not place secrets in a system prompt unless you understand that risk.

`doctor` and `config` report prompt status/path/size/mechanism without printing content. Only `system view/show/cat` prints the full prompt.

## Prompt Files

`run -f/--file` sends the full file content to the model as task prompt text. Review task files before sending if they may contain secrets.

## Codex Profile Flags

`-p` and `--profile` are Codex profile-selection flags. They are not CodeSeeq prompt shortcuts. Use `./codeseeq "prompt"`, `./codeseeq run "prompt"`, or `./codeseeq run -f task.md` for direct prompt execution.

## Workspace Path Display

The safe-mode banner shows both host and container paths:

```text
CodeSeeq workspace:
  host: /path/to/project
  container: /workspace
```

This does not grant the container extra paths. It only explains where the `/workspace` bind mount lands on the host.

## Authentication Model

- No `codex login` flow is required for CodeSeeq model requests.
- Generated provider config uses `env_key = "DEEPSEEK_API_KEY"`.
- Generated provider config uses `requires_openai_auth = false`.
- `OPENAI_API_KEY` may be exported as a compatibility alias to `DEEPSEEK_API_KEY` for OpenAI-shaped tooling.

## Network Scope

- Safe-mode bridge binds to `127.0.0.1` inside the container.
- Danger host-mode bridge is published to the first free host port starting at `CODESEEQ_OPENRESPONSES_PORT`.
- Examples mount only the current project path into `/workspace`.
