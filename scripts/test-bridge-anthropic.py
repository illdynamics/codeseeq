#!/usr/bin/env python3
"""Anthropic bridge regression tests.

Covers the two Anthropic integration bugs fixed in v0.4.2:
  A) assistant tool_calls from prior turns must be forwarded as tool_use
     content blocks (Anthropic 400s tool_result blocks whose tool_use_id has
     no matching tool_use in a previous assistant message);
  B) temperature / top_p must not be sent alongside extended thinking
     (Anthropic rejects them), max_tokens must cover the thinking budget, and
     a specific tool_choice must be downgraded to "auto" while thinking.
Also verifies the CODESEEQ_PROVIDER override routing in normalize_model.
"""
import importlib.util
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

spec = importlib.util.spec_from_file_location(
    "codeseeq_bridge", os.path.join(os.path.dirname(__file__), "..", "bin", "codeseeq-bridge.py")
)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

failures = 0


def check(label, cond, detail=""):
    global failures
    if cond:
        print(f"[test-bridge-anthropic] PASS {label}")
    else:
        failures += 1
        print(f"[test-bridge-anthropic] FAIL {label} {detail}")


def payload_for(messages, model="anthropic@claude-sonnet-4", body=None):
    b = dict(body or {})
    b.setdefault("input", messages)
    b.setdefault("stream", False)
    return bridge.anthropic_payload(
        b, bridge.normalize_model(model), messages
    )


# --- A: assistant tool_calls preserved as tool_use blocks ------------------
messages = [
    {"role": "user", "content": "list files"},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_abc123",
                "type": "function",
                "function": {"name": "shell", "arguments": '{"command":"ls"}'},
            }
        ],
    },
    {"role": "tool", "tool_call_id": "call_abc123", "content": "file1 file2"},
]
p = payload_for(messages)
msgs = p["messages"]
check("A: three messages forwarded", len(msgs) == 3, str(len(msgs)))
asst = msgs[1]
check("A: assistant content is block list", isinstance(asst["content"], list))
blocks = asst["content"]
check("A: assistant has tool_use block", blocks and blocks[0].get("type") == "tool_use", str(blocks))
check("A: tool_use id preserved", blocks and blocks[0].get("id") == "call_abc123", str(blocks))
check("A: tool_use input parsed", blocks and blocks[0].get("input") == {"command": "ls"}, str(blocks))
check("A: tool result references tool_use id", msgs[2]["content"][0]["tool_use_id"] == "call_abc123")

# assistant text + tool_calls -> text block then tool_use block
messages_t = [
    {"role": "user", "content": "hi"},
    {
        "role": "assistant",
        "content": "sure",
        "tool_calls": [
            {"id": "call_x", "type": "function", "function": {"name": "shell", "arguments": "{}"}}
        ],
    },
]
p2 = payload_for(messages_t)
b2 = p2["messages"][1]["content"]
check("A: text block first", b2[0] == {"type": "text", "text": "sure"}, str(b2))
check("A: tool_use second", b2[1]["type"] == "tool_use" and b2[1]["id"] == "call_x", str(b2))

# --- B: thinking constraints ----------------------------------------------
st = bridge.normalize_model("anthropic@claude-sonnet-4-thinking")
p3 = bridge.anthropic_payload(
    {"input": "hi", "stream": False, "temperature": 0.9, "top_p": 0.5},
    st,
    [{"role": "user", "content": "hi"}],
)
check("B: no temperature with thinking", "temperature" not in p3)
check("B: no top_p with thinking", "top_p" not in p3)
check("B: thinking enabled", p3.get("thinking", {}).get("type") == "enabled", str(p3.get("thinking")))
check("B: max_tokens >= budget", p3["max_tokens"] >= p3["thinking"]["budget_tokens"])

# thinking + tools + specific tool_choice -> downgraded to auto
p4 = bridge.anthropic_payload(
    {
        "input": "hi",
        "stream": False,
        "tools": [
            {
                "type": "function",
                "name": "shell",
                "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
            }
        ],
        "tool_choice": {"type": "function", "function": {"name": "shell"}},
    },
    st,
    [{"role": "user", "content": "hi"}],
)
check("B: tools forwarded", "tools" in p4 and p4["tools"][0]["name"] == "shell")
check("B: tool_choice auto with thinking", p4.get("tool_choice") == {"type": "auto"}, str(p4.get("tool_choice")))

# non-thinking keeps temperature from spec
s4 = bridge.normalize_model("anthropic@claude-sonnet-4")
p5 = bridge.anthropic_payload({"input": "hi", "stream": False}, s4, [{"role": "user", "content": "hi"}])
check("B: non-thinking temperature preserved", p5.get("temperature") == 1.0, str(p5.get("temperature")))
check("B: non-thinking no thinking block", "thinking" not in p5)

# --- CODESEEQ_PROVIDER override routing ------------------------------------
old_provider = os.environ.get("CODESEEQ_PROVIDER")
try:
    os.environ["CODESEEQ_PROVIDER"] = "grok"
    spec_over = bridge.normalize_model("anthropic@claude-sonnet-4")
    check("C: provider override routes to grok", spec_over.provider == "grok", spec_over.provider)
    check("C: key env follows override", spec_over.api_key_env == "GROK_API_KEY", str(spec_over.api_key_env))
    check("C: chat url follows override", "api.x.ai" in spec_over.chat_url, spec_over.chat_url)
    check("C: model name kept", spec_over.deepseek_model == "claude-sonnet-4-20250514", spec_over.deepseek_model)
finally:
    if old_provider is None:
        os.environ.pop("CODESEEQ_PROVIDER", None)
    else:
        os.environ["CODESEEQ_PROVIDER"] = old_provider

# sanity: health reports effective provider of configured model
import asyncio
try:
    os.environ["CODESEEQ_MODEL"] = "grok@grok-4"
    h = asyncio.run(bridge.health())
    check("C: health reports grok", h.get("provider") == "grok", str(h.get("provider")))
finally:
    os.environ.pop("CODESEEQ_MODEL", None)

if failures:
    print(f"[test-bridge-anthropic] {failures} failure(s)")
    sys.exit(1)
print("[test-bridge-anthropic] PASS")
sys.exit(0)
