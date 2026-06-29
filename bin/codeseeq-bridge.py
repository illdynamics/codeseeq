#!/usr/bin/env python3
"""
codeseeq-bridge: OpenAI Responses API <-> DeepSeek Chat Completions translation bridge.

v0.2.0 patches:
- Robust streaming DSML tool-call detection (inline, not post-hoc)
- Correct OpenAI Responses streaming event types for function tools
  (response.function_call_arguments.delta / .done) instead of the previous
  custom_tool_call_input.delta which codex's function-tool path ignores.
- Full output_item lifecycle for DSML-extracted tool calls
  (added -> function_call_arguments.delta -> .done -> output_item.done).
- Tool-name aliasing so common LLM-hallucinated names (bash, write,
  execute_command, ...) get rebound to the actually-registered tool names
  before being handed to the codex client.
- Optional system-prompt steering injecting a short instruction telling the
  model to use the structured tool_calls field rather than XML in text.
- Input normalization: strip codex display-obfuscation prefixes
  ("____DSML____", "___DSML___") from prior assistant turns so DeepSeek does
  not parrot them.
- Removed the duplicate response.completed event.
- call_id present in the very first response.output_item.added event for
  every tool call (no None placeholder).
- Defensive defaults so partial / malformed upstream chunks do not break the
  whole stream.
"""
from __future__ import annotations

import asyncio
import difflib
import html
import json
import os
import re
import sys
import tempfile
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional, Set, Tuple

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()

# ---------------------------------------------------------------------------
# Session tracking for exec_command / write_stdin validation
# ---------------------------------------------------------------------------
# Codex returns a numeric session_id from exec_command. The model may
# call write_stdin with a stale or hallucinated session_id. We track
# known live sessions so we can validate or gracefully degrade.
import time as _time

_active_sessions: Dict[int, float] = {}  # session_id -> last_seen_timestamp
SESSION_TTL_SECONDS = float(os.environ.get("CODESEEQ_SESSION_TTL_SECONDS", "900"))


def _register_session(session_id: int) -> None:
    """Track an active exec session."""
    if session_id > 0:
        _active_sessions[session_id] = _time.time()


def _session_is_known(session_id: int) -> bool:
    """Check whether a session ID is known and not expired."""
    if session_id <= 0:
        return False
    last_seen = _active_sessions.get(session_id)
    if last_seen is None:
        return False
    if _time.time() - last_seen > SESSION_TTL_SECONDS:
        _active_sessions.pop(session_id, None)
        return False
    return True


def _prune_expired_sessions() -> None:
    """Remove expired session entries."""
    now = _time.time()
    expired = [sid for sid, ts in _active_sessions.items() if now - ts > SESSION_TTL_SECONDS]
    for sid in expired:
        _active_sessions.pop(sid, None)


DEEPSEEK_CHAT_URL = os.environ.get(
    "DEEPSEEK_CHAT_URL", "https://api.deepseek.com/chat/completions"
)
BRAVE_WEB_URL = os.environ.get(
    "CODESEEQ_BRAVE_WEB_URL", "https://api.search.brave.com/res/v1/web/search"
)
UNSTRUCTURED_API_URL = os.environ.get(
    "UNSTRUCTURED_API_URL", "https://api.unstructuredapp.io/general/v0/general"
)
HTTP_TIMEOUT = float(os.environ.get("CODESEEQ_BRIDGE_TIMEOUT_SECONDS", "120"))
CHUNK_SIZE = int(os.environ.get("CODESEEQ_BRIDGE_STREAM_CHUNK_SIZE", "120"))
DEFAULT_DEEPSEEK_MAX_OUTPUT_TOKENS = 384000

MODEL_ALIASES: Dict[str, Tuple[str, bool]] = {
    "deepseek-v4-flash": ("deepseek-v4-flash", False),
    "deepseek-v4-flash-thinking": ("deepseek-v4-flash", True),
    "deepseek-v4-pro": ("deepseek-v4-pro", False),
    "deepseek-v4-pro-thinking": ("deepseek-v4-pro", True),
    "deepseek@deepseek-v4-flash": ("deepseek-v4-flash", False),
    "deepseek@deepseek-v4-flash-thinking": ("deepseek-v4-flash", True),
    "deepseek@deepseek-v4-pro": ("deepseek-v4-pro", False),
    "deepseek@deepseek-v4-pro-thinking": ("deepseek-v4-pro", True),
}

# ---------------------------------------------------------------------------
# DSML / inline tool-call extraction
# ---------------------------------------------------------------------------

DSML_INVOKE_RE = re.compile(
    r"<\s*[^>]*?invoke\s+name\s*=\s*\"([^\"]+)\"[^>]*>(.*?)<\s*/\s*[^>]*?invoke\s*>",
    re.IGNORECASE | re.DOTALL,
)
DSML_PARAM_RE = re.compile(
    r"<\s*[^>]*?parameter\s+name\s*=\s*\"([^\"]+)\""
    r"(?:\s+string\s*=\s*\"(true|false)\")?[^>]*>(.*?)<\s*/\s*[^>]*?parameter\s*>",
    re.IGNORECASE | re.DOTALL,
)
DSML_TOOL_BLOCK_RE = re.compile(
    r"<\s*[^>]*?(?:tool_call|tool_calls|function_calls)[^>]*>"
    r".*?"
    r"<\s*/\s*[^>]*?(?:tool_call|tool_calls|function_calls)\s*>",
    re.IGNORECASE | re.DOTALL,
)

DSML_OPEN_HINT_RE = re.compile(
    r"<\s*[^>]*?(?:function_calls|tool_calls|tool_call|invoke)\b",
    re.IGNORECASE,
)
DSML_CLOSE_HINT_RE = re.compile(
    r"<\s*/\s*[^>]*?(?:function_calls|tool_calls|tool_call|invoke)\s*>",
    re.IGNORECASE,
)
XML_ATTR_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_.:-]*)\s*=\s*(['\"])(.*?)\2",
    re.IGNORECASE | re.DOTALL,
)
XML_CHILD_TAG_RE = re.compile(
    r"<\s*(?P<name>[A-Za-z_][A-Za-z0-9_.:-]*)\b(?P<attrs>[^>]*)>"
    r"(?P<body>.*?)"
    r"<\s*/\s*(?P=name)\s*>",
    re.IGNORECASE | re.DOTALL,
)
XML_CDATA_RE = re.compile(r"^\s*<!\[CDATA\[(.*?)\]\]>\s*$", re.DOTALL)
XML_TAG_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]*$")

_DSML_DISPLAY_MARKER_RE = r"(?:_{2,}|\|{2,}|\uFF5C{2,})"
_DSML_DISPLAY_PREFIX_RE = re.compile(
    rf"<{_DSML_DISPLAY_MARKER_RE}DSML{_DSML_DISPLAY_MARKER_RE}",
    re.IGNORECASE,
)
_DSML_DISPLAY_PREFIX_CLOSE_RE = re.compile(
    rf"</{_DSML_DISPLAY_MARKER_RE}DSML{_DSML_DISPLAY_MARKER_RE}",
    re.IGNORECASE,
)


def normalize_dsml_display(text: str) -> str:
    """Convert codex-display-obfuscated tags back to plain XML form."""
    if not text or "DSML" not in text:
        return text
    text = _DSML_DISPLAY_PREFIX_RE.sub("<", text)
    text = _DSML_DISPLAY_PREFIX_CLOSE_RE.sub("</", text)
    return text


# ---------------------------------------------------------------------------
# Tool-name aliasing
# ---------------------------------------------------------------------------

# Each key is something an LLM might emit; the value is an ordered tuple of
# preferred replacements. We pick the first one that's actually registered
# with the client; if none are, we use the first as a best-effort fallback.
#
# This is a flat lookup -- much simpler than canonical/alias hierarchies and
# easier to extend at runtime.
TOOL_NAME_ALIASES: Dict[str, Tuple[str, ...]] = {
    # shell-execution variants
    "bash":            ("shell", "local_shell", "exec_command", "run_command"),
    "sh":              ("shell", "local_shell"),
    "shell":           ("shell", "local_shell"),
    "local_shell":     ("local_shell", "shell"),
    "execute_command": ("shell", "local_shell", "exec_command"),
    "exec_command":    ("shell", "local_shell", "exec_command"),
    "execute":         ("shell", "local_shell"),
    "exec":            ("shell", "local_shell"),
    "run_command":     ("shell", "local_shell", "run_command"),
    "run_shell":       ("shell", "local_shell"),
    "run":             ("shell", "local_shell"),
    "command":         ("shell", "local_shell"),
    "terminal":        ("shell", "local_shell"),
    # file write / patch variants
    "write":          ("write_file", "apply_patch", "str_replace", "edit_file"),
    "write_file":     ("write_file", "apply_patch"),
    "create_file":    ("write_file", "apply_patch"),
    "edit_file":      ("apply_patch", "str_replace", "edit_file"),
    "edit":           ("apply_patch", "str_replace", "edit_file"),
    "patch":          ("apply_patch",),
    "apply_patch":    ("apply_patch",),
    "str_replace":    ("str_replace", "apply_patch", "edit_file"),
    "replace":        ("str_replace", "apply_patch"),
    "str_replace_editor": ("str_replace", "apply_patch"),
    # file read variants
    "read":      ("read_file", "view"),
    "read_file": ("read_file", "view"),
    "view":      ("view", "read_file"),
    "cat":       ("read_file", "view"),
    "open":      ("read_file", "view"),
    # listing / nav
    "ls":              ("list_directory", "ls"),
    "list":            ("list_directory",),
    "list_dir":        ("list_directory",),
    "list_directory":  ("list_directory",),
    "dir":             ("list_directory",),
}


SHELL_TOOL_NAMES = {
    "bash",
    "sh",
    "shell",
    "local_shell",
    "execute_command",
    "exec_command",
    "execute",
    "exec",
    "run_command",
    "run_shell",
    "run",
    "command",
    "terminal",
}

GENERIC_TOOL_WRAPPER_TAGS = {
    "function_call",
    "tool_call",
}

STANDARD_DSML_WRAPPER_TAGS = {
    "function_calls",
    "tool_calls",
    "tool_call",
    "invoke",
}


def resolve_tool_name(emitted: str, registered: Set[str]) -> str:
    """
    Resolve the tool name the model emitted to a name the client recognizes.

    Order:
      1. Exact match against registered tools
      2. Case-insensitive match against registered tools
      3. Alias-map lookup: walk the preference tuple, return the first
         candidate that is in the registered set (case-insensitively)
      4. Fuzzy match (difflib) against registered tools (cutoff 0.7)
      5. First entry from alias-map preference tuple (even if unregistered)
      6. Original emitted name (the client will return an unknown-tool
         error, but at least we did not invent a worse name)
    """
    if not emitted:
        return emitted

    if emitted in registered:
        return emitted

    lower = emitted.lower()
    lower_to_actual = {r.lower(): r for r in registered}
    if lower in lower_to_actual:
        return lower_to_actual[lower]

    preferences = TOOL_NAME_ALIASES.get(lower, ())
    for cand in preferences:
        cand_lower = cand.lower()
        if cand in registered:
            return cand
        if cand_lower in lower_to_actual:
            return lower_to_actual[cand_lower]

    if registered:
        match = difflib.get_close_matches(
            lower, list(lower_to_actual.keys()), n=1, cutoff=0.7
        )
        if match:
            return lower_to_actual[match[0]]

    if preferences:
        return preferences[0]

    return emitted


def _candidate_tool_tag_names(registered: Optional[Set[str]] = None) -> Set[str]:
    names: Set[str] = set(TOOL_NAME_ALIASES.keys())
    for preferences in TOOL_NAME_ALIASES.values():
        names.update(preferences)
    names.update(GENERIC_TOOL_WRAPPER_TAGS)
    if registered:
        names.update(registered)
    return {name for name in names if XML_TAG_NAME_RE.match(name)}


def _compile_tag_name_re(names: Set[str], *, closing: bool = False) -> re.Pattern[str]:
    # Longest first avoids matching <tool_call> as <tool> if a future alias adds it.
    alternation = "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    if closing:
        pattern = rf"<\s*/\s*(?:{alternation})\s*>"
    else:
        pattern = rf"<\s*(?:{alternation})\b"
    return re.compile(pattern, re.IGNORECASE)


def compile_stream_open_hint_re(registered: Optional[Set[str]] = None) -> re.Pattern[str]:
    return _compile_tag_name_re(
        _candidate_tool_tag_names(registered) | STANDARD_DSML_WRAPPER_TAGS,
        closing=False,
    )


def compile_stream_close_hint_re(registered: Optional[Set[str]] = None) -> re.Pattern[str]:
    return _compile_tag_name_re(
        _candidate_tool_tag_names(registered) | STANDARD_DSML_WRAPPER_TAGS,
        closing=True,
    )


def compile_permissive_tool_tag_re(registered: Optional[Set[str]] = None) -> re.Pattern[str]:
    names = _candidate_tool_tag_names(registered)
    alternation = "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    return re.compile(
        rf"<\s*(?P<tag>{alternation})\b(?P<attrs>[^>]*)>"
        rf"(?P<body>.*?)"
        rf"<\s*/\s*(?P=tag)\s*>",
        re.IGNORECASE | re.DOTALL,
    )


def parse_xml_attrs(raw_attrs: str) -> Dict[str, str]:
    attrs: Dict[str, str] = {}
    for match in XML_ATTR_RE.finditer(raw_attrs or ""):
        attrs[match.group(1).lower()] = html.unescape(match.group(3))
    return attrs


def clean_xml_value(raw_value: str) -> str:
    value = raw_value.strip()
    cdata = XML_CDATA_RE.match(value)
    if cdata:
        value = cdata.group(1)
    return html.unescape(value.strip())


def maybe_json_value(raw_value: str) -> Any:
    value = clean_xml_value(raw_value)
    if not value:
        return ""
    if value[:1] in "[{\"-0123456789tfn":
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def normalize_xml_param_name(name: str, tool_name: str) -> str:
    lower = name.strip().lower().replace("-", "_")
    if lower in {"shell_command", "terminal_command"}:
        return "command"
    if lower in {"file", "file_path", "filename", "filepath"}:
        return "path"
    if lower in {"contents", "body"}:
        return "content"
    if lower == "args":
        return "arguments"
    if lower == "input" and tool_name.lower() in SHELL_TOOL_NAMES:
        return "command"
    return lower


def parse_tool_xml_body(raw_name: str, body: str) -> Dict[str, Any]:
    args: Dict[str, Any] = {}
    tool_lower = raw_name.lower()

    for param in DSML_PARAM_RE.finditer(body):
        param_name = normalize_xml_param_name(param.group(1), raw_name)
        string_hint = (param.group(2) or "true").strip().lower()
        raw_value = param.group(3)
        if not param_name:
            continue
        if string_hint == "false":
            args[param_name] = maybe_json_value(raw_value)
        else:
            args[param_name] = clean_xml_value(raw_value)

    body_without_params = DSML_PARAM_RE.sub("", body)
    child_matches = list(XML_CHILD_TAG_RE.finditer(body_without_params))
    for child in child_matches:
        child_name = normalize_xml_param_name(child.group("name"), raw_name)
        if not child_name or child_name == "parameter":
            continue
        raw_value = child.group("body")
        value = maybe_json_value(raw_value)
        if child_name in {"arguments", "argument"} and isinstance(value, dict):
            args.update(value)
        else:
            args[child_name] = value

    if args:
        return args

    direct_value = clean_xml_value(body_without_params)
    if not direct_value:
        return {}

    decoded = maybe_json_value(direct_value)
    if isinstance(decoded, dict):
        return decoded

    if tool_lower in SHELL_TOOL_NAMES:
        return {"cmd": decoded}
    if tool_lower in {"read", "read_file", "view", "cat", "open"}:
        return {"path": decoded}
    if tool_lower in {"write", "write_file", "create_file"}:
        return {"content": decoded}
    return {"input": decoded}


def collect_registered_tool_arg_names(tools: Any) -> Dict[str, Set[str]]:
    if not isinstance(tools, list):
        return {}

    schemas: Dict[str, Set[str]] = {}
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else None
        name = None
        params = None
        if fn and isinstance(fn.get("name"), str) and fn["name"]:
            name = fn["name"]
            params = fn.get("parameters")
        elif isinstance(tool.get("name"), str) and tool["name"]:
            name = tool["name"]
            params = tool.get("parameters")

        if not name:
            continue

        properties = params.get("properties") if isinstance(params, dict) else None
        if isinstance(properties, dict):
            schemas[name] = {str(key) for key in properties.keys()}
        else:
            schemas[name] = set()

    return schemas


def normalize_tool_arguments_dict(
    args: Dict[str, Any],
    *,
    raw_name: str,
    resolved_name: str,
    registered_arg_names: Optional[Dict[str, Set[str]]] = None,
) -> Dict[str, Any]:
    if not args:
        return args

    normalized = dict(args)
    arg_names = registered_arg_names or {}
    schema_keys = arg_names.get(resolved_name)
    if schema_keys is None:
        lower_lookup = {name.lower(): name for name in arg_names.keys()}
        actual_name = lower_lookup.get(resolved_name.lower())
        schema_keys = arg_names.get(actual_name, set()) if actual_name else set()

    # Codex's unified exec tool uses `cmd`; DeepSeek often invents XML with
    # `<command>...</command>`. Prefer the registered schema when available.
    if "command" in normalized and "cmd" not in normalized:
        if "cmd" in schema_keys or (not schema_keys and resolved_name.lower() == "exec_command"):
            normalized["cmd"] = normalized.pop("command")

    if "cmd" in normalized and "command" not in normalized and "command" in schema_keys:
        normalized["command"] = normalized.pop("cmd")

    # --- Bridge-level tool argument normalization ---

    # Shell/exec tools: Default tty=true so stdin stays open for subsequent
    # write_stdin calls. Without tty=true, Codex closes stdin immediately
    # after command start, causing "stdin is closed for this session"
    # errors when the model later calls write_stdin. Non-interactive
    # commands work fine with tty=true — they just complete normally.
    # Apply to ALL shell-type resolved names because the model may use
    # aliases (bash, shell, exec_command, etc.) that resolve to various
    # Codex tool names, and any of them may be followed by write_stdin.
    if resolved_name.lower() in SHELL_TOOL_NAMES:
        normalized["tty"] = True  # always force tty=true to keep stdin open for write_stdin

    # update_plan: DeepSeek often flattens {step, status, explanation} at top
    # level instead of nesting inside a `plan` array. Detect and fix.
    if resolved_name.lower() == "update_plan":
        # If there's no `plan` key but there IS a `step` or `status` at top level,
        # wrap them into a proper plan array.
        if "plan" not in normalized and ("step" in normalized or "status" in normalized):
            plan_item = {}
            if "step" in normalized:
                plan_item["step"] = normalized.pop("step")
            if "status" in normalized:
                plan_item["status"] = normalized.pop("status")
            normalized["plan"] = [plan_item]
        # If `plan` is a single dict instead of a list, wrap it.
        if "plan" in normalized and isinstance(normalized["plan"], dict):
            normalized["plan"] = [normalized["plan"]]
        # Codex's tool router rejects `explanation` at the top level of
        # update_plan (Codex's Rust struct does not include it despite the
        # prompt mentioning it). Strip it so Codex can parse the call.
        normalized.pop("explanation", None)

    # update_goal: Ensure status is one of the allowed values.
    if resolved_name.lower() == "update_goal":
        status_val = str(normalized.get("status", "")).lower()
        if status_val not in {"complete", "blocked"}:
            # Map common variants
            if status_val in {"completed", "done", "success", "finished"}:
                normalized["status"] = "complete"
            elif status_val in {"error", "fail", "failed", "stuck"}:
                normalized["status"] = "blocked"

    # create_goal: Normalize objective field.
    if resolved_name.lower() == "create_goal":
        if "objective" not in normalized and "goal" in normalized:
            normalized["objective"] = normalized.pop("goal")
        if "objective" not in normalized and "prompt" in normalized:
            normalized["objective"] = normalized.pop("prompt")

    # request_user_input: Validate it has questions.
    if resolved_name.lower() == "request_user_input":
        if "questions" not in normalized:
            normalized["questions"] = [{"id": "input", "header": "Input", "question": "Please provide input:"}]
        # Ensure each question has required fields
        for q in normalized.get("questions", []):
            if isinstance(q, dict):
                if "id" not in q:
                    q["id"] = "input"
                if "header" not in q:
                    q["header"] = "Input"
                if "question" not in q:
                    q["question"] = q.get("header", "Please provide input:")

    # write_stdin: Ensure session_id is present and numeric.
    if resolved_name.lower() == "write_stdin":
        if "session_id" not in normalized:
            normalized["session_id"] = 0
        elif not isinstance(normalized["session_id"], (int, float)):
            try:
                normalized["session_id"] = int(normalized["session_id"])
            except (ValueError, TypeError):
                normalized["session_id"] = 0
        else:
            normalized["session_id"] = int(normalized["session_id"])

    return normalized


def normalize_tool_arguments_json(
    arguments_json: str,
    *,
    raw_name: str,
    resolved_name: str,
    registered_arg_names: Optional[Dict[str, Set[str]]] = None,
) -> str:
    try:
        parsed = json.loads(arguments_json or "{}")
    except Exception:
        return arguments_json or "{}"
    if not isinstance(parsed, dict):
        return arguments_json or "{}"
    normalized = normalize_tool_arguments_dict(
        parsed,
        raw_name=raw_name,
        resolved_name=resolved_name,
        registered_arg_names=registered_arg_names,
    )
    return json.dumps(normalized, ensure_ascii=False)


def _arguments_value_to_json_text(value: Any) -> str:
    if value is None or value == "":
        return "{}"
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def validate_tool_arguments_json(
    arguments_value: Any,
    *,
    raw_name: str,
    resolved_name: str,
    registered_arg_names: Optional[Dict[str, Set[str]]] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Return normalized arguments JSON, or an error string if it is unsafe.

    Codex parses function-call arguments after the bridge completes the
    Responses lifecycle. Forwarding truncated JSON makes Codex's tool router
    fail with messages such as "EOF while parsing a string", so the bridge must
    validate before exposing a tool call as executable.
    """
    arguments_json = _arguments_value_to_json_text(arguments_value)
    try:
        parsed = json.loads(arguments_json or "{}")
    except Exception as exc:
        return None, str(exc)

    if not isinstance(parsed, dict):
        return None, "tool arguments must be a JSON object"

    normalized = normalize_tool_arguments_dict(
        parsed,
        raw_name=raw_name,
        resolved_name=resolved_name,
        registered_arg_names=registered_arg_names,
    )
    return json.dumps(normalized, ensure_ascii=False), None


def prepare_structured_tool_call(
    tool_call: Dict[str, Any],
    *,
    registered_tools: Set[str],
    registered_arg_names: Optional[Dict[str, Set[str]]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    fn = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else None
    if not isinstance(fn, dict):
        return None, "missing function payload"

    raw_name = str(fn.get("name") or "").strip()
    if not raw_name:
        return None, "missing tool name"

    resolved = resolve_tool_name(raw_name, registered_tools)
    if resolved != raw_name:
        log(f"structured tool name remapped: {raw_name!r} -> {resolved!r}")

    normalized_args, err = validate_tool_arguments_json(
        fn.get("arguments"),
        raw_name=raw_name,
        resolved_name=resolved,
        registered_arg_names=registered_arg_names,
    )
    if err:
        arg_len = len(_arguments_value_to_json_text(fn.get("arguments")))
        return (
            None,
            f"tool={raw_name or '<missing>'} argument_chars={arg_len} error={err}",
        )

    # Bridge-level special tool validation (write_stdin session, etc.)
    fixed_args, special_err = _validate_special_tool_args(
        resolved, normalized_args or "{}"
    )
    if special_err:
        return (None, special_err)

    prepared = dict(tool_call)
    prepared["function"] = dict(fn)
    prepared["function"]["name"] = resolved
    prepared["function"]["arguments"] = fixed_args or normalized_args or "{}"
    return prepared, None


def _validate_special_tool_args(
    resolved_name: str,
    arguments_json: str,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Perform bridge-level validation and fix-ups for specific tool calls
    that DeepSeek often hallucinates with wrong arguments.

    Returns (fixed_arguments_json, error_message).
    If error_message is not None, the tool call should be blocked and
    converted to a text message instead.
    """
    try:
        args = json.loads(arguments_json or "{}")
    except Exception:
        return arguments_json, None

    if not isinstance(args, dict):
        return arguments_json, None

    # request_user_input should only be called in Plan mode. DeepSeek
    # often calls it in Default mode. If we detect this is likely a
    # default-mode call (no goal context), block it gracefully.
    if resolved_name.lower() == "request_user_input":
        # Let it through if args look complete; Codex will handle the
        # mode check. But flag it with a log.
        if not args.get("questions"):
            args["questions"] = [
                {"id": "input", "header": "Input",
                 "question": "Please provide additional information:"}
            ]
        log(f"request_user_input forwarded (Codex mode-check will apply): {json.dumps(args, ensure_ascii=False)[:200]}")
        return json.dumps(args, ensure_ascii=False), None

    # update_goal without an active goal: if we see the model trying to
    # update_goal, it may not have created one first. Let it through
    # since Codex will give a clear error that the model can recover from.
    if resolved_name.lower() == "update_goal":
        status_val = str(args.get("status", "")).lower()
        if status_val not in {"complete", "blocked"}:
            # Map common variants
            if status_val in {"completed", "done", "success", "finished"}:
                args["status"] = "complete"
            elif status_val in {"error", "fail", "failed", "stuck"}:
                args["status"] = "blocked"
            elif status_val:
                log(f"update_goal with unrecognized status={status_val!r}; letting through for Codex to reject")
        return json.dumps(args, ensure_ascii=False), None

    # write_stdin with a likely-stale session_id: if the session_id is 0
    # or clearly not a real session, block it with a recovery message.
    if resolved_name.lower() == "write_stdin":
        sid = args.get("session_id", 0)
        try:
            sid = int(sid)
        except (ValueError, TypeError):
            sid = 0
        if sid <= 0:
            # Stale/missing session ID. Return an error that tells the
            # model to re-run exec_command instead.
            return None, (
                "write_stdin called with invalid session_id="
                f"{args.get('session_id')!r}. The exec_command session "
                "has ended or was never started. Re-run exec_command "
                "to start a new session, then use the returned session_id "
                "for subsequent write_stdin calls."
            )

    return json.dumps(args, ensure_ascii=False), None


def malformed_tool_call_message(errors: List[str]) -> str:
    details = "; ".join(errors[:3])
    if len(errors) > 3:
        details += f"; and {len(errors) - 3} more"
    return (
        "CodeSeeq blocked a malformed upstream tool call before Codex could "
        "execute it. The model produced invalid function-call arguments "
        f"({details}). Retry the request, or ask it to split large file writes "
        "and patches into smaller tool calls."
    )


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    print(f"[codeseeq-bridge] {msg}", file=sys.stderr, flush=True)


def require_deepseek_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="DEEPSEEK_API_KEY is required")
    return key


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def parse_positive_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except Exception:
        return None
    if parsed <= 0:
        return None
    return parsed


def resolve_max_tokens(body: Dict[str, Any]) -> int:
    provider_cap = (
        parse_positive_int(os.environ.get("CODESEEQ_MAX_OUTPUT_TOKENS"))
        or DEFAULT_DEEPSEEK_MAX_OUTPUT_TOKENS
    )
    requested = parse_positive_int(body.get("max_output_tokens"))
    if requested is None:
        requested = parse_positive_int(body.get("max_tokens"))
    if requested is None:
        return provider_cap
    return min(requested, provider_cap)


def normalize_model(model: str) -> Tuple[str, str, bool]:
    default_thinking = env_bool("CODESEEQ_THINKING", False)
    raw = (model or "deepseek@deepseek-v4-flash").strip()
    if raw in MODEL_ALIASES:
        ds_model, thinking = MODEL_ALIASES[raw]
        if raw in {"deepseek@deepseek-v4-flash", "deepseek@deepseek-v4-pro"}:
            thinking = default_thinking
        provider_model = f"deepseek@{ds_model}"
        return raw, provider_model, thinking

    if raw.startswith("deepseek@"):
        tail = raw.split("@", 1)[1]
        if tail in {"deepseek-v4-flash", "deepseek-v4-pro"}:
            return raw, f"deepseek@{tail}", default_thinking

    raise ValueError(
        "unsupported model. supported: "
        "deepseek-v4-flash, deepseek-v4-flash-thinking, deepseek-v4-pro, deepseek-v4-pro-thinking, "
        "deepseek@deepseek-v4-flash, deepseek@deepseek-v4-pro"
    )


# ---------------------------------------------------------------------------
# Responses-API input -> Chat Completions messages
# ---------------------------------------------------------------------------

def content_part_to_text(part: Any) -> str:
    if isinstance(part, str):
        return part
    if not isinstance(part, dict):
        return str(part)

    ptype = part.get("type")
    if ptype in {"text", "input_text", "output_text"}:
        return str(part.get("text", ""))
    if ptype == "input_image":
        return "[image]"
    if ptype == "image_url":
        return "[image_url]"
    if ptype == "message":
        return content_to_text(part.get("content", ""))
    if "text" in part:
        return str(part.get("text", ""))
    return ""


def content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks = [content_part_to_text(item).strip() for item in content]
        chunks = [c for c in chunks if c]
        return "\n".join(chunks)
    if isinstance(content, dict):
        return content_part_to_text(content)
    return str(content)


def output_payload_to_text(output: Any) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        lines: List[str] = []
        for item in output:
            if isinstance(item, dict):
                if item.get("type") in {"input_text", "output_text", "text"}:
                    lines.append(str(item.get("text", "")))
            else:
                lines.append(str(item))
        return "\n".join(x for x in lines if x)
    if isinstance(output, dict):
        if "text" in output:
            return str(output.get("text", ""))
        return json.dumps(output, ensure_ascii=False)
    return str(output)


def reasoning_item_to_text(item: Dict[str, Any]) -> str:
    texts: List[str] = []
    summary = item.get("summary")
    if isinstance(summary, list):
        for part in summary:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"])
    content = item.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"])
    return "\n".join(t for t in texts if t).strip()


def input_to_messages(input_data: Any) -> List[Dict[str, Any]]:
    if isinstance(input_data, str):
        return [{"role": "user", "content": normalize_dsml_display(input_data)}]

    if not isinstance(input_data, list):
        return [{"role": "user", "content": normalize_dsml_display(str(input_data))}]

    messages: List[Dict[str, Any]] = []
    current_msg: Optional[Dict[str, Any]] = None

    def flush():
        nonlocal current_msg
        if current_msg:
            if "tool_calls" in current_msg and current_msg.get("content") is None:
                current_msg["content"] = ""
            messages.append(current_msg)
            current_msg = None

    for item in input_data:
        if not isinstance(item, dict):
            flush()
            messages.append({"role": "user", "content": normalize_dsml_display(str(item))})
            continue

        itype = item.get("type")

        if itype == "reasoning":
            reasoning = reasoning_item_to_text(item)
            if current_msg and current_msg.get("role") == "assistant":
                prev = current_msg.get("reasoning_content")
                current_msg["reasoning_content"] = (
                    (prev + "\n" + reasoning).strip() if prev else reasoning
                )
            else:
                flush()
                current_msg = {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": reasoning,
                }
            continue

        if itype == "message":
            role = str(item.get("role", "user"))
            if role == "developer":
                role = "system"
            content = normalize_dsml_display(content_to_text(item.get("content", "")))

            if role == "assistant":
                if current_msg and current_msg.get("role") == "assistant":
                    prev_content = current_msg.get("content") or ""
                    current_msg["content"] = (
                        (prev_content + "\n" + content).strip()
                        if content
                        else prev_content
                    )
                else:
                    flush()
                    current_msg = {"role": "assistant", "content": content}
            else:
                flush()
                messages.append({"role": role, "content": content})
            continue

        if itype in {"input_text", "text", "output_text"}:
            flush()
            messages.append(
                {"role": "user", "content": normalize_dsml_display(str(item.get("text", "")))}
            )
            continue

        if itype == "function_call":
            tool_call = {
                "id": item.get("call_id") or f"call_{uuid.uuid4().hex[:10]}",
                "type": "function",
                "function": {
                    "name": item.get("name", "tool"),
                    "arguments": item.get("arguments", "{}"),
                },
            }
            if current_msg and current_msg.get("role") == "assistant":
                if "tool_calls" not in current_msg:
                    current_msg["tool_calls"] = []
                current_msg["tool_calls"].append(tool_call)
            else:
                flush()
                current_msg = {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [tool_call],
                }
            continue

        if itype in {
            "function_call_output",
            "custom_tool_call_output",
            "local_shell_call_output",
            "shell_call_output",
        }:
            flush()
            call_id = str(item.get("call_id", ""))
            output = output_payload_to_text(item.get("output", ""))
            messages.append({"role": "tool", "tool_call_id": call_id, "content": output})
            continue

        flush()
        messages.append({"role": "user", "content": normalize_dsml_display(content_to_text(item))})

    flush()

    if not messages:
        return [{"role": "user", "content": ""}]
    return messages


def parse_json_arguments(raw: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Built-in helper tools (web search, doc parse) for smoke tests
# ---------------------------------------------------------------------------

async def brave_web_search(query: str, count: int = 5) -> Dict[str, Any]:
    key = os.environ.get("BRAVE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("BRAVE_API_KEY is missing")

    params = {
        "q": query,
        "count": max(1, min(count, 10)),
        "country": "us",
        "search_lang": "en",
    }
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": key,
    }

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(BRAVE_WEB_URL, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    web_items = data.get("web", {}).get("results", []) if isinstance(data, dict) else []
    compact = []
    for entry in web_items[:5]:
        if not isinstance(entry, dict):
            continue
        compact.append(
            {
                "title": entry.get("title"),
                "url": entry.get("url"),
                "description": entry.get("description"),
            }
        )

    return {"query": query, "results": compact, "result_count": len(compact)}


async def unstructured_parse_text(text: str) -> Dict[str, Any]:
    key = os.environ.get("UNSTRUCTURED_API_KEY", "").strip()
    if not key:
        raise RuntimeError("UNSTRUCTURED_API_KEY is missing")

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(text)
        file_path = fh.name

    headers = {"unstructured-api-key": key}
    files = {
        "files": (os.path.basename(file_path), open(file_path, "rb"), "text/plain"),
    }
    data = {"strategy": "fast", "output_format": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(
                UNSTRUCTURED_API_URL, headers=headers, files=files, data=data
            )
            resp.raise_for_status()
            parsed = resp.json()
    finally:
        try:
            files["files"][1].close()
        except Exception:
            pass
        try:
            os.unlink(file_path)
        except Exception:
            pass

    if isinstance(parsed, list):
        preview = [
            {"type": item.get("type"), "text": str(item.get("text", ""))[:200]}
            for item in parsed[:5]
            if isinstance(item, dict)
        ]
        return {"elements": len(parsed), "preview": preview}

    return {"result": parsed}


# ---------------------------------------------------------------------------
# Output-item shaping helpers
# ---------------------------------------------------------------------------

def to_response_message_item(text: str) -> Dict[str, Any]:
    return {
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text}],
    }


def tool_call_to_response_item(tool_call: Dict[str, Any]) -> Dict[str, Any]:
    fn = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
    call_id = tool_call.get("id") or f"call_{uuid.uuid4().hex[:12]}"
    return {
        "type": "function_call",
        "id": tool_call.get("response_item_id") or f"fc_{uuid.uuid4().hex[:12]}",
        "call_id": call_id,
        "name": fn.get("name") or "tool",
        "arguments": fn.get("arguments") or "{}",
    }


def extract_dsml_tool_calls(
    text: str,
    registered_tools: Optional[Set[str]] = None,
    registered_arg_names: Optional[Dict[str, Set[str]]] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Find XML-ish tool-call blocks in `text`. Returns (cleaned_text, tool_calls)."""
    if not isinstance(text, str) or "<" not in text:
        return text, []

    registered = registered_tools or set()
    extracted: List[Dict[str, Any]] = []

    for invoke in DSML_INVOKE_RE.finditer(text):
        raw_name = invoke.group(1).strip()
        body = invoke.group(2)
        if not raw_name:
            continue

        args = parse_tool_xml_body(raw_name, body)
        resolved_name = resolve_tool_name(raw_name, registered)
        if resolved_name != raw_name:
            log(f"dsml tool name remapped: {raw_name!r} -> {resolved_name!r}")
        args = normalize_tool_arguments_dict(
            args,
            raw_name=raw_name,
            resolved_name=resolved_name,
            registered_arg_names=registered_arg_names,
        )

        args_json = json.dumps(args, ensure_ascii=False)
        fixed_args, special_err = _validate_special_tool_args(resolved_name, args_json)
        if special_err:
            log(f"dsml tool call blocked: {resolved_name} error={special_err}")
            continue
        extracted.append(
            {
                "id": f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {
                    "name": resolved_name,
                    "arguments": fixed_args or args_json,
                },
                "_raw_name": raw_name,
            }
        )

    without_standard_invokes = DSML_INVOKE_RE.sub("", text)
    permissive_re = compile_permissive_tool_tag_re(registered)
    for match in permissive_re.finditer(without_standard_invokes):
        tag_name = match.group("tag").strip()
        attrs = parse_xml_attrs(match.group("attrs") or "")
        raw_name = (attrs.get("name") or attrs.get("tool") or tag_name).strip()
        if tag_name.lower() in GENERIC_TOOL_WRAPPER_TAGS and not raw_name:
            continue
        if tag_name.lower() in GENERIC_TOOL_WRAPPER_TAGS and raw_name.lower() == tag_name.lower():
            # Generic wrappers need a name/tool attribute. Without one this is
            # just markup, not an executable call.
            continue

        body = match.group("body")
        args = parse_tool_xml_body(raw_name, body)
        resolved_name = resolve_tool_name(raw_name, registered)
        if resolved_name != raw_name:
            log(f"permissive xml tool name remapped: {raw_name!r} -> {resolved_name!r}")
        args = normalize_tool_arguments_dict(
            args,
            raw_name=raw_name,
            resolved_name=resolved_name,
            registered_arg_names=registered_arg_names,
        )

        args_json = json.dumps(args, ensure_ascii=False)
        fixed_args, special_err = _validate_special_tool_args(resolved_name, args_json)
        if special_err:
            log(f"permissive dsml tool call blocked: {resolved_name} error={special_err}")
            continue
        extracted.append(
            {
                "id": f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {
                    "name": resolved_name,
                    "arguments": fixed_args or args_json,
                },
                "_raw_name": raw_name,
            }
        )

    if not extracted:
        return text, []

    cleaned = DSML_TOOL_BLOCK_RE.sub("", text)
    cleaned = DSML_INVOKE_RE.sub("", cleaned)
    cleaned = permissive_re.sub("", cleaned)
    cleaned = cleaned.strip()
    return cleaned, extracted


# ---------------------------------------------------------------------------
# Streaming inline-DSML extractor
# ---------------------------------------------------------------------------

class StreamingDsmlBuffer:
    """
    Buffers streaming text, emits text-delta segments only when safe, and
    surfaces complete DSML tool-call blocks as soon as their closing tag is
    seen.

    Usage:

        buf = StreamingDsmlBuffer()
        for incoming_text in chunks:
            text_to_emit, completed_blocks = buf.feed(incoming_text)
            ...
        # At end of stream:
        final_text, final_blocks = buf.flush()
    """

    MAX_PEEK_BUFFER = 1024
    TAIL_GUARD = 32  # max length of a partial opening tag we might be
                     # holding onto before we know whether to emit it as
                     # text or as part of a tool-call.

    def __init__(self, registered_tools: Optional[Set[str]] = None) -> None:
        self._buffer = ""
        self._depth = 0
        self._scan_pos = 0  # only valid when _depth > 0
        self._open_hint_re = compile_stream_open_hint_re(registered_tools)
        self._close_hint_re = compile_stream_close_hint_re(registered_tools)

    def feed(self, chunk: str) -> Tuple[str, List[str]]:
        """
        Push more streamed text into the buffer.
        Returns (safe_text_to_emit, list_of_complete_tool_blocks_text).

        Tracks open/close tag depth so that nested <invoke> inside an outer
        <function_calls> wrapper does not emit prematurely on the inner close.
        """
        if not chunk:
            return "", []

        self._buffer += chunk
        # Display-mangled DSML can arrive split across SSE chunks. Normalize
        # after appending so `<____DS` + `ML____tool_calls>` becomes a normal
        # `<tool_calls>` tag before detection scans the buffer.
        self._buffer = normalize_dsml_display(self._buffer)
        emit_text = ""
        completed: List[str] = []

        while True:
            if self._depth == 0:
                # Looking for the next outermost open tag.
                open_match = self._open_hint_re.search(self._buffer)
                if open_match is None:
                    # No open tag in view. Emit safe text, holding back the
                    # last bytes that could grow into an open tag.
                    if "<" in self._buffer:
                        last_lt = self._buffer.rfind("<")
                        head = self._buffer[:last_lt]
                        tail = self._buffer[last_lt:]
                        if len(tail) > self.TAIL_GUARD:
                            emit_text += self._buffer
                            self._buffer = ""
                        else:
                            emit_text += head
                            self._buffer = tail
                    else:
                        emit_text += self._buffer
                        self._buffer = ""
                    break

                # Emit text before the open tag as safe text.
                start = open_match.start()
                if start > 0:
                    emit_text += self._buffer[:start]
                    self._buffer = self._buffer[start:]

                # Open tag must be terminated by '>' before we can scan inside.
                open_end = self._buffer.find(">")
                if open_end == -1:
                    # Tag not yet complete; wait for more data.
                    if len(self._buffer) > self.MAX_PEEK_BUFFER:
                        # Pathological: spill as text.
                        emit_text += self._buffer
                        self._buffer = ""
                    break

                self._depth = 1
                self._scan_pos = open_end + 1
                continue

            # _depth > 0 -- inside a block, scanning for matching close.
            open_after = self._open_hint_re.search(self._buffer, self._scan_pos)
            close_after = self._close_hint_re.search(self._buffer, self._scan_pos)

            if open_after is None and close_after is None:
                # Need more data.
                if len(self._buffer) > self.MAX_PEEK_BUFFER:
                    # Pathological: spill as text and reset.
                    emit_text += self._buffer
                    self._buffer = ""
                    self._depth = 0
                    self._scan_pos = 0
                break

            # Pick the earlier of the two.
            if close_after is None:
                next_is_open = True
                next_match = open_after
            elif open_after is None:
                next_is_open = False
                next_match = close_after
            elif open_after.start() < close_after.start():
                next_is_open = True
                next_match = open_after
            else:
                next_is_open = False
                next_match = close_after

            if next_is_open:
                gt = self._buffer.find(">", next_match.start())
                if gt == -1:
                    break  # nested open not yet complete
                self._depth += 1
                self._scan_pos = gt + 1
                continue
            else:
                self._depth -= 1
                close_end = next_match.end()
                if self._depth == 0:
                    completed.append(self._buffer[:close_end])
                    self._buffer = self._buffer[close_end:]
                    self._scan_pos = 0
                    continue
                else:
                    self._scan_pos = close_end
                    continue

        return emit_text, completed

    def flush(self) -> Tuple[str, List[str]]:
        """End-of-stream flush. Anything still buffered is emitted as text."""
        text = normalize_dsml_display(self._buffer)
        self._buffer = ""
        self._depth = 0
        self._scan_pos = 0
        return text, []


# ---------------------------------------------------------------------------
# DeepSeek payload construction
# ---------------------------------------------------------------------------

TOOL_STEERING_INSTRUCTION_TEMPLATE = (
    "When you need to use a tool, you MUST emit it via the structured "
    "`tool_calls` field of your response (OpenAI/DeepSeek function-calling "
    "format). Do NOT write tool calls as XML / HTML / markup tags inside "
    "your message text. Tags such as <function_calls>, <invoke>, <tool_call>, "
    "<exec_command><command>...</command></exec_command>, <bash>...</bash>, "
    "or <parameter> in plain text are not the protocol and may be discarded. "
    "Wrong example: <exec_command><command>echo hi</command></exec_command>. "
    "Correct behavior: call the matching function in `tool_calls` with JSON "
    "arguments. Keep every tool-call arguments value complete and valid JSON; "
    "for large file creation or edits, split the work into smaller tool calls "
    "instead of placing a very large file body or patch in one call. "
    "Available tools: {{tool_names}}.\n"
    "\n"
    "IMPORTANT tool-specific rules:\n"
    '- request_user_input is ONLY available after create_goal has been called '
    'to enter Plan mode. Never call request_user_input before create_goal.\n'
    '- update_plan expects arguments: '
    '{{"plan": [{{"step": "...", "status": "pending|in_progress|completed"}}], "explanation": "..."}}. '
    'Do NOT use flat {{step, status, explanation}} at the top level; '
    'always wrap steps inside a `plan` array.\n'
    '- update_goal expects arguments: '
    '{{"status": "complete|blocked"}}. '
    'Only call it when a goal is active. Never call update_goal before '
    'create_goal.\n'
    '- write_stdin expects: '
    '{{"session_id": <number>, "chars": "..."}}. '
    'Only use session IDs that were returned by a previous exec_command '
    'call.\n'
    '- create_goal expects: {{"objective": "..."}}. Call this before '
    'using request_user_input or update_goal.\n'
    '- exec_command returns a session ID; you MUST pass that exact number '
    'to write_stdin for subsequent input to the same process.\n'
    '- exec_command always requires \'tty\': true to keep stdin open '
    'for subsequent write_stdin calls. Always include \'tty\': true '
    'in every exec_command call.'
)
def build_tool_steering_message(tool_names: List[str]) -> Optional[Dict[str, Any]]:
    if not tool_names:
        return None
    if not env_bool("CODESEEQ_BRIDGE_TOOL_STEERING", True):
        return None
    names_repr = ", ".join(tool_names)
    return {
        "role": "system",
        "content": TOOL_STEERING_INSTRUCTION_TEMPLATE.format(tool_names=names_repr),
    }


def collect_registered_tool_names(tools: Any) -> List[str]:
    if not isinstance(tools, list):
        return []
    names: List[str] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function")
        if isinstance(fn, dict) and isinstance(fn.get("name"), str) and fn["name"]:
            names.append(fn["name"])
        elif isinstance(tool.get("name"), str) and tool["name"]:
            names.append(tool["name"])
    return names


def deepseek_payload(
    body: Dict[str, Any],
    deepseek_model: str,
    thinking_enabled: bool,
    messages: List[Dict[str, Any]],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": deepseek_model,
        "messages": messages,
        "stream": bool(body.get("stream", False)),
        "max_tokens": resolve_max_tokens(body),
    }

    tools = body.get("tools")
    if isinstance(tools, list) and tools:
        ds_tools = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            if tool.get("type") != "function":
                continue

            if isinstance(tool.get("function"), dict):
                function_def = dict(tool["function"])
            elif isinstance(tool.get("name"), str) and tool["name"]:
                # Codex Responses tools are top-level function specs:
                # {"type":"function","name":"...","parameters":{...}}.
                # DeepSeek Chat Completions expects the nested OpenAI
                # Chat-style shape: {"type":"function","function":{...}}.
                function_def = {"name": tool["name"]}
                if isinstance(tool.get("description"), str):
                    function_def["description"] = tool["description"]
                if isinstance(tool.get("parameters"), dict):
                    function_def["parameters"] = tool["parameters"]
                if "strict" in tool:
                    function_def["strict"] = tool["strict"]
            else:
                continue

            if function_def.get("name"):
                ds_tools.append({"type": "function", "function": function_def})
        if ds_tools:
            payload["tools"] = ds_tools
            tool_choice = body.get("tool_choice")
            if isinstance(tool_choice, str):
                payload["tool_choice"] = tool_choice
            elif isinstance(tool_choice, dict):
                fn = (
                    tool_choice.get("function")
                    if isinstance(tool_choice.get("function"), dict)
                    else None
                )
                if tool_choice.get("type") == "function" and fn and fn.get("name"):
                    payload["tool_choice"] = {
                        "type": "function",
                        "function": {"name": fn.get("name")},
                    }
                elif (
                    tool_choice.get("type") == "function"
                    and isinstance(tool_choice.get("name"), str)
                    and tool_choice["name"]
                ):
                    payload["tool_choice"] = {
                        "type": "function",
                        "function": {"name": tool_choice["name"]},
                    }

    payload["thinking"] = {"type": "enabled" if thinking_enabled else "disabled"}

    if thinking_enabled and isinstance(body.get("reasoning"), dict):
        effort = body["reasoning"].get("effort")
        if effort in {"minimal", "low", "medium", "high", "xhigh", "max"}:
            if effort in {"low", "medium"}:
                effort = "high"
            if effort == "xhigh":
                effort = "max"
            payload["reasoning_effort"] = effort

    return payload


def deepseek_usage_to_responses_usage(usage: Any) -> Dict[str, int]:
    if not isinstance(usage, dict):
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    input_tokens = int(usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or (input_tokens + output_tokens))
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def split_chunks(text: str, size: int) -> List[str]:
    if not text:
        return []
    return [text[i : i + size] for i in range(0, len(text), size)]


def sse_event(event_type: str, payload: Dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {event_type}\ndata: {data}\n\n"


# ---------------------------------------------------------------------------
# Streaming event helpers
# ---------------------------------------------------------------------------

def _function_call_lifecycle_events(
    *,
    item_id: str,
    call_id: str,
    name: str,
    arguments_json: str,
    output_index: int,
    chunk_size: int = 0,
) -> List[str]:
    """
    Build the full streaming lifecycle for a function-tool call:
        output_item.added -> function_call_arguments.delta(s) -> .done -> output_item.done

    Also emits the legacy custom_tool_call_input.delta events for clients
    that still listen on those (older codex builds).
    """
    out: List[str] = []
    out.append(
        sse_event(
            "response.output_item.added",
            {
                "type": "response.output_item.added",
                "output_index": output_index,
                "item": {
                    "id": item_id,
                    "type": "function_call",
                    "call_id": call_id,
                    "name": name,
                    "arguments": "",
                },
            },
        )
    )

    args_str = arguments_json or "{}"
    if chunk_size and chunk_size > 0:
        deltas = split_chunks(args_str, chunk_size) or [args_str]
    else:
        deltas = [args_str]

    for delta in deltas:
        out.append(
            sse_event(
                "response.function_call_arguments.delta",
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": item_id,
                    "output_index": output_index,
                    "call_id": call_id,
                    "delta": delta,
                },
            )
        )
        out.append(
            sse_event(
                "response.custom_tool_call_input.delta",
                {
                    "type": "response.custom_tool_call_input.delta",
                    "item_id": item_id,
                    "output_index": output_index,
                    "call_id": call_id,
                    "delta": delta,
                },
            )
        )

    out.append(
        sse_event(
            "response.function_call_arguments.done",
            {
                "type": "response.function_call_arguments.done",
                "item_id": item_id,
                "output_index": output_index,
                "call_id": call_id,
                "arguments": args_str,
            },
        )
    )
    out.append(
        sse_event(
            "response.output_item.done",
            {
                "type": "response.output_item.done",
                "output_index": output_index,
                "item": {
                    "type": "function_call",
                    "id": item_id,
                    "call_id": call_id,
                    "name": name,
                    "arguments": args_str,
                },
            },
        )
    )
    return out


# ---------------------------------------------------------------------------
# FastAPI endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/models")
async def models() -> Dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {"id": "deepseek@deepseek-v4-flash", "object": "model", "owned_by": "deepseek"},
            {"id": "deepseek@deepseek-v4-flash-thinking", "object": "model", "owned_by": "deepseek"},
            {"id": "deepseek@deepseek-v4-pro", "object": "model", "owned_by": "deepseek"},
            {"id": "deepseek@deepseek-v4-pro-thinking", "object": "model", "owned_by": "deepseek"},
        ],
    }


@app.post("/v1/responses")
async def responses(request: Request) -> Any:
    try:
        body = await request.json()
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail=f"invalid json: {exc}")

    require_deepseek_key()

    model_in = str(body.get("model", "deepseek@deepseek-v4-flash"))
    try:
        raw_model, provider_model, thinking_enabled = normalize_model(model_in)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    deepseek_model = provider_model.split("@", 1)[1]

    response_id = f"resp_{uuid.uuid4().hex[:20]}"

    # --- bridge built-in helpers (smoke test paths) -----------------------
    bridge_tool = body.get("codeseeq_tool")
    if bridge_tool == "web_search":
        query = str(body.get("query") or "latest DeepSeek API models")
        try:
            result = await brave_web_search(query=query, count=5)
        except Exception as exc:
            return JSONResponse(status_code=502, content={"error": str(exc)})

        text = json.dumps(result, ensure_ascii=False)
        return {
            "id": response_id,
            "object": "response",
            "model": provider_model,
            "status": "completed",
            "output": [to_response_message_item(text)],
            "output_text": text,
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        }

    if bridge_tool == "doc_parse":
        text = str(body.get("text") or "CodeSeeq doc parsing smoke test.")
        try:
            result = await unstructured_parse_text(text)
        except Exception as exc:
            return JSONResponse(status_code=502, content={"error": str(exc)})

        out = json.dumps(result, ensure_ascii=False)
        return {
            "id": response_id,
            "object": "response",
            "model": provider_model,
            "status": "completed",
            "output": [to_response_message_item(out)],
            "output_text": out,
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        }

    # --- normal request path ---------------------------------------------
    messages = input_to_messages(body.get("input", ""))
    registered_tool_names = collect_registered_tool_names(body.get("tools"))
    registered_set: Set[str] = set(registered_tool_names)
    registered_arg_names = collect_registered_tool_arg_names(body.get("tools"))

    steering = build_tool_steering_message(registered_tool_names)
    if steering:
        insert_idx = 0
        for i, m in enumerate(messages):
            if m.get("role") == "system":
                insert_idx = i + 1
            else:
                break
        messages.insert(insert_idx, steering)

    payload = deepseek_payload(body, deepseek_model, thinking_enabled, messages)
    stream = bool(body.get("stream", False))

    headers = {
        "Authorization": f"Bearer {os.environ.get('DEEPSEEK_API_KEY', '')}",
        "Content-Type": "application/json",
    }

    log(
        f"request model={raw_model} mapped={provider_model} thinking={thinking_enabled} "
        f"stream={stream} messages={len(messages)} tools_registered={len(registered_set)}"
    )
    if registered_tool_names:
        log(f"registered tool names: {registered_tool_names}")

    # ---------------- non-streaming path --------------------------------
    if not stream:
        payload["stream"] = False
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(DEEPSEEK_CHAT_URL, json=payload, headers=headers)
            if resp.status_code >= 400:
                detail = resp.text[:1000]
                log(f"deepseek error status={resp.status_code} body={detail}")
                return JSONResponse(status_code=resp.status_code, content={"error": detail})
            ds = resp.json()

        choice = ((ds.get("choices") or [{}])[0]) if isinstance(ds, dict) else {}
        msg = choice.get("message") if isinstance(choice, dict) else {}
        msg = msg if isinstance(msg, dict) else {}
        usage = deepseek_usage_to_responses_usage(
            ds.get("usage") if isinstance(ds, dict) else None
        )

        output_items: List[Dict[str, Any]] = []
        tool_calls = msg.get("tool_calls") if isinstance(msg, dict) else None
        structured_tool_call_count = 0
        malformed_tool_errors: List[str] = []
        prepared_tool_calls: List[Dict[str, Any]] = []
        if isinstance(tool_calls, list) and tool_calls:
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                prepared, err = prepare_structured_tool_call(
                    tc,
                    registered_tools=registered_set,
                    registered_arg_names=registered_arg_names,
                )
                if err:
                    malformed_tool_errors.append(err)
                    continue
                if prepared:
                    prepared_tool_calls.append(prepared)

            if malformed_tool_errors:
                log(
                    "blocked malformed non-stream structured tool call(s): "
                    + "; ".join(malformed_tool_errors)
                )
            else:
                for prepared in prepared_tool_calls:
                    output_items.append(tool_call_to_response_item(prepared))
                    structured_tool_call_count += 1

        reasoning = msg.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning.strip():
            output_items.append(
                {
                    "type": "reasoning",
                    "id": f"rs_{uuid.uuid4().hex[:12]}",
                    "summary": [{"type": "summary_text", "text": reasoning[:1000]}],
                    "content": [{"type": "reasoning_text", "text": reasoning}],
                    "encrypted_content": None,
                }
            )

        text = normalize_dsml_display(str(msg.get("content") or ""))
        if malformed_tool_errors:
            text = (
                (text.strip() + "\n\n") if text.strip() else ""
            ) + malformed_tool_call_message(malformed_tool_errors)
        elif structured_tool_call_count == 0:
            text, dsml_calls = extract_dsml_tool_calls(
                text,
                registered_set,
                registered_arg_names,
            )
            if dsml_calls:
                log(f"parsed dsml tool calls count={len(dsml_calls)} in non-stream response")
                for tc in dsml_calls:
                    output_items.append(tool_call_to_response_item(tc))

        if text.strip():
            output_items.append(to_response_message_item(text))
        if not output_items:
            output_items.append(to_response_message_item(""))

        return {
            "id": response_id,
            "object": "response",
            "model": provider_model,
            "status": "completed",
            "output": output_items,
            "output_text": text,
            "usage": usage,
        }

    # ---------------- streaming path ------------------------------------
    payload["stream"] = True

    async def event_stream() -> AsyncIterator[str]:
        usage: Dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        text_parts: List[str] = []
        reasoning_parts: List[str] = []
        tool_states: Dict[int, Dict[str, Any]] = {}
        message_item_id_local = f"msg_{uuid.uuid4().hex[:20]}"
        message_item_open = {"value": False}
        message_output_index: Dict[str, Optional[int]] = {"value": None}
        next_output_index = {"value": 0}
        dsml_buf = StreamingDsmlBuffer(registered_set)

        def allocate_output_index() -> int:
            idx = next_output_index["value"]
            next_output_index["value"] += 1
            return idx

        yield sse_event(
            "response.created",
            {
                "type": "response.created",
                "response": {
                    "id": response_id,
                    "object": "response",
                    "model": provider_model,
                    "status": "in_progress",
                },
            },
        )

        def text_delta_events(text: str) -> List[str]:
            """Build SSE events for emitting `text` as a text delta."""
            out: List[str] = []
            if not text:
                return out
            if not message_item_open["value"]:
                message_item_open["value"] = True
                message_output_index["value"] = allocate_output_index()
                out.append(
                    sse_event(
                        "response.output_item.added",
                        {
                            "type": "response.output_item.added",
                            "output_index": message_output_index["value"],
                            "item": {
                                "id": message_item_id_local,
                                "type": "message",
                                "role": "assistant",
                                "content": [],
                            },
                        },
                    )
                )
            text_parts.append(text)
            out.append(
                sse_event(
                    "response.output_text.delta",
                    {
                        "type": "response.output_text.delta",
                        "delta": text,
                        "item_id": message_item_id_local,
                        "output_index": message_output_index["value"],
                        "content_index": 0,
                    },
                )
            )
            return out

        def dsml_block_events(blocks: List[str]) -> List[str]:
            out: List[str] = []
            for block in blocks:
                _, calls = extract_dsml_tool_calls(
                    block,
                    registered_set,
                    registered_arg_names,
                )
                for tc in calls:
                    fn = tc.get("function") or {}
                    item_id = f"fc_{uuid.uuid4().hex[:12]}"
                    call_id = tc.get("id") or f"call_{uuid.uuid4().hex[:12]}"
                    name = fn.get("name") or "tool"
                    args_json = fn.get("arguments") or "{}"
                    log(f"streaming dsml tool call name={name} call_id={call_id}")
                    out.extend(
                        _function_call_lifecycle_events(
                            item_id=item_id,
                            call_id=call_id,
                            name=name,
                            arguments_json=args_json,
                            output_index=allocate_output_index(),
                            chunk_size=CHUNK_SIZE,
                        )
                    )
            return out

        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                async with client.stream(
                    "POST", DEEPSEEK_CHAT_URL, json=payload, headers=headers
                ) as resp:
                    if resp.status_code >= 400:
                        detail = (await resp.aread()).decode("utf-8", errors="replace")[:1000]
                        log(f"deepseek stream error status={resp.status_code} body={detail}")
                        yield sse_event(
                            "response.failed",
                            {
                                "type": "response.failed",
                                "response": {
                                    "id": response_id,
                                    "object": "response",
                                    "model": provider_model,
                                    "status": "failed",
                                    "error": {
                                        "code": "deepseek_error",
                                        "message": detail or "upstream error",
                                    },
                                },
                            },
                        )
                        return

                    async for raw_line in resp.aiter_lines():
                        line = raw_line.strip()
                        if not line or line.startswith(":") or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if not data:
                            continue
                        if data == "[DONE]":
                            break

                        try:
                            chunk = json.loads(data)
                        except Exception:
                            continue

                        chunk_usage = chunk.get("usage") if isinstance(chunk, dict) else None
                        if isinstance(chunk_usage, dict):
                            usage = deepseek_usage_to_responses_usage(chunk_usage)

                        choices = chunk.get("choices") if isinstance(chunk, dict) else None
                        if not isinstance(choices, list) or not choices:
                            continue

                        choice = choices[0] if isinstance(choices[0], dict) else {}
                        delta = (
                            choice.get("delta")
                            if isinstance(choice.get("delta"), dict)
                            else {}
                        )

                        # 1. Reasoning
                        reasoning_delta = delta.get("reasoning_content")
                        if isinstance(reasoning_delta, str) and reasoning_delta:
                            reasoning_parts.append(reasoning_delta)
                            yield sse_event(
                                "response.reasoning_text.delta",
                                {
                                    "type": "response.reasoning_text.delta",
                                    "delta": reasoning_delta,
                                    "content_index": 0,
                                },
                            )

                        # 2. Structured tool calls
                        tool_calls_delta = delta.get("tool_calls")
                        if isinstance(tool_calls_delta, list):
                            for tc in tool_calls_delta:
                                if not isinstance(tc, dict):
                                    continue
                                idx_raw = tc.get("index", 0)
                                try:
                                    idx = int(idx_raw)
                                except Exception:
                                    idx = 0

                                if idx not in tool_states:
                                    new_call_id = (
                                        tc.get("id")
                                        if isinstance(tc.get("id"), str) and tc.get("id")
                                        else f"call_{uuid.uuid4().hex[:12]}"
                                    )
                                    tool_states[idx] = {
                                        "id": new_call_id,
                                        "type": "function",
                                        "function": {"name": "", "arguments": ""},
                                    }

                                state = tool_states[idx]
                                if isinstance(tc.get("id"), str) and tc.get("id"):
                                    state["id"] = tc["id"]

                                fn = tc.get("function")
                                if isinstance(fn, dict):
                                    delta_name = fn.get("name") or ""
                                    delta_args = fn.get("arguments") or ""

                                    if delta_name:
                                        state["function"]["name"] += delta_name

                                    if delta_args:
                                        state["function"]["arguments"] += delta_args

                        # 3. Text content (with inline DSML detection)
                        content_delta = delta.get("content")
                        if isinstance(content_delta, str) and content_delta:
                            normalized = normalize_dsml_display(content_delta)
                            safe_text, completed_blocks = dsml_buf.feed(normalized)
                            for ev in text_delta_events(safe_text):
                                yield ev
                            if completed_blocks:
                                for ev in dsml_block_events(completed_blocks):
                                    yield ev
        except (httpx.RemoteProtocolError, httpx.ReadError, asyncio.CancelledError) as exc:
            log(f"deepseek stream connection error: {exc!r}")
            yield sse_event(
                "response.failed",
                {
                    "type": "response.failed",
                    "response": {
                        "id": response_id,
                        "object": "response",
                        "model": provider_model,
                        "status": "failed",
                        "error": {"code": "bridge_stream_error", "message": str(exc)},
                    },
                },
            )
            return
        except Exception as exc:
            log(f"deepseek stream bridge error: {exc!r}")
            yield sse_event(
                "response.failed",
                {
                    "type": "response.failed",
                    "response": {
                        "id": response_id,
                        "object": "response",
                        "model": provider_model,
                        "status": "failed",
                        "error": {"code": "bridge_stream_error", "message": str(exc)},
                    },
                },
            )
            return

        # Flush residual buffer at end of stream.
        residual_text, residual_blocks = dsml_buf.flush()
        if residual_text:
            for ev in text_delta_events(residual_text):
                yield ev
        if residual_blocks:
            for ev in dsml_block_events(residual_blocks):
                yield ev

        # Close structured tool items only after the full argument JSON is
        # available and validated. DeepSeek can end a stream while still inside
        # a large JSON string; forwarding that partial call makes Codex's tool
        # router fail before the model can recover.
        prepared_structured_calls: List[Dict[str, Any]] = []
        malformed_tool_errors: List[str] = []
        for idx in sorted(tool_states.keys()):
            prepared, err = prepare_structured_tool_call(
                tool_states[idx],
                registered_tools=registered_set,
                registered_arg_names=registered_arg_names,
            )
            if err:
                malformed_tool_errors.append(err)
                continue
            if prepared:
                prepared_structured_calls.append(prepared)

        if malformed_tool_errors:
            log(
                "blocked malformed streaming structured tool call(s): "
                + "; ".join(malformed_tool_errors)
            )
            diagnostic = malformed_tool_call_message(malformed_tool_errors)
            if text_parts and not text_parts[-1].endswith("\n"):
                diagnostic = "\n\n" + diagnostic
            for ev in text_delta_events(diagnostic):
                yield ev
        else:
            for prepared in prepared_structured_calls:
                fn = prepared.get("function") or {}
                for ev in _function_call_lifecycle_events(
                    item_id=f"fc_{uuid.uuid4().hex[:12]}",
                    call_id=prepared.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                    name=fn.get("name") or "tool",
                    arguments_json=fn.get("arguments") or "{}",
                    output_index=allocate_output_index(),
                    chunk_size=CHUNK_SIZE,
                ):
                    yield ev

        # Reasoning summary item.
        full_reasoning = "".join(reasoning_parts).strip()
        if full_reasoning:
            yield sse_event(
                "response.output_item.done",
                {
                    "type": "response.output_item.done",
                    "output_index": allocate_output_index(),
                    "item": {
                        "type": "reasoning",
                        "id": f"rs_{uuid.uuid4().hex[:12]}",
                        "summary": [{"type": "summary_text", "text": full_reasoning[:1000]}],
                        "content": [{"type": "reasoning_text", "text": full_reasoning}],
                        "encrypted_content": None,
                    },
                },
            )

        # Close the message item.
        full_text = "".join(text_parts)
        if message_item_open["value"]:
            yield sse_event(
                "response.output_item.done",
                {
                    "type": "response.output_item.done",
                    "output_index": message_output_index["value"],
                    "item": {
                        "id": message_item_id_local,
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": full_text}],
                    },
                },
            )

        yield sse_event(
            "response.completed",
            {
                "type": "response.completed",
                "response": {
                    "id": response_id,
                    "object": "response",
                    "model": provider_model,
                    "status": "completed",
                    "usage": usage,
                },
            },
        )

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def main():
    import uvicorn
    host = os.environ.get(
        "CODESEEQ_BRIDGE_HOST",
        os.environ.get("CODESEEQ_OPENRESPONSES_HOST", "127.0.0.1"),
    )
    port = int(
        os.environ.get(
            "CODESEEQ_BRIDGE_PORT",
            os.environ.get("CODESEEQ_OPENRESPONSES_PORT", "8080"),
        )
    )
    if host != "127.0.0.1":
        log(f"warning: bridge binding to non-localhost address: {host}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
