# CodeSeeq Quickstart

Current version: `0.2.5`

## 1. Install user-local command

From the CodeSeeq repo:

```bash
./codeseeq install
```

This installs:

- repo snapshot: `~/.config/codeseeq`
- launcher: `~/bin/codeseeq`

If needed, add `~/bin` to `PATH`.

## 2. Prereqs

- Podman installed
- `DEEPSEEK_API_KEY` available in your shell
- Optional: `BRAVE_API_KEY` for web search, `UNSTRUCTURED_API_KEY` for doc parsing

## 3. Build image

```bash
codeseeq models
```

The first command auto-builds `codeseeq:dev` if missing.

## 4. Start interactive mode

```bash
codeseeq
```

`codeseeq` always mounts your current directory into `/workspace`, so coding happens in the directory you run it from.

## 5. Run one prompt

```bash
codeseeq run "Return exactly: codeseeq-ok"
```

Use `--yolo` / `-y` when you intentionally want Codex launched with
`--dangerously-bypass-approvals-and-sandbox`; `run`/`exec` paths also include
`--skip-git-repo-check`:

```bash
codeseeq --yolo run "create ./test.txt with hi"
```

## 6. Health and diagnostics

```bash
codeseeq models
codeseeq doctor
codeseeq ping
codeseeq ping-stream
codeseeq ping-web
codeseeq ping-docs
```

## 7. Optional `.env` loading for local tests

```bash
set -a
source .env
set +a
```

Do not modify `.env` from automation.

## 8. Container-only usage

```bash
podman run --rm -it \
  -e DEEPSEEK_API_KEY="$DEEPSEEK_API_KEY" \
  -e BRAVE_API_KEY="$BRAVE_API_KEY" \
  -e UNSTRUCTURED_API_KEY="$UNSTRUCTURED_API_KEY" \
  -v "$PWD:/workspace:Z" \
  -w /workspace \
  codeseeq:dev
```
