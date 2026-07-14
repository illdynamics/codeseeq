# Venice Image Backend — Implementation Plan for CodeSeeq v0.3.6

> **Status:** Ready for agent implementation  
> **Version:** v0.3.6  
> **Plan authored:** 2026-07-14  

---

## Table of Contents

1. [Overview](#overview)
2. [Venice API Reference (Researched)](#venice-api-reference-researched)
3. [Architecture Decision](#architecture-decision)
4. [Detailed File-by-File Implementation Plan](#detailed-file-by-file-implementation-plan)
5. [Version Bump Checklist](#version-bump-checklist)
6. [Implementation Order (Dependency-Aware)](#implementation-order-dependency-aware)
7. [Testing Plan](#testing-plan)

---

## Overview

Add an **image backend configuration option** to CodeSeeq. By default, CodeSeeq has **no** image backend configured (`image_backend = "none"`). Users can now configure `image_backend = "venice"` which uses the Venice.ai API (https://api.venice.ai/api/v1) to generate images via the `/image/generate` endpoint, authenticated with `VENICE_API_KEY`.

### Key Features

| Feature | Description |
|---|---|
| `CODESEEQ_IMAGE_BACKEND` | New env var: `none` (default), `venice` (use Venice API) |
| `CODESEEQ_VENICE_IMAGE_MODEL` | Model selection: `auto` (default), or specific model name e.g. `z-image-turbo`, `gpt-image-2`, `nano-banana-pro` |
| `VENICE_API_KEY` | Required when backend is `venice` |
| Aspect ratio control | `CODESEEQ_VENICE_IMAGE_ASPECT_RATIO` env var (default: `1:1`) |
| Resolution control | `CODESEEQ_VENICE_IMAGE_RESOLUTION` env var (default: `1K`, options: `1K`, `2K`, `4K`) |
| Format control | `CODESEEQ_VENICE_IMAGE_FORMAT` env var (default: `webp`, options: `jpeg`, `png`, `webp`) |
| Variants control | `CODESEEQ_VENICE_IMAGE_VARIANTS` (1–4, default: 1) |
| Safe mode toggle | `CODESEEQ_VENICE_IMAGE_SAFE_MODE` (default: `true`) |
| Watermark toggle | `CODESEEQ_VENICE_IMAGE_HIDE_WATERMARK` (default: `false`) |
| Negative prompt | `CODESEEQ_VENICE_IMAGE_NEGATIVE_PROMPT` (default: empty) |
| Seed control | `CODESEEQ_VENICE_IMAGE_SEED` (default: 0 = random) |
| Return binary | `CODESEEQ_VENICE_IMAGE_RETURN_BINARY` (default: `false` — returns base64 JSON) |

### Defaults

| Setting | Default |
|---|---|
| `CODESEEQ_IMAGE_BACKEND` | `none` |
| `CODESEEQ_VENICE_IMAGE_MODEL` | `auto` |
| `CODESEEQ_VENICE_IMAGE_ASPECT_RATIO` | `1:1` |
| `CODESEEQ_VENICE_IMAGE_RESOLUTION` | `1K` |
| `CODESEEQ_VENICE_IMAGE_FORMAT` | `webp` |
| `CODESEEQ_VENICE_IMAGE_VARIANTS` | `1` |
| `CODESEEQ_VENICE_IMAGE_SAFE_MODE` | `true` |
| `CODESEEQ_VENICE_IMAGE_HIDE_WATERMARK` | `false` |
| `CODESEEQ_VENICE_IMAGE_RETURN_BINARY` | `false` |

---

## Venice API Reference (Researched)

### Base URL
```
https://api.venice.ai/api/v1
```

### Image Generation Endpoint (Primary — Full Features)

**`POST /image/generate`** — Venice's native image generation endpoint with full parameter support.

**Authentication:** `Authorization: Bearer <VENICE_API_KEY>`

**Request Body (JSON):**

```json
{
  "model": "z-image-turbo",
  "prompt": "A beautiful sunset over a mountain range",
  "aspect_ratio": "16:9",
  "resolution": "4K",
  "format": "png",
  "variants": 1,
  "safe_mode": false,
  "hide_watermark": false,
  "return_binary": false,
  "negative_prompt": "Clouds, Rain",
  "seed": 123456789,
  "cfg_scale": 7.5,
  "steps": 30,
  "lora_strength": 50,
  "style_preset": "3D Model",
  "embed_exif_metadata": false,
  "disable_prompt_optimization_thinking": false,
  "enable_web_search": false
}
```

**Required fields:** `model`, `prompt`

**Response (200, `return_binary: false`):**
```json
{
  "id": "generate-image-1234567890",
  "images": ["base64_encoded_image_data..."],
  "request": { ... },
  "timing": {
    "inferenceDuration": 1234,
    "inferencePreprocessingTime": 567,
    "inferenceQueueTime": 89,
    "total": 1890
  }
}
```

**Response (200, `return_binary: true`):** Raw binary image bytes with appropriate Content-Type header.

### Image Generation Endpoint (OpenAI-Compatible — Simpler)

**`POST /images/generations`** — OpenAI-compatible endpoint with fewer features.

**Sizing options (model-specific):**

| Model Type | Sizing Parameters | Example Models |
|---|---|---|
| Pixel-based | `width`, `height` (max 1280) | `venice-sd35`, `qwen-image` |
| Aspect-ratio | `aspect_ratio` (e.g., `"1:1"`, `"16:9"`) | `qwen-image-2` |
| Resolution-tier | `aspect_ratio` + `resolution` (`1K`, `2K`, `4K`) | `gpt-image-2`, `nano-banana-pro`, `z-image-turbo` |

**Quality tiers (gpt-image-2 only):**
- `low`, `medium`, `high` (default: `high`)
- Billed by resolution × quality tier combination

### Supported Image Models (from Venice docs/models/image)

Models are discoverable via `GET /models` or `GET /models/traits` with trait `image`. Notable image models include:
- `z-image-turbo` — Resolution-tier model (aspect_ratio + resolution)
- `gpt-image-2` — Resolution-tier, quality tiers supported
- `nano-banana-2` — Resolution-tier
- `nano-banana-pro` — Resolution-tier
- `grok-imagine-image` — General image generation
- `qwen-image-2` — Aspect-ratio model
- `venice-sd35` — Pixel-based (width/height)
- `qwen-image` — Pixel-based (width/height)

**Format support:** `jpeg`, `png`, `webp` (default: `webp`)  
**Variants:** 1–4 images per request (only when `return_binary: false`)  
**Safe mode:** `true` by default — blurs adult content  
**Prompt character limit:** 7500 (model-specific, check `promptCharacterLimit` in model metadata)  
**Negative prompt character limit:** 7500  

### Auto-Model Resolution Logic

When `CODESEEQ_VENICE_IMAGE_MODEL=auto`, CodeSeeq will:
1. Fetch `GET /models?trait=image` and select the first available/cheapest model
2. Or use a hardcoded fallback list: `z-image-turbo` → `nano-banana-pro` → `gpt-image-2`
3. Configure sizing parameters based on the selected model's capabilities

---

## Architecture Decision

### Where the Venice Integration Lives

The Venice image backend is implemented as a **new bridge endpoint** and a **new standalone script**, with configuration integrated into the existing CodeSeeq launcher and entrypoint:

1. **`bin/codeseeq-venice-image.py`** — New standalone Python script that:
   - Reads environment variables for configuration
   - Calls the Venice `/image/generate` endpoint
   - Decodes and saves the generated image(s) to a specified output path
   - Reports timing and metadata

2. **Bridge extension in `bin/codeseeq-bridge.py`** — A new `/v1/images/generations` endpoint that:
   - Proxies image generation requests to Venice when `image_backend=venice`
   - This allows the existing `image_gen` tool in Codex's code path to potentially be routed through it

3. **Launcher/Entrypoint changes** — Environment variable plumbing in `./codeseeq` and `bin/codeseeq-entrypoint`

4. **Documentation** — Updated README, RELEASE-NOTES, docs/ARCHITECTURE, .env.example

### Why a Standalone Script + Bridge Route

- The standalone script (`codeseeq-venice-image.py`) gives users a direct CLI for image generation independent of Codex
- The bridge route gives the possibility of wiring the built-in `image_gen` tool through it transparently (future capability)
- Both paths share the same Venice API calling logic

### Key Design Principles

1. **Default = no backend.** Existing users are unaffected.
2. **Explicit opt-in.** Set `CODESEEQ_IMAGE_BACKEND=venice` and `VENICE_API_KEY`.
3. **Graceful degradation.** If `VENICE_API_KEY` is missing and backend is `venice`, print a clear error.
4. **Respects existing architecture.** No changes to container build, no new Python deps needed (httpx already available).
5. **Privacy-first.** No telemetry, no data retention concerns — Venice already operates zero-retention.

---

## Detailed File-by-File Implementation Plan

### 1. NEW: `bin/codeseeq-venice-image.py`

**Purpose:** Standalone CLI script for Venice image generation. Can be used directly: `python3 bin/codeseeq-venice-image.py --prompt "a cat" --out cat.png`

**Implementation:**

```python
#!/usr/bin/env python3
"""
codeseeq-venice-image: Generate images via Venice.ai API.

Usage:
  python3 bin/codeseeq-venice-image.py --prompt "description" [--out output.png]
  python3 bin/codeseeq-venice-image.py --prompt "description" --model gpt-image-2 --aspect-ratio 16:9

Environment:
  VENICE_API_KEY                     Required
  CODESEEQ_VENICE_IMAGE_MODEL        Model name or "auto" (default: auto)
  CODESEEQ_VENICE_IMAGE_ASPECT_RATIO Default aspect ratio (default: 1:1)
  CODESEEQ_VENICE_IMAGE_RESOLUTION   Resolution: 1K, 2K, 4K (default: 1K)
  CODESEEQ_VENICE_IMAGE_FORMAT       jpeg, png, webp (default: webp)
  CODESEEQ_VENICE_IMAGE_VARIANTS     1-4 (default: 1)
  CODESEEQ_VENICE_IMAGE_SAFE_MODE    true/false (default: true)
  CODESEEQ_VENICE_IMAGE_HIDE_WATERMARK true/false (default: false)
  CODESEEQ_VENICE_IMAGE_SEED         integer (default: 0 = random)
  CODESEEQ_VENICE_IMAGE_RETURN_BINARY true/false (default: false)
  CODESEEQ_VENICE_IMAGE_NEGATIVE_PROMPT Negative prompt text
  CODESEEQ_VENICE_IMAGE_CFG_SCALE    CFG scale (default: 7.5)
  CODESEEQ_VENICE_IMAGE_STEPS        Steps (default: 30)
"""
```

**Key functions:**
- `parse_args()` — Parse CLI args (--prompt, --out, --model, --aspect-ratio, --resolution, etc.)
- `build_payload(prompt, config)` — Build JSON payload from env vars + CLI overrides
- `call_venice_api(payload, api_key)` — POST to `https://api.venice.ai/api/v1/image/generate`
- `save_images(response, output_dir)` — Decode base64 and save to files
- `main()` — Orchestrator

**Output paths:**
- Default: `./codeseeq-images/generated_YYYYMMDD-HHMMSS_N.png`
- Custom: `--out path/to/file.png` (with variant suffix if variants > 1)
- Directory: `--out-dir path/to/dir/`

**Error handling:**
- Missing `VENICE_API_KEY` → print error, exit 1
- 401 → "Invalid VENICE_API_KEY"
- 402 → "Insufficient Venice balance"
- 429 → "Rate limited, retry after N seconds"
- 503 → "Model at capacity, try again later"
- Timeout → retry once, then fail

### 2. MODIFY: `bin/codeseeq-bridge.py`

Add a new bridge route for image generation proxy:

**New endpoint:** `POST /v1/images/generations`

This endpoint is only active when `CODESEEQ_IMAGE_BACKEND=venice`. It:
1. Receives an OpenAI-compatible image generation request
2. Translates to Venice `/image/generate` format
3. Calls the Venice API
4. Translates Venice response back to OpenAI-compatible format
5. Returns the result

**Add to FastAPI app (after existing routes):**

```python
IMAGE_BACKEND = os.environ.get("CODESEEQ_IMAGE_BACKEND", "none")
VENICE_API_KEY = os.environ.get("VENICE_API_KEY", "")
VENICE_IMAGE_URL = os.environ.get(
    "CODESEEQ_VENICE_IMAGE_URL", "https://api.venice.ai/api/v1/image/generate"
)

@app.post("/v1/images/generations")
async def image_generations(request: Request):
    if IMAGE_BACKEND != "venice":
        raise HTTPException(status_code=501, detail="Image backend not configured. Set CODESEEQ_IMAGE_BACKEND=venice.")
    if not VENICE_API_KEY:
        raise HTTPException(status_code=500, detail="VENICE_API_KEY not set.")
    
    body = await request.json()
    # Translate OpenAI format to Venice format
    venice_payload = translate_to_venice(body)
    # Call Venice
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            VENICE_IMAGE_URL,
            json=venice_payload,
            headers={"Authorization": f"Bearer {VENICE_API_KEY}"}
        )
    if resp.status_code >= 400:
        detail = resp.text[:500]
        raise HTTPException(status_code=resp.status_code, detail=detail)
    
    # Translate Venice response to OpenAI format
    result = translate_from_venice(resp.json(), body)
    return result

def translate_to_venice(openai_body: dict) -> dict:
    """Convert OpenAI /images/generations format to Venice /image/generate format."""
    venice = {
        "model": openai_body.get("model") or os.environ.get("CODESEEQ_VENICE_IMAGE_MODEL", "auto"),
        "prompt": openai_body.get("prompt", ""),
    }
    # Map OpenAI size to Venice aspect_ratio + resolution
    size = openai_body.get("size", "1024x1024")
    aspect, resolution = map_size_to_venice(size)
    venice["aspect_ratio"] = os.environ.get("CODESEEQ_VENICE_IMAGE_ASPECT_RATIO", aspect)
    venice["resolution"] = os.environ.get("CODESEEQ_VENICE_IMAGE_RESOLUTION", resolution)
    venice["format"] = openai_body.get("response_format", 
        os.environ.get("CODESEEQ_VENICE_IMAGE_FORMAT", "webp"))
    venice["variants"] = openai_body.get("n", 
        int(os.environ.get("CODESEEQ_VENICE_IMAGE_VARIANTS", "1")))
    
    for env_key, venice_key in [
        ("CODESEEQ_VENICE_IMAGE_SAFE_MODE", "safe_mode"),
        ("CODESEEQ_VENICE_IMAGE_HIDE_WATERMARK", "hide_watermark"),
        ("CODESEEQ_VENICE_IMAGE_NEGATIVE_PROMPT", "negative_prompt"),
        ("CODESEEQ_VENICE_IMAGE_SEED", "seed"),
        ("CODESEEQ_VENICE_IMAGE_RETURN_BINARY", "return_binary"),
        ("CODESEEQ_VENICE_IMAGE_CFG_SCALE", "cfg_scale"),
        ("CODESEEQ_VENICE_IMAGE_STEPS", "steps"),
    ]:
        val = os.environ.get(env_key)
        if val is not None:
            venice[venice_key] = type_coerce(val, venice_key)
    
    return venice

def map_size_to_venice(size: str) -> tuple:
    """Map OpenAI size string to Venice (aspect_ratio, resolution)."""
    size_map = {
        "256x256": ("1:1", "1K"),
        "512x512": ("1:1", "1K"),
        "1024x1024": ("1:1", "1K"),
        "1792x1024": ("16:9", "2K"),
        "1024x1792": ("9:16", "2K"),
    }
    return size_map.get(size, ("1:1", "1K"))

def translate_from_venice(venice_resp: dict, original_request: dict) -> dict:
    """Convert Venice response to OpenAI /images/generations format."""
    import time
    data = []
    for img_b64 in venice_resp.get("images", []):
        data.append({"b64_json": img_b64})
    return {
        "created": int(time.time()),
        "data": data,
    }
```

**Add health info to existing `/health` endpoint:**

```python
@app.get("/health")
async def health():
    info = {"status": "ok", "version": "0.3.6"}
    info["image_backend"] = IMAGE_BACKEND
    if IMAGE_BACKEND == "venice":
        info["venice_api_key_configured"] = bool(VENICE_API_KEY)
        info["venice_image_model"] = os.environ.get("CODESEEQ_VENICE_IMAGE_MODEL", "auto")
    return info
```

### 3. MODIFY: `./codeseeq` (Host Launcher)

Add image backend configuration variables and CLI flags.

**New env var defaults (in the defaults block, alongside existing ones):**

```bash
: "${CODESEEQ_IMAGE_BACKEND:=none}"
: "${CODESEEQ_VENICE_IMAGE_MODEL:=auto}"
: "${CODESEEQ_VENICE_IMAGE_ASPECT_RATIO:=1:1}"
: "${CODESEEQ_VENICE_IMAGE_RESOLUTION:=1K}"
: "${CODESEEQ_VENICE_IMAGE_FORMAT:=webp}"
: "${CODESEEQ_VENICE_IMAGE_VARIANTS:=1}"
: "${CODESEEQ_VENICE_IMAGE_SAFE_MODE:=true}"
: "${CODESEEQ_VENICE_IMAGE_HIDE_WATERMARK:=false}"
: "${CODESEEQ_VENICE_IMAGE_SEED:=0}"
: "${CODESEEQ_VENICE_IMAGE_RETURN_BINARY:=false}"
```

**Export them alongside other exports:**

```bash
export CODESEEQ_IMAGE_BACKEND CODESEEQ_VENICE_IMAGE_MODEL
export CODESEEQ_VENICE_IMAGE_ASPECT_RATIO CODESEEQ_VENICE_IMAGE_RESOLUTION
export CODESEEQ_VENICE_IMAGE_FORMAT CODESEEQ_VENICE_IMAGE_VARIANTS
export CODESEEQ_VENICE_IMAGE_SAFE_MODE CODESEEQ_VENICE_IMAGE_HIDE_WATERMARK
export CODESEEQ_VENICE_IMAGE_SEED CODESEEQ_VENICE_IMAGE_RETURN_BINARY
```

**Add CLI flag parsing (in the flag pre-parsing loop):**

```bash
--image-backend)
  [[ $# -ge 2 ]] || die "--image-backend requires a value (none or venice)"
  CODESEEQ_IMAGE_BACKEND="$2"
  shift 2
  ;;
--image-backend=*)
  CODESEEQ_IMAGE_BACKEND="${1#--image-backend=}"
  shift
  ;;
--venice-image-model)
  [[ $# -ge 2 ]] || die "--venice-image-model requires a value"
  CODESEEQ_VENICE_IMAGE_MODEL="$2"
  shift 2
  ;;
--venice-image-model=*)
  CODESEEQ_VENICE_IMAGE_MODEL="${1#--venice-image-model=}"
  shift
  ;;
```

**Add to `print_help()`:**

```
Image backend:
  --image-backend BACKEND       Set image backend: none (default) or venice
  --venice-image-model MODEL    Venice image model (default: auto,
                                e.g. z-image-turbo, gpt-image-2)
  CODESEEQ_IMAGE_BACKEND        Set image backend via environment
  VENICE_API_KEY                Required when backend=venice
```

**Add diagnostic output to `doctor`:**

```bash
venice_key_state="$(key_state "${VENICE_API_KEY:-}")"
printf 'Image backend: %s\n' "$CODESEEQ_IMAGE_BACKEND"
printf 'Venice image model: %s\n' "$CODESEEQ_VENICE_IMAGE_MODEL"
printf 'Venice API key: %s\n' "$venice_key_state"
```

**New `ping-image` subcommand:**

```bash
ping_image)  # Matched from "ping-image" in the case statement
  shift
  [[ -n "${VENICE_API_KEY:-}" ]] || die "VENICE_API_KEY is required for ping-image"
  if [[ "$CODESEEQ_IMAGE_BACKEND" != "venice" ]]; then
    die "ping-image requires CODESEEQ_IMAGE_BACKEND=venice"
  fi
  # Start bridge, then call the image endpoint
  start_bridge || die "failed to start bridge"
  local payload
  payload=$(cat <<EOF
{"model":"${CODESEEQ_VENICE_IMAGE_MODEL}","prompt":"A single red pixel on white background","aspect_ratio":"1:1","resolution":"1K","format":"webp","variants":1}
EOF
)
  local out
  out="$(curl --silent --show-error --fail \
    -H 'Content-Type: application/json' \
    -H "Authorization: Bearer ${VENICE_API_KEY}" \
    -d "$payload" \
    "${CODESEEQ_OPENRESPONSES_URL%/v1}/v1/images/generations")"
  if echo "$out" | grep -q '"b64_json"'; then
    echo "PONG-IMAGE: success"
  else
    echo "$out" >&2
    die "image generation ping failed"
  fi
  ;;
```

### 4. MODIFY: `bin/codeseeq-entrypoint` (Container Entrypoint)

Add the same env var defaults and exports. Also wire the new env vars into the container's generated `config.toml`.

**Add to env var defaults block:**

```bash
: "${CODESEEQ_IMAGE_BACKEND:=none}"
: "${CODESEEQ_VENICE_IMAGE_MODEL:=auto}"
: "${CODESEEQ_VENICE_IMAGE_ASPECT_RATIO:=1:1}"
: "${CODESEEQ_VENICE_IMAGE_RESOLUTION:=1K}"
: "${CODESEEQ_VENICE_IMAGE_FORMAT:=webp}"
: "${CODESEEQ_VENICE_IMAGE_VARIANTS:=1}"
: "${CODESEEQ_VENICE_IMAGE_SAFE_MODE:=true}"
: "${CODESEEQ_VENICE_IMAGE_HIDE_WATERMARK:=false}"
: "${CODESEEQ_VENICE_IMAGE_SEED:=0}"
: "${CODESEEQ_VENICE_IMAGE_RETURN_BINARY:=false}"
```

**Add to exports:**

```bash
export CODESEEQ_IMAGE_BACKEND CODESEEQ_VENICE_IMAGE_MODEL
export CODESEEQ_VENICE_IMAGE_ASPECT_RATIO CODESEEQ_VENICE_IMAGE_RESOLUTION
export CODESEEQ_VENICE_IMAGE_FORMAT CODESEEQ_VENICE_IMAGE_VARIANTS
export CODESEEQ_VENICE_IMAGE_SAFE_MODE CODESEEQ_VENICE_IMAGE_HIDE_WATERMARK
export CODESEEQ_VENICE_IMAGE_SEED CODESEEQ_VENICE_IMAGE_RETURN_BINARY
```

**Add to `doctor()` output:**

```bash
printf 'Image backend: %s\n' "${CODESEEQ_IMAGE_BACKEND:-none}"
printf 'Venice image model: %s\n' "${CODESEEQ_VENICE_IMAGE_MODEL:-auto}"
printf 'Venice API key: %s\n' "$(key_state "${VENICE_API_KEY:-}")"
```

### 5. MODIFY: `Dockerfile`

**Copy the new script into the container:**

```dockerfile
COPY bin/codeseeq-venice-image.py /usr/local/bin/codeseeq-venice-image.py
```

**Make it executable (alongside the existing chmod):**

```dockerfile
RUN chmod +x \
    /usr/local/bin/codeseeq-entrypoint \
    /usr/local/bin/codeseeq-bridge.py \
    /usr/local/bin/codeseeq-venice-image.py \
    /usr/local/bin/codeseeq-healthcheck \
    /usr/local/bin/codeseeq-print-config
```

### 6. MODIFY: `.env` / `.env.example`

Add new environment variables:

```bash
# --- Image Backend ---
# Backend: none (default, no image generation) or venice (Venice.ai API)
CODESEEQ_IMAGE_BACKEND=none
# Venice API key (required when CODESEEQ_IMAGE_BACKEND=venice)
VENICE_API_KEY=
# Venice image model: auto (auto-select) or specific model name
CODESEEQ_VENICE_IMAGE_MODEL=auto
# Venice image generation options
CODESEEQ_VENICE_IMAGE_ASPECT_RATIO=1:1
CODESEEQ_VENICE_IMAGE_RESOLUTION=1K
CODESEEQ_VENICE_IMAGE_FORMAT=webp
CODESEEQ_VENICE_IMAGE_VARIANTS=1
CODESEEQ_VENICE_IMAGE_SAFE_MODE=true
CODESEEQ_VENICE_IMAGE_HIDE_WATERMARK=false
CODESEEQ_VENICE_IMAGE_SEED=0
CODESEEQ_VENICE_IMAGE_RETURN_BINARY=false
```

### 7. MODIFY: `Makefile`

Add `ping-image` target:

```makefile
.PHONY: ping-image
ping-image:
	@test -n "$$VENICE_API_KEY" || (echo "VENICE_API_KEY is required" >&2; exit 1)
	@test "$${CODESEEQ_IMAGE_BACKEND:-none}" = "venice" || (echo "CODESEEQ_IMAGE_BACKEND=venice is required" >&2; exit 1)
	CODESEEQ_IMAGE_BACKEND=venice CODESEEQ_MODEL=$(MODEL) IMAGE=$(IMAGE) CONTAINER=$(CONTAINER) ./codeseeq ping-image
```

### 8. MODIFY: `README.md`

**Add to "Quickstart" prerequisites:**

```markdown
- **VENICE_API_KEY** (optional) — needed for image generation via Venice.ai when `CODESEEQ_IMAGE_BACKEND=venice`.
```

**Add new section after "Supported Models":**

```markdown
## Image Generation Backend

CodeSeeq supports an optional image generation backend via [Venice.ai](https://venice.ai) — a privacy-first, uncensored AI platform.

### Configuration

| Variable | Default | Description |
|---|---|---|
| `CODESEEQ_IMAGE_BACKEND` | `none` | Image backend: `none` (default) or `venice` |
| `VENICE_API_KEY` | — | Venice API key (required when backend is `venice`) |
| `CODESEEQ_VENICE_IMAGE_MODEL` | `auto` | Model: `auto` or specific name (e.g. `z-image-turbo`, `gpt-image-2`) |
| `CODESEEQ_VENICE_IMAGE_ASPECT_RATIO` | `1:1` | Aspect ratio: `1:1`, `16:9`, `9:16`, `4:3`, `3:4`, etc. |
| `CODESEEQ_VENICE_IMAGE_RESOLUTION` | `1K` | Resolution: `1K`, `2K`, `4K` |
| `CODESEEQ_VENICE_IMAGE_FORMAT` | `webp` | Output format: `jpeg`, `png`, `webp` |
| `CODESEEQ_VENICE_IMAGE_VARIANTS` | `1` | Number of variants: 1–4 |
| `CODESEEQ_VENICE_IMAGE_SAFE_MODE` | `true` | Blur adult content |
| `CODESEEQ_VENICE_IMAGE_HIDE_WATERMARK` | `false` | Hide Venice watermark |

### Usage

```bash
# Enable Venice image backend
export CODESEEQ_IMAGE_BACKEND=venice
export VENICE_API_KEY=your-key-here

# Test connectivity
./codeseeq ping-image

# Use auto model selection (default)
./codeseeq run "generate a picture of a cat"

# Specify model and aspect ratio
CODESEEQ_VENICE_IMAGE_MODEL=z-image-turbo \
CODESEEQ_VENICE_IMAGE_ASPECT_RATIO=16:9 \
CODESEEQ_VENICE_IMAGE_RESOLUTION=4K \
./codeseeq run "generate a cinematic wide shot of venice at sunset"

# Direct CLI usage (no Codex needed)
python3 bin/codeseeq-venice-image.py --prompt "a beautiful sunset" --out sunset.png
```

**Add to the environment variables table:**

```markdown
| `CODESEEQ_IMAGE_BACKEND` | `none` | Image backend: `none` or `venice` |
| `VENICE_API_KEY` | — | Venice API key (image generation) |
| `CODESEEQ_VENICE_IMAGE_MODEL` | `auto` | Venice image model |
```

**Add `ping-image` to the commands list:**

```markdown
./codeseeq ping-image
```

**Add `ping-image` to the Makefile targets table:**

```markdown
| `ping-image` | Test Venice image generation connectivity |
```

### 9. MODIFY: `RELEASE-NOTES.md`

Add at the top:

```markdown
## v0.3.6 - 2026-07-14

### Added
- **Venice.ai image generation backend.** New `CODESEEQ_IMAGE_BACKEND` configuration
  option (default `none`). Set to `venice` to enable image generation via the
  Venice.ai API using `VENICE_API_KEY`.
  - Supports all Venice `/image/generate` parameters: model selection (`auto` or
    specific models like `z-image-turbo`, `gpt-image-2`, `nano-banana-pro`),
    aspect ratio, resolution (1K/2K/4K), format (jpeg/png/webp), variants (1–4),
    safe mode, watermark control, negative prompts, seed, and CFG scale.
  - New standalone script: `bin/codeseeq-venice-image.py` for direct CLI usage
    without Codex.
  - New bridge endpoint: `POST /v1/images/generations` for OpenAI-compatible
    image generation proxied through Venice.
  - New `ping-image` diagnostic command.
  - New `--image-backend` and `--venice-image-model` CLI flags.
  - Doctor output now includes image backend status and Venice API key state.
- **Comprehensive environment variable configuration** for all Venice image
  parameters: `CODESEEQ_VENICE_IMAGE_MODEL`, `CODESEEQ_VENICE_IMAGE_ASPECT_RATIO`,
  `CODESEEQ_VENICE_IMAGE_RESOLUTION`, `CODESEEQ_VENICE_IMAGE_FORMAT`,
  `CODESEEQ_VENICE_IMAGE_VARIANTS`, `CODESEEQ_VENICE_IMAGE_SAFE_MODE`,
  `CODESEEQ_VENICE_IMAGE_HIDE_WATERMARK`, `CODESEEQ_VENICE_IMAGE_SEED`,
  `CODESEEQ_VENICE_IMAGE_RETURN_BINARY`, `CODESEEQ_VENICE_IMAGE_NEGATIVE_PROMPT`,
  `CODESEEQ_VENICE_IMAGE_CFG_SCALE`, `CODESEEQ_VENICE_IMAGE_STEPS`.

### Changed
- **Version bump to v0.3.6.**
- **Health endpoint** now reports `image_backend` status and Venice configuration.
- **Dockerfile** now includes `codeseeq-venice-image.py`.

---

### 10. MODIFY: `docs/ARCHITECTURE.md`

**Update version line:**

```markdown
Current version: `v0.3.6`
```

**Add new section after "Bridge API Format":**

```markdown
## Image Generation Backend

CodeSeeq supports an optional image generation backend. The default is `none` (no
image backend configured). Set `CODESEEQ_IMAGE_BACKEND=venice` to enable image
generation via the Venice.ai API.

### Venice Backend Architecture

```text
Codex / user
  -> POST /v1/images/generations (bridge endpoint)
  -> bin/codeseeq-bridge.py
  -> POST https://api.venice.ai/api/v1/image/generate
  -> Venice.ai inference
  -> Base64-encoded images returned
```

### Standalone Script

`bin/codeseeq-venice-image.py` provides direct CLI access to Venice image
generation without going through the bridge or Codex:

```bash
python3 bin/codeseeq-venice-image.py --prompt "a cat" --out cat.png --model z-image-turbo
```

### Configuration

| Variable | Default | Description |
|---|---|---|
| `CODESEEQ_IMAGE_BACKEND` | `none` | `none` or `venice` |
| `VENICE_API_KEY` | — | Venice API key |
| `CODESEEQ_VENICE_IMAGE_MODEL` | `auto` | Model name |
| `CODESEEQ_VENICE_IMAGE_ASPECT_RATIO` | `1:1` | Aspect ratio |
| `CODESEEQ_VENICE_IMAGE_RESOLUTION` | `1K` | Resolution tier |
| `CODESEEQ_VENICE_IMAGE_FORMAT` | `webp` | Output format |

The bridge's `/health` endpoint reports the current image backend status.
```

### 11. MODIFY: `VERSION`

```
v0.3.6
```

### 12. MODIFY: `config/model-catalog.json` (if needed)

**No changes needed.** Image models are queried dynamically from Venice's `/models` endpoint.

### 13. MODIFY: `.gitignore`

Add (if not already present):

```
codeseeq-images/
```

### 14. MODIFY: `bin/codeseeq-healthcheck`

Add image backend check (non-blocking):

```bash
# Optional: check image backend status
image_backend="${CODESEEQ_IMAGE_BACKEND:-none}"
if [[ "$image_backend" == "venice" ]]; then
  if [[ -z "${VENICE_API_KEY:-}" ]]; then
    echo "[health:warn] image_backend=venice but VENICE_API_KEY is not set" >&2
  fi
fi
```

---

## Version Bump Checklist

Every file containing `v0.3.5` must be updated to `v0.3.6`:

| File | Status |
|---|---|
| `VERSION` | Update to `v0.3.6` |
| `README.md` | Update version line to `v0.3.6` |
| `docs/ARCHITECTURE.md` | Update version line to `v0.3.6` |
| `docs/TROUBLESHOOTING.md` | Update version line to `v0.3.6` |
| `docs/SECURITY.md` | Update version line to `v0.3.6` |
| `RELEASE-NOTES.md` | Add v0.3.6 entry at top |
| `bin/codeseeq-bridge.py` | Update docstring and health endpoint version |
| `.env` / `.env.example` | No version stored, just add new vars |

---

## Implementation Order (Dependency-Aware)

Execute in this order to minimize rework:

### Phase 1: Core Infrastructure (no external dependencies)

1. **Create `bin/codeseeq-venice-image.py`** — The standalone script. Test manually.
2. **Modify `bin/codeseeq-bridge.py`** — Add `/v1/images/generations` endpoint and health info.
3. **Modify `bin/codeseeq-healthcheck`** — Add non-blocking image backend warning.

### Phase 2: Launcher/Entrypoint Wiring

4. **Modify `./codeseeq`** — Add env vars, CLI flags, `ping-image`, doctor output, print_help.
5. **Modify `bin/codeseeq-entrypoint`** — Add env vars, exports, doctor output.

### Phase 3: Build & Config

6. **Modify `Dockerfile`** — Copy new script, chmod it.
7. **Modify `.env`** — Add new vars with defaults.
8. **Modify `.env.example`** (if separate from `.env`) — Same additions.
9. **Modify `Makefile`** — Add `ping-image` target.

### Phase 4: Documentation

10. **Modify `README.md`** — New section, env var table, commands, Makefile targets.
11. **Modify `RELEASE-NOTES.md`** — Add v0.3.6 entry.
12. **Modify `docs/ARCHITECTURE.md`** — New section, version bump.
13. **Modify `docs/TROUBLESHOOTING.md`** — Version bump (and any image-related troubleshooting).
14. **Modify `docs/SECURITY.md`** — Version bump.
15. **Modify `VERSION`** — Change to `v0.3.6`.
16. **Modify `.gitignore`** — Add `codeseeq-images/`.

### Phase 5: Validation

17. **Run `./scripts/check.sh`** — Ensure no regressions.
18. **Run `make bridge-check`** — Verify Python syntax.
19. **Manual smoke test** — With Venice API key, test `ping-image`.

---

## Testing Plan

### Unit Tests (Manual)

1. **`bin/codeseeq-venice-image.py` syntax check:**
   ```bash
   python3 -c "import py_compile; py_compile.compile('bin/codeseeq-venice-image.py', doraise=True)"
   ```

2. **Bridge syntax check:**
   ```bash
   python3 -c "import py_compile; py_compile.compile('bin/codeseeq-bridge.py', doraise=True)"
   ```

3. **Bash syntax checks:**
   ```bash
   bash -n codeseeq
   bash -n bin/codeseeq-entrypoint
   bash -n bin/codeseeq-healthcheck
   ```

### Integration Tests (Requires VENICE_API_KEY)

4. **Standalone script smoke test:**
   ```bash
   VENICE_API_KEY=your-key python3 bin/codeseeq-venice-image.py \
     --prompt "A red circle on white background" \
     --model z-image-turbo \
     --aspect-ratio 1:1 \
     --resolution 1K \
     --out /tmp/venice-test.png
   ```

5. **Bridge endpoint smoke test:**
   ```bash
   # Start bridge first
   CODESEEQ_IMAGE_BACKEND=venice VENICE_API_KEY=your-key \
     python3 bin/codeseeq-bridge.py &
   
   # Test health
   curl -s http://127.0.0.1:8080/health | grep image_backend
   
   # Test generation
   curl -s -X POST http://127.0.0.1:8080/v1/images/generations \
     -H 'Content-Type: application/json' \
     -d '{"model":"z-image-turbo","prompt":"A red circle on white","size":"1024x1024"}' \
     | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Got {len(d[\"data\"])} image(s)')"
   ```

6. **Launcher smoke test:**
   ```bash
   CODESEEQ_IMAGE_BACKEND=venice VENICE_API_KEY=your-key \
     CODESEEQ_BRIDGE_MODE=process DEEPSEEK_API_KEY=sk-test \
     ./codeseeq ping-image
   ```

7. **Doctor output check:**
   ```bash
   CODESEEQ_IMAGE_BACKEND=venice VENICE_API_KEY=your-key ./codeseeq doctor | grep -i venice
   ```

### Edge Cases to Test

- `CODESEEQ_IMAGE_BACKEND=venice` without `VENICE_API_KEY` → clear error
- `CODESEEQ_IMAGE_BACKEND=venice` with invalid `VENICE_API_KEY` → 401 error
- `CODESEEQ_IMAGE_BACKEND=none` → `ping-image` fails with clear message
- `--image-backend venice --venice-image-model z-image-turbo` via CLI
- Venice 402 (insufficient balance) → clear error
- Venice 429 (rate limit) → clear error with retry info
- Venice 503 (model capacity) → clear error
- Large prompt > 7500 chars → truncated or rejected
- `variants=4` with `return_binary=true` → Venice returns JSON array (binary only for single)

---

## Estimated Effort

| Phase | Files | Estimated Time |
|---|---|---|
| Phase 1: Core | 3 files (1 new, 2 mod) | ~30 minutes |
| Phase 2: Launcher | 2 files | ~20 minutes |
| Phase 3: Build/Config | 5 files | ~15 minutes |
| Phase 4: Docs | 7 files | ~20 minutes |
| Phase 5: Validation | N/A | ~15 minutes |
| **Total** | **~18 files** | **~100 minutes** |

---

## Notes to the Implementing Agent

1. **No new Python dependencies.** The bridge already has `httpx`. The standalone script only uses `httpx` and stdlib (`json`, `os`, `argparse`, `base64`, `sys`, `time`, `pathlib`).

2. **Do not commit `.env`.** The `.env` file is in `.gitignore`. Only `.env.example` should be updated.

3. **Keep backward compatibility.** When `CODESEEQ_IMAGE_BACKEND=none` (default), everything behaves exactly as before. No existing workflows are affected.

4. **`VENICE_API_KEY` is NEVER auto-populated from `DEEPSEEK_API_KEY`.** Consistent with privacy hardening policy.

5. **The `make` in the Dockerfile runs `codeseeq install`.** Make sure the new script is copied BEFORE `RUN chmod` lines in the Dockerfile.

6. **The Venice `/image/generate` endpoint returns base64-encoded images in JSON by default.** `return_binary: true` returns raw binary instead. We default to base64 for easiest integration.

7. **`versions` in the OpenAPI spec shown in Venice docs are auto-generated (date-based).** Don't hardcode version numbers from the docs — the API accepts requests without version pins.

8. **Run `./scripts/check.sh` after all changes** to catch syntax errors, missing executables, and version mismatches.
