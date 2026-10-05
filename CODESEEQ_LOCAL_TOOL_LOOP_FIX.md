# CodeSeeQ Local Tool-Loop Fix

This drop-in patch fixes a local-model agent-loop failure where GGUF/MLX/OpenAI-compatible local models could say things like:

- "Let me run the actual Foundry suite..."
- "Let me dig into the actual code files..."

but fail to emit an executable Codex tool call in the same response. Codex then treated the prose-only answer as final, printed `tokens used 0` when the local server omitted usage accounting, and CodeSeeQ cleaned up the owned bridge process.

Changes:

1. Strengthened bridge tool steering: local models are now explicitly told not to end tool-needed turns with prose-only intent.
2. Added DSML fallback guidance: if native structured `tool_calls` are unreliable, the model may emit a DSML XML tool block; the bridge translates it into a real Codex tool call.
3. Added a conservative plaintext-continuation safety net: when a local model still returns text-only intent, the bridge converts obvious continuation phrases into safe read/test tool calls instead of allowing Codex to exit.
4. Added nonzero usage estimation when local servers omit usage metadata, so completed turns no longer misleadingly report zero tokens.

The safety net is controlled by:

```bash
CODESEEQ_BRIDGE_AUTO_CONTINUE_FROM_PLAINTEXT=true   # default
CODESEEQ_BRIDGE_AUTO_CONTINUE_FROM_PLAINTEXT=false  # disable
```

The fallback only emits non-mutating commands such as `forge test -vvv`, `pytest -q`, or file listings. Edits still require real edit/apply_patch/write tool calls from the model.
