#!/usr/bin/env python3
"""Regression test for the GGUF local-model provider.

Verifies, without requiring a real llama-server:

  - normalize_model(<path>.gguf) resolves to a gguf spec with an absolute
    gguf_path, keyless api_key_env, and the file basename (minus .gguf) as the
    upstream model name.
  - normalize_model("gguf@<path>") is equivalent to the bare path form.
  - non-.gguf / non-gguf@ values are never misclassified as gguf.
  - _derive_chat_url("gguf", ...) maps to /v1/chat/completions.
  - resolve_provider_for_slug("gguf@x") == "gguf".
  - GGUFServerManager lazily starts one llama-server per path, reuses it on
    subsequent requests, and tears it down on shutdown (mocked subprocess).
"""
import asyncio
import importlib.util
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "codeseeq_bridge", os.path.join(ROOT, "bin", "codeseeq-bridge.py")
)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

failures = 0


def check(name, cond, detail=""):
    global failures
    if cond:
        print(f"[test-bridge-gguf] PASS {name}")
    else:
        failures += 1
        print(f"[test-bridge-gguf] FAIL {name} {detail}")


tmp = tempfile.mkdtemp(prefix="codeseeq-gguf-test-")
gguf_path = os.path.join(tmp, "model.gguf")
with open(gguf_path, "w", encoding="utf-8") as fh:
    fh.write("fake gguf")

# 1) Bare path form.
s = bridge.normalize_model(gguf_path)
check("bare .gguf path resolves provider=gguf", s.provider == "gguf", s.provider)
check("bare .gguf path gguf_path is absolute", os.path.isabs(s.gguf_path), s.gguf_path)
check("bare .gguf path is keyless", s.api_key_env is None, str(s.api_key_env))
check("bare .gguf path upstream model is basename", s.deepseek_model == "model", s.deepseek_model)

# 2) Explicit slug form must produce the same result.
s2 = bridge.normalize_model("gguf@" + gguf_path)
check(
    "gguf@<path> slug matches bare path",
    (s2.provider, s2.gguf_path, s2.deepseek_model, s2.api_key_env)
    == (s.provider, s.gguf_path, s.deepseek_model, s.api_key_env),
    f"{s2.provider} {s2.gguf_path} {s2.deepseek_model} {s2.api_key_env}",
)

# 3) Non-gguf values must not be classified as gguf.
plain = bridge.normalize_model("local@my-model")
check("local@my-model stays provider=local", plain.provider == "local", plain.provider)
check("local@my-model has no gguf_path", plain.gguf_path is None, str(plain.gguf_path))
ds = bridge.normalize_model("deepseek-v4-flash")
check("deepseek-v4-flash stays provider=deepseek", ds.provider == "deepseek", ds.provider)

# 4) Chat endpoint derivation.
check(
    "gguf chat endpoint derivation",
    bridge._derive_chat_url("gguf", "http://127.0.0.1:9")
    == "http://127.0.0.1:9/v1/chat/completions",
    bridge._derive_chat_url("gguf", "http://127.0.0.1:9"),
)

# 5) Provider slug routing.
check("resolve_provider_for_slug('gguf@x') == gguf",
      bridge.resolve_provider_for_slug("gguf@x") == "gguf",
      bridge.resolve_provider_for_slug("gguf@x"))

# 6) Lifecycle (mocked): start once, reuse, teardown.
class FakeProcess:
    def __init__(self):
        self.pid = 424242
        self.terminated = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


started = {"n": 0, "proc": None}
orig_find = bridge.GGUFServerManager._find_binary
orig_start = bridge.GGUFServerManager._start_server
orig_wait = bridge.GGUFServerManager._wait_healthy


def fake_find(self):
    return "/fake/llama-server"


def fake_start(self, binary, abs_path):
    started["n"] += 1
    started["proc"] = FakeProcess()
    return bridge._GGUFServer(abs_path, 12345, started["proc"])


async def fake_wait(self, server):
    return True


bridge.GGUFServerManager._find_binary = fake_find
bridge.GGUFServerManager._start_server = fake_start
bridge.GGUFServerManager._wait_healthy = fake_wait


async def _lifecycle():
    s1 = await bridge.GGUF_MANAGER.ensure(gguf_path)
    s2 = await bridge.GGUF_MANAGER.ensure(gguf_path)
    return s1, s2


s1, s2 = asyncio.run(_lifecycle())
check("ensure reuses a single llama-server per path", s1 is s2, "not identical")
check("ensure starts llama-server exactly once", started["n"] == 1, str(started["n"]))
bridge.GGUF_MANAGER.shutdown_all()
check("shutdown_all terminates the child", started["proc"].terminated, "not terminated")

# Restore class methods (hygiene; process exits right after anyway).
bridge.GGUFServerManager._find_binary = orig_find
bridge.GGUFServerManager._start_server = orig_start
bridge.GGUFServerManager._wait_healthy = orig_wait

# 7) argv construction honors the llama-server tuning flags (env vars map 1:1
#    to -c / -ngl / -np / --port; the same names work as JSON config keys).
os.environ["CODESEEQ_GGUF_CONTEXT_WINDOW"] = "131072"
os.environ["CODESEEQ_GGUF_N_GPU_LAYERS"] = "all"
os.environ["CODESEEQ_GGUF_PARALLEL"] = "1"
os.environ.pop("CODESEEQ_GGUF_THREADS", None)
os.environ.pop("GGUF_API_KEY", None)
argv = bridge.GGUFServerManager._argv("/fake/llama-server", gguf_path, 8888, "model")
check(
    "-c context flag emitted",
    "-c" in argv and argv[argv.index("-c") + 1] == "131072",
    str(argv),
)
check(
    "-ngl flag emitted (string passthrough for 'all')",
    "-ngl" in argv and argv[argv.index("-ngl") + 1] == "all",
    str(argv),
)
check(
    "-np parallel flag emitted",
    "-np" in argv and argv[argv.index("-np") + 1] == "1",
    str(argv),
)
check(
    "--port flag emitted",
    "--port" in argv and argv[argv.index("--port") + 1] == "8888",
    str(argv),
)

# 8) Fixed-port parsing for CODESEEQ_GGUF_PORT.
os.environ["CODESEEQ_GGUF_PORT"] = "8888"
check("fixed port parses", bridge.GGUFServerManager._fixed_port() == 8888,
      str(bridge.GGUFServerManager._fixed_port()))
os.environ["CODESEEQ_GGUF_PORT"] = "not-a-port"
check("invalid fixed port -> None", bridge.GGUFServerManager._fixed_port() is None,
      str(bridge.GGUFServerManager._fixed_port()))
os.environ["CODESEEQ_GGUF_PORT"] = "70000"
check("out-of-range fixed port -> None", bridge.GGUFServerManager._fixed_port() is None,
      str(bridge.GGUFServerManager._fixed_port()))
os.environ.pop("CODESEEQ_GGUF_PORT", None)
check("unset fixed port -> None", bridge.GGUFServerManager._fixed_port() is None,
      str(bridge.GGUFServerManager._fixed_port()))

# 9) System-message collapse (strict Qwen3-style llama.cpp Jinja templates
#    raise "System message must be at the beginning" when a second system
#    message appears after the first one).
collapsed = bridge.collapse_system_messages([
    {"role": "system", "content": "developer instructions"},
    {"role": "system", "content": "tool steering"},
    {"role": "user", "content": "say hi"},
])
check(
    "multiple system messages collapse to a single leading system",
    [m["role"] for m in collapsed] == ["system", "user"],
    str(collapsed),
)
check(
    "collapsed system content preserves both instructions",
    collapsed[0]["content"] == "developer instructions\n\ntool steering",
    collapsed[0]["content"],
)

unchanged = bridge.collapse_system_messages([
    {"role": "user", "content": "no system message here"},
])
check(
    "no-system message list is unchanged",
    unchanged == [{"role": "user", "content": "no system message here"}],
    str(unchanged),
)

with_empty = bridge.collapse_system_messages([
    {"role": "system", "content": ""},
    {"role": "user", "content": "hello"},
])
check(
    "empty system messages are dropped",
    with_empty == [{"role": "user", "content": "hello"}],
    str(with_empty),
)

os.remove(gguf_path)
os.rmdir(tmp)

if failures:
    print(f"[test-bridge-gguf] {failures} failure(s)")
    sys.exit(1)
print("[test-bridge-gguf] PASS")
sys.exit(0)
