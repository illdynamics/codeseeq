#!/usr/bin/env python3
"""Verify wrapper <-> bridge per-model env-name agreement.

The wrapper forwards CODESEEQ_<MODEL>_<SUFFIX> names (codeseeq_bridge_env_names)
into the container/standalone bridge. The bridge derives the same names with
_model_env_key(), which strips the "provider@" slug prefix. If the two diverge,
per-model overrides are silently dropped. This test asserts they agree for
every model slug in the bridge's MODEL_SPECS.
"""
import importlib.util
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "codeseeq_bridge", os.path.join(ROOT, "bin", "codeseeq-bridge.py")
)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

wrapper = open(os.path.join(ROOT, "codeseeq")).read()
m = re.search(
    r"for key in (DEEPSEEK_V4_FLASH[^;]*?); do",
    wrapper,
    re.S,
)
assert m, "could not locate the per-model key list in codeseeq"
forwarded = set(m.group(1).replace("\\\n", " ").split())

expected = {bridge._model_env_key(slug) for slug in bridge.MODEL_SPECS}
failures = 0

missing = sorted(expected - forwarded)
extra = sorted(forwarded - expected)
if missing:
    failures += 1
    print(f"[test-bridge-env-names] FAIL wrapper does not forward: {missing}")
if extra:
    failures += 1
    print(f"[test-bridge-env-names] FAIL wrapper forwards unknown keys: {extra}")

# The wrapper must emit the CODESEEQ_${key}_${suffix} pattern for every
# documented suffix (generated in a loop, so no literal names exist).
suffix_loop = re.search(
    r"for suffix in (BASE_URL[^;]*?); do\n\s*printf '%s\\n' \"CODESEEQ_\$\{key\}_\$\{suffix\}\"",
    wrapper,
)
if not suffix_loop:
    failures += 1
    print("[test-bridge-env-names] FAIL wrapper suffix loop pattern not found")
else:
    suffixes = set(suffix_loop.group(1).replace("\\", "").split())
    expected_suffixes = {"BASE_URL", "CHAT_URL", "TEMPERATURE", "TOP_P", "TOP_K",
                         "MAX_OUTPUT_TOKENS", "TIMEOUT_SECONDS", "ENABLE_THINKING",
                         "SYSTEM_PROMPT"}
    if suffixes != expected_suffixes:
        failures += 1
        print(f"[test-bridge-env-names] FAIL wrapper suffix set {sorted(suffixes)} != {sorted(expected_suffixes)}")

# The wrapper must also forward per-model overrides for the CURRENTLY
# configured model when it is an arbitrary <provider>@<model> slug (e.g.
# local@llama-4-maverick typed in `codeseeq config`), not just for catalog
# models. Extract the real function and run it under bash.
func_start = wrapper.index("codeseeq_bridge_env_names() {")
func_end = wrapper.index("start_bridge_container() {", func_start)
func_src = wrapper[func_start:func_end]
r = subprocess.run(
    ["bash", "-c", func_src + '\nCODESEEQ_MODEL="local@llama-4-maverick" codeseeq_bridge_env_names\n'],
    capture_output=True,
    text=True,
)
if r.returncode != 0:
    failures += 1
    print("[test-bridge-env-names] FAIL dynamic env forwarding crashed: " + r.stderr.strip()[:200])
else:
    dyn_lines = [ln for ln in r.stdout.splitlines() if "CODESEEQ_LLAMA_4_MAVERICK_" in ln]
    if len(dyn_lines) != len(expected_suffixes):
        failures += 1
        print(
            f"[test-bridge-env-names] FAIL dynamic per-model forwarding "
            f"({len(dyn_lines)} lines, expected {len(expected_suffixes)}): {dyn_lines}"
        )

if failures:
    print(f"[test-bridge-env-names] {failures} failure(s)")
    sys.exit(1)
print(f"[test-bridge-env-names] PASS ({len(expected)} model keys in sync)")
sys.exit(0)
