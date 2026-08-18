#!/usr/bin/env python3
"""
codeseeq-bridge: OpenAI Responses API <-> provider translation bridge.

v0.4.2 patches:
- Multi-provider routing: DeepSeek (OpenAI-compatible chat completions),
  Anthropic Claude (native Messages API, streaming + non-streaming,
  extended thinking, tool use), Google Gemini (OpenAI-compatible endpoint),
  Grok/xAI, Venice.ai, and arbitrary local OpenAI-compatible gateways.
  Model slugs use the provider@model form; unknown <provider>@<model> names
  are accepted and routed to the provider's base URL.
- Anthropic tool-loop fixes: assistant tool_calls from prior turns are now
  forwarded as tool_use content blocks (Anthropic 400s tool_result blocks
  whose tool_use_id has no matching tool_use in history), and temperature /
  top_p are no longer sent alongside extended thinking (Anthropic rejects
  them); max_tokens is raised to at least the thinking budget and a specific
  tool_choice is downgraded to "auto" while thinking is enabled.
- CODESEEQ_PROVIDER override is honored for routing and key selection
  (wrapper and bridge stay in sync), and /health reports the effective
  provider of the configured model.

v0.4.1 patches:
- Parent-death watchdog: the bridge shuts itself down and releases its port
  the moment its parent process disappears (even under SIGKILL), so
  hard-killed `codeseeq run` invocations can no longer orphan bridges and
  leak ports (previously the auto-select port range could fill up).
- Stale-bridge reaping on startup: before starting a process bridge, orphaned
  codeseeq-bridge.py processes writing to the same bridge log are terminated
  so ports leaked by older versions are reclaimed.

v0.3.9 patches:
- JSON config fallback (CODESEEQ_CONFIG_JSON) with env > JSON > built-in
  precedence; every CODESEEQ_* variable plus provider/credential names is
  configurable either way.
- Bind-based port auto-selection (no connect-probe TOCTOU) via main() with a
  port discovery file (CODESEEQ_BRIDGE_PORT_FILE) and a fixed-port path.
- Enforced CODESEEQ_STREAM_IDLE_TIMEOUT_MS as an idle (between-chunk) read
  timeout on streaming responses.
- Per-model env keys strip the "provider@" prefix so documented
  CODESEEQ_<MODEL>_* overrides (e.g. CODESEEQ_QWIBUS_QWIKK_BASE_URL) work, and
  explicit env/config values take precedence over the model catalog.
- Removed dead session-tracking code and CODESEEQ_SESSION_TTL_SECONDS.

Prior patches:
- Per-model endpoint + sampling configuration (base/chat URL, temperature,
  top_p, top_k, context window, max output tokens, timeout, thinking, and
  per-model system prompt). Added qwibus-qwikk and qwibus-qmplx local
  gateway models with no API-key requirement, plus per-model
  CODESEEQ_<KEY>_* env overrides and generic OPENAI/DEEPSEEK/CODESEEQ_BASE_URL
  fallbacks.
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
import signal
import socket
import sys
import threading
import tempfile
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional, Set, Tuple

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()

# ---------------------------------------------------------------------------
# JSON configuration fallback (env vars override config.toml/json at all times)
# ---------------------------------------------------------------------------
# Every CODESEEQ_* environment variable (plus the provider/credential names
# below) can alternatively be supplied through a JSON config file. JSON keys
# are the literal environment variable names. The precedence is:
#
#   explicit environment variable  >  JSON config  >  built-in default
#
# The config path is resolved from CODESEEQ_CONFIG_JSON, or
# $CODESEEQ_CONFIG_HOME/config.json, or ~/.config/codeseeq/config.json, or
# /etc/codeseeq/config.json (first file that exists wins).

_NON_CODESEEQ_CONFIG_KEYS = frozenset(
    {
        "DEEPSEEK_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GROK_API_KEY",
        "BRAVE_API_KEY",
        "UNSTRUCTURED_API_KEY",
        "RESPONSES_API_KEY",
        "VENICE_API_KEY",
        "CONTAINER",
        "IMAGE",
        "OPENAI_BASE_URL",
        "DEEPSEEK_BASE_URL",
        "DEEPSEEK_CHAT_URL",
        "ANTHROPIC_BASE_URL",
        "GOOGLE_BASE_URL",
        "GROK_BASE_URL",
        "VENICE_BASE_URL",
        "LOCAL_BASE_URL",
        "UNSTRUCTURED_API_URL",
        "QWIBUS_NO_API_KEY",
    }
)


def _config_json_candidates() -> List[str]:
    candidates: List[str] = []
    explicit = os.environ.get("CODESEEQ_CONFIG_JSON")
    if explicit:
        candidates.append(explicit)
    config_home = os.environ.get("CODESEEQ_CONFIG_HOME")
    if config_home:
        candidates.append(os.path.join(config_home, "config.json"))
    candidates.append(os.path.expanduser("~/.config/codeseeq/config.json"))
    candidates.append("/etc/codeseeq/config.json")
    return candidates


def _load_config_json() -> None:
    """Populate os.environ from the JSON config for any configurable key that
    is not already set in the environment (env var always wins)."""
    path = None
    for candidate in _config_json_candidates():
        if os.path.isfile(candidate):
            path = candidate
            break
    if path is None:
        return

    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        print(f"[codeseeq-bridge] warning: ignoring unreadable/invalid JSON config: {path}", file=sys.stderr, flush=True)
        return

    if not isinstance(data, dict):
        print(f"[codeseeq-bridge] warning: ignoring JSON config (not an object): {path}", file=sys.stderr, flush=True)
        return

    for key, value in data.items():
        if not isinstance(key, str) or not key:
            continue
        # Only apply keys that are real config parameters: any CODESEEQ_*
        # variable, plus the explicit provider/credential allowlist.
        if not (key.startswith("CODESEEQ_") or key in _NON_CODESEEQ_CONFIG_KEYS):
            continue
        if os.environ.get(key) not in (None, ""):
            continue
        if isinstance(value, bool):
            os.environ[key] = "true" if value else "false"
        elif isinstance(value, (str, int, float)):
            os.environ[key] = str(value)


# Load JSON config before any module-level os.environ reads below.
_load_config_json()


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
# CODESEEQ_STREAM_IDLE_TIMEOUT_MS enforces an idle (between-chunk) timeout on
# streaming responses so a stalled upstream/client cannot hang a uvicorn task.
STREAM_IDLE_TIMEOUT_MS = float(os.environ.get("CODESEEQ_STREAM_IDLE_TIMEOUT_MS", "600000"))
DEFAULT_DEEPSEEK_MAX_OUTPUT_TOKENS = 384000

# ---------------------------------------------------------------------------
# Per-model configuration.
#
# Each supported model carries its own endpoint + sampling defaults so that a
# model can point at a different OpenAI-compatible gateway (localhost, custom
# proxy, etc.) without flipping global env vars. Values below are defaults;
# they can be overridden per-model via the following env-var naming scheme:
#
#   CODESEEQ_<KEY>_BASE_URL            e.g. CODESEEQ_QWIBUS_QWIKK_BASE_URL
#   CODESEEQ_<KEY>_CHAT_URL            e.g. CODESEEQ_QWIBUS_QWIKK_CHAT_URL
#   CODESEEQ_<KEY>_TEMPERATURE
#   CODESEEQ_<KEY>_TOP_P
#   CODESEEQ_<KEY>_TOP_K
#   CODESEEQ_<KEY>_MAX_OUTPUT_TOKENS
#   CODESEEQ_<KEY>_TIMEOUT_SECONDS
#   CODESEEQ_<KEY>_ENABLE_THINKING
#   CODESEEQ_<KEY>_SYSTEM_PROMPT
#
# where <KEY> is the model slug with non-alphanumerics replaced by "_" and
# upper-cased (e.g. "qwibus-qwikk" -> "QWIBUS_QWIKK"). If no per-model env
# override is present, the generic *_BASE_URL fallback order is:
#   OPENAI_BASE_URL -> DEEPSEEK_BASE_URL -> CODESEEQ_BASE_URL -> built-in default.
# ---------------------------------------------------------------------------


_MODEL_ENV_KEY_RE = re.compile(r"[^A-Za-z0-9]+")


def _model_env_key(name: str) -> str:
    # Strip a "provider@model" prefix so the generated env key matches the
    # documented CODESEEQ_<MODEL>_* scheme (e.g. "qwibus@qwibus-qwikk" ->
    # "QWIBUS_QWIKK"). Without this, per-model env overrides never matched.
    if "@" in name:
        name = name.rsplit("@", 1)[-1]
    return _MODEL_ENV_KEY_RE.sub("_", name).strip("_").upper()


def _env_first(*names: str) -> Optional[str]:
    for n in names:
        v = os.environ.get(n)
        if v is not None and v != "":
            return v
    return None


def _env_float(*names: str) -> Optional[float]:
    v = _env_first(*names)
    if v is None:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _env_int(*names: str) -> Optional[int]:
    v = _env_first(*names)
    if v is None:
        return None
    try:
        return int(v)
    except ValueError:
        return None


# Built-in provider base/chat defaults. The DeepSeek models keep these as
# their default; the qwibus models override them (localhost gateway).
_DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"
_DEEPSEEK_DEFAULT_CHAT_URL = "https://api.deepseek.com/chat/completions"


class ModelSpec:
    """Resolved per-model runtime configuration."""

    __slots__ = (
        "slug",
        "deepseek_model",
        "provider",
        "api_key_env",
        "thinking",
        "enable_thinking",
        "base_url",
        "chat_url",
        "temperature",
        "top_p",
        "top_k",
        "context_window",
        "max_output_tokens",
        "timeout_seconds",
        "system_prompt",
    )

    def __init__(
        self,
        *,
        slug: str,
        deepseek_model: str,
        thinking: bool,
        base_url: str,
        chat_url: str,
        temperature: Optional[float],
        top_p: Optional[float],
        top_k: Optional[int],
        context_window: int,
        max_output_tokens: int,
        timeout_seconds: float,
        system_prompt: Optional[str],
        provider: str = "deepseek",
        api_key_env: Optional[str] = "DEEPSEEK_API_KEY",
    ) -> None:
        self.slug = slug
        self.deepseek_model = deepseek_model
        self.provider = provider
        self.api_key_env = api_key_env
        self.thinking = thinking
        self.enable_thinking = thinking
        self.base_url = base_url
        self.chat_url = chat_url
        self.temperature = temperature
        self.top_p = top_p
        self.top_k = top_k
        self.context_window = context_window
        self.max_output_tokens = max_output_tokens
        self.timeout_seconds = timeout_seconds
        self.system_prompt = system_prompt


# Canonical model slugs -> upstream model name + thinking flag.
MODEL_ALIASES: Dict[str, Tuple[str, bool]] = {
    "deepseek-v4-flash": ("deepseek-v4-flash", False),
    "deepseek-v4-flash-thinking": ("deepseek-v4-flash", True),
    "deepseek-v4-pro": ("deepseek-v4-pro", False),
    "deepseek-v4-pro-thinking": ("deepseek-v4-pro", True),
    "deepseek@deepseek-v4-flash": ("deepseek-v4-flash", False),
    "deepseek@deepseek-v4-flash-thinking": ("deepseek-v4-flash", True),
    "deepseek@deepseek-v4-pro": ("deepseek-v4-pro", False),
    "deepseek@deepseek-v4-pro-thinking": ("deepseek-v4-pro", True),
    "qwibus-qwikk": ("qwibus-qwikk", False),
    "qwibus-qmplx": ("qwibus-qmplx", True),
    "qwibus@qwibus-qwikk": ("qwibus-qwikk", False),
    "qwibus@qwibus-qmplx": ("qwibus-qmplx", True),
}


# ---------------------------------------------------------------------------
# Provider routing
# ---------------------------------------------------------------------------
# The bridge emulates the OpenAI Responses API for Codex and forwards to the
# configured upstream provider. OpenAI-compatible providers (deepseek, grok,
# venice, google's OpenAI-compat endpoint, local gateways) share the chat-
# completions translation; anthropic uses the native Messages API.
PROVIDER_DEEPSEEK = "deepseek"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_GOOGLE = "google"
PROVIDER_GROK = "grok"
PROVIDER_VENICE = "venice"
PROVIDER_LOCAL = "local"

OPENAI_COMPAT_PROVIDERS = frozenset(
    {
        PROVIDER_DEEPSEEK,
        PROVIDER_GROK,
        PROVIDER_VENICE,
        PROVIDER_GOOGLE,
        PROVIDER_LOCAL,
    }
)

# Provider -> environment variable holding the API key. Local gateways are
# keyless (None). The configured provider is chosen by the slug prefix or the
# CODESEEQ_PROVIDER override (see resolve_provider_for_slug).
PROVIDER_API_KEY_ENV = {
    PROVIDER_DEEPSEEK: "DEEPSEEK_API_KEY",
    PROVIDER_ANTHROPIC: "ANTHROPIC_API_KEY",
    PROVIDER_GOOGLE: "GOOGLE_API_KEY",
    PROVIDER_GROK: "GROK_API_KEY",
    PROVIDER_VENICE: "VENICE_API_KEY",
    PROVIDER_LOCAL: None,
}

# Generic provider base-URL override env vars, checked in order per provider.
PROVIDER_BASE_URL_ENV = {
    PROVIDER_DEEPSEEK: ("DEEPSEEK_BASE_URL", "OPENAI_BASE_URL", "CODESEEQ_BASE_URL"),
    PROVIDER_ANTHROPIC: ("ANTHROPIC_BASE_URL",),
    PROVIDER_GOOGLE: ("GOOGLE_BASE_URL", "OPENAI_BASE_URL", "CODESEEQ_BASE_URL"),
    PROVIDER_GROK: ("GROK_BASE_URL", "OPENAI_BASE_URL", "CODESEEQ_BASE_URL"),
    PROVIDER_VENICE: ("VENICE_BASE_URL", "OPENAI_BASE_URL", "CODESEEQ_BASE_URL"),
    PROVIDER_LOCAL: ("LOCAL_BASE_URL", "OPENAI_BASE_URL", "CODESEEQ_BASE_URL"),
}

# Provider base URLs (used as default_base for each model when no override).
PROVIDER_DEFAULT_BASE_URL = {
    PROVIDER_DEEPSEEK: "https://api.deepseek.com",
    PROVIDER_ANTHROPIC: "https://api.anthropic.com",
    PROVIDER_GOOGLE: "https://generativelanguage.googleapis.com",
    PROVIDER_GROK: "https://api.x.ai",
    PROVIDER_VENICE: "https://api.venice.ai",
    PROVIDER_LOCAL: "http://127.0.0.1:1337",
}


def resolve_provider_for_slug(slug: str) -> str:
    """Map a canonical model slug to a provider id.

    Slugs use the provider@model convention; bare deepseek/qwibus legacy slugs
    keep their historical providers. CODESEEQ_PROVIDER can override routing so
    a user can point e.g. a `local@*` model at a different local gateway.
    """
    override = os.environ.get("CODESEEQ_PROVIDER", "").strip().lower()
    if override:
        if override not in PROVIDER_API_KEY_ENV:
            raise ValueError(
                "invalid CODESEEQ_PROVIDER: "
                + ", ".join(sorted(PROVIDER_API_KEY_ENV.keys()))
            )
        return override
    owner = slug.split("@", 1)[0].lower() if "@" in slug else ""
    if owner == "qwibus":
        return PROVIDER_LOCAL
    if owner:
        return owner
    if slug.startswith("qwibus"):
        return PROVIDER_LOCAL
    return PROVIDER_DEEPSEEK


def provider_api_key_env(provider: str) -> Optional[str]:
    return PROVIDER_API_KEY_ENV.get(provider)


def provider_api_key(provider: str) -> Optional[str]:
    env_name = provider_api_key_env(provider)
    if not env_name:
        return None
    return os.environ.get(env_name, "").strip() or None


def require_provider_key(provider: str) -> Optional[str]:
    """Return the API key for `provider`, or None for keyless providers.

    Raises HTTP 400 with a helpful message when the provider requires a key
    that is not configured.
    """
    env_name = provider_api_key_env(provider)
    if not env_name:
        return None  # keyless provider (local gateway)
    key = os.environ.get(env_name, "").strip()
    if not key:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{env_name} is required for the {provider} provider. "
                f"Set it in your environment or run `codeseeq config`."
            ),
        )
    return key




def _build_model_specs() -> Dict[str, ModelSpec]:
    """Build the canonical per-model runtime configuration.

    Defaults encode the deepseek, qwibus (local), anthropic, google, grok and
    venice models. Per-model env overrides use the CODESEEQ_<KEY>_* naming
    scheme (see the comment above). Generic base-url fallbacks are resolved
    per provider via PROVIDER_BASE_URL_ENV (deepseek keeps the historical
    OPENAI_BASE_URL -> DEEPSEEK_BASE_URL -> CODESEEQ_BASE_URL order).
    """
    def _spec(
        slug: str,
        deepseek_model: str,
        thinking: bool,
        *,
        default_base: Optional[str] = None,
        default_chat_url: Optional[str] = None,
        default_temperature: Optional[float] = None,
        default_top_p: Optional[float] = None,
        default_top_k: Optional[int] = None,
        context_window: int = 1000000,
        max_output_tokens: int = 384000,
        timeout_seconds: float = 120.0,
        provider: str = PROVIDER_DEEPSEEK,
    ) -> ModelSpec:
        key = _model_env_key(slug)
        # Provider base URL: per-model override, provider generic override
        # (e.g. ANTHROPIC_BASE_URL), then the built-in default.
        per_model_base = _env_first(f"CODESEEQ_{key}_BASE_URL")
        generic_base = _env_first(*PROVIDER_BASE_URL_ENV.get(provider, ()))
        base_url = per_model_base or generic_base or default_base or PROVIDER_DEFAULT_BASE_URL[provider]
        # Chat URL: per-model override wins; deepseek keeps the legacy
        # DEEPSEEK_CHAT_URL fallback (used by tests and single-endpoint
        # deployments). When the caller supplied a custom base URL (per-model
        # or generic provider override), the chat endpoint is derived from it
        # (Anthropic -> /v1/messages, everything else -> /chat/completions)
        # so proxy/re-gateway overrides actually take effect. Otherwise the
        # provider's built-in default chat URL is used (e.g. Google's
        # /v1beta/openai/chat/completions path, which cannot be guessed from
        # the bare base URL).
        per_model_chat = _env_first(f"CODESEEQ_{key}_CHAT_URL")
        if per_model_chat:
            chat_url = per_model_chat  # type: ignore[assignment]
        elif provider == PROVIDER_DEEPSEEK and _env_first("DEEPSEEK_CHAT_URL"):
            chat_url = _env_first("DEEPSEEK_CHAT_URL")  # type: ignore[assignment]
        elif per_model_base or generic_base:
            chat_url = (
                f"{base_url.rstrip('/')}/v1/messages"
                if provider == PROVIDER_ANTHROPIC
                else f"{base_url.rstrip('/')}/chat/completions"
            )
        elif default_chat_url:
            chat_url = default_chat_url
        elif provider == PROVIDER_ANTHROPIC:
            chat_url = f"{base_url.rstrip('/')}/v1/messages"
        else:
            chat_url = f"{base_url.rstrip('/')}/chat/completions"
        temperature = _env_float(f"CODESEEQ_{key}_TEMPERATURE")
        if temperature is None:
            temperature = default_temperature
        top_p = _env_float(f"CODESEEQ_{key}_TOP_P")
        if top_p is None:
            top_p = default_top_p
        top_k = _env_int(f"CODESEEQ_{key}_TOP_K")
        if top_k is None:
            top_k = default_top_k
        mt = _env_int(f"CODESEEQ_{key}_MAX_OUTPUT_TOKENS")
        if mt is None:
            mt = max_output_tokens
        to = _env_int(f"CODESEEQ_{key}_TIMEOUT_SECONDS")
        if to is None:
            to = int(timeout_seconds)
        enable_raw = os.environ.get(f"CODESEEQ_{key}_ENABLE_THINKING")
        if enable_raw is not None:
            thinking = enable_raw.strip().lower() in {"1", "true", "yes", "on"}
        sys_prompt = os.environ.get(f"CODESEEQ_{key}_SYSTEM_PROMPT")
        return ModelSpec(
            slug=slug,
            deepseek_model=deepseek_model,
            thinking=thinking,
            base_url=base_url,
            chat_url=chat_url,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            context_window=context_window,
            max_output_tokens=mt,
            timeout_seconds=float(to),
            system_prompt=sys_prompt,
            provider=provider,
            api_key_env=PROVIDER_API_KEY_ENV.get(provider),
        )

    return {
        "deepseek@deepseek-v4-flash": _spec(
            "deepseek@deepseek-v4-flash",
            "deepseek-v4-flash",
            False,
            default_base=_DEEPSEEK_DEFAULT_BASE_URL,
            default_temperature=None,
            default_top_p=None,
            default_top_k=None,
            context_window=1000000,
            max_output_tokens=384000,
            timeout_seconds=60,
        ),
        "deepseek@deepseek-v4-flash-thinking": _spec(
            "deepseek@deepseek-v4-flash-thinking",
            "deepseek-v4-flash",
            True,
            default_base=_DEEPSEEK_DEFAULT_BASE_URL,
            default_temperature=None,
            default_top_p=None,
            default_top_k=None,
            context_window=1000000,
            max_output_tokens=384000,
            timeout_seconds=600,
        ),
        "deepseek@deepseek-v4-pro": _spec(
            "deepseek@deepseek-v4-pro",
            "deepseek-v4-pro",
            False,
            default_base=_DEEPSEEK_DEFAULT_BASE_URL,
            default_temperature=None,
            default_top_p=None,
            default_top_k=None,
            context_window=1000000,
            max_output_tokens=384000,
            timeout_seconds=120,
        ),
        "deepseek@deepseek-v4-pro-thinking": _spec(
            "deepseek@deepseek-v4-pro-thinking",
            "deepseek-v4-pro",
            True,
            default_base=_DEEPSEEK_DEFAULT_BASE_URL,
            default_temperature=None,
            default_top_p=None,
            default_top_k=None,
            context_window=1000000,
            max_output_tokens=384000,
            timeout_seconds=1200,
        ),
        "qwibus@qwibus-qwikk": _spec(
            "qwibus@qwibus-qwikk",
            "qwibus-qwikk",
            False,
            default_base="http://127.0.0.1:1337",
            default_chat_url="http://127.0.0.1:1337/v1/chat/completions",
            default_temperature=0.4,
            default_top_p=0.92,
            default_top_k=20,
            context_window=16384,
            max_output_tokens=4096,
            timeout_seconds=60,
        ),
        "qwibus@qwibus-qmplx": _spec(
            "qwibus@qwibus-qmplx",
            "qwibus-qmplx",
            True,
            default_base="http://127.0.0.1:1337",
            default_chat_url="http://127.0.0.1:1337/v1/chat/completions",
            default_temperature=0.6,
            default_top_p=0.95,
            default_top_k=20,
            context_window=32768,
            max_output_tokens=8192,
            timeout_seconds=600,
            provider=PROVIDER_LOCAL,
        ),
        # Anthropic (native Messages API, non-OpenAI-compatible wire).
        "anthropic@claude-sonnet-4": _spec(
            "anthropic@claude-sonnet-4",
            "claude-sonnet-4-20250514",
            False,
            provider=PROVIDER_ANTHROPIC,
            default_temperature=1.0,
            context_window=200000,
            max_output_tokens=64000,
            timeout_seconds=600,
        ),
        "anthropic@claude-sonnet-4-thinking": _spec(
            "anthropic@claude-sonnet-4-thinking",
            "claude-sonnet-4-20250514",
            True,
            provider=PROVIDER_ANTHROPIC,
            default_temperature=1.0,
            context_window=200000,
            max_output_tokens=64000,
            timeout_seconds=600,
        ),
        "anthropic@claude-opus-4": _spec(
            "anthropic@claude-opus-4",
            "claude-opus-4-20250514",
            False,
            provider=PROVIDER_ANTHROPIC,
            default_temperature=1.0,
            context_window=200000,
            max_output_tokens=64000,
            timeout_seconds=600,
        ),
        "anthropic@claude-opus-4-thinking": _spec(
            "anthropic@claude-opus-4-thinking",
            "claude-opus-4-20250514",
            True,
            provider=PROVIDER_ANTHROPIC,
            default_temperature=1.0,
            context_window=200000,
            max_output_tokens=64000,
            timeout_seconds=600,
        ),
        "anthropic@claude-haiku-4": _spec(
            "anthropic@claude-haiku-4",
            "claude-haiku-4-20250514",
            False,
            provider=PROVIDER_ANTHROPIC,
            default_temperature=1.0,
            context_window=200000,
            max_output_tokens=64000,
            timeout_seconds=300,
        ),
        # Google Gemini (OpenAI-compatible endpoint).
        "google@gemini-2.5-pro": _spec(
            "google@gemini-2.5-pro",
            "gemini-2.5-pro",
            True,
            provider=PROVIDER_GOOGLE,
            default_chat_url="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            default_temperature=1.0,
            context_window=1000000,
            max_output_tokens=65536,
            timeout_seconds=600,
        ),
        "google@gemini-2.5-flash": _spec(
            "google@gemini-2.5-flash",
            "gemini-2.5-flash",
            True,
            provider=PROVIDER_GOOGLE,
            default_chat_url="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            default_temperature=1.0,
            context_window=1000000,
            max_output_tokens=65536,
            timeout_seconds=600,
        ),
        "google@gemini-2.0-flash": _spec(
            "google@gemini-2.0-flash",
            "gemini-2.0-flash",
            False,
            provider=PROVIDER_GOOGLE,
            default_chat_url="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            default_temperature=1.0,
            context_window=1000000,
            max_output_tokens=8192,
            timeout_seconds=300,
        ),
        "google@gemini-1.5-pro": _spec(
            "google@gemini-1.5-pro",
            "gemini-1.5-pro",
            False,
            provider=PROVIDER_GOOGLE,
            default_chat_url="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            default_temperature=1.0,
            context_window=2000000,
            max_output_tokens=8192,
            timeout_seconds=300,
        ),
        # Grok / xAI (OpenAI-compatible chat completions).
        "grok@grok-4": _spec(
            "grok@grok-4",
            "grok-4",
            False,
            provider=PROVIDER_GROK,
            default_chat_url="https://api.x.ai/v1/chat/completions",
            default_temperature=0.7,
            context_window=256000,
            max_output_tokens=32768,
            timeout_seconds=600,
        ),
        "grok@grok-4-thinking": _spec(
            "grok@grok-4-thinking",
            "grok-4",
            True,
            provider=PROVIDER_GROK,
            default_chat_url="https://api.x.ai/v1/chat/completions",
            default_temperature=0.7,
            context_window=256000,
            max_output_tokens=32768,
            timeout_seconds=600,
        ),
        "grok@grok-3": _spec(
            "grok@grok-3",
            "grok-3",
            False,
            provider=PROVIDER_GROK,
            default_chat_url="https://api.x.ai/v1/chat/completions",
            default_temperature=0.7,
            context_window=131072,
            max_output_tokens=32768,
            timeout_seconds=600,
        ),
        "grok@grok-3-thinking": _spec(
            "grok@grok-3-thinking",
            "grok-3",
            True,
            provider=PROVIDER_GROK,
            default_chat_url="https://api.x.ai/v1/chat/completions",
            default_temperature=0.7,
            context_window=131072,
            max_output_tokens=32768,
            timeout_seconds=600,
        ),
        "grok@grok-3-mini": _spec(
            "grok@grok-3-mini",
            "grok-3-mini",
            False,
            provider=PROVIDER_GROK,
            default_chat_url="https://api.x.ai/v1/chat/completions",
            default_temperature=0.7,
            context_window=131072,
            max_output_tokens=32768,
            timeout_seconds=600,
        ),
        "grok@grok-3-mini-thinking": _spec(
            "grok@grok-3-mini-thinking",
            "grok-3-mini",
            True,
            provider=PROVIDER_GROK,
            default_chat_url="https://api.x.ai/v1/chat/completions",
            default_temperature=0.7,
            context_window=131072,
            max_output_tokens=32768,
            timeout_seconds=600,
        ),
        "grok@grok-3-fast": _spec(
            "grok@grok-3-fast",
            "grok-3-fast",
            False,
            provider=PROVIDER_GROK,
            default_chat_url="https://api.x.ai/v1/chat/completions",
            default_temperature=0.7,
            context_window=131072,
            max_output_tokens=32768,
            timeout_seconds=600,
        ),
        "grok@grok-3-fast-thinking": _spec(
            "grok@grok-3-fast-thinking",
            "grok-3-fast",
            True,
            provider=PROVIDER_GROK,
            default_chat_url="https://api.x.ai/v1/chat/completions",
            default_temperature=0.7,
            context_window=131072,
            max_output_tokens=32768,
            timeout_seconds=600,
        ),
        # Venice.ai (OpenAI-compatible chat completions).
        "venice@venice-qwen-3-32b": _spec(
            "venice@venice-qwen-3-32b",
            "venice-qwen-3-32b",
            False,
            provider=PROVIDER_VENICE,
            default_chat_url="https://api.venice.ai/api/v1/chat/completions",
            default_temperature=0.7,
            context_window=128000,
            max_output_tokens=32768,
            timeout_seconds=600,
        ),
        "venice@venice-qwen-3-32b-thinking": _spec(
            "venice@venice-qwen-3-32b-thinking",
            "venice-qwen-3-32b",
            True,
            provider=PROVIDER_VENICE,
            default_chat_url="https://api.venice.ai/api/v1/chat/completions",
            default_temperature=0.7,
            context_window=128000,
            max_output_tokens=32768,
            timeout_seconds=600,
        ),
        "venice@venice-qwen-3-14b": _spec(
            "venice@venice-qwen-3-14b",
            "venice-qwen-3-14b",
            False,
            provider=PROVIDER_VENICE,
            default_chat_url="https://api.venice.ai/api/v1/chat/completions",
            default_temperature=0.7,
            context_window=128000,
            max_output_tokens=32768,
            timeout_seconds=600,
        ),
        "venice@venice-deepseek-r1-0528": _spec(
            "venice@venice-deepseek-r1-0528",
            "deepseek-r1-0528",
            False,
            provider=PROVIDER_VENICE,
            default_chat_url="https://api.venice.ai/api/v1/chat/completions",
            default_temperature=0.7,
            context_window=128000,
            max_output_tokens=32768,
            timeout_seconds=600,
        ),
        "venice@venice-llama-3.3-70b": _spec(
            "venice@venice-llama-3.3-70b",
            "llama-3.3-70b",
            False,
            provider=PROVIDER_VENICE,
            default_chat_url="https://api.venice.ai/api/v1/chat/completions",
            default_temperature=0.7,
            context_window=128000,
            max_output_tokens=32768,
            timeout_seconds=600,
        ),
        "venice@venice-qwen-2.5-coder-32b": _spec(
            "venice@venice-qwen-2.5-coder-32b",
            "qwen2.5-coder-32b",
            False,
            provider=PROVIDER_VENICE,
            default_chat_url="https://api.venice.ai/api/v1/chat/completions",
            default_temperature=0.7,
            context_window=128000,
            max_output_tokens=32768,
            timeout_seconds=600,
        ),
        # Local OpenAI-compatible gateway (manual model name, keyless).
        "local@local": _spec(
            "local@local",
            "local",
            False,
            provider=PROVIDER_LOCAL,
            default_base="http://127.0.0.1:1337",
            default_chat_url="http://127.0.0.1:1337/v1/chat/completions",
            default_temperature=0.7,
            context_window=131072,
            max_output_tokens=32768,
            timeout_seconds=600,
        ),
    }


def _apply_catalog_overrides(specs: Dict[str, ModelSpec]) -> Dict[str, ModelSpec]:
    """Layer per-model config from the human-facing model catalog JSON on top
    of the built-in defaults, keeping each entry's spec objects intact.

    The catalog path is CODESEEQ_MODEL_CATALOG_JSON (default
    /etc/codeseeq/model-catalog.json in the container). The JSON catalog is the
    fallback source of truth for endpoint/sampling knobs, but any explicit
    environment variable (or JSON config value, which is loaded into
    os.environ) always wins over the catalog, matching the documented
    precedence: env > config > catalog > built-in default.
    """
    path = os.environ.get(
        "CODESEEQ_MODEL_CATALOG_JSON", "/etc/codeseeq/model-catalog.json"
    )
    try:
        with open(path, "r", encoding="utf-8") as fh:
            catalog = json.load(fh)
    except (OSError, ValueError):
        return specs

    entries = catalog.get("models") if isinstance(catalog, dict) else None
    if not isinstance(entries, list):
        return specs

    def _env_present(*names: str) -> bool:
        return _env_first(*names) is not None

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        provider_model = entry.get("provider_model") or entry.get("slug")
        if not isinstance(provider_model, str):
            continue
        spec = specs.get(provider_model)
        if spec is None:
            continue
        key = _model_env_key(provider_model)

        if entry.get("base_url") is not None and not _env_present(
            f"CODESEEQ_{key}_BASE_URL",
            "OPENAI_BASE_URL",
            "DEEPSEEK_BASE_URL",
            "CODESEEQ_BASE_URL",
        ):
            spec.base_url = str(entry["base_url"])
        if entry.get("chat_url") is not None and not _env_present(
            f"CODESEEQ_{key}_CHAT_URL", "DEEPSEEK_CHAT_URL"
        ):
            spec.chat_url = str(entry["chat_url"])
        if entry.get("temperature") is not None and not _env_present(
            f"CODESEEQ_{key}_TEMPERATURE"
        ):
            try:
                spec.temperature = float(entry["temperature"])
            except (TypeError, ValueError):
                pass
        if entry.get("top_p") is not None and not _env_present(
            f"CODESEEQ_{key}_TOP_P"
        ):
            try:
                spec.top_p = float(entry["top_p"])
            except (TypeError, ValueError):
                pass
        if entry.get("top_k") is not None and not _env_present(
            f"CODESEEQ_{key}_TOP_K"
        ):
            try:
                spec.top_k = int(entry["top_k"])
            except (TypeError, ValueError):
                pass
        if entry.get("max_output_tokens") is not None and not _env_present(
            f"CODESEEQ_{key}_MAX_OUTPUT_TOKENS", "CODESEEQ_MAX_OUTPUT_TOKENS"
        ):
            try:
                spec.max_output_tokens = int(entry["max_output_tokens"])
            except (TypeError, ValueError):
                pass
        if entry.get("timeout_seconds") is not None and not _env_present(
            f"CODESEEQ_{key}_TIMEOUT_SECONDS"
        ):
            try:
                spec.timeout_seconds = float(entry["timeout_seconds"])
            except (TypeError, ValueError):
                pass
        if entry.get("context_window") is not None and not _env_present(
            f"CODESEEQ_{key}_CONTEXT_WINDOW"
        ):
            try:
                spec.context_window = int(entry["context_window"])
            except (TypeError, ValueError):
                pass

        if "enable_thinking" in entry and not _env_present(
            f"CODESEEQ_{key}_ENABLE_THINKING"
        ):
            spec.enable_thinking = bool(entry["enable_thinking"])
            spec.thinking = bool(entry["enable_thinking"])
        elif "thinking" in entry and not _env_present(
            f"CODESEEQ_{key}_ENABLE_THINKING"
        ):
            spec.thinking = bool(entry["thinking"])
            spec.enable_thinking = bool(entry["thinking"])

        if entry.get("system_prompt") is not None and not _env_present(
            f"CODESEEQ_{key}_SYSTEM_PROMPT"
        ):
            spec.system_prompt = entry["system_prompt"]

    return specs


MODEL_SPECS: Dict[str, ModelSpec] = _apply_catalog_overrides(_build_model_specs())

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
    # commands work fine with tty=true - they just complete normally.
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


def resolve_max_tokens(body: Dict[str, Any], spec: "ModelSpec") -> int:
    provider_cap = (
        parse_positive_int(os.environ.get("CODESEEQ_MAX_OUTPUT_TOKENS"))
        or spec.max_output_tokens
        or DEFAULT_DEEPSEEK_MAX_OUTPUT_TOKENS
    )
    requested = parse_positive_int(body.get("max_output_tokens"))
    if requested is None:
        requested = parse_positive_int(body.get("max_tokens"))
    if requested is None:
        return provider_cap
    return min(requested, provider_cap)


def normalize_model(model: str) -> "ModelSpec":
    default_thinking = env_bool("CODESEEQ_THINKING", False)
    raw = (model or "deepseek@deepseek-v4-flash").strip()

    # Resolve aliases to a canonical slug that is present in MODEL_SPECS.
    # The two bare/wrapper non-thinking deepseek aliases keep the legacy
    # "CODESEEQ_THINKING" env override for backwards compatibility.
    canonical = raw
    if raw in MODEL_ALIASES:
        ds_model, thinking = MODEL_ALIASES[raw]
        if raw.startswith("qwibus"):
            canonical = f"qwibus@{ds_model}"
        else:
            # Preserve the "-thinking" suffix so thinking variants resolve to
            # their own spec (deepseek@deepseek-v4-flash-thinking) instead of
            # collapsing onto the non-thinking spec. Without this, every
            # -thinking alias silently ran with thinking disabled.
            if thinking and not ds_model.endswith("-thinking"):
                ds_model = f"{ds_model}-thinking"
            canonical = f"deepseek@{ds_model}"

    if canonical not in MODEL_SPECS:
        # Legacy "deepseek@<tail>" form without a full alias entry.
        if raw.startswith("deepseek@"):
            candidate = f"deepseek@{raw.split('@', 1)[1]}"
            if candidate in MODEL_SPECS:
                canonical = candidate
        # Generic "<provider>@<model>" form: accept any model name for a
        # known provider (e.g. local@my-model, grok@grok-4-1). The upstream
        # model name is the part after "@" and provider routing is resolved
        # from the slug prefix.
        elif "@" in raw:
            owner, _, upstream = raw.partition("@")
            owner = owner.lower()
            if owner in PROVIDER_API_KEY_ENV and upstream:
                spec = MODEL_SPECS.get("local@local")
                base = (
                    _env_first(*PROVIDER_BASE_URL_ENV.get(owner, ()))
                    or PROVIDER_DEFAULT_BASE_URL[owner]
                )
                chat_url = f"{base.rstrip('/')}/chat/completions"
                if owner == PROVIDER_ANTHROPIC:
                    chat_url = f"{base.rstrip('/')}/v1/messages"
                thinking = env_bool("CODESEEQ_THINKING", False)
                if spec is not None:
                    spec = ModelSpec(
                        slug=f"{owner}@{upstream}",
                        deepseek_model=upstream,
                        thinking=thinking,
                        base_url=base,
                        chat_url=chat_url,
                        temperature=spec.temperature,
                        top_p=spec.top_p,
                        top_k=spec.top_k,
                        context_window=spec.context_window,
                        max_output_tokens=spec.max_output_tokens,
                        timeout_seconds=spec.timeout_seconds,
                        system_prompt=spec.system_prompt,
                        provider=owner,
                        api_key_env=PROVIDER_API_KEY_ENV.get(owner),
                    )
                else:
                    spec = ModelSpec(
                        slug=f"{owner}@{upstream}",
                        deepseek_model=upstream,
                        thinking=thinking,
                        base_url=base,
                        chat_url=chat_url,
                        temperature=None,
                        top_p=None,
                        top_k=None,
                        context_window=131072,
                        max_output_tokens=32768,
                        timeout_seconds=600.0,
                        system_prompt=None,
                        provider=owner,
                        api_key_env=PROVIDER_API_KEY_ENV.get(owner),
                    )
                return spec
    if canonical not in MODEL_SPECS:
        raise ValueError(
            "unsupported model. supported: "
            + ", ".join(sorted(MODEL_SPECS.keys()))
        )

    src = MODEL_SPECS[canonical]
    spec = ModelSpec(
        slug=src.slug,
        deepseek_model=src.deepseek_model,
        thinking=src.thinking,
        base_url=src.base_url,
        chat_url=src.chat_url,
        temperature=src.temperature,
        top_p=src.top_p,
        top_k=src.top_k,
        context_window=src.context_window,
        max_output_tokens=src.max_output_tokens,
        timeout_seconds=src.timeout_seconds,
        system_prompt=src.system_prompt,
        provider=src.provider,
        api_key_env=src.api_key_env,
    )

    # Legacy "CODESEEQ_THINKING" global toggle only affects the explicit
    # *non-thinking* deepseek slugs; thinking variants and qwibus keep their
    # hard-coded defaults.
    if canonical in {"deepseek@deepseek-v4-flash", "deepseek@deepseek-v4-pro"}:
        spec.thinking = default_thinking
        spec.enable_thinking = default_thinking

    # CODESEEQ_PROVIDER (when set) overrides routing: the same slug can be
    # pointed at a different provider's base URL / key env (e.g. re-point a
    # local@* gateway model, or force a hosted provider). The wrapper mirrors
    # this override so the key it requires stays in sync with the bridge.
    effective_provider = resolve_provider_for_slug(spec.slug)
    if effective_provider != spec.provider:
        override_base = (
            _env_first(*PROVIDER_BASE_URL_ENV.get(effective_provider, ()))
            or PROVIDER_DEFAULT_BASE_URL[effective_provider]
        )
        if effective_provider == PROVIDER_ANTHROPIC:
            override_chat_url = f"{override_base.rstrip('/')}/v1/messages"
        else:
            override_chat_url = f"{override_base.rstrip('/')}/chat/completions"
        spec = ModelSpec(
            slug=spec.slug,
            deepseek_model=spec.deepseek_model,
            thinking=spec.thinking,
            base_url=override_base,
            chat_url=override_chat_url,
            temperature=spec.temperature,
            top_p=spec.top_p,
            top_k=spec.top_k,
            context_window=spec.context_window,
            max_output_tokens=spec.max_output_tokens,
            timeout_seconds=spec.timeout_seconds,
            system_prompt=spec.system_prompt,
            provider=effective_provider,
            api_key_env=PROVIDER_API_KEY_ENV.get(effective_provider),
        )

    return spec


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
    spec: "ModelSpec",
    messages: List[Dict[str, Any]],
) -> Dict[str, Any]:
    thinking_enabled = spec.thinking
    payload: Dict[str, Any] = {
        "model": spec.deepseek_model,
        "messages": messages,
        "stream": bool(body.get("stream", False)),
        "max_tokens": resolve_max_tokens(body, spec),
    }

    # Sampling parameters: prefer the request body, then the per-model spec.
    temperature = body.get("temperature")
    if temperature is None or not isinstance(temperature, (int, float)):
        temperature = spec.temperature
    if temperature is not None:
        payload["temperature"] = float(temperature)

    top_p = body.get("top_p")
    if top_p is None or not isinstance(top_p, (int, float)):
        top_p = spec.top_p
    if top_p is not None:
        payload["top_p"] = float(top_p)

    top_k = body.get("top_k")
    if top_k is None or not isinstance(top_k, int):
        top_k = spec.top_k
    if top_k is not None:
        payload["top_k"] = int(top_k)

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

    # Qwibus models run on lightning-mlx. Non-thinking variants MUST send
    # enable_thinking=false so the Qwen3 chat template renders an empty
    # <think> block instead of an open <think> tag; otherwise chain-of-
    # thought leaks into reasoning_content (and sometimes the content).
    if spec.slug.startswith("qwibus") and not thinking_enabled:
        payload["enable_thinking"] = False

    # FIXED - only send when enabled; omit entirely for non-thinking models
    if thinking_enabled:
        payload["thinking"] = {"type": "enabled"}
        reasoning = body.get("reasoning")
        if not isinstance(reasoning, dict):
            reasoning = {}
        effort = reasoning.get("effort") or os.environ.get("CODESEEQ_REASONING_EFFORT", "")
        if effort and effort in {"minimal", "low", "medium", "high", "xhigh", "max"}:
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


def anthropic_payload(
    body: Dict[str, Any],
    spec: "ModelSpec",
    messages: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Translate Responses-API messages to an Anthropic Messages payload.

    Chat-completions style assistant messages carrying ``tool_calls`` are
    expanded into Anthropic ``tool_use`` content blocks so tool results in
    later turns can reference them (Anthropic 400s any ``tool_result`` whose
    ``tool_use_id`` does not exist in a previous assistant message).

    Extended thinking constraints are honored: ``temperature`` / ``top_p`` are
    omitted while thinking is enabled (Anthropic rejects them), ``max_tokens``
    is raised to at least the thinking budget, and a specific tool_choice is
    downgraded to "auto" (the only choice Anthropic allows with thinking).
    """
    thinking_enabled = spec.thinking
    system_parts: List[str] = []
    msgs: List[Dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            if content:
                system_parts.append(str(content))
            continue
        if role == "tool":
            # Codex tool results arrive as tool-role messages.
            tool_call_id = m.get("tool_call_id") or m.get("name") or "tool_result"
            msgs.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_call_id,
                            "content": str(content or ""),
                        }
                    ],
                }
            )
            continue
        if role == "assistant":
            # Preserve any previous tool_use blocks: Anthropic requires the
            # assistant message that called a tool to carry the tool_use
            # content block (with its id) so the following tool_result can
            # reference it.
            blocks: List[Dict[str, Any]] = []
            text = str(content or "")
            if text:
                blocks.append({"type": "text", "text": text})
            tool_calls = m.get("tool_calls")
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function") if isinstance(tc.get("function"), dict) else None
                    name = (fn or {}).get("name") or tc.get("name") or "tool"
                    raw_args = (fn or {}).get("arguments") if fn else None
                    if isinstance(raw_args, str):
                        try:
                            parsed_args = json.loads(raw_args) if raw_args.strip() else {}
                        except Exception:
                            parsed_args = {"_raw": raw_args}
                    elif isinstance(raw_args, dict):
                        parsed_args = raw_args
                    else:
                        parsed_args = {}
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": str(tc.get("id") or f"call_{uuid.uuid4().hex[:12]}"),
                            "name": name,
                            "input": parsed_args,
                        }
                    )
            msgs.append({"role": "assistant", "content": blocks})
            continue
        msgs.append({"role": "user", "content": str(content or "")})

    payload: Dict[str, Any] = {
        "model": spec.deepseek_model,
        "max_tokens": resolve_max_tokens(body, spec),
        "messages": msgs,
    }
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)

    thinking_budget: Optional[int] = None
    if thinking_enabled:
        thinking_block: Dict[str, Any] = {"type": "enabled"}
        reasoning = body.get("reasoning")
        if not isinstance(reasoning, dict):
            reasoning = {}
        effort = reasoning.get("effort") or os.environ.get("CODESEEQ_REASONING_EFFORT", "")
        if effort in {"low", "medium", "high", "xhigh", "max"}:
            thinking_budget = {
                "low": 2048,
                "medium": 8192,
                "high": 16384,
                "xhigh": 32000,
                "max": 32000,
            }.get(effort, 16384)
            thinking_block["budget_tokens"] = thinking_budget
        else:
            # No explicit effort: pick a moderate default budget so the
            # request is valid (Anthropic requires budget_tokens when
            # thinking is enabled).
            thinking_budget = 8192
            thinking_block["budget_tokens"] = thinking_budget
        payload["thinking"] = thinking_block
        # Anthropic forbids temperature/top_p/top_k together with thinking,
        # so they are never forwarded while extended thinking is enabled.
    else:
        temperature = body.get("temperature")
        if temperature is None or not isinstance(temperature, (int, float)):
            temperature = spec.temperature
        if temperature is not None:
            payload["temperature"] = float(temperature)

        top_p = body.get("top_p")
        if top_p is None or not isinstance(top_p, (int, float)):
            top_p = spec.top_p
        if top_p is not None:
            payload["top_p"] = float(top_p)

    if thinking_enabled:
        payload["max_tokens"] = max(
            int(payload.get("max_tokens") or 0), int(thinking_budget or 0)
        )

    tools = body.get("tools")
    if isinstance(tools, list) and tools:
        anthropic_tools: List[Dict[str, Any]] = []
        for tool in tools:
            if not isinstance(tool, dict) or tool.get("type") != "function":
                continue
            fn = tool.get("function") if isinstance(tool.get("function"), dict) else None
            name = (fn or {}).get("name") or tool.get("name")
            if not name:
                continue
            t: Dict[str, Any] = {
                "name": name,
                "input_schema": {"type": "object", "properties": {}},
            }
            description = (fn or {}).get("description") or tool.get("description")
            if description:
                t["description"] = str(description)
            parameters = (fn or {}).get("parameters") or tool.get("parameters")
            if isinstance(parameters, dict):
                t["input_schema"] = parameters
            anthropic_tools.append(t)
        if anthropic_tools:
            payload["tools"] = anthropic_tools
            tool_choice = body.get("tool_choice")
            if thinking_enabled:
                # With extended thinking Anthropic only allows auto/none/any.
                if isinstance(tool_choice, str) and tool_choice in {"none", "any"}:
                    payload["tool_choice"] = {"type": tool_choice}
                else:
                    payload["tool_choice"] = {"type": "auto"}
            elif isinstance(tool_choice, str):
                payload["tool_choice"] = {"type": tool_choice}
            elif isinstance(tool_choice, dict):
                fn = (
                    tool_choice.get("function")
                    if isinstance(tool_choice.get("function"), dict)
                    else None
                )
                name = (fn or {}).get("name") or tool_choice.get("name")
                if name:
                    payload["tool_choice"] = {"type": "tool", "name": name}
    return payload


def anthropic_usage_to_responses_usage(usage: Any) -> Dict[str, int]:
    if not isinstance(usage, dict):
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or (input_tokens + output_tokens))
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def anthropic_content_to_items(
    content: Any,
    spec: "ModelSpec",
    registered_set: Set[str],
    registered_arg_names: Dict[str, Set[str]],
) -> List[Dict[str, Any]]:
    """Convert Anthropic content blocks into Responses output items.

    Handles text blocks, thinking blocks (forwarded only when spec.thinking),
    and tool_use blocks (validated like DeepSeek tool calls).
    """
    items: List[Dict[str, Any]] = []
    if not isinstance(content, list):
        return items
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = normalize_dsml_display(str(block.get("text") or ""))
            if text.strip():
                items.append(to_response_message_item(text))
        elif btype == "thinking" and spec.thinking:
            text = str(block.get("thinking") or "")
            if text.strip():
                items.append(
                    {
                        "type": "reasoning",
                        "id": f"rs_{uuid.uuid4().hex[:12]}",
                        "summary": [{"type": "summary_text", "text": text[:1000]}],
                        "content": [{"type": "reasoning_text", "text": text}],
                        "encrypted_content": None,
                    }
                )
        elif btype == "tool_use":
            name = str(block.get("name") or "tool")
            raw_args = block.get("input")
            if not isinstance(raw_args, dict):
                raw_args = {}
            tc = {
                "id": str(block.get("id") or f"call_{uuid.uuid4().hex[:12]}"),
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(raw_args, ensure_ascii=False),
                },
            }
            prepared, err = prepare_structured_tool_call(
                tc,
                registered_tools=registered_set,
                registered_arg_names=registered_arg_names,
            )
            if err:
                items.append(
                    to_response_message_item(malformed_tool_call_message([err]))
                )
            elif prepared:
                items.append(tool_call_to_response_item(prepared))
    return items


async def anthropic_event_stream(
    *,
    response_id: str,
    provider_model: str,
    spec: "ModelSpec",
    payload: Dict[str, Any],
    headers: Dict[str, str],
    chat_url: str,
    timeout_seconds: float,
    registered_set: Set[str],
    registered_arg_names: Dict[str, Set[str]],
) -> AsyncIterator[str]:
    """Stream an Anthropic Messages response and translate it to Responses SSE.

    Anthropic streams `content_block_delta` events with `text_delta`,
    `thinking_delta`, `input_json_delta` (tool args) and `signature_delta`;
    tool starts arrive as `content_block_start` events with `tool_use`.
    """
    usage: Dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    text_parts: List[str] = []
    reasoning_parts: List[str] = []
    tool_states: Dict[str, Dict[str, Any]] = {}
    message_item_id_local = f"msg_{uuid.uuid4().hex[:20]}"
    message_item_open = {"value": False}
    message_output_index: Dict[str, Optional[int]] = {"value": None}
    reasoning_item_id_local = f"rs_{uuid.uuid4().hex[:20]}"
    reasoning_item_open = {"value": False}
    reasoning_output_index: Dict[str, Optional[int]] = {"value": None}
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

    def tool_call_events() -> List[str]:
        """Emit lifecycle events for fully-buffered anthropic tool_use blocks."""
        out: List[str] = []
        for block_id, state in tool_states.items():
            if state.get("emitted"):
                continue
            fn = state.get("function") or {}
            arguments_json = fn.get("arguments") or "{}"
            out.extend(
                _function_call_lifecycle_events(
                    item_id=f"fc_{uuid.uuid4().hex[:12]}",
                    call_id=state.get("id") or block_id,
                    name=fn.get("name") or "tool",
                    arguments_json=arguments_json,
                    output_index=allocate_output_index(),
                    chunk_size=CHUNK_SIZE,
                )
            )
            state["emitted"] = True
        return out

    try:
        stream_idle_timeout = max(1.0, STREAM_IDLE_TIMEOUT_MS / 1000.0)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, read=stream_idle_timeout)
        ) as client:
            async with client.stream(
                "POST", chat_url, json=payload, headers=headers
            ) as resp:
                if resp.status_code >= 400:
                    detail = (await resp.aread()).decode("utf-8", errors="replace")[:1000]
                    log(f"anthropic stream error status={resp.status_code} body={detail}")
                    yield sse_event(
                        "response.failed",
                        {
                            "type": "response.failed",
                            "response": {
                                "id": response_id,
                                "object": "response",
                                "model": provider_model,
                                "status": "failed",
                                "error": {"code": "anthropic_error", "message": detail or "upstream error"},
                            },
                        },
                    )
                    return

                async for raw_line in resp.aiter_lines():
                    line = raw_line.strip()
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if not line:
                        continue
                    try:
                        evt = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(evt, dict):
                        continue
                    evt_type = evt.get("type")

                    if evt_type == "message_start":
                        msg = evt.get("message")
                        if isinstance(msg, dict) and isinstance(msg.get("usage"), dict):
                            usage = anthropic_usage_to_responses_usage(msg["usage"])
                    elif evt_type == "message_delta":
                        delta = evt.get("delta")
                        if isinstance(delta, dict) and isinstance(delta.get("usage"), dict):
                            usage = anthropic_usage_to_responses_usage(delta["usage"])
                        elif isinstance(evt.get("usage"), dict):
                            usage = anthropic_usage_to_responses_usage(evt["usage"])
                    elif evt_type == "content_block_start":
                        block = evt.get("content_block")
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            block_id = str(evt.get("index", block.get("id") or f"tb_{uuid.uuid4().hex[:12]}"))
                            tool_states[block_id] = {
                                "id": str(block.get("id") or f"call_{uuid.uuid4().hex[:12]}"),
                                "type": "function",
                                "function": {
                                    "name": str(block.get("name") or "tool"),
                                    "arguments": "",
                                },
                                "emitted": False,
                            }
                    elif evt_type == "content_block_delta":
                        delta = evt.get("delta")
                        if not isinstance(delta, dict):
                            continue
                        dtype = delta.get("type")
                        if dtype == "thinking_delta" and spec.thinking:
                            text = str(delta.get("thinking") or "")
                            if text:
                                reasoning_parts.append(text)
                                if not reasoning_item_open["value"]:
                                    reasoning_item_open["value"] = True
                                    reasoning_output_index["value"] = allocate_output_index()
                                    yield sse_event(
                                        "response.output_item.added",
                                        {
                                            "type": "response.output_item.added",
                                            "output_index": reasoning_output_index["value"],
                                            "item": {
                                                "id": reasoning_item_id_local,
                                                "type": "reasoning",
                                                "summary": [],
                                            },
                                        },
                                    )
                                yield sse_event(
                                    "response.reasoning_text.delta",
                                    {
                                        "type": "response.reasoning_text.delta",
                                        "delta": text,
                                        "item_id": reasoning_item_id_local,
                                        "output_index": reasoning_output_index["value"],
                                        "content_index": 0,
                                    },
                                )
                        elif dtype == "text_delta":
                            text = str(delta.get("text") or "")
                            if text:
                                normalized = normalize_dsml_display(text)
                                safe_text, completed_blocks = dsml_buf.feed(normalized)
                                for ev in text_delta_events(safe_text):
                                    yield ev
                                if completed_blocks:
                                    for ev in tool_call_events():
                                        yield ev
                        elif dtype == "input_json_delta":
                            # Find the open tool block by index; append partial JSON.
                            idx = evt.get("index")
                            block_id = str(idx) if idx is not None else None
                            if block_id is not None and block_id in tool_states:
                                tool_states[block_id]["function"]["arguments"] += str(
                                    delta.get("partial_json") or ""
                                )
                    elif evt_type == "content_block_stop":
                        idx = evt.get("index")
                        block_id = str(idx) if idx is not None else None
                        if block_id in tool_states and not tool_states[block_id]["emitted"]:
                            state = tool_states[block_id]
                            tc = {
                                "id": state["id"],
                                "type": "function",
                                "function": state["function"],
                            }
                            prepared, err = prepare_structured_tool_call(
                                tc,
                                registered_tools=registered_set,
                                registered_arg_names=registered_arg_names,
                            )
                            if err:
                                for ev in text_delta_events(
                                    malformed_tool_call_message([err])
                                ):
                                    yield ev
                            elif prepared:
                                fn = prepared.get("function") or {}
                                for ev in _function_call_lifecycle_events(
                                    item_id=f"fc_{uuid.uuid4().hex[:12]}",
                                    call_id=prepared.get("id") or state["id"],
                                    name=fn.get("name") or "tool",
                                    arguments_json=fn.get("arguments") or "{}",
                                    output_index=allocate_output_index(),
                                    chunk_size=CHUNK_SIZE,
                                ):
                                    yield ev
                            state["emitted"] = True
                    elif evt_type == "message_stop":
                        break
    except (httpx.RemoteProtocolError, httpx.ReadError, httpx.TimeoutException, asyncio.CancelledError) as exc:
        log(f"anthropic stream connection/idle error: {exc!r}")
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
        log(f"anthropic stream bridge error: {exc!r}")
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

    # Flush residual buffered text.
    residual_text, residual_blocks = dsml_buf.flush()
    if residual_text:
        for ev in text_delta_events(residual_text):
            yield ev
    if residual_blocks:
        for ev in tool_call_events():
            yield ev
    # Any tool blocks that were not stopped before stream end.
    for ev in tool_call_events():
        yield ev

    # Close reasoning item.
    full_reasoning = "".join(reasoning_parts).strip()
    if reasoning_item_open["value"]:
        yield sse_event(
            "response.reasoning_text.done",
            {
                "type": "response.reasoning_text.done",
                "item_id": reasoning_item_id_local,
                "output_index": reasoning_output_index["value"],
                "content_index": 0,
                "text": full_reasoning,
            },
        )
        yield sse_event(
            "response.output_item.done",
            {
                "type": "response.output_item.done",
                "output_index": reasoning_output_index["value"],
                "item": {
                    "id": reasoning_item_id_local,
                    "type": "reasoning",
                    "summary": [
                        {"type": "summary_text", "text": full_reasoning[:1000]}
                    ] if full_reasoning else [],
                },
            },
        )

    # Close message item.
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
    image_backend = os.environ.get("CODESEEQ_IMAGE_BACKEND", "none")
    venice_key = os.environ.get("VENICE_API_KEY", "")
    # Auto-detection: if no backend configured but VENICE_API_KEY is set, report venice
    effective_backend = image_backend
    if image_backend == "none" and venice_key:
        effective_backend = "venice"
    info: Dict[str, str] = {"status": "ok", "version": "0.4.2", "image_backend": effective_backend}
    providers = sorted({spec.provider for spec in MODEL_SPECS.values()})
    info["providers"] = ",".join(providers)
    try:
        info["provider"] = normalize_model(os.environ.get("CODESEEQ_MODEL", "")).provider
    except Exception:
        info["provider"] = os.environ.get("CODESEEQ_PROVIDER", "") or "deepseek"
    if effective_backend == "venice":
        info["venice_api_key_configured"] = str(bool(venice_key))
        info["venice_image_model"] = os.environ.get("CODESEEQ_VENICE_IMAGE_MODEL", "z-image-turbo")
    return info


@app.get("/v1/models")
async def models() -> Dict[str, Any]:
    data = []
    for slug in sorted(MODEL_SPECS.keys()):
        spec = MODEL_SPECS[slug]
        data.append(
            {
                "id": slug,
                "object": "model",
                "owned_by": spec.provider,
            }
        )
    return {"object": "list", "data": data}


@app.post("/v1/responses")
async def responses(request: Request) -> Any:
    try:
        body = await request.json()
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=400, detail=f"invalid json: {exc}")

    model_in = str(body.get("model", "deepseek@deepseek-v4-flash"))
    try:
        spec = normalize_model(model_in)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    raw_model = spec.slug
    provider_model = spec.slug
    deepseek_model = spec.deepseek_model
    provider = spec.provider
    thinking_enabled = spec.thinking
    model_chat_url = spec.chat_url
    model_timeout = spec.timeout_seconds

    # Resolve the API key for the configured provider. qwibus and local
    # gateways are keyless; every hosted provider requires its own key env.
    if spec.slug.startswith("qwibus"):
        if not env_bool("QWIBUS_NO_API_KEY", True):
            require_provider_key(provider)
    else:
        require_provider_key(provider)

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

    # Per-model system prompt. All models default to no system prompt for now,
    # but the wiring is here so a system prompt can be set per model via
    # CODESEEQ_<KEY>_SYSTEM_PROMPT without changing global config.
    if spec.system_prompt:
        messages.insert(0, {"role": "system", "content": spec.system_prompt})

    # Qwen3-family chat templates (lightning-mlx) render ONLY the first
    # system message when tools are present; every additional system message
    # is silently dropped by the template. Codex also forwards its developer
    # instructions as a system-role message (input_to_messages maps
    # role "developer" -> "system"), so a request can carry three system
    # messages (/no_think, developer instructions, tool steering). Collapse
    # them into a single system message for qwibus models so all content
    # reaches the model and the MLX log shows roles=['system','user'].
    if spec.slug.startswith("qwibus"):
        system_parts: List[str] = []
        rest: List[Dict[str, Any]] = []
        for _m in messages:
            if _m.get("role") == "system" and _m.get("content"):
                system_parts.append(str(_m["content"]))
            else:
                rest.append(_m)
        if system_parts:
            rest.insert(0, {"role": "system", "content": "\n\n".join(system_parts)})
        messages = rest

    stream = bool(body.get("stream", False))

    is_anthropic = provider == PROVIDER_ANTHROPIC
    key = provider_api_key(provider)
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if key:
        if is_anthropic:
            headers["x-api-key"] = key
            headers["anthropic-version"] = "2023-06-01"
        else:
            headers["Authorization"] = f"Bearer {key}"

    # OpenAI-compatible providers reuse the chat-completions translation;
    # anthropic uses the native Messages API payload.
    if is_anthropic:
        payload = anthropic_payload(body, spec, messages)
    else:
        payload = deepseek_payload(body, spec, messages)

    log(
        f"request model={raw_model} mapped={provider_model} thinking={thinking_enabled} "
        f"stream={stream} messages={len(messages)} tools_registered={len(registered_set)} "
        f"chat_url={model_chat_url} timeout={model_timeout}"
    )
    if registered_tool_names:
        log(f"registered tool names: {registered_tool_names}")

    # ---------------- non-streaming path --------------------------------
    if not stream:
        if is_anthropic:
            payload.pop("stream", None)
            async with httpx.AsyncClient(timeout=model_timeout) as client:
                resp = await client.post(model_chat_url, json=payload, headers=headers)
                if resp.status_code >= 400:
                    detail = resp.text[:1000]
                    log(f"anthropic error status={resp.status_code} body={detail}")
                    return JSONResponse(status_code=resp.status_code, content={"error": detail})
                ds = resp.json()

            usage = anthropic_usage_to_responses_usage(
                ds.get("usage") if isinstance(ds, dict) else None
            )
            output_items: List[Dict[str, Any]] = anthropic_content_to_items(
                ds.get("content") if isinstance(ds, dict) else None,
                spec,
                registered_set,
                registered_arg_names,
            )
            text = "".join(
                str(it.get("text") or "")
                for it in output_items
                if isinstance(it, dict) and it.get("type") == "message"
            )
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

        payload["stream"] = False
        async with httpx.AsyncClient(timeout=model_timeout) as client:
            resp = await client.post(model_chat_url, json=payload, headers=headers)
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
        if (
            spec.thinking
            and isinstance(reasoning, str)
            and reasoning.strip()
        ):
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

    if is_anthropic:
        return StreamingResponse(
            anthropic_event_stream(
                response_id=response_id,
                provider_model=provider_model,
                spec=spec,
                payload=payload,
                headers=headers,
                chat_url=model_chat_url,
                timeout_seconds=model_timeout,
                registered_set=registered_set,
                registered_arg_names=registered_arg_names,
            ),
            media_type="text/event-stream",
        )

    async def event_stream() -> AsyncIterator[str]:
        usage: Dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        text_parts: List[str] = []
        reasoning_parts: List[str] = []
        tool_states: Dict[int, Dict[str, Any]] = {}
        message_item_id_local = f"msg_{uuid.uuid4().hex[:20]}"
        message_item_open = {"value": False}
        message_output_index: Dict[str, Optional[int]] = {"value": None}
        reasoning_item_id_local = f"rs_{uuid.uuid4().hex[:20]}"
        reasoning_item_open = {"value": False}
        reasoning_output_index: Dict[str, Optional[int]] = {"value": None}
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
            stream_idle_timeout = max(1.0, STREAM_IDLE_TIMEOUT_MS / 1000.0)
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(model_timeout, read=stream_idle_timeout)
            ) as client:
                async with client.stream(
                    "POST", model_chat_url, json=payload, headers=headers
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

                        # 1. Reasoning - only forward reasoning deltas when the
                        # model spec has thinking enabled. DeepSeek can still
                        # emit reasoning_content on non-thinking models; leaking
                        # it into the Responses stream would open reasoning
                        # items Codex never asked for and waste output tokens.
                        reasoning_delta = (
                            delta.get("reasoning_content") if spec.thinking else None
                        )
                        if isinstance(reasoning_delta, str) and reasoning_delta:
                            reasoning_parts.append(reasoning_delta)
                            # Open the reasoning output item on first delta (Codex requires
                            # response.output_item.added before any reasoning delta)
                            if not reasoning_item_open["value"]:
                                reasoning_item_open["value"] = True
                                reasoning_output_index["value"] = allocate_output_index()
                                yield sse_event(
                                    "response.output_item.added",
                                    {
                                        "type": "response.output_item.added",
                                        "output_index": reasoning_output_index["value"],
                                        "item": {
                                            "id": reasoning_item_id_local,
                                            "type": "reasoning",
                                            "summary": [],
                                        },
                                    },
                                )
                            yield sse_event(
                                "response.reasoning_text.delta",
                                {
                                    "type": "response.reasoning_text.delta",
                                    "delta": reasoning_delta,
                                    "item_id": reasoning_item_id_local,
                                    "output_index": reasoning_output_index["value"],
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
        except (httpx.RemoteProtocolError, httpx.ReadError, httpx.TimeoutException, asyncio.CancelledError) as exc:
            log(f"deepseek stream connection/idle error: {exc!r}")
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
        if reasoning_item_open["value"]:
            # Close reasoning text content
            yield sse_event(
                "response.reasoning_text.done",
                {
                    "type": "response.reasoning_text.done",
                    "item_id": reasoning_item_id_local,
                    "output_index": reasoning_output_index["value"],
                    "content_index": 0,
                    "text": full_reasoning,
                },
            )
            # Close reasoning output item
            yield sse_event(
                "response.output_item.done",
                {
                    "type": "response.output_item.done",
                    "output_index": reasoning_output_index["value"],
                    "item": {
                        "id": reasoning_item_id_local,
                        "type": "reasoning",
                        "summary": [
                            {"type": "summary_text", "text": full_reasoning[:1000]}
                        ] if full_reasoning else [],
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

# ---------------------------------------------------------------------------
# Image generation backend (Venice.ai)
# ---------------------------------------------------------------------------
# Image backend: auto-detect Venice if VENICE_API_KEY is set
_configured_backend = os.environ.get("CODESEEQ_IMAGE_BACKEND", "none")
_venice_key = os.environ.get("VENICE_API_KEY", "")
IMAGE_BACKEND = "venice" if (_configured_backend == "none" and _venice_key) else _configured_backend
VENICE_IMAGE_URL = os.environ.get(
    "CODESEEQ_VENICE_IMAGE_URL", "https://api.venice.ai/api/v1/image/generate"
)


def _translate_openai_to_venice(
    body: Dict[str, Any],
) -> Dict[str, Any]:
    """Convert OpenAI /images/generations request to Venice /image/generate format."""
    venice: Dict[str, Any] = {
        "model": body.get("model") or os.environ.get("CODESEEQ_VENICE_IMAGE_MODEL", "z-image-turbo"),
        "prompt": body.get("prompt", ""),
    }

    # Map OpenAI size to Venice aspect_ratio + resolution
    size = body.get("size", "1024x1024")
    size_map = {
        "256x256": ("1:1", "1K"),
        "512x512": ("1:1", "1K"),
        "1024x1024": ("1:1", "1K"),
        "1792x1024": ("16:9", "2K"),
        "1024x1792": ("9:16", "2K"),
    }
    default_aspect, default_resolution = size_map.get(size, ("1:1", "1K"))

    venice["aspect_ratio"] = os.environ.get(
        "CODESEEQ_VENICE_IMAGE_ASPECT_RATIO", default_aspect
    )
    venice["resolution"] = os.environ.get(
        "CODESEEQ_VENICE_IMAGE_RESOLUTION", default_resolution
    )

    # Format
    response_format = body.get("response_format")
    if response_format == "b64_json":
        venice["format"] = os.environ.get("CODESEEQ_VENICE_IMAGE_FORMAT", "webp")
    elif response_format == "url":
        venice["format"] = os.environ.get("CODESEEQ_VENICE_IMAGE_FORMAT", "webp")
    else:
        venice["format"] = os.environ.get("CODESEEQ_VENICE_IMAGE_FORMAT", "webp")

    # Variants (n)
    n = body.get("n", 1)
    try:
        n_int = int(n)
    except (TypeError, ValueError):
        n_int = 1
    venice["variants"] = min(max(n_int, 1), 4)

    # Inject other Venice params from env
    for env_key, venice_key, coerce_fn in [
        ("CODESEEQ_VENICE_IMAGE_SAFE_MODE", "safe_mode", lambda v: v.strip().lower() in ("1", "true", "yes", "on")),
        ("CODESEEQ_VENICE_IMAGE_HIDE_WATERMARK", "hide_watermark", lambda v: v.strip().lower() in ("1", "true", "yes", "on")),
        ("CODESEEQ_VENICE_IMAGE_NEGATIVE_PROMPT", "negative_prompt", str),
        ("CODESEEQ_VENICE_IMAGE_SEED", "seed", lambda v: int(v) if v.strip() else 0),
        ("CODESEEQ_VENICE_IMAGE_RETURN_BINARY", "return_binary", lambda v: v.strip().lower() in ("1", "true", "yes", "on")),
        ("CODESEEQ_VENICE_IMAGE_CFG_SCALE", "cfg_scale", lambda v: float(v)),
        ("CODESEEQ_VENICE_IMAGE_STEPS", "steps", lambda v: int(v)),
        ("CODESEEQ_VENICE_IMAGE_QUALITY", "quality", str),
    ]:
        val = os.environ.get(env_key, "")
        if val:
            try:
                venice[venice_key] = coerce_fn(val)
            except (ValueError, TypeError):
                pass  # skip invalid values

    return venice


def _translate_venice_to_openai(
    venice_resp: Dict[str, Any],
) -> Dict[str, Any]:
    """Convert Venice /image/generate response to OpenAI /images/generations format."""
    import time as _time

    data = []
    for img_b64 in venice_resp.get("images", []):
        data.append({"b64_json": img_b64})

    return {
        "created": int(_time.time()),
        "data": data,
    }


@app.post("/v1/images/generations")
async def image_generations(request: Request):
    """OpenAI-compatible image generation endpoint proxied through Venice.ai."""
    if IMAGE_BACKEND != "venice":
        raise HTTPException(
            status_code=501,
            detail="Image backend not configured. Set CODESEEQ_IMAGE_BACKEND=venice or provide VENICE_API_KEY for auto-detection.",
        )

    venice_api_key = os.environ.get("VENICE_API_KEY", "")
    if not venice_api_key:
        raise HTTPException(
            status_code=500,
            detail="VENICE_API_KEY environment variable is not set.",
        )

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    venice_payload = _translate_openai_to_venice(body)

    log(f"venice image gen: model={venice_payload.get('model')} "
        f"aspect={venice_payload.get('aspect_ratio')} "
        f"resolution={venice_payload.get('resolution')}")

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                VENICE_IMAGE_URL,
                json=venice_payload,
                headers={
                    "Authorization": f"Bearer {venice_api_key}",
                    "Content-Type": "application/json",
                },
            )
    except httpx.TimeoutException:
        raise HTTPException(
            status_code=504,
            detail="Venice API request timed out.",
        )
    except httpx.ConnectError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Cannot connect to Venice API: {exc}",
        )

    if resp.status_code >= 400:
        try:
            err_body = resp.json()
            err_msg = err_body.get("error", resp.text[:500])
        except Exception:
            err_msg = resp.text[:500]
        log(f"venice image error: status={resp.status_code} detail={err_msg}")
        raise HTTPException(status_code=resp.status_code, detail=err_msg)

    try:
        venice_data = resp.json()
    except Exception:
        raise HTTPException(status_code=502, detail="Invalid JSON response from Venice API.")

    result = _translate_venice_to_openai(venice_data)
    return result


def _parse_port_env() -> int:
    raw = os.environ.get(
        "CODESEEQ_BRIDGE_PORT",
        os.environ.get("CODESEEQ_OPENRESPONSES_PORT", "8080"),
    )
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise SystemExit(f"invalid bridge port: {raw!r}")


def _write_port_file(path: Optional[str], port: int) -> None:
    if not path:
        return
    try:
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(str(port))
        os.replace(tmp, path)
    except OSError as exc:  # pragma: no cover - best effort discovery file
        print(f"[codeseeq-bridge] warning: could not write port file {path}: {exc}", file=sys.stderr, flush=True)


def _bind_socket(host: str, port: int) -> socket.socket:
    """Bind a listening TCP socket, raising OSError on failure."""
    family = socket.AF_INET
    if host and ":" in host:
        family = socket.AF_INET6
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(2048)
    return sock


def _install_parent_watchdog() -> None:
    """Exit when the launching process dies so the bridge is never orphaned.

    codeseeq launches the bridge as a background child and relies on a bash
    EXIT trap to stop it. When the parent is killed hard (SIGKILL or a
    process-group teardown, e.g. an agent-call timeout in a pipeline), that
    trap cannot run: the bridge would be reparented to PID 1 and hold its
    port forever, eventually exhausting the auto-select range with
    "no free bridge port found in range ...".  This watchdog makes the
    bridge close its port and exit as soon as the parent goes away, so a
    killed parent can never leak a bridge.
    """
    parent = os.getppid()
    if parent <= 1:
        return  # already orphaned (or PID 1 in a container): nothing to watch

    def _watch_parent() -> None:
        try:
            while True:
                # SIGHUP(1)/SIGTERM(15) => parent gone. Python re-raises
                # interrupted system calls, so keep polling if it was just
                # a transient signal. Reparenting to PID 1 (or 0) is
                # definitive and cannot recover.
                if os.getppid() != parent or parent == 1:
                    break
                time.sleep(0.5)
        except BaseException:  # pragma: no cover - best effort
            pass
        finally:
            log("parent process exited; bridge shutting down")
            try:
                os.kill(os.getpid(), signal.SIGTERM)
            except OSError:  # pragma: no cover - already gone
                pass

    threading.Thread(target=_watch_parent, name="parent-watchdog", daemon=True).start()


def main():
    import uvicorn
    from uvicorn import Config, Server

    _install_parent_watchdog()

    host = os.environ.get(
        "CODESEEQ_BRIDGE_HOST",
        os.environ.get("CODESEEQ_OPENRESPONSES_HOST", "127.0.0.1"),
    )
    port_file = os.environ.get("CODESEEQ_BRIDGE_PORT_FILE") or None

    if host != "127.0.0.1" and host != "localhost":
        log(f"warning: bridge binding to non-localhost address: {host}")

    # Explicitly requested fixed port -> bind exactly that port (no fallback).
    fixed = "CODESEEQ_BRIDGE_PORT" in os.environ
    if fixed:
        port = _parse_port_env()
        try:
            sock = _bind_socket(host, port)
        except OSError as exc:
            raise SystemExit(f"failed to bind fixed port {host}:{port}: {exc}")
        _write_port_file(port_file, port)
        log(f"bridge listening on {host}:{port}")
        config = Config(app, host=host, port=port)
        server = Server(config)
        server.run(sockets=[sock])
        return

    # Auto-select: do a true bind (not a connect probe) and increment on
    # conflict, eliminating the TOCTOU gap entirely. If a port already holds
    # a bound-but-not-listening socket, bind() still fails and we move on.
    start = _parse_port_env()
    limit_raw = os.environ.get("CODESEEQ_OPENRESPONSES_PORT_SCAN_LIMIT", "100")
    try:
        limit = int(limit_raw)
    except (TypeError, ValueError):
        limit = 100
    if limit < 1:
        limit = 1

    sock = None
    chosen = None
    for candidate in range(start, start + limit):
        try:
            sock = _bind_socket(host, candidate)
            chosen = candidate
            break
        except OSError:
            sock = None
            continue

    if sock is None or chosen is None:
        raise SystemExit(f"no free bridge port found in range {start}-{start + limit - 1}")

    _write_port_file(port_file, chosen)
    log(f"bridge listening on {host}:{chosen}")
    config = Config(app, host=host, port=chosen)
    server = Server(config)
    server.run(sockets=[sock])


if __name__ == "__main__":
    main()
