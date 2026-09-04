#!/usr/bin/env python3
"""Regression test for the MLX local-model provider.

Verifies, without requiring a real mlx_lm / Apple Silicon server:

  - normalize_model("mlx@<dir>") resolves to an mlx spec with an absolute
    mlx_path, keyless api_key_env, and the directory basename as the upstream
    model name.
  - A model directory missing config.json / .safetensors is rejected with a
    helpful message.
  - Per-model config/mlx-models.json values win over CODESEEQ_MLX_* env vars,
    which win over the model's config.json, which wins over built-in defaults.
  - _derive_chat_url("mlx", ...) maps to /v1/chat/completions without
    doubling a trailing /v1.
  - resolve_provider_for_slug("mlx@x") == "mlx".
  - External-server selection: MLX_BASE_URL / CODESEEQ_MLX_BASE_URL always
    mean "reuse a running server"; generic OPENAI_BASE_URL / CODESEEQ_BASE_URL
    only count when they target loopback (so an ambient hosted-API base URL
    never hijacks a local mlx model).
  - MLXServerManager lazily starts one mlx_lm server per directory, reuses it
    on subsequent requests, and tears it down on shutdown (mocked subprocess).
"""
import asyncio
import importlib.util
import json
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
        print(f"[test-bridge-mlx] PASS {name}")
    else:
        failures += 1
        print(f"[test-bridge-mlx] FAIL {name} {detail}")


tmp = tempfile.mkdtemp(prefix="codeseeq-mlx-test-")
mlx_dir = os.path.join(tmp, "some-mlx-model")
os.makedirs(mlx_dir, exist_ok=True)
with open(os.path.join(mlx_dir, "config.json"), "w", encoding="utf-8") as fh:
    json.dump({"model_type": "qwen3", "max_position_embeddings": 32768}, fh)
with open(os.path.join(mlx_dir, "model.safetensors"), "w", encoding="utf-8") as fh:
    fh.write("fake weights")

# Ensure a clean env for the core checks (ambient host vars would otherwise
# flip external-server mode).
for k in ("MLX_BASE_URL", "CODESEEQ_MLX_BASE_URL", "OPENAI_BASE_URL",
          "CODESEEQ_BASE_URL", "CODESEEQ_MLX_TEMPERATURE", "CODESEEQ_TEMPERATURE",
          "CODESEEQ_MLX_CONTEXT_WINDOW", "CODESEEQ_MLX_MAX_OUTPUT_TOKENS",
          "CODESEEQ_MLX_MODELS_JSON"):
    os.environ.pop(k, None)

# 1) Basic mlx@<dir> slug resolution.
s = bridge.normalize_model("mlx@" + mlx_dir)
check("mlx@<dir> resolves provider=mlx", s.provider == "mlx", s.provider)
check("mlx@<dir> mlx_path is absolute", os.path.isabs(s.mlx_path or ""), str(s.mlx_path))
check("mlx@<dir> is keyless", s.api_key_env is None, str(s.api_key_env))
check("mlx@<dir> upstream model is basename", s.deepseek_model == "some-mlx-model", s.deepseek_model)
check("mlx@<dir> context from model config.json", s.context_window == 32768, str(s.context_window))
check("mlx@<dir> spawn mode (no external base)", s.mlx_path is not None and s.chat_url.startswith("http://127.0.0.1:1/"),
      f"{s.mlx_path} {s.chat_url}")

# 2) Bad directories.
try:
    bridge.normalize_model("mlx@/no/such/dir")
    check("missing mlx dir raises", False)
except ValueError as exc:
    check("missing mlx dir raises", "mlx model directory not found" in str(exc), str(exc))

no_cfg = os.path.join(tmp, "no-config")
os.makedirs(no_cfg, exist_ok=True)
with open(os.path.join(no_cfg, "model.safetensors"), "w", encoding="utf-8") as fh:
    fh.write("fake")
try:
    bridge.normalize_model("mlx@" + no_cfg)
    check("mlx dir without config.json raises", False)
except ValueError as exc:
    check("mlx dir without config.json raises", "no config.json" in str(exc), str(exc))

no_w = os.path.join(tmp, "no-weights")
os.makedirs(no_w, exist_ok=True)
with open(os.path.join(no_w, "config.json"), "w", encoding="utf-8") as fh:
    json.dump({}, fh)
try:
    bridge.normalize_model("mlx@" + no_w)
    check("mlx dir without safetensors raises", False)
except ValueError as exc:
    check("mlx dir without safetensors raises", "no .safetensors" in str(exc), str(exc))

# 3) Non-mlx values must not be classified as mlx.
plain = bridge.normalize_model("local@my-model")
check("local@my-model stays provider=local", plain.provider == "local", plain.provider)
check("local@my-model has no mlx_path", plain.mlx_path is None, str(plain.mlx_path))

# 4) Chat endpoint derivation (no doubled /v1).
check(
    "mlx chat endpoint derivation (no /v1 base)",
    bridge._derive_chat_url("mlx", "http://127.0.0.1:8888")
    == "http://127.0.0.1:8888/v1/chat/completions",
    bridge._derive_chat_url("mlx", "http://127.0.0.1:8888"),
)
check(
    "mlx chat endpoint derivation (/v1 base not doubled)",
    bridge._derive_chat_url("mlx", "http://127.0.0.1:8888/v1")
    == "http://127.0.0.1:8888/v1/chat/completions",
    bridge._derive_chat_url("mlx", "http://127.0.0.1:8888/v1"),
)

# 5) Provider slug routing.
check("resolve_provider_for_slug('mlx@x') == mlx",
      bridge.resolve_provider_for_slug("mlx@x") == "mlx",
      bridge.resolve_provider_for_slug("mlx@x"))

# 6) External-server selection semantics.
os.environ["MLX_BASE_URL"] = "http://192.168.1.50:9999"
se = bridge.normalize_model("mlx@" + mlx_dir)
check("MLX_BASE_URL (remote) => external", se.mlx_path is None, str(se.mlx_path))
check("MLX_BASE_URL chat endpoint", se.chat_url == "http://192.168.1.50:9999/v1/chat/completions", se.chat_url)
os.environ.pop("MLX_BASE_URL", None)

os.environ["CODESEEQ_BASE_URL"] = "https://api.deepseek.com"
sg = bridge.normalize_model("mlx@" + mlx_dir)
check("ambient non-loopback CODESEEQ_BASE_URL ignored (spawn)",
      sg.mlx_path is not None, f"{sg.mlx_path} {sg.chat_url}")
os.environ["CODESEEQ_BASE_URL"] = "http://127.0.0.1:8888/v1"
sl = bridge.normalize_model("mlx@" + mlx_dir)
check("loopback CODESEEQ_BASE_URL => external", sl.mlx_path is None, str(sl.mlx_path))
check("loopback external chat endpoint", sl.chat_url == "http://127.0.0.1:8888/v1/chat/completions", sl.chat_url)
os.environ.pop("CODESEEQ_BASE_URL", None)

os.environ["CODESEEQ_MLX_BASE_URL"] = "http://localhost:9000"
sm = bridge.normalize_model("mlx@" + mlx_dir)
check("CODESEEQ_MLX_BASE_URL => external", sm.mlx_path is None, str(sm.mlx_path))
check("CODESEEQ_MLX_BASE_URL chat endpoint", sm.chat_url == "http://localhost:9000/v1/chat/completions", sm.chat_url)
os.environ.pop("CODESEEQ_MLX_BASE_URL", None)

# 7) Global sampling env override.
os.environ["CODESEEQ_TEMPERATURE"] = "0.0"
st = bridge.normalize_model("mlx@" + mlx_dir)
check("CODESEEQ_TEMPERATURE applied to mlx spec", st.temperature == 0.0, str(st.temperature))
os.environ.pop("CODESEEQ_TEMPERATURE", None)
os.environ["CODESEEQ_MLX_CONTEXT_WINDOW"] = "65536"
sc = bridge.normalize_model("mlx@" + mlx_dir)
check("CODESEEQ_MLX_CONTEXT_WINDOW overrides model config.json", sc.context_window == 65536, str(sc.context_window))
os.environ.pop("CODESEEQ_MLX_CONTEXT_WINDOW", None)

# 8) Lifecycle (mocked): start once, reuse, teardown.
class FakeProcess:
    def __init__(self):
        self.pid = 424243
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
orig_find = bridge.MLXServerManager._find_python
orig_avail = bridge.MLXServerManager._mlx_available
orig_start = bridge.MLXServerManager._start_server
orig_wait = bridge.MLXServerManager._wait_healthy


def fake_find(self):
    # The lifecycle test must be hermetic: ensure() pre-flights the MLX
    # interpreter before _start_server(), so stub both checks (CI runners
    # have no mlx_lm installed; a dev Mac may or may not).
    return "/usr/bin/python3"


def fake_avail(self, python):
    return True


def fake_start(self, python, abs_path):
    started["n"] += 1
    started["proc"] = FakeProcess()
    return bridge._MLXServer(abs_path, 12346, started["proc"])


async def fake_wait(self, server):
    return True


bridge.MLXServerManager._find_python = fake_find
bridge.MLXServerManager._mlx_available = fake_avail
bridge.MLXServerManager._start_server = fake_start
bridge.MLXServerManager._wait_healthy = fake_wait


async def _lifecycle():
    s1 = await bridge.MLX_MANAGER.ensure(mlx_dir)
    s2 = await bridge.MLX_MANAGER.ensure(mlx_dir)
    return s1, s2


s1, s2 = asyncio.run(_lifecycle())
check("ensure reuses a single mlx server per dir", s1 is s2, "not identical")
check("ensure starts mlx server exactly once", started["n"] == 1, str(started["n"]))
bridge.MLX_MANAGER.shutdown_all()
check("shutdown_all terminates the child", started["proc"].terminated, "not terminated")
bridge.MLXServerManager._find_python = orig_find
bridge.MLXServerManager._mlx_available = orig_avail
bridge.MLXServerManager._start_server = orig_start
bridge.MLXServerManager._wait_healthy = orig_wait

# 9) argv construction.
os.environ.pop("CODESEEQ_MLX_SERVER_ARGS", None)
argv_mod = bridge.MLXServerManager._argv("/usr/bin/python3", mlx_dir, 8888, True, "some-mlx-model")
check("modern CLI form used", argv_mod[:6] == ["/usr/bin/python3", "-m", "mlx_lm", "server", "--model", mlx_dir], str(argv_mod))
check("--host 127.0.0.1", "--host" in argv_mod and argv_mod[argv_mod.index("--host") + 1] == "127.0.0.1", str(argv_mod))
check("--port emitted", "--port" in argv_mod and argv_mod[argv_mod.index("--port") + 1] == "8888", str(argv_mod))
argv_leg = bridge.MLXServerManager._argv("/usr/bin/python3", mlx_dir, 8888, False, "some-mlx-model")
check("legacy module form used", argv_leg[:3] == ["/usr/bin/python3", "-m", "mlx_lm.server"], str(argv_leg))

# 10) Fixed-port parsing.
os.environ["CODESEEQ_MLX_PORT"] = "8888"
check("fixed port parses", bridge.MLXServerManager._fixed_port() == 8888,
      str(bridge.MLXServerManager._fixed_port()))
os.environ["CODESEEQ_MLX_PORT"] = "not-a-port"
check("invalid fixed port -> None", bridge.MLXServerManager._fixed_port() is None,
      str(bridge.MLXServerManager._fixed_port()))
os.environ.pop("CODESEEQ_MLX_PORT", None)
check("unset fixed port -> None", bridge.MLXServerManager._fixed_port() is None,
      str(bridge.MLXServerManager._fixed_port()))

# 11) Per-model MLX config (config/mlx-models.json / CODESEEQ_MLX_MODELS_JSON):
#     per-model values win over the global env vars.
per_model_dir = tempfile.mkdtemp(prefix="codeseeq-mlx-permodel-")
pm_a = os.path.join(per_model_dir, "alpha-dir")
os.makedirs(pm_a, exist_ok=True)
with open(os.path.join(pm_a, "config.json"), "w", encoding="utf-8") as fh:
    json.dump({"model_type": "qwen3", "max_position_embeddings": 32768}, fh)
with open(os.path.join(pm_a, "model.safetensors"), "w", encoding="utf-8") as fh:
    fh.write("fake weights")
pm_b = os.path.join(per_model_dir, "beta-dir")
os.makedirs(pm_b, exist_ok=True)
with open(os.path.join(pm_b, "config.json"), "w", encoding="utf-8") as fh:
    json.dump({"text_config": {"max_position_embeddings": 131072}}, fh)
with open(os.path.join(pm_b, "model.safetensors"), "w", encoding="utf-8") as fh:
    fh.write("fake weights")
pm_cfg = os.path.join(per_model_dir, "mlx-models.json")
with open(pm_cfg, "w", encoding="utf-8") as fh:
    json.dump({
        "models": {
            os.path.join(per_model_dir, "alpha-dir"): {
                "context_window": 262144, "max_output_tokens": 8192,
                "temperature": 0.1, "port": 9898, "enable_thinking": True,
            },
            "beta-dir": {"context_window": 16384},
        }
    }, fh)
bridge._mlx_models_cache = None
os.environ["CODESEEQ_MLX_MODELS_JSON"] = pm_cfg

sa = bridge.normalize_model("mlx@" + pm_a)
sb = bridge.normalize_model("mlx@" + pm_b)
check("per-model context_window (absolute key)", sa.context_window == 262144, sa.context_window)
check("per-model max_output_tokens", sa.max_output_tokens == 8192, sa.max_output_tokens)
check("per-model temperature", sa.temperature == 0.1, sa.temperature)
check("per-model enable_thinking", sa.thinking is True, sa.thinking)
check("per-model context_window (basename key)", sb.context_window == 16384, sb.context_window)
os.environ.pop("CODESEEQ_MLX_MODELS_JSON", None)
bridge._mlx_models_cache = None

# 12) Nested text_config context fallback (multimodal MLX conversions):
#     a directory with no per-model JSON entry and no env override falls back
#     to the model's own config.json (nested text_config.max_position_embeddings).
pm_c = os.path.join(per_model_dir, "gamma-dir")
os.makedirs(pm_c, exist_ok=True)
with open(os.path.join(pm_c, "config.json"), "w", encoding="utf-8") as fh:
    json.dump({"model_type": "qwen3_5_moe", "text_config": {"max_position_embeddings": 262144}}, fh)
with open(os.path.join(pm_c, "model.safetensors"), "w", encoding="utf-8") as fh:
    fh.write("fake weights")
scfg = bridge.normalize_model("mlx@" + pm_c)
check("nested text_config context fallback honored",
      scfg.context_window == 262144, str(scfg.context_window))

if failures:
    print(f"[test-bridge-mlx] FAILED ({failures})")
    sys.exit(1)
print("[test-bridge-mlx] PASS")
