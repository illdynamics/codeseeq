╰─❯❯❯ LOCAL_BASE_URL="http://127.0.0.1:8888" CODESEEQ_TEMPERATURE="0.0" CODESEEQ_ALLOW_UPSTREAM_CODEX_SERVICES=true CODESEEQ_RUNTIME_MODE=host codeseeq --model local@~/Qoding/ai/BugTraceAI-Apex-G4-26B.MXFP4_MOE.gguf -y run "say hi"
[codeseeq] auto-detected VENICE_API_KEY; image backend set to venice
[codeseeq] runtime_mode=host cmd_arg=-y
[codeseeq] gguf model detected; using host runtime
[codeseeq] runtime: explicit host
[codeseeq] bridge mode: process
[codeseeq] bridge mode=process starting python3 on http://127.0.0.1:8080
[codeseeq] bridge log: /Users/wicked/x/jaqhammah/.codeseeq/log/bridge.log
[codeseeq] bridge process healthy (pid=42750) on http://127.0.0.1:8080
[codeseeq] host mode: running local Codex with bridge at http://127.0.0.1:8080/v1
OpenAI Codex v0.130.0
--------
workdir: /Users/wicked/x/jaqhammah
model: gguf@/Users/wicked/x/jaqhammah/local@~/Qoding/ai/BugTraceAI-Apex-G4-26B.MXFP4_MOE.gguf
provider: codeseeq
approval: never
sandbox: danger-full-access
reasoning effort: none
reasoning summaries: none
session id: 01a05d01-d717-78b3-8a02-14fedb339aa2
--------
user
run say hi
ERROR: {"detail":"gguf model file not found: /Users/wicked/x/jaqhammah/local@~/Qoding/ai/BugTraceAI-Apex-G4-26B.MXFP4_MOE.gguf"}
ERROR: {"detail":"gguf model file not found: /Users/wicked/x/jaqhammah/local@~/Qoding/ai/BugTraceAI-Apex-G4-26B.MXFP4_MOE.gguf"}
[codeseeq] stopping owned bridge process (pid=42750)
^C[codeseeq] stopping owned bridge process (pid=42750)


╰─❯❯❯ cd ../codeseeq
(base) ╭─[ꝖꝖ]─wicked↯infranux in ~/x/codeseeq
╰─❯❯❯ vim last-fixes.md
(base) ╭─[ꝖꝖ]─wicked↯infranux in ~/x/codeseeq
╰─❯❯❯ GGUF_BASE_URL="http://127.0.0.1:8888" CODESEEQ_TEMPERATURE="0.0" CODESEEQ_ALLOW_UPSTREAM_CODEX_SERVICES=true CODESEEQ_RUNTIME_MODE=host codeseeq --model gguf@~/Qoding/ai/BugTraceAI-Apex-G4-26B.MXFP4_MOE.gguf -y run "say hi"
[codeseeq] auto-detected VENICE_API_KEY; image backend set to venice
[codeseeq] runtime_mode=host cmd_arg=-y
[codeseeq] gguf model detected; using host runtime
[codeseeq] runtime: explicit host
[codeseeq] bridge mode: process
[codeseeq] bridge mode=process starting python3 on http://127.0.0.1:8080
[codeseeq] bridge log: /Users/wicked/x/codeseeq/.codeseeq/log/bridge.log
[codeseeq] bridge process healthy (pid=44149) on http://127.0.0.1:8080
[codeseeq] host mode: running local Codex with bridge at http://127.0.0.1:8080/v1
OpenAI Codex v0.130.0
--------
workdir: /Users/wicked/x/codeseeq
model: gguf@/Users/wicked/Qoding/ai/BugTraceAI-Apex-G4-26B.MXFP4_MOE.gguf
provider: codeseeq
approval: never
sandbox: danger-full-access
reasoning effort: none
reasoning summaries: none
session id: 01a05d02-a784-7483-b3ec-8fdcb609fdd2
--------
user
run say hi
ERROR: Reconnecting... 1/2
ERROR: Reconnecting... 2/2
ERROR: stream disconnected before completion: All connection attempts failed
ERROR: stream disconnected before completion: All connection attempts failed
[codeseeq] stopping owned bridge process (pid=44149)
(base) ╭─[ꝖꝖ]─wicked↯infranux in ~/x/codeseeq
╰─❯❯❯ 

