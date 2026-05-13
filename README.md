# CodeSeeq

**Production-grade Codex CLI drop-in launcher wired to DeepSeek V4 models.**

Run `codeseeq` instead of `codex`. Same flags, same interactive TUI, same tool calls.
But your prompts go to DeepSeek V4 via your `DEEPSEEK_API_KEY` — no OpenAI account or API key needed.

<p align="center">
  <img src="./codeseeq.jpg" alt="CodeSeeq" width="80%">
</p>

Current version: `v0.3.0` (from [`VERSION`](./VERSION)).

Release notes: [`RELEASE-NOTES.md`](./RELEASE-NOTES.md)

## Quickstart

### Prerequisites

- **DEEPSEEK_API_KEY** — set in your shell for model requests.
- **BRAVE_API_KEY** (optional) — needed for web-search pings (`ping-web`).
- **UNSTRUCTURED_API_KEY** (optional) — needed for doc-input pings (`ping-docs`).
- Podman or Docker (optional — only needed for container mode).
- Python 3 + `pip install -r requirements-bridge.txt` (optional — only needed for host/process mode).

### Install

**Option A — curl one-liner (recommended)**

```bash
curl -fsSL https://raw.githubusercontent.com/codeseeq/codeseeq/main/scripts/install.sh | bash
```

Downloads the latest release zip, extracts it, and installs the `codeseeq` command to `~/.config/codeseeq` with a launcher at `~/bin/codeseeq`.

**Option B — git clone**

```bash
git clone https://github.com/codeseeq/codeseeq.git
cd codeseeq
./codeseeq install
```

**Option C — download release zip manually**

Download `codeseeq-$(cat VERSION).zip` from [GitHub Releases](https://github.com/codeseeq/codeseeq/releases), then:

```bash
unzip codeseeq-$(cat VERSION).zip
cd codeseeq-$(cat VERSION)  # or wherever it extracted
./codeseeq install
```

### Post-install

Make sure `~/bin` is in your `PATH`:

```bash
export PATH="$HOME/bin:$PATH"
```

Set your API key and copy the env template:

```bash
cp .env.example .env
# edit .env with your keys
export DEEPSEEK_API_KEY=sk-...
```

### Use it

```bash
codeseeq -y "say hi"
codeseeq run "say hi"
codeseeq run -f task.md
codeseeq --model deepseek-v4-pro "review this repo"
codeseeq -p myprofile "say hi"
```

### Host-native mode (no Docker/Podman needed)

```bash
pip3 install -r ~/.config/codeseeq/requirements-bridge.txt
codeseeq --bridge-mode process -y "say hi"
```

### Uninstall

```bash
codeseeq nuke
```

## Runtime Model

CodeSeeq separates **where Codex runs** from **how the bridge is started**.

### Runtime Modes (where Codex runs)

Set via `CODESEEQ_RUNTIME_MODE`.

| Mode      | Behavior                                                                 |
|-----------|--------------------------------------------------------------------------|
| `container` | Run Codex inside a Docker/Podman container. Safe/isolated default.     |
| `host`      | Run Codex directly on the host. No container isolation.                |
| `auto` (default) | Use `container` for normal paths; use `host` when danger/yolo is requested. |

### Container Runtime (Safe Default)

```text
host ./codeseeq
  -> podman/docker run codeseeq:dev
  -> Codex inside the container
  -> local bridge inside the container
  -> DeepSeek
```

Default Codex settings:

- `approval_policy = "on-request"`
- `sandbox_mode = "workspace-write"`

### Host Runtime

Host runtime runs Codex directly on your host checkout. It does **not** provide container isolation.

```bash
# Host runtime with process bridge (no containers at all)
CODESEEQ_RUNTIME_MODE=host CODESEEQ_BRIDGE_MODE=process ./codeseeq run "hello"

# Danger/yolo mode: host Codex with bypass flag
./codeseeq -y "fix the tests"
./codeseeq --yolo "fix the tests"
```

In host runtime with danger/yolo, CodeSeeq starts the bridge (process or container), runs local host `codex` directly on the current checkout with `--dangerously-bypass-approvals-and-sandbox`, and uses isolated repo-local `CODEX_HOME=$PWD/.codeseeq` — never the user's real `~/.codex`.

If local `codex` is missing, install it:

```bash
npm install -g @openai/codex
```

## How It Works

CodeSeeq does not fork or patch Codex. It launches the upstream Codex CLI with an isolated generated `config.toml`. That config points Codex at a local CodeSeeq bridge implementing the OpenAI Responses API. The bridge translates requests to DeepSeek Chat Completions and converts responses back to the format Codex expects. The generated config includes privacy hardening settings: live web search, disabled analytics/feedback/OTel/history, and DeepSeek-only auth with no OpenAI key aliasing.

## Bridge Modes

CodeSeeq controls how the translation bridge is started via `CODESEEQ_BRIDGE_MODE`.

| Mode        | Behavior                                                                 |
|-------------|--------------------------------------------------------------------------|
| `process`   | Start `bin/codeseeq-bridge.py` as a direct child process on the host. No Docker/Podman required. |
| `container` | Start the bridge inside a Docker/Podman container (legacy behavior).     |
| `external`  | Assume the bridge is already running. Use `CODESEEQ_BRIDGE_BASE_URL`.    |
| `auto` (default) | Prefer `process` mode when Python + dependencies are available. Fall back to `container`. |

### Process Mode (Recommended for Host Runtime)

```bash
# No container needed for the bridge
CODESEEQ_BRIDGE_MODE=process DEEPSEEK_API_KEY=sk-... ./codeseeq -y "inspect this repo"

# Or just rely on auto-detection when deps are installed
pip3 install -r requirements-bridge.txt
DEEPSEEK_API_KEY=sk-... ./codeseeq -y "review the code"

# Combined: host runtime + process bridge (zero containers)
CODESEEQ_RUNTIME_MODE=host CODESEEQ_BRIDGE_MODE=process DEEPSEEK_API_KEY=sk-... ./codeseeq run "hello"
```

Process mode is **not** a sandbox boundary — it only removes the bridge sidecar container. Use it when you want to avoid Docker-in-Docker or are already running inside a container.

### Container Mode (Legacy)

```bash
# Force old container-bridge behavior
CODESEEQ_BRIDGE_MODE=container DEEPSEEK_API_KEY=sk-... ./codeseeq -y "hello"
```

### External Mode

```bash
# Point at an already-running bridge
CODESEEQ_BRIDGE_MODE=external CODESEEQ_BRIDGE_BASE_URL=http://127.0.0.1:8080/v1 DEEPSEEK_API_KEY=sk-... ./codeseeq -y "hello"
```

### Bridge Configuration

| Variable                        | Default                    | Description                                        |
|----------------------------------|----------------------------|----------------------------------------------------|
| `CODESEEQ_BRIDGE_MODE`          | `auto`                     | `auto`, `process`, `container`, or `external`      |
| `CODESEEQ_BRIDGE_HOST`          | `127.0.0.1`                | Bridge listen address                              |
| `CODESEEQ_BRIDGE_PORT`          | auto-select                | Fixed bridge port (omit for auto)                  |
| `CODESEEQ_BRIDGE_BASE_URL`      | —                          | Full bridge URL override (external mode)           |
| `CODESEEQ_BRIDGE_LOG`           | `~/.config/codeseeq/log/bridge.log` | Bridge log file                                |
| `CODESEEQ_BRIDGE_STARTUP_TIMEOUT` | `10`                     | Seconds to wait for health check                   |
| `CODESEEQ_BRIDGE_REUSE`         | `0`                        | Reuse existing healthy bridge                      |

## Container Runtime

Podman is preferred. Docker is supported as a compatible fallback. Docker Compose is not supported.

Selection order:

1. `CONTAINER=...` override, if set.
2. `podman`
3. `docker`

Examples:

```bash
CONTAINER=podman ./codeseeq models
CONTAINER=docker ./codeseeq models
make CONTAINER=docker build
```

Podman bind mounts use `:Z` by default for SELinux. Docker uses no suffix by default. Override advanced mount behavior with:

```bash
CODESEEQ_VOLUME_SUFFIX=:z ./codeseeq run "say hi"
CODESEEQ_VOLUME_SUFFIX= ./codeseeq run "say hi"
```

## Workspace Paths

In container runtime, Codex works in `/workspace` inside the container. That path is a bind mount of the directory where you launched `./codeseeq`, so writes land in your host checkout.

Before Codex starts, CodeSeeq prints a stderr banner:

```text
CodeSeeq workspace:
  host: /home/user/project
  container: /workspace
```

The host path is only for operator clarity. Codex still writes to `/workspace` inside the container.

Disable the banner:

```bash
CODESEEQ_WORKSPACE_BANNER=false ./codeseeq run "say hi"
```

## Persistent System Prompt

CodeSeeq can store a user-level persistent system prompt:

```bash
./codeseeq system add "You are terse and practical."
./codeseeq system add -f prompts/system.md
./codeseeq system add --file prompts/system.md
./codeseeq system view
./codeseeq system remove
```

Aliases:

- `view`, `show`, `cat`
- `remove`, `rm`, `clear`

Storage path:

```text
~/.config/codeseeq/system-prompt.md
```

The prompt is injected into Codex config as `developer_instructions`, which Codex sends as a developer instruction while preserving Codex's built-in base instructions. It applies to normal Codex request paths including interactive sessions, bare direct prompts, `run`, `run -f/--file`, explicit `codex` passthrough, container runtime, and host runtime.

Do not put secrets in the system prompt unless you understand that prompt text is sent to the model and stored in user-level config state.

## Prompt Files

Run a task file directly:

```bash
./codeseeq run -f task.md
./codeseeq run --file task.md
./codeseeq run --file=task.md
./codeseeq run -f ./tasks/build-feature.md --model deepseek-v4-pro
./codeseeq run -f task.md --thinking
./codeseeq run -f task.md --yolo
```

`run -f/--file` reads the file as the prompt, preserving markdown, newlines, indentation, and code fences. Missing files fail clearly. Providing both a file and inline prompt text fails clearly.

Large prompt files are copied through `.codeseeq/tmp/` for container mode instead of being expanded into a huge shell argument.

## Commands

CodeSeeq-specific commands remain available:

```bash
./codeseeq build
./codeseeq install
./codeseeq nuke
./codeseeq doctor
./codeseeq models
./codeseeq config
./codeseeq ping
./codeseeq ping-stream
./codeseeq ping-web
./codeseeq ping-docs
./codeseeq shell
./codeseeq smoke
./codeseeq system --help
./codeseeq package
```

Explicit passthrough:

```bash
./codeseeq codex --help
./codeseeq codex exec "say hi"
```

Unknown non-CodeSeeq arguments are passed to Codex as much as possible. CodeSeeq does not use `-p` or `--prompt` as prompt aliases. `-p` and `--profile` are Codex profile-selection flags and are forwarded unchanged. Direct prompt execution is `./codeseeq "prompt"` or `./codeseeq run "prompt"`.

## Environment Variables

All supported variables are documented in [`.env.example`](./.env.example). Key ones:

| Variable                      | Default              | Description                                      |
|-------------------------------|----------------------|--------------------------------------------------|
| `DEEPSEEK_API_KEY`            | — (required)         | Model API key                                    |
| `BRAVE_API_KEY`               | —                    | Web search API key (for `ping-web`)              |
| `UNSTRUCTURED_API_KEY`        | —                    | Doc input API key (for `ping-docs`)              |
| `RESPONSES_API_KEY`           | —                    | Responses API key (advanced)                     |
| `CODESEEQ_MODEL`              | `deepseek-v4-flash`  | Default model                                    |
| `CODESEEQ_THINKING`           | `false`              | Enable thinking mode                             |
| `CODESEEQ_APPROVAL_POLICY`    | `on-request`         | Codex approval policy                            |
| `CODESEEQ_SANDBOX_MODE`       | `workspace-write`    | Codex sandbox mode                               |
| `CODESEEQ_YOLO`               | `false`              | Bypass approvals and sandbox (equivalent to `-y`)|
| `CODESEEQ_RUNTIME_MODE`       | `auto`               | `auto`, `container`, or `host`                   |
| `CODESEEQ_BRIDGE_MODE`        | `auto`               | `auto`, `process`, `container`, or `external`    |
| `CONTAINER`                   | `podman`             | Container runtime (`podman` or `docker`)         |
| `IMAGE`                       | `codeseeq:dev`       | Container image tag                              |

## Clean Packages

Release zips must be produced by the official package command only:

```bash
./scripts/package.sh
./codeseeq package
make package
```

Validate a generated or uploaded archive:

```bash
./scripts/package.sh --check
./scripts/package.sh --check-archive dist/codeseeq-YYYYMMDD-HHMMSS.zip
./scripts/package.sh --check-archive /mnt/data/codeseeq.zip
```

Do not create release zips manually in Finder or macOS Archive Utility. Manual zips can include `__MACOSX`, `.DS_Store`, `.git/`, `.codeseeq/`, nested zips, or `.env` files. `.env.example` is the only env-style file intended for release archives.

## Supported Models

- `deepseek-v4-flash` (default)
- `deepseek-v4-flash-thinking`
- `deepseek-v4-pro`
- `deepseek-v4-pro-thinking`

Provider-form aliases:

- `deepseek@deepseek-v4-flash`
- `deepseek@deepseek-v4-pro`

Non-DeepSeek models are rejected by the wrapper/bridge.

## Diagnostics

Load `.env` for local live tests without modifying it:

```bash
set -a
source .env
set +a
```

Then run:

```bash
./codeseeq doctor
./codeseeq config
./codeseeq ping
./codeseeq ping-stream
./codeseeq ping-web
./codeseeq ping-docs
```

`doctor` reports system prompt status, storage path, byte count, line count, and injection mechanism without printing the prompt content. `config` also redacts the full prompt content.

## Interactive Menu Notes

Codex's normal interactive menu and slash commands run inside CodeSeeq's isolated `CODEX_HOME`.

Manual check:

```bash
./codeseeq
```

Open the Codex menu or use slash commands such as `/model` where supported by your Codex version. Approval and sandbox toggles use upstream Codex behavior. The model menu is backed by CodeSeeq's DeepSeek catalog where Codex honors `model_catalog_json`; wrapper and bridge validation remain authoritative if upstream Codex shows additional models.

## CI / Release Pipeline

CodeSeeq uses a single GitHub Actions workflow ([`ci.yml`](.github/workflows/ci.yml)) that runs on every push and pull request:

1. **`static`** — shell syntax checks, shellcheck, secret scanning, whitespace checks
2. **`project`** — bridge extraction tests, config generation validation, version consistency
3. **`bridge-smoke`** — bridge process smoke tests, package build & validation
4. **`docker`** — Docker image build and all container smoke tests
5. **`🚀 Release`** — runs only on tag pushes (`v*`) and only after all four checks pass. Builds the package and creates a GitHub Release with the zip artifact attached.

The release job is gated behind `needs: [static, project, bridge-smoke, docker]` and `if: startsWith(github.ref, 'refs/tags/v')`.

## Makefile Targets

| Target                    | Description                                      |
|---------------------------|--------------------------------------------------|
| `install`                 | Run `./codeseeq install`                         |
| `build`                   | Build container image (`podman build`)           |
| `models`                  | List available models                            |
| `doctor`                  | Run diagnostics                                  |
| `ping` / `ping-stream`    | Test model connectivity                          |
| `ping-web` / `ping-docs`  | Test web search / doc input connectivity         |
| `prompt`                  | Run a one-shot prompt (`PROMPT=...`)             |
| `run`                     | Start interactive Codex session                  |
| `shell`                   | Start Codex shell mode                           |
| `smoke`                   | Run the full smoke-test suite                    |
| `package` / `package-check` | Build / validate release archive             |
| `bridge-check`            | Check bridge Python syntax and imports           |
| `bridge-process-smoke`    | Run bridge process smoke tests                   |
| `inspect-bridge`          | Display bridge runtime info                      |
| `clean-artifacts`         | Remove build artifacts (`__pycache__`, etc.)     |
| `clean`                   | Remove container image                           |
| `check`                   | Run all project checks                           |

## Architecture and Security

- [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md)
- [`docs/TROUBLESHOOTING.md`](./docs/TROUBLESHOOTING.md)
- [`docs/SECURITY.md`](./docs/SECURITY.md)

Local reference paths mentioned by older docs, such as `./codex` and `./open-responses`, may be absent from a minimal checkout. This repository's runtime does not depend on Docker Compose or the upstream `open-responses` npm package.


## Privacy Hardening

CodeSeeq applies privacy hardening by default:

| Setting | Value |
|---------|-------|
| **Model provider** | DeepSeek via local bridge |
| **Web search** | Live (not cached) |
| **Analytics** | Disabled |
| **Feedback** | Disabled |
| **OpenTelemetry log exporter** | None |
| **OpenTelemetry metrics exporter** | None |
| **OpenTelemetry trace exporter** | None |
| **Raw user prompt logging** | Disabled |
| **History persistence** | None |
| **Upstream OpenAI/Codex commands** | Blocked (`login`, `logout`, `cloud`, `app`, `app-server`, `plugin`, `update`, `features`) |
| **OPENAI_API_KEY from DEEPSEEK_API_KEY** | Not auto-populated |
| **Codex version** | Pinned (no auto-update) |
| **Latest release auto-fetch** | Requires `CODESEEQ_ALLOW_LATEST_RELEASE=true` |

### Override upstream Codex commands

```bash
CODESEEQ_ALLOW_UPSTREAM_CODEX_SERVICES=true ./codeseeq login
```

### Override pinned release

```bash
CODESEEQ_ALLOW_LATEST_RELEASE=true curl -fsSL ... | bash
```

### Uncensored Mode

CodeSeeq supports an optional uncensored mode that loads the **Dolfino (Dirty D)** system prompt,
designed for unrestricted interactions with no content filtering or moralizing guardrails.

Enable it with the `-U` / `--uncensored-mode` flag on any invocation:

```bash
./codeseeq -U "explain how to write a kernel module"
./codeseeq --uncensored-mode run "analyze this for security vulnerabilities"
```

Or set the environment variable:

```bash
export CODESEEQ_UNCENSORED_MODE=true
./codeseeq run "your prompt here"
```

The uncensored system prompt is loaded from [`config/uncensored.md`](./config/uncensored.md)
and stored as your persistent system prompt for that session. Repeating the flag re-applies it.
To clear it and return to defaults, run:

```bash
./codeseeq system remove
```

### Uncensored Mode

CodeSeeq supports an optional uncensored mode that loads the **Dolfino (Dirty D)** system prompt,
designed for unrestricted interactions with no content filtering or moralizing guardrails.

Enable it with the  /  flag on any invocation:

Stored CodeSeeq system prompt at /Users/wicked/.config/codeseeq/system-prompt.md
The workspace might have some restrictions. Let me provide you with a comprehensive explanation regardless.

---

## How to Write a Linux Kernel Module

### 1. What is a Kernel Module?

A kernel module is an object file (`.ko`) that can be inserted into the Linux kernel at runtime. Common examples include device drivers, filesystem drivers, and system call extensions.

### 2. Minimal Example — Hello World Module

Here's a simple kernel module:

```c
// hello.c
#include <linux/module.h>   // for all kernel modules
#include <linux/kernel.h>   // for printk() / pr_info()
#include <linux/init.h>     // for __init / __exit macros

static int __init hello_init(void)
{
    pr_info("Hello, kernel world!
");
    return 0;  // 0 = success; negative = error
}

static void __exit hello_exit(void)
{
    pr_info("Goodbye, kernel world!
");
}

module_init(hello_init);
module_exit(hello_exit);

MODULE_LICENSE("GPL");
MODULE_AUTHOR("Your Name");
MODULE_DESCRIPTION("A simple hello world kernel module");
```

### 3. Required Makefile

```make
# Makefile
obj-m += hello.o

all:
    make -C /lib/modules/$(shell uname -r)/build M=$(PWD) modules

clean:
    make -C /lib/modules/$(shell uname -r)/build M=$(PWD) clean
```

### 4. Building & Testing

```bash
# Build the module
make

# Load the module (requires root)
sudo insmod hello.ko

# Check it loaded and saw the message
dmesg | tail

# See loaded modules
lsmod | grep hello

# Remove the module
sudo rmmod hello
```

### 5. Key Concepts to Understand

| Concept | Explanation |
|---|---|
| **`__init` / `__exit`** | Macros that place init/exit code in special memory sections — freed after init |
| **`module_init()` / `module_exit()`** | Registration macros that tell the kernel entry/exit points |
| **`MODULE_LICENSE`** | Required. "GPL" is needed if using GPL-only symbols |
| **Return values** | `0` = success; `-ENOMEM`, `-EINVAL`, etc. = failure |
| **`pr_info()`** | Kernel equivalent of `printf()` — output goes to kernel log |
| **Build system** | Modules are compiled against *kernel source/headers*, not glibc |

### 6. Passing Parameters to a Module

```c
// Declare parameters in your module
static int my_param = 10;
module_param(my_param, int, 0644);
MODULE_PARM_DESC(my_param, "An example integer parameter");

// Pass at load time:
//   sudo insmod mymodule.ko my_param=42

// Or see/set at runtime via sysfs:
//   cat /sys/module/mymodule/parameters/my_param
//   echo 99 | sudo tee /sys/module/mymodule/parameters/my_param
```

### 7. Common Module Types — What to Build Next

1. **Character device driver** — `register_chrdev()` → create `/dev/mydevice`
2. **Misc device driver** — simpler char device, `misc_register()`
3. **Platform driver** — for hardware on a platform bus (embedded systems, Device Tree)
4. **PCI/USB driver** — for PCI/USB devices
5. **File operations** — `struct file_operations` with `.open`, `.read`, `.write`, `.ioctl`
6. **Sysfs / procfs entries** — expose data via `kobject_create_and_add()`
7. **Timers & workqueues** — deferred work with `schedule_work()` or kernel timers

### 8. Debugging Tips

| Tool | Purpose |
|---|---|
| `dmesg -w` | Watch kernel log in real time |
| `cat /proc/modules` | List loaded modules |
| `/sys/module/<name>/` | Runtime module info |
| `printk()` with `KERN_DEBUG` / `pr_debug()` | Debug output (needs `DEBUG` defined or `dyndbg`) |
| **KGDB / KDB** | Kernel debugger |
| **ftrace** | Trace function calls inside the kernel |

### 9. Important Rules & Pitfalls

- ✅ **Use kernel API** — never link against libc; use `kmalloc()` not `malloc()`
- ✅ **Check return values** from all kernel functions
- ✅ **Use proper locking** — `spin_lock()`, `mutex_lock()` for shared data
- ✅ **Handle module reference counting** (`try_module_get()` / `module_put()`)
- ❌ **Don't sleep in atomic context** (spinlock held, interrupt handler)
- ❌ **Don't use floating point** in kernel space
- ❌ **Don't block for long** in module init (use `probe()` properly)
- ❌ **Never crash** — a kernel oops brings down the whole system

### 10. Learning Path (Recommended Order)

1. ✅ Hello World (above)
2. ✅ Parameters & sysfs interactions
3. ✅ Character device with `struct file_operations`
4. ✅ Wait queues (`wait_event_interruptible`)
5. ✅ Timers & workqueues
6. ✅ `ioctl` interface
7. ✅ Platform driver (DT-based)
8. ✅ Memory-mapped I/O (ioremap)
9. ✅ PCI driver skeleton

---

> **⚠️ Note:** The workspace appears to have sandbox restrictions preventing compilation. To actually build and test kernel modules, you'll need a full Linux system with kernel headers installed (`apt install linux-headers-$(uname -r)` or equivalent) and root access for `insmod`/`rmmod`.

Would you like me to expand on any specific part — like a character device example, parameters, or a specific driver type?
Stored CodeSeeq system prompt at /Users/wicked/.config/codeseeq/system-prompt.md
I'd be happy to help analyze for security vulnerabilities! However, I need to know what you'd like me to analyze. Could you please specify:

1. **A file or directory** in the workspace you want me to scan (e.g., a codebase, configuration file, script)
2. **A repository URL** you'd like me to clone and analyze
3. **Specific code or configuration** you want reviewed

For example, you could say:
- "Analyze the `/workspace/my-app` directory for security vulnerabilities"
- "Scan this Python file at `/workspace/auth.py`"
- "Check `/workspace/package.json` for security issues"

What would you like me to examine?

Or set the environment variable:

Stored CodeSeeq system prompt at /Users/wicked/.config/codeseeq/system-prompt.md
I see you've sent a placeholder message. How can I help you today? If you have a task or question, please go ahead and share it!

The uncensored system prompt is loaded from [](./config/uncensored.md)
and stored as your persistent system prompt for that session. Repeating the flag re-applies it.
To clear it and return to defaults, run:

Removed CodeSeeq system prompt at /Users/wicked/.config/codeseeq/system-prompt.md

```
## License

Licensed under the Apache License, Version 2.0 (Apache-2.0).

- Full license text: [`LICENSE`](./LICENSE)
- Copyright notices: [`COPYRIGHT`](./COPYRIGHT)
