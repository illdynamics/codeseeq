CONTAINER ?= podman
IMAGE ?= codeseeq:dev

# OPENRESPONSES_INSTALL is a legacy alias kept for backward compatibility.
OPENRESPONSES_INSTALL ?= npm
OPENRESPONSES_SOURCE ?= ./open-responses
OPENRESPONSES_REPO ?= https://github.com/open-responses/open-responses
OPENRESPONSES_DOCS ?= https://docs.julep.ai/responses/quickstart
OPENRESPONSES_REF ?= repo-local

CODEX_REPO ?= https://github.com/openai/codex.git
CODEX_DOCS ?= https://developers.openai.com/codex
DEEPSEEK_DOCS ?= https://api-docs.deepseek.com/
CODEX_NPM_VERSION ?= 0.130.0

MODEL ?= deepseek-v4-flash
THINKING ?=
PROMPT ?= Return exactly: codeseeq-ok

.PHONY: env-load-help
env-load-help:
	@echo 'Load .env safely before live tests:'
	@echo '  set -a'
	@echo '  source .env'
	@echo '  set +a'
	@echo 'Do not modify .env in automation.'

.PHONY: install
install:
	./codeseeq install

.PHONY: build
build:
	$(CONTAINER) build \
		--build-arg CODEX_NPM_VERSION=$(CODEX_NPM_VERSION) \
		-t $(IMAGE) .

.PHONY: inspect-bridge
inspect-bridge:
	@echo "CodeSeeq bridge install mode: $(OPENRESPONSES_INSTALL)"
	@echo "CodeSeeq bridge source dir: $(OPENRESPONSES_SOURCE)"
	@echo "CodeSeeq bridge repo: $(OPENRESPONSES_REPO)"
	@echo "CodeSeeq bridge docs: $(OPENRESPONSES_DOCS)"
	@echo "CodeSeeq bridge ref: $(OPENRESPONSES_REF)"
	@if test -d open-responses/.git; then \
		git -C open-responses remote -v | sed -n '1,2p'; \
		git -C open-responses rev-parse --short HEAD; \
		git -C open-responses remote -v | rg -q 'open-responses/open-responses' || (echo "open-responses remote mismatch" >&2; exit 1); \
	else \
		echo "local open-responses source not included in this package; CodeSeeq uses npm/local bridge runtime; see docs."; \
	fi
	@if test -d codex/.git; then \
		echo "Codex source dir: ./codex"; \
		git -C codex remote -v | sed -n '1,2p'; \
		git -C codex rev-parse --short HEAD; \
	else \
		echo "local codex source not included in this package; CodeSeeq uses the installed Codex CLI in the image/host; see docs."; \
	fi
	@echo "Note: upstream open-responses CLI is Docker/Compose-oriented; CodeSeeq keeps single-container runtime by running an in-container local bridge process."

# Legacy alias for backward compatibility.
.PHONY: inspect-openresponses
inspect-openresponses: inspect-bridge

.PHONY: inspect-bridge-strict
inspect-bridge-strict:
	@test -d open-responses/.git || (echo "CodeSeeq bridge source checkout missing" >&2; exit 1)
	@git -C open-responses remote -v | rg -q 'open-responses/open-responses' || (echo "CodeSeeq bridge remote mismatch" >&2; exit 1)
	@test -d codex/.git || (echo "codex source checkout missing" >&2; exit 1)
	@git -C codex remote -v | rg -q 'openai/codex' || (echo "codex remote mismatch" >&2; exit 1)

# Legacy alias for backward compatibility.
.PHONY: inspect-openresponses-strict
inspect-openresponses-strict: inspect-bridge-strict

.PHONY: models
models:
	CODESEEQ_MODEL=$(MODEL) CODESEEQ_THINKING=$(THINKING) IMAGE=$(IMAGE) CONTAINER=$(CONTAINER) ./codeseeq models

.PHONY: doctor
doctor:
	CODESEEQ_MODEL=$(MODEL) CODESEEQ_THINKING=$(THINKING) IMAGE=$(IMAGE) CONTAINER=$(CONTAINER) ./codeseeq doctor

.PHONY: ping
ping:
	@test -n "$$DEEPSEEK_API_KEY" || (echo "DEEPSEEK_API_KEY is required" >&2; exit 1)
	CODESEEQ_MODEL=$(MODEL) CODESEEQ_THINKING=$(THINKING) IMAGE=$(IMAGE) CONTAINER=$(CONTAINER) ./codeseeq ping

.PHONY: ping-stream
ping-stream:
	@test -n "$$DEEPSEEK_API_KEY" || (echo "DEEPSEEK_API_KEY is required" >&2; exit 1)
	CODESEEQ_MODEL=$(MODEL) CODESEEQ_THINKING=$(THINKING) IMAGE=$(IMAGE) CONTAINER=$(CONTAINER) ./codeseeq ping-stream

.PHONY: ping-web
ping-web:
	@test -n "$$DEEPSEEK_API_KEY" || (echo "DEEPSEEK_API_KEY is required" >&2; exit 1)
	@test -n "$$BRAVE_API_KEY" || (echo "BRAVE_API_KEY is required" >&2; exit 1)
	CODESEEQ_MODEL=$(MODEL) CODESEEQ_THINKING=$(THINKING) IMAGE=$(IMAGE) CONTAINER=$(CONTAINER) ./codeseeq ping-web

.PHONY: ping-docs
ping-docs:
	@test -n "$$DEEPSEEK_API_KEY" || (echo "DEEPSEEK_API_KEY is required" >&2; exit 1)
	@test -n "$$UNSTRUCTURED_API_KEY" || (echo "UNSTRUCTURED_API_KEY is required" >&2; exit 1)
	CODESEEQ_MODEL=$(MODEL) CODESEEQ_THINKING=$(THINKING) IMAGE=$(IMAGE) CONTAINER=$(CONTAINER) ./codeseeq ping-docs

.PHONY: prompt
prompt:
	@test -n "$$DEEPSEEK_API_KEY" || (echo "DEEPSEEK_API_KEY is required" >&2; exit 1)
	@test -n "$(PROMPT)" || (echo "PROMPT is required" >&2; exit 1)
	CODESEEQ_MODEL=$(MODEL) CODESEEQ_THINKING=$(THINKING) IMAGE=$(IMAGE) CONTAINER=$(CONTAINER) ./codeseeq run "$(PROMPT)"

.PHONY: bridge-check
bridge-check:
	@echo "Checking bridge Python syntax..."
	python3 -c "import py_compile; py_compile.compile('bin/codeseeq-bridge.py', doraise=True)" && echo "  bridge.py: OK"
	@echo "Checking bridge can import and execute..."
	python3 -c "import importlib.util; spec = importlib.util.spec_from_file_location('codeseeq_bridge', 'bin/codeseeq-bridge.py'); module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); print('  imports: OK')" || echo "  imports: check dependencies (fastapi, uvicorn, httpx)"
	@echo "Checking wrapper Bash syntax..."
	bash -n codeseeq && echo "  codeseeq: OK"

.PHONY: bridge-process-smoke
bridge-process-smoke:
	./scripts/smoke-bridge-process.sh

.PHONY: run
run:
	@test -n "$$DEEPSEEK_API_KEY" || (echo "DEEPSEEK_API_KEY is required" >&2; exit 1)
	CODESEEQ_MODEL=$(MODEL) CODESEEQ_THINKING=$(THINKING) IMAGE=$(IMAGE) CONTAINER=$(CONTAINER) ./codeseeq

.PHONY: shell
shell:
	CODESEEQ_MODEL=$(MODEL) CODESEEQ_THINKING=$(THINKING) IMAGE=$(IMAGE) CONTAINER=$(CONTAINER) ./codeseeq shell

.PHONY: smoke
smoke:
	IMAGE=$(IMAGE) CONTAINER=$(CONTAINER) ./scripts/smoke-all.sh

.PHONY: package
package:
	./scripts/package.sh

.PHONY: package-check
package-check:
	@test -n "$(ZIP)" || (echo "Usage: make package-check ZIP=/path/to/archive.zip" >&2; exit 1)
	./scripts/package.sh --check-archive "$(ZIP)"

.PHONY: clean-artifacts
clean-artifacts:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete 2>/dev/null || true
	find . -type f -name '*.pyo' -delete 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	rm -f /tmp/bridge-smoke.log 2>/dev/null || true
	rm -f /tmp/codeseeq-*.zip 2>/dev/null || true
	@echo "Artifacts cleaned"

.PHONY: clean
clean:
	-$(CONTAINER) image rm -f $(IMAGE)

.PHONY: check
check:
	./scripts/check.sh
