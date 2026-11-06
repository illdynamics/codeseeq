#!/usr/bin/env python3
"""Regression checks for DeepSeek reasoning_content round-tripping.

DeepSeek defaults to thinking mode when the request omits the ``thinking``
field.  A non-thinking CodeSeeq model therefore used to generate
``reasoning_content`` that the bridge deliberately hid from Codex.  On the
next tool-call continuation DeepSeek rejected the request with:

    The `reasoning_content` in the thinking mode must be passed back to the API.

These checks pin both halves of the fix:

* non-thinking DeepSeek requests explicitly send ``thinking.type=disabled``;
* readable reasoning items are translated back into assistant
  ``reasoning_content`` (and empty/opaque reasoning items are ignored).
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


def install_import_stubs() -> None:
    """Let this test import the bridge even on hosts without runtime deps."""

    if importlib.util.find_spec("fastapi") is None:
        fastapi = types.ModuleType("fastapi")

        class FastAPI:
            def get(self, *_args, **_kwargs):
                return lambda fn: fn

            def post(self, *_args, **_kwargs):
                return lambda fn: fn

        class HTTPException(Exception):
            def __init__(self, status_code: int, detail: str):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        fastapi.FastAPI = FastAPI
        fastapi.HTTPException = HTTPException
        sys.modules["fastapi"] = fastapi

        responses = types.ModuleType("fastapi.responses")
        responses.JSONResponse = dict
        responses.StreamingResponse = object
        sys.modules["fastapi.responses"] = responses

    if importlib.util.find_spec("httpx") is None:
        httpx = types.ModuleType("httpx")
        httpx.RemoteProtocolError = RuntimeError
        httpx.ReadError = RuntimeError
        httpx.AsyncClient = object
        sys.modules["httpx"] = httpx


def load_bridge():
    install_import_stubs()
    repo_root = Path(__file__).resolve().parents[1]
    bridge_path = repo_root / "bin" / "codeseeq-bridge.py"
    spec = importlib.util.spec_from_file_location("codeseeq_bridge", bridge_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {bridge_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    bridge = load_bridge()

    non_thinking = bridge.normalize_model("deepseek@deepseek-v4-flash")
    thinking = bridge.normalize_model("deepseek@deepseek-v4-flash-thinking")

    non_thinking_payload = bridge.deepseek_payload(
        {"stream": True}, non_thinking, [{"role": "user", "content": "hi"}]
    )
    assert non_thinking_payload.get("thinking") == {"type": "disabled"}, (
        non_thinking_payload.get("thinking")
    )

    thinking_payload = bridge.deepseek_payload(
        {"stream": True}, thinking, [{"role": "user", "content": "hi"}]
    )
    assert thinking_payload.get("thinking") == {"type": "enabled"}, (
        thinking_payload.get("thinking")
    )

    input_items = [
        {"role": "user", "content": "run it", "type": "message"},
        {
            "type": "reasoning",
            "id": "rs_1",
            "summary": [{"type": "summary_text", "text": "I should run it."}],
            "content": [{"type": "reasoning_text", "text": "I should run it."}],
        },
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "exec_command",
            "arguments": '{"cmd":"pwd"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": "/tmp",
        },
    ]
    messages = bridge.input_to_messages(input_items)
    assistants = [m for m in messages if m.get("role") == "assistant"]
    assert len(assistants) == 1, assistants
    assert assistants[0].get("reasoning_content") == "I should run it.", assistants[0]
    assert assistants[0].get("tool_calls"), assistants[0]

    # Opaque reasoning items must not create an empty assistant message.
    opaque = bridge.input_to_messages(
        [
            {"role": "user", "content": "hi", "type": "message"},
            {"type": "reasoning", "id": "rs_opaque", "encrypted_content": "abc"},
        ]
    )
    assert [m.get("role") for m in opaque] == ["user"], opaque

    payload = bridge.deepseek_payload({"stream": True}, thinking, messages)
    assistant_payload = next(
        m for m in payload["messages"] if m.get("role") == "assistant"
    )
    assert assistant_payload.get("reasoning_content") == "I should run it.", (
        assistant_payload
    )

    print("DeepSeek reasoning_content: PASS")


if __name__ == "__main__":
    main()
