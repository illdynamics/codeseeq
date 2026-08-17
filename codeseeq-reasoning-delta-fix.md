# CodeSeeq Bridge — Fix `ReasoningRawContentDelta without active item` Errors

## What Is Happening

The Codex CLI (v0.130.0) consumes the OpenAI Responses API SSE stream from the
CodeSeeq bridge. The Responses API requires a strict event ordering for reasoning output:

```
response.output_item.added   (type: "reasoning", opens the item, gives it an id)
response.reasoning_text.delta  (references item_id + output_index)
response.reasoning_text.done
response.output_item.done
```

The bridge (`bin/codeseeq-bridge.py`) currently emits `response.reasoning_text.delta`
events **without** first emitting a `response.output_item.added` event to open a
reasoning item. Codex therefore logs:

```
ERROR codex_core::util: ReasoningRawContentDelta without active item
```

This happens on EVERY model that returns `reasoning_content` in its streaming delta —
including **deepseek-v4-flash** (non-thinking) because the DeepSeek API may still emit
`reasoning_content` chunks even when thinking mode is not explicitly enabled.

The fix is in `bin/codeseeq-bridge.py` in the `event_stream()` async generator,
inside the streaming delta handling section (around the `# 1. Reasoning` comment).

---

## Root Cause — Exact Location

File: `bin/codeseeq-bridge.py`

Find the streaming section with this comment and code block:

```python
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
```

**Problem:** `response.reasoning_text.delta` is emitted with only `content_index: 0`
but missing `item_id` and `output_index`. More critically, no
`response.output_item.added` event with `type: "reasoning"` is ever emitted before
the first delta. Codex has no active reasoning item to attach the delta to.

---

## The Fix

### Step 1 — Find the install root and open the bridge file

```bash
INSTALL_ROOT="$(dirname "$(realpath "$(which codeseeq)")")"
echo "Install root: $INSTALL_ROOT"
# Edit the bridge
$EDITOR "$INSTALL_ROOT/bin/codeseeq-bridge.py"
```

### Step 2 — Add reasoning item state tracking

In the `event_stream()` function, near the top where other state variables are
initialised (next to `message_item_open`, `message_output_index`, etc.), add:

```python
reasoning_item_id_local = f"rs_{uuid.uuid4().hex[:20]}"
reasoning_item_open = {"value": False}
reasoning_output_index: Dict[str, Optional[int]] = {"value": None}
```

### Step 3 — Replace the reasoning delta block

Replace the current `# 1. Reasoning` block:

```python
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
```

With this corrected version:

```python
# 1. Reasoning
reasoning_delta = delta.get("reasoning_content")
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
```

### Step 4 — Close the reasoning item properly at stream end

After the streaming loop, find where the full reasoning summary is emitted
(look for `full_reasoning = "".join(reasoning_parts).strip()`).

After that block, before emitting `response.output_item.done` for the message item,
add a `response.reasoning_text.done` and `response.output_item.done` for the
reasoning item if it was opened:

Find the section that looks like:
```python
full_reasoning = "".join(reasoning_parts).strip()
if full_reasoning:
    # ... existing reasoning output_item logic ...
```

Make sure the reasoning item done sequence is emitted when `reasoning_item_open["value"]`
is True:

```python
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
```

> **Note:** If the existing code already emits a reasoning `output_item.done` block,
> update it to use `reasoning_item_id_local` and `reasoning_output_index["value"]`
> instead of allocating a fresh index — the item was already opened above and must
> use the same `output_index` and `id`.

---

## Why This Affects Non-Thinking Models Too

DeepSeek's API returns `reasoning_content` in the streaming delta even for
`deepseek-v4-flash` (non-thinking mode). The model still performs internal reasoning
but it leaks into the SSE stream. The bridge faithfully forwards it, and Codex chokes
because no reasoning item was opened.

**There are two valid approaches:**

**Option A (recommended) — Apply the fix above for all models.**
Reasoning content is forwarded properly. Codex can display or ignore it.
No data is lost.

**Option B — Filter out reasoning_content for non-thinking models.**
In the bridge, check `spec.thinking` (or `spec.enable_thinking`) for the active model
and skip forwarding `reasoning_content` when thinking is disabled:

```python
# 1. Reasoning — only forward if thinking is enabled for this model
reasoning_delta = delta.get("reasoning_content")
if isinstance(reasoning_delta, str) and reasoning_delta and spec.thinking:
    # ... existing + fixed emit logic ...
```

Option B silences the errors and reduces token waste for non-thinking models,
but discards the reasoning trace. Use Option A if you want reasoning traces
forwarded correctly for all models; use Option B if you just want clean output
for `deepseek-v4-flash` and `qwibus-qwikk`.

---

## Verify The Fix

Restart the bridge (kill any running bridge process, it will auto-restart on next `qwf`
invocation), then run:

```bash
# Using deepseek (standard alias, not qwf)
CODESEEQ_RUNTIME_MODE=host CODESEEQ_ALLOW_UPSTREAM_CODEX_SERVICES=true \
  codeseeq -m deepseek-v4-flash -y run "say hi"
```

The `ReasoningRawContentDelta without active item` errors should be gone.
The `say` command should still execute and the Mac should speak "hi" aloud.

Check the bridge log for clean output:
```bash
tail -f ~/.codeseeq/log/bridge.log
```

---

## Expected End State

- Zero `ReasoningRawContentDelta without active item` errors in Codex stderr
- `deepseek-v4-flash` works cleanly for all prompts
- `deepseek-v4-flash-thinking` works cleanly with reasoning traces forwarded
- `qwibus-qwikk` and `qwibus-qmplx` unaffected (local model, different path)
- Tools still work — this fix is purely in the SSE event emission layer
