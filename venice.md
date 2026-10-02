╰─❯❯❯ codeseeq -m venice@qwen-3-8-flash "say hi"
[codeseeq] auto-detected VENICE_API_KEY; image backend set to venice
[codeseeq] runtime_mode=host cmd_arg=say hi
[codeseeq] runtime: explicit host
[codeseeq] bridge mode: auto
[codeseeq] bridge mode=process starting python3 on http://127.0.0.1:8080
[codeseeq] bridge log: /Users/wicked/x/codeseeq/.codeseeq/log/bridge.log
[codeseeq] bridge process healthy (pid=82342) on http://127.0.0.1:8081
[codeseeq] host mode: running local Codex with bridge at http://127.0.0.1:8081/v1
OpenAI Codex v0.154.0
--------
workdir: /Users/wicked/x/codeseeq
model: venice@qwen-3-8-flash
provider: codeseeq
approval: never
sandbox: workspace-write [workdir, /tmp, $TMPDIR]
reasoning effort: none
reasoning summaries: none
session id: 01a0d2d8-f39a-7372-831b-8d4930ecefb4
--------
user
say hi
ERROR: Reconnecting... 1/2
ERROR: Reconnecting... 2/2
ERROR: stream disconnected before completion: {"error":{"message":"Authentication Fails, Your api key: ****6yQw is invalid (request_id: 96aff088-a0b4-4b3c-8045-d7e3e89d9782)","type":"authentication_error","param":null,"code":"invalid_request_error"}}
ERROR: stream disconnected before completion: {"error":{"message":"Authentication Fails, Your api key: ****6yQw is invalid (request_id: 96aff088-a0b4-4b3c-8045-d7e3e89d9782)","type":"authentication_error","param":null,"code":"invalid_request_error"}}
[codeseeq] stopping owned bridge process (pid=82342)

