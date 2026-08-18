#!/usr/bin/env python3
"""Verify the wrapper reaps orphaned bridge containers.

A standalone bridge container (codeseeq-bridge-<port>-<owner-pid>) whose
owner wrapper was killed hard (SIGKILL / pipeline teardown) keeps its host
port forever and is never cleaned up by the EXIT trap. This test extracts
bridge_reap_orphan_containers() from the wrapper and verifies that:
  - a container whose name-embedded owner PID is dead is removed,
  - a container whose owner PID is still alive is never touched,
  - CODESEEQ_KEEP_BRIDGE_CONTAINER=true disables reaping entirely.
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
wrapper = open(os.path.join(ROOT, "codeseeq"), encoding="utf-8").read()

start = wrapper.index("bridge_reap_orphan_containers() {")
end = wrapper.index("bridge_resolve_log_pid_paths() {", start)
func_src = wrapper[start:end]

failures = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global failures
    if cond:
        print(f"[test-bridge-reap-containers] PASS {name}")
    else:
        failures += 1
        print(f"[test-bridge-reap-containers] FAIL {name} {detail}")


def run_reaper(fake: str, reap_log: str, live_pid: str, keep: bool = False) -> str:
    keep_line = "export CODESEEQ_KEEP_BRIDGE_CONTAINER=true" if keep else ""
    script = (
        "source /dev/stdin <<'FUNCEOF'\n"
        + func_src
        + "\nFUNCEOF\n"
        + """
is_truthy() {
  case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}
warn() { printf '[warn] %s\\n' "$*"; }
export CONTAINER="__FAKE__"
export REAP_LOG="__REAPLOG__"
__KEEP__
: > "$REAP_LOG"
bridge_reap_orphan_containers
cat "$REAP_LOG"
""".replace("__FAKE__", fake).replace("__REAPLOG__", reap_log).replace("__KEEP__", keep_line)
    )
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    return r.stdout.strip()


with tempfile.TemporaryDirectory() as tmp:
    fake = os.path.join(tmp, "podman")
    reap_log = os.path.join(tmp, "reap.log")
    live_pid = str(os.getpid())
    with open(fake, "w", encoding="utf-8") as fh:
        fh.write(
            "#!/usr/bin/env bash\n"
            'if [[ "$1" == "ps" ]]; then\n'
            '  printf "%s\\n" "codeseeq-bridge-18081-424242"\n'  # dead owner
            f'  printf "%s\\n" "codeseeq-bridge-18082-{live_pid}"\n'  # live owner
            '  printf "%s\\n" "unrelated-container"\n'
            "  exit 0\n"
            "fi\n"
            'if [[ "$1" == "rm" ]]; then\n'
            '  printf "%s\\n" "$3" >> "$REAP_LOG"\n'
            "  exit 0\n"
            "fi\n"
            "exit 0\n"
        )
    os.chmod(fake, 0o755)

    reaped = run_reaper(fake, reap_log, live_pid).splitlines()
    check("dead-owner container reaped", "codeseeq-bridge-18081-424242" in reaped, str(reaped))
    check(
        "live-owner container untouched",
        f"codeseeq-bridge-18082-{live_pid}" not in reaped,
        str(reaped),
    )
    check("unrelated container untouched", "unrelated-container" not in reaped, str(reaped))

    kept = run_reaper(fake, reap_log, live_pid, keep=True)
    check("KEEP_BRIDGE_CONTAINER disables reaping", kept == "", kept[:100])

if failures:
    print(f"[test-bridge-reap-containers] {failures} failure(s)")
    sys.exit(1)
print("[test-bridge-reap-containers] PASS")
sys.exit(0)
