╰─❯❯❯ ./codeseeq nuke ; ./codeseeq build ; ./codeseeq install
[codeseeq] auto-detected VENICE_API_KEY; image backend set to venice
[codeseeq:warn] This will remove:
[codeseeq:warn]   - /Users/wicked/.config/codeseeq (installed snapshot)
[codeseeq:warn]   - /Users/wicked/bin/codeseeq (launcher script)
[codeseeq:warn]   - /Users/wicked/.codeseeq (user Codex state, if any)
[codeseeq:warn] 
[codeseeq:warn] Your repo checkout and container images are left untouched.
Are you sure? [y/N] y
[codeseeq] CodeSeeq has been nuked from your user environment.
[codeseeq] auto-detected VENICE_API_KEY; image backend set to venice
Cannot connect to Podman. Please verify your connection to the Linux system using `podman system connection list`, or try `podman machine init` and `podman machine start` to manage a new Linux VM
Error: unable to connect to Podman socket: failed to connect: ssh: handshake failed: EOF
[codeseeq] auto-detected VENICE_API_KEY; image backend set to venice
[codeseeq] seeded default system prompt at /Users/wicked/.config/codeseeq/system-prompt.md (edit or run: codeseeq system add)
[codeseeq] codex CLI found: /Users/wicked/.nvm/versions/node/v20.20.2/bin/codex
[codeseeq] Python bridge deps: OK
[codeseeq] installed repo snapshot from /Users/wicked/x/codeseeq to /Users/wicked/.config/codeseeq
[codeseeq] installed launcher to /Users/wicked/bin/codeseeq
(base) ╭─[ꝖꝖ]─wicked↯infranux in ~/x/codeseeq
╰─❯❯❯ podman machine init
Error: machine "podman-machine-default" already exists

