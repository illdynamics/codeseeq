#!/usr/bin/env python3
"""Regression test: request_user_input is not forwarded outside Plan mode.

Local / DeepSeek models used to call request_user_input to ask the user for
permission. Codex answers "request_user_input is unavailable in Default mode"
and the model stalls. The bridge must block the call itself (returning
actionable feedback) and steer the model to just run exec_command.
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "codeseeq_bridge", os.path.join(ROOT, "bin", "codeseeq-bridge.py")
)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

failures = []

def check(name, cond, detail=""):
    if cond:
        print(f"{name}: PASS")
    else:
        failures.append(name)
        print(f"{name}: FAIL {detail}")

args, err = bridge._validate_special_tool_args(
    "request_user_input", '{"questions": []}'
)
check("request_user_input blocked", err is not None, "expected a blocking error")
check(
    "request_user_input no args fixup",
    err is None or "unavailable in Default mode" in err,
    f"err={err!r}",
)
check(
    "request_user_input feedback mentions exec_command",
    err is not None and "exec_command" in err,
    f"err={err!r}",
)
check(
    "request_user_input feedback mentions escalation",
    err is not None and "sandbox_permissions" in err,
    f"err={err!r}",
)

os.environ["CODESEEQ_BRIDGE_BLOCK_DEFAULT_MODE_INPUT"] = "false"
try:
    args, err = bridge._validate_special_tool_args(
        "request_user_input", '{"questions": [{"id": "q", "header": "Q", "question": "?"}]}'
    )
    check(
        "request_user_input opt-out forwards",
        err is None,
        f"err={err!r}",
    )
finally:
    os.environ.pop("CODESEEQ_BRIDGE_BLOCK_DEFAULT_MODE_INPUT", None)

steering = bridge.build_tool_steering_message(["exec_command", "request_user_input"])
content = steering.get("content", "") if steering else ""
check(
    "steering forbids permission questions",
    "never use request_user_input" in content,
    "steering does not mention request_user_input",
)
check(
    "steering explains never/on-request escalation",
    "OnRequest" in content,
    "steering does not explain approval-policy escalation",
)

if failures:
    print(f"FAIL: {', '.join(failures)}")
    sys.exit(1)
print("request_user_input gating: PASS")
