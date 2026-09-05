# Plan: QonQrete Chat Interface — one chat message = one CodeSeeq prompt

Status: **plan** (not yet implemented)
Target version: next feature after `v0.4.9`
Owner: CodeSeeq maintainers
Related docs: `docs/ARCHITECTURE.md` (Prompt File Flow), `README.md` (`run -f`), `.env.example`

## 1. Goal & User Story

**Story.** "As a CodeSeeq user I want to type one chat message into a QonQrete
chat interface and have that message become the input to a CodeSeeq run —
exactly like I now pass a text file with `codeseeq run -f task.md`."

Today the only supported "single-shot prompt" inputs are:

- inline text: `codeseeq run "prompt"` / `codeseeq "prompt"`
- a text file: `codeseeq run -f task.md` / `--file=task.md`

This plan adds a third input source: **a QonQrete chat message**. The user
composes a message once in a chat interface (or a `codeseeq chat` TUI/one-shot
command), sends it, and the resulting message/response is consumed by the
normal prompt plumbing as if it were a file, then handed to Codex.

### Primary acceptance criteria

1. `codeseeq chat --once "…"` (or `codeseeq chat -m "…"`) sends **one** chat
   message to QonQrete and, on success, uses the chat turn as the CodeSeeq
   prompt — same code path as `run -f`, same stdin feeding, same flags.
2. Interactive mode: `codeseeq chat` opens a QonQrete chat loop; the *final*
   message of the session can be "exported/used as input" (`Ctrl+E` / `/run`
   command) and is then treated as the run prompt (file-equivalent).
3. Works in both runtime modes: host (`run_host_codex`) and container
   (`run_safe_container` + prompt-file rewrite), mirroring the existing
   `run -f` flow in `docs/ARCHITECTURE.md` → "Prompt File Flow".
4. Configuration lives in `.env.example`/`config.json` style keys
   (`QONQRETE_*`) and is optional: no QonQrete config → `codeseeq chat` fails
   with a clear setup hint, and nothing else changes.
5. Tests added under `scripts/check.sh` + a `scripts/smoke-chat-*.sh`; docs
   updated (README, docs/ARCHITECTURE.md, `.env.example`, RELEASE-NOTES).

## 2. Open questions (must resolve before/while implementing)

These are deliberately unresolved here because the QonQrete API contract is
not yet pinned down. Each maps to a decision point in Phase 1.

| # | Question | Options / default assumption |
|---|----------|------------------------------|
| Q1 | QonQrete transport | REST `POST /chat` (default assumption) vs SSE/WebSocket stream |
| Q2 | QonQrete auth | API key (`QONQRETE_API_KEY`) vs OAuth vs none |
| Q3 | Message semantics | Whole conversation history sent, or a single stateless message? (plan: single message) |
| Q4 | What becomes the CodeSeeq prompt | The user's raw message, or QonQrete's *reply* text, or a "task document" the chat produced? (plan: the chat turn result / reply text, configurable `QONQRETE_INPUT_MODE=message|reply|file`) |
| Q5 | Session/thread ids | Does QonQrete need `thread_id` / `conversation_id`? (plan: optional env override) |
| Q6 | Where the chat UI lives | New `codeseeq chat` TUI in the repo vs thin client to a hosted QonQrete chat page vs plain one-shot flag only |
| Q7 | Streaming replies | Must the chat stream? If yes the reply must be materialized to a temp file before Codex runs (like `run -f`) |

## 3. Architecture

### 3.1 The core idea: reuse the `run -f` pipeline

Instead of inventing a parallel input path, the chat result is materialized
into the exact same "prompt source" the wrapper already manages:

```text
codeseeq chat --once "rewrite auth to JWT"
   │
   ├─ 1. codeseeq chat CLI (new subcommand)
   │      · resolves QONQRETE_* config (env > config.json > defaults)
   │      · POST one message to QonQrete endpoint
   │      · receives turn result (text) → writes to a temp "prompt file"
   │
   ├─ 2. Reuse run -f plumbing
   │      · host runtime: file content fed to codex exec - (stdin)
   │      · container runtime: rewrite into .codeseeq/tmp/ + managed env
   │        (CODESEEQ_RUN_PROMPT_FILE), exactly like run -f
   │
   └─ 3. codex exec … -  < prompt   (all existing providers incl. chatgpt)
```

Implementation target: a function `run_from_chat_message()` that internally
calls the same `parse_run_prompt` / `rewrite_safe_run_file_args` /
`prepare_prompt_temp_from_text` helpers used by `run -f`, so behavior stays
identical (file size limits, markdown fences, container path rewrite, TMP
cleanup).

### 3.2 New surface area

**New subcommand: `codeseeq chat`**

```
codeseeq chat                       # interactive QonQrete chat TUI (later)
codeseeq chat --once "msg"          # one message → run prompt (this plan's core)
codeseeq chat --once -f plan.md     # read the single message from a file
codeseeq chat --once --model deepseek@deepseek-v4-pro "msg"   # choose model
codeseeq chat --once --runtime-mode container -f msg.txt      # container path
```

Flags reuse CodeSeeq conventions (`--once`, `--model`, `-f/--file`,
`--runtime-mode`, `--bridge-mode`, `--approval-policy`…). `chat` is a
CodeSeeq-owned subcommand, added to `is_codeseeq_diagnostic_command`? No —
it is **not** diagnostic; it is a first-class run mode (like `run`), so it
must bypass the "diagnostics go to container" shortcut and follow the normal
runtime resolution.

**New env/config keys** (all optional, documented in `.env.example`):

| Key | Purpose | Example |
|-----|---------|---------|
| `QONQRETE_BASE_URL` | QonQrete endpoint | `https://chat.qonqrete.example` |
| `QONQRETE_CHAT_URL` | full chat URL (override) | `…/api/chat` |
| `QONQRETE_API_KEY` | auth token | `qk-…` |
| `QONQRETE_THREAD_ID` | optional thread/conversation id | |
| `QONQRETE_INPUT_MODE` | what becomes the prompt | `reply` (default) |
| `QONQRETE_TIMEOUT_SECONDS` | request timeout | `60` |
| `QONQRETE_EXTRA_HEADERS` | extra JSON headers | `{"X-Tenant":"acme"}` |

**New script:** `bin/codeseeq-chat.py` (small, dependency-light; uses
`urllib`/`httpx` when available) to perform the QonQrete round-trip and print
the result document to stdout so the wrapper can capture it. Keeping the HTTP
client in Python (like `bin/codeseeq-venice-image.py`) avoids shell quoting
pain and matches repo layout.

### 3.3 Code paths to touch (when implemented)

1. `codeseeq` (host wrapper):
   - `print_help()`, top-level `case` → add `chat` subcommand.
   - new `cmd_chat()` → `run_chat_prompt()`:
     - resolve QONQRETE config; `die` with setup hint when unset.
     - `python3 bin/codeseeq-chat.py --once "<msg>"` → capture result.
     - write result to a temp file under `.codeseeq/tmp/` (same helper the
       `run -f` rewrite uses), then hand off to the shared exec path
       (`run_host_codex` for host mode / `run_safe_container` for container
       mode with `CODESEEQ_RUN_PROMPT_FILE` pointing at the managed copy).
2. `bin/codeseeq-entrypoint`: add `chat` case that mirrors `run_codex_exec`
   but sources the prompt from the QonQrete result file (container mode).
3. `bin/codeseeq-bridge.py`: **no changes needed** — chat is an input-source
   feature, not a provider; requests still go through the normal bridge path.
4. `config/…`, catalogs: no changes.
5. `scripts/check.sh`: hermetic fake-QonQrete server test asserting:
   - `codeseeq chat --once` writes result file and invokes codex with the
     same stdin content as `run -f`;
   - container path rewrites the prompt file into `.codeseeq/tmp/`;
   - missing `QONQRETE_*` produces the setup hint and exits non-zero.
6. Docs: README section, `docs/ARCHITECTURE.md` ("Chat Message Input" next to
   "Prompt File Flow"), `.env.example`, RELEASE-NOTES, `docs/TROUBLESHOOTING.md`.

### 3.4 Suggested milestones

| Phase | Deliverable | Exit criteria |
|-------|-------------|---------------|
| P0 | Contract spike | Fake/real QonQrete endpoint captured; sample curl works; Q1–Q7 answered |
| P1 | One-shot core (host) | `codeseeq chat --once "…"` == `codeseeq run -f <file>` byte-for-byte on stdin; passes with fake QonQrete |
| P2 | Container parity | Same behavior through `run_safe_container` + `.codeseeq/tmp` rewrite |
| P3 | Interactive `codeseeq chat` | Chat loop + `/run` (use last message as prompt), streaming if Q7 says so |
| P4 | Hardening | check.sh tests, docs, version bump, release notes |

## 4. Risks & mitigations

- **QonQrete contract drift** → isolate all HTTP behind `bin/codeseeq-chat.py`
  with a tiny adapter layer; CI runs a mock server.
- **Prompt size** → reuse `run -f` limits/checks; stream reply to temp file
  instead of shell variable if large (Q7).
- **Interactive chat complexity** → ship `--once` first (P1/P2), defer full
  TUI to P3; `--once` alone satisfies the user story above.
- **Secret hygiene** → `QONQRETE_API_KEY` follows the same rules as provider
  keys: never printed, chmod 600 config, excluded from release archives,
  `.env.example` only documents the name.

## 5. Definition of done

- `codeseeq chat --once "message"` produces the same Codex invocation as
  `codeseeq run -f` with that message as file content (verified by check.sh).
- Works in host and container runtime; works with `chatgpt` provider too.
- New env keys documented; README + ARCHITECTURE + RELEASE-NOTES updated.
- `./scripts/check.sh` passes; new smoke script added.
