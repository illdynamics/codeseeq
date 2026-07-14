#!/usr/bin/env python3
"""
codeseeq-venice-image: Generate images via Venice.ai API.

Usage:
  python3 bin/codeseeq-venice-image.py --prompt "description" [--out output.png]
  python3 bin/codeseeq-venice-image.py --prompt "description" --model gpt-image-2 --aspect-ratio 16:9

Environment:
  VENICE_API_KEY                     Required
  CODESEEQ_VENICE_IMAGE_MODEL        Model name (default: z-image-turbo)
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
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

VENICE_IMAGE_URL = os.environ.get(
    "CODESEEQ_VENICE_IMAGE_URL",
    "https://api.venice.ai/api/v1/image/generate",
)
HTTP_TIMEOUT = float(os.environ.get("CODESEEQ_VENICE_IMAGE_TIMEOUT", "180"))


def _default_output_dir() -> Path:
    """Default output directory: current working directory."""
    return Path.cwd()


def _env_bool(key: str, default: bool = True) -> bool:
    val = os.environ.get(key, str(default)).strip().lower()
    return val in ("1", "true", "yes", "on")


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except (ValueError, TypeError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, str(default)))
    except (ValueError, TypeError):
        return default


def _env_str(key: str, default: str) -> str:
    return os.environ.get(key, default)


def build_payload(prompt: str, args: argparse.Namespace) -> Dict[str, Any]:
    """Build the JSON payload for Venice /image/generate."""

    payload: Dict[str, Any] = {
        "prompt": prompt,
    }

    # Model
    model = args.model or _env_str("CODESEEQ_VENICE_IMAGE_MODEL", "z-image-turbo")
    payload["model"] = model

    # Aspect ratio
    aspect_ratio = args.aspect_ratio or _env_str("CODESEEQ_VENICE_IMAGE_ASPECT_RATIO", "1:1")
    if aspect_ratio:
        payload["aspect_ratio"] = aspect_ratio

    # Resolution
    resolution = args.resolution or _env_str("CODESEEQ_VENICE_IMAGE_RESOLUTION", "1K")
    if resolution:
        payload["resolution"] = resolution

    # Format
    fmt = args.format or _env_str("CODESEEQ_VENICE_IMAGE_FORMAT", "webp")
    payload["format"] = fmt

    # Variants
    variants = args.variants if args.variants is not None else _env_int("CODESEEQ_VENICE_IMAGE_VARIANTS", 1)
    if variants > 1:
        payload["variants"] = variants

    # Safe mode
    if args.no_safe_mode:
        payload["safe_mode"] = False
    else:
        payload["safe_mode"] = _env_bool("CODESEEQ_VENICE_IMAGE_SAFE_MODE", True)

    # Hide watermark
    if args.hide_watermark:
        payload["hide_watermark"] = True
    else:
        payload["hide_watermark"] = _env_bool("CODESEEQ_VENICE_IMAGE_HIDE_WATERMARK", False)

    # Return binary
    if args.return_binary:
        payload["return_binary"] = True
    else:
        payload["return_binary"] = _env_bool("CODESEEQ_VENICE_IMAGE_RETURN_BINARY", False)

    # Negative prompt
    neg_prompt = args.negative_prompt or _env_str("CODESEEQ_VENICE_IMAGE_NEGATIVE_PROMPT", "")
    if neg_prompt:
        payload["negative_prompt"] = neg_prompt

    # Seed
    seed = args.seed if args.seed is not None else _env_int("CODESEEQ_VENICE_IMAGE_SEED", 0)
    if seed != 0:
        payload["seed"] = seed

    # CFG scale
    cfg_scale = args.cfg_scale if args.cfg_scale is not None else _env_float("CODESEEQ_VENICE_IMAGE_CFG_SCALE", 7.5)
    if cfg_scale:
        payload["cfg_scale"] = cfg_scale

    # Steps
    steps = args.steps if args.steps is not None else _env_int("CODESEEQ_VENICE_IMAGE_STEPS", 30)
    if steps:
        payload["steps"] = steps

    # Quality (only for gpt-image-2)
    quality = args.quality or _env_str("CODESEEQ_VENICE_IMAGE_QUALITY", "")
    if quality:
        payload["quality"] = quality

    return payload


def call_venice_api(payload: Dict[str, Any], api_key: str) -> httpx.Response:
    """Send request to Venice /image/generate and return the response."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        client = httpx.Client(timeout=HTTP_TIMEOUT)
        resp = client.post(VENICE_IMAGE_URL, json=payload, headers=headers)
        return resp
    except httpx.TimeoutException:
        print("error: Venice API request timed out after {} seconds".format(HTTP_TIMEOUT), file=sys.stderr)
        sys.exit(1)
    except httpx.ConnectError as e:
        print(f"error: Cannot connect to Venice API: {e}", file=sys.stderr)
        sys.exit(1)


def handle_api_error(resp: httpx.Response) -> None:
    """Handle Venice API error responses with clear messages."""
    status = resp.status_code
    try:
        body = resp.json()
        err_msg = body.get("error", resp.text[:500])
    except Exception:
        err_msg = resp.text[:500]

    if status == 401:
        print(f"error: Invalid VENICE_API_KEY (401 Unauthorized). Check your API key.", file=sys.stderr)
    elif status == 402:
        print(f"error: Insufficient Venice balance (402 Payment Required). "
              f"Top up at https://venice.ai/settings/billing", file=sys.stderr)
    elif status == 429:
        print(f"error: Rate limited (429). Wait and try again.", file=sys.stderr)
    elif status == 503:
        print(f"error: Venice model at capacity (503). Try again later.", file=sys.stderr)
    else:
        print(f"error: Venice API returned {status}: {err_msg}", file=sys.stderr)
    sys.exit(1)


def save_images(response_data: Dict[str, Any], output_path: Optional[str],
                output_dir: Optional[str], fmt: str) -> List[str]:
    """Decode base64 images and save to files. Returns list of saved paths."""
    images = response_data.get("images", [])
    if not images:
        print("error: No images in Venice response", file=sys.stderr)
        sys.exit(1)

    # Determine output directory
    if output_path:
        out_path = Path(output_path)
        out_dir = out_path.parent
        stem = out_path.stem
    elif output_dir:
        out_dir = Path(output_dir)
        stem = "generated"
    else:
        out_dir = _default_output_dir()
        stem = "generated"

    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")

    ext = {"jpeg": "jpg", "png": "png", "webp": "webp"}.get(fmt, "webp")

    saved_paths: List[str] = []
    for i, img_b64 in enumerate(images):
        if len(images) == 1 and output_path:
            fname = f"{stem}.{ext}"
        elif len(images) == 1:
            fname = f"{stem}_{timestamp}.{ext}"
        else:
            fname = f"{stem}_{timestamp}_{i + 1}.{ext}"

        fpath = out_dir / fname
        try:
            img_bytes = base64.b64decode(img_b64)
            fpath.write_bytes(img_bytes)
            saved_paths.append(str(fpath))
            print(f"saved: {fpath}")
        except Exception as e:
            print(f"error: Failed to save image {i + 1} to {fpath}: {e}", file=sys.stderr)

    return saved_paths


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate images via Venice.ai API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --prompt "a cat sitting on a chair" --out cat.png
  %(prog)s --prompt "sunset over mountains" --model gpt-image-2 --aspect-ratio 16:9 --resolution 4K
  %(prog)s --prompt "a logo" --format png --no-safe-mode --hide-watermark
        """,
    )
    parser.add_argument("--prompt", "-p", required=True, help="Image description prompt")
    parser.add_argument("--out", "-o", default=None, help="Output file path")
    parser.add_argument("--out-dir", "-d", default=None, help="Output directory (created if needed)")
    parser.add_argument("--model", "-m", default=None,
                        help="Model name (default: z-image-turbo)")
    parser.add_argument("--aspect-ratio", "-a", default=None,
                        help="Aspect ratio (default: 1:1, e.g. 16:9, 4:3)")
    parser.add_argument("--resolution", "-r", default=None,
                        help="Resolution: 1K, 2K, 4K (default: 1K)")
    parser.add_argument("--format", "-f", default=None,
                        choices=["jpeg", "png", "webp"], help="Output format (default: webp)")
    parser.add_argument("--variants", "-n", type=int, default=None,
                        help="Number of variants 1-4 (default: 1)")
    parser.add_argument("--no-safe-mode", action="store_true",
                        help="Disable safe mode (allow adult content)")
    parser.add_argument("--hide-watermark", action="store_true",
                        help="Hide Venice watermark")
    parser.add_argument("--return-binary", action="store_true",
                        help="Return raw binary instead of base64 JSON")
    parser.add_argument("--negative-prompt", default=None,
                        help="What to avoid in the image")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed (0 = random)")
    parser.add_argument("--cfg-scale", type=float, default=None,
                        help="CFG scale (default: 7.5, higher = more prompt adherence)")
    parser.add_argument("--steps", type=int, default=None,
                        help="Inference steps (default: 30)")
    parser.add_argument("--quality", default=None,
                        choices=["low", "medium", "high"],
                        help="Quality tier for gpt-image-2 (default: high)")
    parser.add_argument("--json", action="store_true",
                        help="Output full JSON response instead of saving files")

    args = parser.parse_args()

    # Validate API key
    api_key = os.environ.get("VENICE_API_KEY", "")
    if not api_key:
        print("error: VENICE_API_KEY environment variable is required", file=sys.stderr)
        print("  Get a key at: https://venice.ai/settings/api", file=sys.stderr)
        sys.exit(1)

    # Build payload
    payload = build_payload(args.prompt, args)
    print(f"model: {payload.get('model')}", file=sys.stderr)
    print(f"size: {payload.get('aspect_ratio', 'N/A')} @ {payload.get('resolution', 'N/A')}", file=sys.stderr)
    print(f"format: {payload.get('format', 'N/A')}", file=sys.stderr)
    print(f"generating...", file=sys.stderr)

    # Call Venice API
    resp = call_venice_api(payload, api_key)

    if resp.status_code >= 400:
        handle_api_error(resp)

    data = resp.json()

    # Print timing
    timing = data.get("timing", {})
    if timing:
        total_ms = timing.get("total", 0)
        print(f"done: {total_ms}ms total", file=sys.stderr)

    if args.json:
        print(json.dumps(data, indent=2))
        return

    # Save images
    fmt = payload.get("format", "webp")
    saved = save_images(data, args.out, args.out_dir, fmt)
    if saved:
        print(f"\n{len(saved)} image(s) saved:")
        for p in saved:
            print(f"  {p}")


if __name__ == "__main__":
    main()
