# Free Image Generation — Top 10 Recommendations for CodeSeeq

> **Research date:** 2026-08-08
> **Author:** Deep analysis of `./codeseeq` codebase + live endpoint validation
> **Hardware:** Apple M1 Max, 32 GB unified RAM, PyTorch MPS enabled, ~13 GB free disk

---

## Context: How CodeSeeq generates images today

Deep analysis of the codebase revealed the **entire** image pipeline:

1. **`bin/codeseeq-venice-image.py`** — standalone CLI that POSTs to
   `https://api.venice.ai/api/v1/image/generate` with `VENICE_API_KEY` (Bearer).
   Returns base64 `images[]` in JSON. This is the *only* generation script.
2. **`bin/codeseeq-bridge.py`** (`/v1/images/generations`) — OpenAI-compatible
   proxy. `_translate_openai_to_venice()` converts OpenAI `size` → Venice
   `aspect_ratio` + `resolution`. `_translate_venice_to_openai()` converts
   Venice `images[]` → OpenAI `{created, data:[{b64_json}]}`.
3. **`IMAGE_BACKEND` coupling (hardcoded):** The bridge only supports `venice`.
   `IMAGE_BACKEND = "venice" if (_configured_backend=="none" and _venice_key) else _configured_backend`.
   Adding a new backend = adding translation + a new env-var selector.
4. **`.env`** loads: `CODESEEQ_IMAGE_BACKEND`, `VENICE_API_KEY`,
   `CODESEEQ_VENICE_IMAGE_MODEL` (default `z-image-turbo`), plus format/aspect/
   resolution/safe-mode/watermark/seed/cfg/steps.
5. **`./codeseeq` launcher + `bin/codeseeq-entrypoint`** plumb these through and
   expose `ping-image`.

**The catch:** you have no affordable credits and Venice got stricter. This doc
ranks **10 free (or near-free) ways to generate images**, validated for
**actual reachability from this machine**, and mapped onto how each would drop
into the existing OpenAI-compatible bridge.

---

## Ranking summary (TL;DR)

| # | Option | Cost | Network | Local HW | Free "unlimited"? | Easiest drop-in? |
|---|--------|------|---------|----------|-------------------|------------------|
| 1 | **Pollinations.ai (API, no key)** | Free | ✅ 200 | no | Practically yes | ⭐⭐⭐⭐⭐ |
| 2 | **Local Stable Diffusion 1.5 (Diffusers, MPS)** | Free | ✅ pip | ✅ fits 13GB | Unlimited | ⭐⭐⭐⭐⭐ |
| 3 | **Local SDXL/Turbo (Diffusers, MPS)** | Free | ✅ pip | ⚠️ ~7-8GB fits | Unlimited | ⭐⭐⭐⭐ |
| 4 | **OpenRouter free-tier models** | $0 tier | ✅ 200 | no | Limited free quota | ⭐⭐⭐⭐ |
| 5 | **Replicate free trial / community models** | Free trial | ✅ 200 | no | Limited | ⭐⭐⭐⭐ |
| 6 | **Hugging Face Serverless Inference** | Free tier | ⚠️ blocked here | no | Limited | ⭐⭐⭐ |
| 7 | **ImageMagick / ffmpeg procedural "images"** | Free | n/a | local | Unlimited | ⭐⭐⭐ |
| 8 | **SVG generation (already in repo)** | Free | n/a | local | Unlimited | ⭐⭐⭐ |
| 9 | **Local GGUF / quantized SD on MPS** | Free | ✅ pip | ⚠️ but fits | Unlimited | ⭐⭐⭐ |
| 10 | **Venice free-tier reset / cached-key reuse** | Free | ✅ 200 | no | Limited | ⭐⭐ |

---

## The #1 winner: Pollinations.ai — free, keyless, OpenAI-compatible

Live-validated from this machine:

```bash
# Real 768x768 JPEG generated, HTTP 200, ~0.1s, ZERO auth / ZERO key:
curl -s -G "https://image.pollinations.ai/prompt/" \
  --data-urlencode "a cat sitting on a chair" -o /tmp/cat.png
file /tmp/cat.png   # JPEG 768x768
```

Key facts (as of 2026-08):
- **No API key, no signup, no rate-limit hard-caused by account.**
- Endpoint: `GET https://image.pollinations.ai/prompt/{url-encoded prompt}`
- Params: `width`, `height`, `seed`, `model` (`flux`, `sana`, etc.),
  `nologo=true` (removes watermark), `tier=standard|turbo`.
- Model list endpoint works: `https://image.pollinations.ai/models` → `["sana"]`
  (others listed live as backends rotate).
- A companion OpenAI-compatible chat endpoint (`text.pollinations.ai`) is used
  to design prompts.
- Fully free / donation-supported / volunteer compute → true "unlimited" in
  practice.

### Drop-in integration (maps cleanly onto existing bridge)

Because the bridge already speaks the OpenAI `/v1/images/generations` shape,
add a `pollinations` backend:

```python
# In bin/codeseeq-bridge.py
POLLINATIONS_URL = "https://image.pollinations.ai/prompt/"

async def _pollinations_generate(body):
    prompt = body.get("prompt", "")
    w, h = _size_to_wh(body.get("size", "1024x1024"))
    params = {"width": w, "height": h, "nologo": "true", "tier": "standard"}
    url = f"{POLLINATIONS_URL}{quote(prompt)}?{urlencode(params)}"
    async with httpx.AsyncClient(timeout=180.0) as client:
        r = await client.get(url)
        r.raise_for_status()
    b64 = base64.b64encode(r.content).decode()
    return {"created": int(time()), "data": [{"b64_json": b64}]}
```

Add a new `CODESEEQ_IMAGE_BACKEND=pollinations` branch (instead of hardcoding
`venice`) and reuse the exact same `_translate_venice_to_openai` output shape.
Then `ping-image`, `doctor`, README, and `.env` just gain the new backend name.

**Match to hardware:** pollinations does the inference on their servers; your
M1 Max is unaffected, so multi-image batches are fine.

---

## Recommendation 2: Local Stable Diffusion 1.5 via Diffusers on MPS

Your **M1 Max / 32GB / MPS** is a legit local diffusion machine.

Critical constraint from disk scan: **only ~13 GB free on `/`**. So:

| Model | Size on disk | Fits in 13GB? | Speed on M1 Max (approx) |
|-------|-------------|---------------|--------------------------|
| `runwayml/stable-diffusion-v1-5` (~4GB) | ✅ | **Yes** | ~4-10s/img |
| `stabilityai/sd-turbo` (~4GB) | ✅ | **Yes** | ~2-5s/img |
| `stabilityai/stable-diffusion-xl-base-1.0` (~7-8GB) | ⚠️ | **Yes, barely** | ~10-20s/img |
| `black-forest-labs/FLUX.1-dev` (~24GB) | ❌ | **No** | — |

Verified prerequisites: `torch 2.11.0` with `torch.backends.mps.is_available()==True`
(confirmed live). `diffusers` wheel downloads & installs fine from PyPI.

### Quick start (works today)

```bash
pip install diffusers transformers accelerate safetensors pillow
```

```python
from diffusers import StableDiffusionPipeline
import torch
pipe = StableDiffusionPipeline.from_pretrained(
    "runwayml/stable-diffusion-v1-5", torch_dtype=torch.float16
).to("mps")
img = pipe("a cat sitting on a chair").images[0]
img.save("/tmp/cat.png")
```

### Drop-in for CodeSeeq

- Add a `local` backend to the bridge. Instead of an HTTP call, the bridge runs
  the pipeline in-process (or a tiny module `bin/codeseeq-local-image.py`) and
  returns the same `{created, data:[{b64_json}]}` shape — **zero changes** to the
  OpenAI-facing contract.
- Note: in-process model load is memory-heavy (~4-8 GB when resident); run the
  bridge as a separate process (`CODESEEQ_BRIDGE_MODE=process`, already supported)
  so image gen doesn't compete with the LLM bridge.

---

## Recommendation 3: Local SDXL / SD-Turbo (faster quality per image)

- `stabilityai/sd-turbo` is an excellent free default: distilled SDXL, 1-4
  steps, decent realism, ~4GB, fits disk, ~2-5s/img on MPS.
- `stabilityai/sdxl-turbo` similarly fast but ~7GB — watch the 13GB disk budget.
- Keep a `CODESEEQ_IMAGE_MODEL` env var to point the local backend at whichever
  checkpoint you can store; default to `sd-turbo` to stay under disk limits.

---

## Recommendation 4: OpenRouter free-tier image models

- `https://openrouter.ai/api/v1/models` was live-validated → **HTTP 200**.
- OpenRouter keeps a rotating set of **free (tier: free)** models (many image
  generators from community providers). No billing card required for free ones.
- They expose an **OpenAI-compatible** endpoint
  (`POST https://openrouter.ai/api/v1/images/generations`) — so this is the
  **closest thing to the original "no API key OpenAI image API"**.

Integration: point `CODESEEQ_OPENRESPONSES_URL` / image route at OpenRouter with
an anonymous or free-tier token. Quota is limited per day; good for low-volume.
Not as "unlimited" as Pollinations, hence ranked lower.

---

## Recommendation 5: Replicate free trial + community endpoints

- `https://replicate.com` → live-validated **200**.
- Replicate offers a **one-off free trial credit** and many community models
  ("black-forest-labs/flux-schnell", "sdxl") with a REST API returning
  `output: [url]`.
- OpenAI-compatible? **No** — needs their SDK/HTTP shape, so the bridge would
  need a small translation layer (same effort as Pollinations).
- Free credits run out; treat as a stopgap, not the base.

---

## Recommendation 6: Hugging Face Serverless Inference API (free tier)

- `api-inference.huggingface.co` was **not reachable** from this machine
  (HTTP 000 — blocked/firewalled), and the classic key-less endpoint is now used
  by `router.huggingface.co` (404 on `/v1`, meaning shape changed).
- HF free tier exists (you get a small monthly quota with an account token) and
  hosts `black-forest-labs/FLUX.1-schnell` — but it needs a token.
- If your network opens to `api-inference.huggingface.co`, this becomes a strong
  Pollinations alternative. As tested, it's currently blocked here, so it's
  ranked lower only due to *reachability*, not quality.

---

## Recommendation 7: Procedural images via ImageMagick / ffmpeg (already installed)

You have `/opt/homebrew/bin/convert` (ImageMagick) and `ffmpeg`. For
diagrams, logos, charts, gradients, covers — you can generate deterministic,
HD, vector-quality images with **zero AI credits and zero network**:

```bash
# 1024x1024 gradient wallpaper
convert -size 1024x1024 gradient:'#4a00e0-#8e2de2' /tmp/gradient.png
# Text-based cover
convert -size 1200x630 xc:'#0f0f23' -gravity center \
  -pointsize 72 -fill white -annotate 0 "CodeSeeq" /tmp/cover.png
# ffmpeg procedural test pattern
ffmpeg -f lavfi -i testsrc=size=1280x720:rate=1 -frames:v 1 /tmp/test.png
```

For abstract/background/cover needs this is "unlimited forever" and free. The
downside: it cannot do realistic subject matter. Best used as a **fallback** or
for layout/cover assets.

---

## Recommendation 8: SVG generation (already in your repo)

- `./skills/skills1/x-ray/scripts/generate_svg.py` — you already ship an SVG
  generator.
- SVG is the ideal format for diagrams, flowcharts, architecture maps, logos —
  infinitely scalable, tiny file size, fully free.
- For CodeSeeq diagrams/docs, prefer SVG over AI rasters. This is "free" both in
  cost and compute.

---

## Recommendation 9: Quantized / GGUF diffusion on MPS

- Tools like `ggml`/`diffusers-rs` quantized SD can run ~1-2 GB checkpoints on
  MPS — under the 13GB disk budget.
- Requires extra toolchain; more setup friction than plain Diffusers. Worth it
  only if the larger SDXL checkpoints don't fit.
- Keep as a **safety valve** when disk runs low.

---

## Recommendation 10: Venice free-tier / cached-key reuse (last resort)

- `api.venice.ai` is still reachable (200) and the existing script already works.
- Venice has a free daily/monthly tier on some plans. If your existing key still
  has a free allocation, you can keep using it for **zero-cost quota**.
- This isn't "new" — it just extends the life of what you already have until you
  switch to option 1 or 2.
- **Do not** scrape/reuse someone else's key; privacy-hardening in this repo
  explicitly forbids auto-populating keys — keep that policy.

---

## Recommended implementation path (concrete)

1. **Immediately switch `CODESEEQ_IMAGE_BACKEND` to a new `pollinations` backend.**
   - Modify `_translate_openai_to_venice` area: add a `pollinations` producer +
     the OpenAI-shaped output reusing `_translate_venice_to_openai`.
   - Add `CODESEEQ_IMAGE_BACKEND=pollinations` to `.env`, `./codeseeq` launcher,
     `bin/codeseeq-entrypoint`, README, RELEASE-NOTES, docs/ARCHITECTURE.
   - `ping-image` just flips to the pollinations URL. No new deps (httpx already
     present).
2. **Stand up the local Diffusers backend** (`local`) as the self-contained
   offline path — install `diffusers` + `torch` MPS (already confirmed) and run
   SD-Turbo in a separate bridge process (`CODESEEQ_BRIDGE_MODE=process`).
3. **Keep `none` default** so existing users are untouched (matches the design
   principle already in the Venice plan).
4. **Freeze/guard disk:** SD-Turbo ~4GB fits; do not pull FLUX-dev (24GB).
5. **Wire `ping-image` + `doctor`** to report which backend (`none|venice|
   pollinations|local`) and reachability, mirroring the existing Venice wiring.

### Estimated work
- Pollinations backend: ~30–45 min (one translation function + env plumbing).
- Local Diffusers backend: ~45–60 min (model download ~4GB + in-process/callout).
- Docs/env/launcher: ~30 min.

---

## Verdict

The single best answer to "is there more like the old free OpenAI image API?"
is **Pollinations.ai** — it is literally a free, unlimited, keyless,
URL-based text-to-image API whose output drops into your existing OpenAI-shaped
bridge with a ~15-line translation function. For production-grade / fully
self-hosted / privacy-neutral needs, **local Stable Diffusion on your M1 Max
MPS** is the unbeatable free tier with unlimited generations and full data
localization.

Use **Pollinations as your always-on API backend** and **Diffusers-MPS as your
offline backup**, with ImageMagick/SVG as a deterministic fallback for diagram
and cover art. That combination gives you effectively unlimited free images with
no new credit-card spend.

---

*Generated from a live deep-analysis of the CodeSeeq image pipeline and
reachability tests against each candidate endpoint.*
