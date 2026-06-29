#!/usr/bin/env python3
"""Focused regression checks for bridge XML/tool-call extraction."""
from __future__ import annotations

import importlib.util
import json
import os
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


def assert_call(text: str, registered: set[str], schemas: dict[str, set[str]], name: str, args: dict[str, str]) -> None:
    bridge = load_bridge()
    cleaned, calls = bridge.extract_dsml_tool_calls(text, registered, schemas)
    assert cleaned == "", cleaned
    assert len(calls) == 1, calls
    fn = calls[0]["function"]
    assert fn["name"] == name, fn
    assert json.loads(fn["arguments"]) == args, fn["arguments"]


def main() -> None:
    bridge = load_bridge()

    assert_call(
        '<exec_command><command>echo "hi" > /workspace/test.txt</command></exec_command>',
        {"exec_command"},
        {"exec_command": {"cmd", "yield_time_ms", "max_output_tokens"}},
        "exec_command",
        {"cmd": 'echo "hi" > /workspace/test.txt', "tty": True},
    )
    assert_call(
        "<exec_command><command>pwd</command></exec_command>",
        {"shell"},
        {"shell": {"cmd"}},
        "shell",
        {"cmd": "pwd", "tty": True},
    )
    assert_call(
        '<tool_call name="exec_command"><command>pwd</command></tool_call>',
        {"exec_command"},
        {"exec_command": {"cmd"}},
        "exec_command",
        {"cmd": "pwd", "tty": True},
    )
    assert_call(
        '<function_calls><invoke name="bash"><parameter name="command">pwd</parameter></invoke></function_calls>',
        {"shell"},
        {"shell": {"cmd"}},
        "shell",
        {"cmd": "pwd", "tty": True},
    )

    events = bridge._function_call_lifecycle_events(
        item_id="fc_test",
        call_id="call_test",
        name="exec_command",
        arguments_json='{"cmd":"pwd"}',
        output_index=3,
        chunk_size=0,
    )
    payloads = [json.loads(event.split("data: ", 1)[1]) for event in events]
    assert payloads[0]["type"] == "response.output_item.added"
    assert payloads[0]["output_index"] == 3
    assert payloads[1]["type"] == "response.function_call_arguments.delta"
    assert payloads[1]["output_index"] == 3
    assert payloads[-2]["type"] == "response.function_call_arguments.done"
    assert payloads[-2]["output_index"] == 3
    assert payloads[-1]["type"] == "response.output_item.done"
    assert payloads[-1]["output_index"] == 3

    prepared, err = bridge.prepare_structured_tool_call(
        {
            "id": "call_good",
            "type": "function",
            "function": {
                "name": "exec_command",
                "arguments": '{"command":"pwd"}',
            },
        },
        registered_tools={"exec_command"},
        registered_arg_names={"exec_command": {"cmd"}},
    )
    assert err is None, err
    assert prepared is not None
    assert json.loads(prepared["function"]["arguments"]) == {"cmd": "pwd", "tty": True}

    prepared, err = bridge.prepare_structured_tool_call(
        {
            "id": "call_bad",
            "type": "function",
            "function": {
                "name": "exec_command",
                "arguments": '{"cmd":"unterminated',
            },
        },
        registered_tools={"exec_command"},
        registered_arg_names={"exec_command": {"cmd"}},
    )
    assert prepared is None, prepared
    assert err and "argument_chars" in err and "tool=exec_command" in err, err
    assert "blocked a malformed upstream tool call" in bridge.malformed_tool_call_message([err])

    previous_max = os.environ.pop("CODESEEQ_MAX_OUTPUT_TOKENS", None)
    try:
        payload = bridge.deepseek_payload(
            {
                "stream": True,
                "tools": [
                    {
                        "type": "function",
                        "name": "exec_command",
                        "description": "Run a command",
                        "parameters": {
                            "type": "object",
                            "properties": {"cmd": {"type": "string"}},
                        },
                        "strict": False,
                    }
                ],
                "tool_choice": {"type": "function", "name": "exec_command"},
            },
            "deepseek-v4-flash",
            False,
            [{"role": "user", "content": "pwd"}],
        )
        assert payload["tools"][0]["function"]["name"] == "exec_command"
        assert payload["tools"][0]["function"]["parameters"]["properties"]["cmd"]["type"] == "string"
        assert payload["tool_choice"]["function"]["name"] == "exec_command"
        assert payload["max_tokens"] == 384000

        capped_payload = bridge.deepseek_payload(
            {"stream": False, "max_output_tokens": 999999},
            "deepseek-v4-flash",
            False,
            [{"role": "user", "content": "pwd"}],
        )
        assert capped_payload["max_tokens"] == 384000
    finally:
        if previous_max is not None:
            os.environ["CODESEEQ_MAX_OUTPUT_TOKENS"] = previous_max

    buf = bridge.StreamingDsmlBuffer({"exec_command"})
    safe_parts: list[str] = []
    blocks: list[str] = []
    for chunk in ["prefix ", "<exec_", "command><com", "mand>pwd</command></exec_command>", " suffix"]:
        safe, done = buf.feed(chunk)
        safe_parts.append(safe)
        blocks.extend(done)
    assert "".join(safe_parts) == "prefix  suffix"
    assert blocks == ["<exec_command><command>pwd</command></exec_command>"], blocks

    buf = bridge.StreamingDsmlBuffer({"exec_command"})
    safe_parts = []
    blocks = []
    split_display_chunks = [
        "Done. Let me verify:\n\n<____DS",
        "ML____tool_calls>\n<____DSML____invoke name=\"exec_command\">",
        "\n<____DSML____parameter name=\"cmd\" string=\"true\">cat /workspace/test.txt</____DSML____parameter>",
        "\n</____DSML____invoke>\n</____DSML____tool_calls>",
    ]
    for chunk in split_display_chunks:
        safe, done = buf.feed(chunk)
        safe_parts.append(safe)
        blocks.extend(done)
    assert "____DSML____" not in "".join(safe_parts)
    assert "<tool_calls>" not in "".join(safe_parts)
    assert len(blocks) == 1, blocks
    assert blocks[0].startswith("<tool_calls>")
    _, calls = bridge.extract_dsml_tool_calls(
        blocks[0],
        {"exec_command"},
        {"exec_command": {"cmd"}},
    )
    assert len(calls) == 1, calls
    assert json.loads(calls[0]["function"]["arguments"]) == {"cmd": "cat /workspace/test.txt", "tty": True}

    buf = bridge.StreamingDsmlBuffer({"exec_command"})
    safe_parts = []
    blocks = []
    split_fullwidth_chunks = [
        "Text before\n<\uff5c\uff5cDS",
        "ML\uff5c\uff5ctool_calls>\n<\uff5c\uff5cDSML\uff5c\uff5cinvoke name=\"exec_command\">",
        "\n<\uff5c\uff5cDSML\uff5c\uff5cparameter name=\"cmd\" string=\"true\">cat /workspace/test.txt</\uff5c\uff5cDSML\uff5c\uff5cparameter>",
        "\n</\uff5c\uff5cDSML\uff5c\uff5cinvoke>\n</\uff5c\uff5cDSML\uff5c\uff5ctool_calls>",
    ]
    for chunk in split_fullwidth_chunks:
        safe, done = buf.feed(chunk)
        safe_parts.append(safe)
        blocks.extend(done)
    assert "DSML" not in "".join(safe_parts)
    assert len(blocks) == 1, blocks
    assert blocks[0].startswith("<tool_calls>")
    _, calls = bridge.extract_dsml_tool_calls(
        blocks[0],
        {"exec_command"},
        {"exec_command": {"cmd"}},
    )
    assert len(calls) == 1, calls
    assert json.loads(calls[0]["function"]["arguments"]) == {"cmd": "cat /workspace/test.txt", "tty": True}
    print("[test-bridge-extraction] PASS")


if __name__ == "__main__":
    main()
