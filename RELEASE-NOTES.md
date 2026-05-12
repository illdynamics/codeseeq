## 0.2.9 - 2026-05-12

### Fixed
- **Flags after `run` now parsed before runtime dispatch.**  
  `./codeseeq run --runtime-mode host --bridge-mode process "hello"` correctly routes to host runtime.
- **Host diagnostics no longer require container.**  
  `models`, `doctor`, and `config` work without Docker/Podman in host runtime mode.
- **Process bridge cleanup verified.** Owned bridges stopped on all exit paths (EXIT/INT/TERM/HUP).
- **Prompt temp files no longer leak.** `TMP_FILES` properly tracked after command substitution.
- **External mode supports `/v1/` trailing slash.**
- **0.0.0.0 bridge host now writes `127.0.0.1` in client config** (warning printed).
- **`make bridge-process-smoke` no longer leaks processes.** POSIX-safe script with cleanup trap.
- **Container bridge smoke updated** with correct model IDs and bridge host binding.
- **Doctor output cleaned up** — "Bridge URL" instead of "OpenResponses", runtime/bridge mode fields.
- **Package hygiene strengthened** — `make clean-artifacts`, `make package-check`, docs warn against manual zips.
- **No Codex source modified.** No upstream `open-responses` runtime dependency.


### Added

- **Host-native bridge process mode.** `CODESEEQ_BRIDGE_MODE=process` starts
  `bin/codeseeq-bridge.py` as a direct child process on the host with no
  Docker or Podman required. All four bridge modes are supported: `process`,
  `container`, `external`, and `auto` (default, prefers process when Python
  dependencies are available, falling back to container).
- **Host runtime mode.** `CODESEEQ_RUNTIME_MODE=host` launches Codex directly
  on the host alongside a process bridge, without going through the container
  runtime at all. `container` and `auto` (default) remain available.
- **External bridge mode.** `CODESEEQ_BRIDGE_MODE=external` with
  `CODESEEQ_BRIDGE_BASE_URL` lets CodeSeeq talk to a pre-existing bridge
  without starting anything locally.
- **Bridge reuse.** `CODESEEQ_BRIDGE_REUSE=1` causes CodeSeeq to check for a
  healthy bridge at the configured port and reuse it instead of starting a
  new one.
- **CLI flags for bridge configuration.** `--bridge-mode`, `--bridge-url`,
  and `--bridge-port` are now accepted on the `codeseeq` launcher and the
  `run` subcommand so bridge mode can be set per-invocation without
  environment variables.

### Changed

- **Dockerfile no longer pulls `open-responses` npm dependency.** The
  upstream `open-responses` package was removed from the container image
  build. The actual runtime bridge is entirely `bin/codeseeq-bridge.py`.
- **Bridge mode architecture rewrite.** The launcher now has a unified
  `bridge_start()` abstraction that selects between process, container,
  external, and auto modes with consistent health-check and cleanup
  behavior. Process-mode owned bridges are stopped on launcher exit.

### Notes

- No Codex source was modified. The bridge remains a drop-in
  Responses-compatible API that Codex talks to exactly as it would talk to
  any OpenAI-compatible provider.
- `wire_api = "responses"` in the generated Codex config stays because Codex
  expects that value; it does not mean the upstream `open-responses` package
  is used.

---

## 0.2.8 - 2026-05-12

## 0.2.7 - 2026-05-08

### Added

- **`codeseeq nuke` subcommand.** Uninstalls all CodeSeeq user artifacts — the
  installed snapshot at `~/.config/codeseeq`, the launcher at `~/bin/codeseeq`,
  and any leftover `~/.codeseeq` state — with a confirmation prompt before
  removal. The local repo checkout and container images are left untouched.

### Changed

- **README.md description rewritten.** The first paragraph now describes
  CodeSeeq as a "drop-in launcher and CLI wrapper" rather than a "command
  switch", better reflecting its role as a full launcher/substitute that
  manages container runtime, bridge lifecycle, configuration, and DeepSeek
  model wiring.
- **CodeSeeq splash image.** The `codeseeq.jpg` image was added to the README
  between the introductory paragraph and the version/release-notes section.
- **Version bumped to `0.2.7`.** Updated `VERSION`, `README.md`, and all
  doc-version references in `docs/ARCHITECTURE.md`, `docs/SECURITY.md`, and
  `docs/TROUBLESHOOTING.md`.

### Fixed

- **Typo `--sanbox` in README.md and doc examples.** The documented alias
  examples in `README.md`, `docs/TROUBLESHOOTING.md`, and `docs/SECURITY.md`
  were using `--sanbox` instead of the correct `--sandbox`. Fixed to show the
  proper flag spelling while keeping `--sanbox` as an accepted internal alias.

---

# Release Notes

## 0.2.6 - 2026-05-08

### Added

- **Container-launcher rewrite.** The `./codeseeq` launcher was substantially
  rewritten with robust configuration defaults, helper functions (`warn`,
  `bool_normalize`), and expanded environment-variable plumbing for
  `CODESEEQ_MODEL`, `CODESEEQ_THINKING`, `CODESEEQ_APPROVAL_POLICY`,
  `CODESEEQ_SANDBOX_MODE`, `CODESEEQ_OPENRESPONSES_PORT`,
  `CODESEEQ_OPENRESPONSES_URL`, `CODESEEQ_CONTEXT_WINDOW`,
  `CODESEEQ_HOST_CODEX_HOME`, and `CODESEEQ_SYSTEM_PROMPT_FILE`.
- **System prompt injection.** New `CODESEEQ_SYSTEM_PROMPT_FILE` (default
  `${WORKDIR}/.codeseeq/system-prompt.md`) is read and injected into Codex's
  TOML config as a quoted string. Helper functions (`system_prompt_present`,
  `system_prompt_state`, `system_prompt_bytes`, `system_prompt_lines`) report
  prompt state at startup.
- **Workspace banner.** Entrypoint prints a summary banner showing the
  workspace path, version, model, approval policy, sandbox mode, key
  configuration hash, and system-prompt state on each launch.
- **`.env.example` template.** Documented all supported environment variables
  with their defaults, so users can copy `.env.example` to `.env` and customize.
- **Expanded smoke-test suite.** `scripts/smoke-all.sh` now runs container
  smoke tests and host-cli smoke tests. New `scripts/runtime.sh` checks for
  container and GPU host capabilities.
- **Bridge binary on `codeseeq-bridge.py`.** The bridge now lives at its own
  path (`bin/codeseeq-bridge.py`) in the container, launched side-by-side with
  Codex rather than being embedded.

### Changed

- **Launcher becomes dual-purpose.** `./codeseeq` now detects the `install`
  subcommand automatically and delegates to `scripts/install.sh`. Running
  without subcommand starts the container with all configuration variables
  forwarded.
- **`CODESEEQ_WORKDIR_HOST` now resolves symlinks.** Uses `pwd -P` instead of
  plain `$PWD` so bind-mount paths are canonical.
- **Documentation overhaul.** `README.md`, `docs/ARCHITECTURE.md`,
  `docs/SECURITY.md`, and `docs/TROUBLESHOOTING.md` were rewritten with
  up-to-date configuration references, container-runtime instructions, and
  security/architecture guidance.
- **Scripts polished.** `scripts/check.sh` extended with bridge-extraction
  regression tests; `scripts/package.sh` streamlined; `scripts/install.sh`
  updated for the new launcher layout.

### Fixed

- **`codeseeq` binary made executable in-repo.** The root `codeseeq` file now
  has the executable bit set so it runs directly without `bash codeseeq`.

---

0.2.5 - 2026-05-07

### Fixed

- Fixed split display-mangled DSML such as
  `<____DSML____tool_calls>...` leaking into the Codex UI after a successful
  tool call. The streaming buffer now normalizes obfuscated DSML after chunk
  reassembly, so the block is either converted into a tool call or suppressed
  instead of being shown as assistant text.
- Added missing `output_index` metadata to streamed message/tool lifecycle
  events so current Codex builds keep output deltas attached to their active
  items instead of logging orphaned `OutputTextDelta` diagnostics.
- Fixed Responses top-level function tools being collected for steering but
  not forwarded to DeepSeek's nested Chat Completions `tools` shape. This keeps
  DeepSeek able to emit actual structured tool calls instead of plain bash
  snippets.
- Updated README, quickstart, state docs, bridge docs, and CI build metadata
  to reflect the current single-container local-bridge runtime.
- Added `workspace/` to `.gitignore` so the local repro clone does not break
  `git add .`.

## 0.2.4 - 2026-05-07

### Fixed

- Fixed regular `danger-full-access` launches emitting both
  `--ask-for-approval ...` and
  `--dangerously-bypass-approvals-and-sandbox`. Codex rejects that
  combination. The launcher now omits `--ask-for-approval` whenever it emits
  the bypass flag.

## 0.2.3 - 2026-05-07

### Changed

- `codeseeq --yolo` and `codeseeq -y` now only add Codex launch switches
  `--dangerously-bypass-approvals-and-sandbox` and, for `codex exec` paths,
  `--skip-git-repo-check`.
- Yolo mode no longer injects `--ask-for-approval never`, no longer injects
  `--sandbox danger-full-access`, and no longer rewrites
  `CODESEEQ_APPROVAL_POLICY` / `CODESEEQ_SANDBOX_MODE` config values.

## 0.2.2 - 2026-05-07

### Added

- `codeseeq --yolo` and `codeseeq -y` wrapper flags. They force
  `CODESEEQ_APPROVAL_POLICY=never` and
  `CODESEEQ_SANDBOX_MODE=danger-full-access`, and launch Codex with
  `--ask-for-approval never` plus
  `--sandbox danger-full-access` and
  `--dangerously-bypass-approvals-and-sandbox`.
- Direct `run`/prompt shortcuts keep using `codex exec --skip-git-repo-check`.
  `codeseeq --yolo codex exec ...` also injects `--skip-git-repo-check` when
  it is not already present.

## 0.2.1 - 2026-05-07

Malformed XML compatibility patch for DeepSeek tool-use output.

### Fixed

- Recognizes model-invented outer tool tags such as
  `<exec_command><command>...</command></exec_command>`, `<bash>...</bash>`,
  and `<tool_call name="...">...</tool_call>` as real Codex function calls
  instead of streaming them as assistant text.
- Extends streaming buffering to hold those malformed tags until the closing
  tag arrives, preventing visible XML leakage in the Codex UI.
- Normalizes common XML argument aliases against the registered Codex tool
  schema, including `command` -> `cmd` for `exec_command`/unified shell tools.
- Adds focused bridge extraction regression coverage and wires it into
  `scripts/check.sh`.

## 0.2.0 - 2026-05-07

DSML/tool-calling correctness pass for `bin/codeseeq-bridge.py`. The bridge now
properly streams tool calls to Codex CLI, normalizes display-mangled DSML in
history, and remaps emitted tool names onto whatever the client actually
registered. Drop-in replacement; no changes required to Codex, Dockerfile,
entrypoint, or scripts.

### Fixed

- **DSML leakage during streaming.** Raw `<function_calls>...</function_calls>`
  XML was being streamed verbatim to the Codex TUI before the post-stream
  extractor ran. Replaced with `StreamingDsmlBuffer` that detects DSML inline,
  emits only safe text deltas, and surfaces tool-call blocks as soon as their
  closing tag is seen. Buffer uses depth tracking so a nested `</invoke>`
  inside an outer `<function_calls>` wrapper does not terminate prematurely.
- **`call_id: None` on `response.output_item.added`.** The added event was
  fired before the tool name and call id were known, then back-filled. Now
  deferred until the call has a real name and id, so Codex never sees a
  partial item.
- **Wrong delta event for function tools.** Used
  `response.custom_tool_call_input.delta` for function-typed tools; modern
  Codex listens on `response.function_call_arguments.delta`. Both are now
  emitted (modern + legacy) so older Codex builds keep working.
- **Missing `response.function_call_arguments.done`.** Now emitted, in the
  proper place in the lifecycle.
- **Broken DSML extraction lifecycle.** Post-stream DSML extraction emitted
  only `output_item.done`. Now emits the full sequence: `output_item.added`
  -> `function_call_arguments.delta` -> `function_call_arguments.done` ->
  `output_item.done` (plus legacy `custom_tool_call_input.delta`).
- **Duplicate `response.completed`.** Deduplicated to a single emission.
- **Display-mangled DSML in history.** Codex's TUI obfuscates `<` to
  `<____DSML____` for safe display. When that text fed back as history,
  DeepSeek imitated the malformed format. Added `normalize_dsml_display()`
  applied to ALL inbound message content so the model only ever sees clean
  XML or, ideally, structured `tool_calls`.

### Added

- **Tool-name aliasing.** Flat `TOOL_NAME_ALIASES` map -- emitted name ->
  ordered tuple of preferred replacements. `resolve_tool_name()` does
  exact -> case-insensitive -> alias-prefs (only those actually registered)
  -> fuzzy (`difflib`, cutoff 0.7) -> first preference fallback. Common
  variants covered: `bash`/`sh`/`execute_command`/`exec_command`/`run_command`
  -> `shell`; `write`/`write_file`/`create_file` -> `apply_patch`/`write_file`;
  `edit`/`patch`/`str_replace_editor` -> `str_replace`/`apply_patch`;
  `read_file`/`view_file`/`cat` -> `view`; etc. Toggle with env
  `CODESEEQ_BRIDGE_TOOL_ALIAS_FUZZY` (default on).
- **Tool-use steering system message.** When tools are present in the
  request, a small system message is injected telling the model to emit
  structured `tool_calls` rather than XML. Toggle via env
  `CODESEEQ_BRIDGE_TOOL_STEERING` (default on).
- **Stricter error handling for upstream stream.** `httpx.RemoteProtocolError`,
  `httpx.ReadError`, and `asyncio.CancelledError` are caught separately so
  the bridge logs and surfaces the right SSE error type rather than 500ing.

### Notes

- No schema changes to `/v1/responses`, `/v1/models`, or `/health`.
- No changes to Codex configuration, Dockerfile, container entrypoint, or
  smoke scripts; behavior is fully on the bridge side.
- `CODESEEQ_BRIDGE_DEBUG_LOG=1` continues to dump full request/response
  payloads to `/tmp/codeseeq-bridge.log` for diagnostics.

## 0.1.0 - 2026-05-07

- Initial public version of CodeSeeq single-container CLI workflow.
- Added root `./codeseeq` launcher and container entrypoint path.
- Added DeepSeek/OpenResponses bridge runtime wiring and smoke scripts.
- Added `VERSION` file with starting semantic version.
- Switched license from AGPL-3.0 to Apache 2.0 (updated `LICENSE`, `COPYRIGHT`, `README.md`).

