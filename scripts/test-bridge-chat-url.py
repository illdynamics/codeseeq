#!/usr/bin/env python3
"""Verify provider chat-endpoint derivation under base-URL overrides.

Regression test for the v0.4.2 local-provider flow: when `codeseeq config`
saves LOCAL_BASE_URL (or any provider base-URL override is set), the bridge
must call the provider's REAL chat endpoint, not a guessed
"<base>/chat/completions" 404 path. Covers:
  - generic local@<model> slugs (typed in the config wizard) -> /v1/...
  - catalog models with provider base-URL overrides
  - per-model CODESEEQ_<MODEL>_* overrides for arbitrary local@ models
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

failures = 0

# Env vars the bridge reads at import time (catalog specs) and request time
# (generic <provider>@<model> specs). Track everything we set so the module
# can be re-imported with a fresh environment for each section.
MANAGED = []


def set_env(**kwargs):
    global MANAGED
    for k in MANAGED:
        os.environ.pop(k, None)
    MANAGED = []
    for k, v in kwargs.items():
        MANAGED.append(k)
        os.environ[k] = v


def clear_env():
    for k in MANAGED:
        os.environ.pop(k, None)


def load_bridge():
    spec = importlib.util.spec_from_file_location(
        "codeseeq_bridge", os.path.join(ROOT, "bin", "codeseeq-bridge.py")
    )
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    return bridge


def check(name: str, cond: bool, detail: str = "") -> None:
    global failures
    if cond:
        print(f"[test-bridge-chat-url] PASS {name}")
    else:
        failures += 1
        print(f"[test-bridge-chat-url] FAIL {name} {detail}")


# 1) Wizard-configured local gateway: LOCAL_BASE_URL + typed model name.
set_env(LOCAL_BASE_URL="http://127.0.0.1:1337")
bridge = load_bridge()
s = bridge.normalize_model("local@my-custom-model")
check(
    "local@model uses /v1/chat/completions under LOCAL_BASE_URL",
    s.chat_url == "http://127.0.0.1:1337/v1/chat/completions",
    s.chat_url,
)

# 2) Per-model override wins for arbitrary local@ slugs.
set_env(
    LOCAL_BASE_URL="http://127.0.0.1:1337",
    CODESEEQ_LLAMA_4_MAVERICK_BASE_URL="http://192.168.1.50:8000",
    CODESEEQ_LLAMA_4_MAVERICK_TEMPERATURE="0.1",
)
bridge = load_bridge()
s = bridge.normalize_model("local@llama-4-maverick")
check(
    "per-model BASE_URL override applied to local@model",
    s.base_url == "http://192.168.1.50:8000",
    s.base_url,
)
check(
    "per-model BASE_URL override re-points chat endpoint",
    s.chat_url == "http://192.168.1.50:8000/v1/chat/completions",
    s.chat_url,
)
check("per-model TEMPERATURE override applied", s.temperature == 0.1, str(s.temperature))

# 3) Catalog models get provider-correct chat paths under base overrides.
set_env(
    GROK_BASE_URL="https://grok.example",
    GOOGLE_BASE_URL="https://google.example",
    VENICE_BASE_URL="https://venice.example",
    ANTHROPIC_BASE_URL="https://anthropic.example",
    DEEPSEEK_BASE_URL="https://deepseek.example",
    LOCAL_BASE_URL="http://127.0.0.1:1337",
)
bridge = load_bridge()
expect = {
    "grok@grok-4": "https://grok.example/v1/chat/completions",
    "google@gemini-2.5-pro": "https://google.example/v1beta/openai/chat/completions",
    "venice@venice-qwen-3-32b": "https://venice.example/api/v1/chat/completions",
    "anthropic@claude-sonnet-4": "https://anthropic.example/v1/messages",
    "deepseek@deepseek-v4-flash": "https://deepseek.example/chat/completions",
    "local@local": "http://127.0.0.1:1337/v1/chat/completions",
}
for slug, want in expect.items():
    s = bridge.normalize_model(slug)
    check(f"catalog {slug} chat path", s.chat_url == want, f"{s.chat_url} != {want}")

# 4) CODESEEQ_PROVIDER override to a non-anthropic provider keeps a valid path.
set_env(CODESEEQ_PROVIDER="grok")
bridge = load_bridge()
s = bridge.normalize_model("anthropic@claude-sonnet-4")
check(
    "CODESEEQ_PROVIDER override derives grok /v1 chat path",
    s.chat_url == "https://api.x.ai/v1/chat/completions",
    s.chat_url,
)

clear_env()
if failures:
    print(f"[test-bridge-chat-url] {failures} failure(s)")
    sys.exit(1)
print("[test-bridge-chat-url] PASS")
sys.exit(0)
