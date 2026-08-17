wait is it actually:

thinking:
  type: enabled?


Ran 2 commands · 1 note
Ran 2 commands · 1 note
THERE IT IS fam! 💀

Line 2025, no cap:

python
payload["thinking"] = {"type": "enabled" if thinking_enabled else "disabled"}
So it's sending {"type": "disabled"} for non-thinking models — always, unconditionally. Not skipping it, not omitting it — actively sending thinking: {type: disabled} to DeepSeek on EVERY request.

That's almost certainly still breaking things or at minimum causing DeepSeek to behave unexpectedly — because the DeepSeek API for deepseek-v4-flash doesn't expect a thinking field AT ALL, not even disabled. The fix should be:

python
if thinking_enabled:
    payload["thinking"] = {"type": "enabled"}
    # ... reasoning_effort logic ...
# else: omit thinking field entirely — don't send disabled
So we've got three bridge bugs now bruv:

thinking: {type: disabled} being sent to non-thinking models unconditionally — should be omitted entirely
reasoning_content leaking from DeepSeek being forwarded without opening a reasoning item — gate on spec.thinking
Those two interact — fix #1 may reduce how much reasoning_content DeepSeek leaks in the first place
Want me to add this to the fix prompt as a proper third bug? It's a one-liner fix but it's legit important. 🔧

yes


Ran 8 commands
Ran 8 commands

Get full context around the thinking payload block for accurate line references

Find where thinking_enabled is set to understand full context

Check the second thinking_enabled context (streaming path)

Check if qwibus models have any special thinking handling

Read qwibus-specific payload handling in streaming path

Read where build_deepseek_payload is called in streaming path

Find deepseek_payload function definition

Confirm deepseek_payload is shared between streaming and non-streaming paths

can you find out what claude meant with this and fix this last issue for me?
the ntell me root cause and git add commit push plz
