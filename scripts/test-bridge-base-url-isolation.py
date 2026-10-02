#!/usr/bin/env python3
"""Regression test: ambient generic base URLs must not hijack other providers.

Root cause of the venice@* 401: `export OPENAI_BASE_URL=https://api.deepseek.com`
(and the matching CODESEEQ_BASE_URL / DEEPSEEK_BASE_URL) is ambient on many dev
machines to pin the deepseek provider. The bridge read those generic variables
for *every* provider, so `venice@qwen-3-8-flash` was sent to
https://api.deepseek.com/api/v1/chat/completions with the VENICE_API_KEY
attached and DeepSeek answered:

  {"error":{"message":"Authentication Fails, Your api key: ****XXXX is invalid"}}

Covered here:
  - a generic base URL pinned to another provider's own host is ignored
    (venice/google/grok keep their real endpoints);
  - provider-specific overrides (VENICE_BASE_URL, ...) still always win;
  - generic base URLs pointing at a custom proxy/gateway still apply, so
    re-gateway deployments keep working;
  - the deepseek provider still honours its own ambient variables;
  - catalog layering is provider-aware (an ambient deepseek URL does not
    suppress a venice entry's endpoint from the catalog JSON).
"""
import importlib.util
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Everything the bridge reads at import/request time that could leak in from
# the caller's shell - the test always sets exactly what it needs.
MANAGED = [
    "OPENAI_BASE_URL",
    "CODESEEQ_BASE_URL",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_CHAT_URL",
    "VENICE_BASE_URL",
    "GOOGLE_BASE_URL",
    "GROK_BASE_URL",
    "ANTHROPIC_BASE_URL",
    "LOCAL_BASE_URL",
    "CODESEEQ_PROVIDER",
    "CODESEEQ_MODEL_CATALOG_JSON",
    "CODESEEQ_THINKING",
]

failures = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global failures
    if cond:
        print(f"[test-bridge-base-url-isolation] PASS {name}")
    else:
        failures += 1
        print(f"[test-bridge-base-url-isolation] FAIL {name} {detail}")


def load_bridge(**env):
    """Import the bridge with only the variables under test in the env."""
    for k in MANAGED:
        os.environ.pop(k, None)
    os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")
    os.environ.setdefault("VENICE_API_KEY", "test-key")
    os.environ.setdefault("GOOGLE_API_KEY", "test-key")
    os.environ.setdefault("GROK_API_KEY", "test-key")
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
    os.environ.update({k: v for k, v in env.items() if v is not None})
    spec = importlib.util.spec_from_file_location(
        "codeseeq_bridge", os.path.join(ROOT, "bin", "codeseeq-bridge.py")
    )
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    return bridge


# 1) The reported bug: an ambient deepseek base URL must not re-point a
#    *different* hosted provider (this is exactly the venice 401 case).
AMBIENT_DEEPSEEK = {
    "OPENAI_BASE_URL": "https://api.deepseek.com",
    "CODESEEQ_BASE_URL": "https://api.deepseek.com",
    "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
    "DEEPSEEK_CHAT_URL": "https://api.deepseek.com/v1/chat/completions",
}
bridge = load_bridge(**AMBIENT_DEEPSEEK)
expect = {
    "venice@qwen-3-8-flash": (
        "venice",
        "https://api.venice.ai",
        "https://api.venice.ai/api/v1/chat/completions",
    ),
    "venice@venice-qwen-3-32b": (
        "venice",
        "https://api.venice.ai",
        "https://api.venice.ai/api/v1/chat/completions",
    ),
    "google@gemini-2.5-flash": (
        "google",
        "https://generativelanguage.googleapis.com",
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    ),
    "grok@grok-4": ("grok", "https://api.x.ai", "https://api.x.ai/v1/chat/completions"),
}
for slug, (provider, base, chat) in expect.items():
    s = bridge.normalize_model(slug)
    check(f"ambient deepseek URL ignored for {slug} (provider)", s.provider == provider, s.provider)
    check(f"ambient deepseek URL ignored for {slug} (base_url)", s.base_url == base, s.base_url)
    check(f"ambient deepseek URL ignored for {slug} (chat_url)", s.chat_url == chat, s.chat_url)

# The deepseek provider itself still honours those same variables.
# (base_url falls back to the built-in default, which is the same host.)
s = bridge.normalize_model("deepseek@deepseek-v4-flash")
check("deepseek keeps its own chat_url override", s.chat_url == "https://api.deepseek.com/v1/chat/completions", s.chat_url)

# 2) Provider-specific overrides still win over everything generic.
bridge = load_bridge(
    VENICE_BASE_URL="https://venice-proxy.example/v1-tier",
    GROK_BASE_URL="https://grok-proxy.example",
    **AMBIENT_DEEPSEEK,
)
expected = {
    "venice@qwen-3-8-flash": "https://venice-proxy.example/v1-tier/api/v1/chat/completions",
    "venice@venice-qwen-3-14b": "https://venice-proxy.example/v1-tier/api/v1/chat/completions",
    "grok@grok-4": "https://grok-proxy.example/v1/chat/completions",
}
for slug, want in expected.items():
    s = bridge.normalize_model(slug)
    check(f"provider-specific override honoured for {slug}", s.chat_url == want, f"{s.chat_url} != {want}")

# 3) A generic base URL that is *not* another provider's host (self-hosted
#    re-gateway) must still be honoured, otherwise proxy deployments break.
bridge = load_bridge(
    OPENAI_BASE_URL="https://gateway.internal.example/openai",
    CODESEEQ_BASE_URL="https://gateway.internal.example/openai",
)
s = bridge.normalize_model("venice@qwen-3-8-flash")
check(
    "custom generic gateway still honoured for venice",
    s.base_url == "https://gateway.internal.example/openai",
    s.base_url,
)
s = bridge.normalize_model("google@gemini-2.5-flash")
check(
    "custom generic gateway still honoured for google",
    s.base_url == "https://gateway.internal.example/openai",
    s.base_url,
)

# 4) Catalog layering is provider-aware: an ambient deepseek base URL must not
#    suppress the venice endpoint from the model catalog JSON.
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
    json.dump(
        {
            "models": [
                {
                    "provider_model": "venice@qwen-3-8-flash",
                    "base_url": "https://api.venice.ai",
                    "chat_url": "https://api.venice.ai/api/v1/chat/completions",
                }
            ]
        },
        fh,
    )
    catalog_path = fh.name
try:
    bridge = load_bridge(CODESEEQ_MODEL_CATALOG_JSON=catalog_path, **AMBIENT_DEEPSEEK)
    s = bridge.normalize_model("venice@qwen-3-8-flash")
    check(
        "catalog venice endpoint survives ambient deepseek URL",
        s.chat_url == "https://api.venice.ai/api/v1/chat/completions",
        s.chat_url,
    )
finally:
    os.unlink(catalog_path)

for k in MANAGED:
    os.environ.pop(k, None)

if failures:
    print(f"[test-bridge-base-url-isolation] {failures} failure(s)")
    sys.exit(1)
print("[test-bridge-base-url-isolation] PASS")
sys.exit(0)
