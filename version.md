- The latest commit `19303c4` ("conf fixed") only changed `config/codex-model-catalog.json` — **not the docs** — and its GitHub Actions run shows `completed failure`.
- GitHub `main` is in the exact same broken state (I fetched the raw files: `VERSION` = `v0.4.6`, docs = `v0.4.5`).

## 3. What your commands actually did (nobody pulled GitHub for the install)

| Command | What it really does |
|---|---|
| `./codeseeq nuke` | Deletes `~/.config/codeseeq`, `~/bin/codeseeq`, `~/.codeseeq` (asks for `y`) |
| `./codeseeq build` | **Docker build** using the Dockerfile — and *this* is a GitHub pull: `RUN git clone https://github.com/illdynamics/codeseeq.git /tmp/codeseeq` then installs that inside the container image. But it pulls **main**, which is equally broken, so it wouldn't hide the error either. |
| `./codeseeq install` | rsync of the **local** tree → `~/.config/codeseeq` |
| `./scripts/install-local.sh` | rsync of the **local** tree → `~/.config/codeseeq` (same logic, just called directly) |

`install-local.sh` is **not** the GitHub puller — it's the local one. The only GitHub-pulling installer is `scripts/install.sh` (the `curl … | bash` one-liner), which downloads the **release zip** from GitHub Releases. You didn't run that.

**Proof the installed copy is local, not GitHub:** `~/.config/codeseeq` is byte-identical to your repo for every file I diffed (VERSION, README, all docs, `codeseeq`, `scripts/*`, `config/*`) — and it **contains `build-error.md`**, which is *untracked* in git, so it could never exist in a GitHub release zip. A local rsync copied it; a release download couldn't have.

## 4. Then why does it "seem gone"?

Because the only version-consistent copy on GitHub is the **old release**. The latest release tag is `v0.4.0`, and its zip actually ships **v0.3.9 content** (`VERSION` = `v0.3.9`, docs = `v0.3.9`) — internally consistent, so `check.sh` passes on it. There are **no v0.4.1–v0.4.6 releases at all** (you're right: the release wasn't updated). So if the "passing" check ever ran against a release zip (or any stale pre-v0.4.6 snapshot), it passed because that copy is *old and consistent* — not because the current code is fixed. Installing the release would also silently downgrade you from v0.4.6 to v0.3.9 content and lose all the v0.4.x fixes (bridge reaping, GGUF, reasoning-effort, etc.).

If you run `./scripts/check.sh` in this repo right now, you'll get the exact same 4 errors from `build-error.md`.

## 5. The fix (4 lines + a couple of consistency touches)

- `README.md:14` → `Current version: \`v0.4.6\``
- `docs/ARCHITECTURE.md:3` → `v0.4.6`
- `docs/SECURITY.md:3` → `v0.4.6`
- `docs/TROUBLESHOOTING.md:3` → `v0.4.6`
- (nice-to-have, not required by the check) `README.md:710-711` `CODESEEQ_RELEASE_TAG=v0.4.5` → `v0.4.6`, and `scripts/install.sh`'s fallback/`die` strings mentioning `v0.4.5`.

Want me to apply those edits and re-run the version check so the build gate goes green?


---

## RESOLVED — 2026-09-01

**Answer to the question above: YES.** The version-consistency edits were applied and the build gate is green:

- `README.md:14` → `Current version: \`v0.4.6\`` (also README pin examples at lines 723-724 → `v0.4.6`)
- `docs/ARCHITECTURE.md:3`, `docs/SECURITY.md:3`, `docs/TROUBLESHOOTING.md:3` → `v0.4.6`
- `RELEASE-NOTES.md` → new `## v0.4.6` section
- `scripts/install.sh` → pinned-release example and curl fallback both `v0.4.6`
- `VERSION` → `v0.4.6`

`./scripts/check.sh` now reports **`[check] all checks passed`** (previously FAILED with the 4 version-documentation errors captured in `build-error.md`).

Additionally, this release fixed the missing default system prompt (`config/default-system-prompt.md` seeded on install + host/container fallback) and the `-U`/`--uncensored-mode` flag now loads `config/uncensored.md` as the system prompt, plus the workspace permission mapping (podman `--userns=keep-id` / docker `--user`).
