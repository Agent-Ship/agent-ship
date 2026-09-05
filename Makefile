# AgentShip demo — dev workflow.
#
# This demo is LIVE: `make demo` and `make test` call the real OpenAI API and need a
# real OPENAI_API_KEY. Load one from a local .env (copy .env.example to .env and set
# it) or from the sibling framework checkout:
#
#     set -a; source ../agentship/.env; set +a
#
# Until the framework is published to PyPI, `make install` installs it editable from
# the sibling monorepo checkout (../agentship/packages/*).

STUDIO_URL ?= http://localhost:7005/studio
VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: help install test test-live test-docker record demo demo-multiagent demo-observability demo-service ask run ui docker-setup docker-build docker-build-clean docker-up docker-down docker-restart docker-logs docker-reload clean

## help: list every target with what it does (also what you get by running plain `make`).
##   Every target already carried a `## name: description` line; nothing printed them, so
##   the only way to find a target was to read the file.
.DEFAULT_GOAL := help
help:
	@echo "AgentShip demo — available targets:"
	@echo ""
	@grep -E '^## [a-z][a-z0-9-]*:' $(MAKEFILE_LIST) \
		| sed -e 's/^## //' -e 's/:/§/' \
		| awk -F'§' '{printf "  \033[1m%-20s\033[0m%s\n", $$1, $$2}'
	@echo ""
	@echo "  Start here:  make docker-setup   (first run)"
	@echo "               make docker-up      (thereafter)"

## install: create .venv and install the framework editable-local + test tooling
install:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-dev.txt

## test: run the suite by REPLAYING committed cassettes — no key, no network, no spend.
##   This is the CI gate and what a fresh clone gets. A missing cassette fails loudly.
test:
	$(VENV)/bin/pytest -q

## test-live: run the same tests against the real API (needs OPENAI_API_KEY; costs money).
##   Same test bodies as `make test` — this is the drift check against the live providers.
test-live:
	$(VENV)/bin/pytest -q --live

## record: refresh the committed cassettes from real calls (needs OPENAI_API_KEY; costs money).
##   Credentials are redacted on write, so a recorded cassette is safe to commit.
record:
	$(VENV)/bin/pytest -q --live --record-mode=once

## demo: SEE every capability run LIVE against OpenAI, one labeled block each.
##   Needs a real OPENAI_API_KEY; exits non-zero if unset or if any slice fails.
demo:
	$(PY) demos/run_all.py

## demo-multiagent: PROVE the sub-agents are really called. One command: it fans a
##   question out to all 3 specialist sub-agents in parallel and prints each one's live
##   answer + the resolver's pick, then a green PASS asserting all 3 actually ran.
##   Needs a real OPENAI_API_KEY (in .env or the shell).
demo-multiagent:
	$(VENV)/bin/pytest tests/test_triage.py::test_triage_panel_fans_out_to_multiple_sub_agents_in_parallel \
		-q -s -p no:cacheprovider --log-cli-level=INFO --log-cli-format="  | %(message)s"

## demo-observability: SEE a full OTel trace fall out of one YAML block. Runs the traced
##   calculator agent for a live turn; the span tree (agent→node→model→tool) prints to stderr.
##   Needs a real OPENAI_API_KEY. Ship it to Opik/LangFuse/LangSmith by editing the YAML's
##   exporters: and setting that backend's keys (see tests/test_observability.py, .env.example).
demo-observability:
	$(PY) demos/observability.py

## demo-service: SEE the served /v1 surface answer over all three transports. Boots the real
##   `agentship serve` on a loopback port, then calls invoke (JSON), stream (SSE, frame by
##   frame) and the /live WebSocket, and shows the 401 / 403 / RFC-9457 error envelope.
##   Needs NO key — the served agent runs on the echo engine.
demo-service:
	$(PY) demos/serve_and_call.py

## docker-up: run the demo as a CONTAINER you can call from outside (API on :7005).
##   Builds the image (AgentShip installed as a package, demo agents copied in) and starts
##   it with a Postgres so durable agents keep their checkpoints across a restart.
##   Model keys are read from your .env; the echo-engine agents work without any key.
docker-up:
	AGENTSHIP_BUILD=$$(date +%Y%m%d-%H%M%S) docker compose up -d --build
	@echo ""
	@echo "  Studio   $(STUDIO_URL)   <- the UI; API key is 'dev'"
	@echo "  API      http://localhost:7005"
	@echo "  Swagger  http://localhost:7005/docs"
	@echo "  Health   curl http://localhost:7005/healthz"
	@echo "  Agents   curl -H 'Authorization: Bearer dev' http://localhost:7005/v1/agents"
	@echo ""

## docker-down: stop the containers (the Postgres volume survives).
docker-down:
	docker compose down

## docker-logs: follow the demo container's logs.
docker-logs:
	docker compose logs -f demo

## docker-build: rebuild the image without starting anything.
docker-build:
	DOCKER_BUILDKIT=1 docker compose build

## docker-reload: hard reload — rebuild the image and restart everything.
docker-reload:
	docker compose down
	DOCKER_BUILDKIT=1 docker compose build
	docker compose up -d
	@echo ""
	@echo "  Studio   $(STUDIO_URL)   (hard-refresh once: the browser caches it)"

## docker-setup: first run — create .env if missing, then build and start.
##   Separate from docker-up because it is the only target that writes a file into your
##   checkout; everything else is safe to run repeatedly without thinking about it.
docker-setup:
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "Created .env from .env.example — put your OPENAI_API_KEY in it."; \
	else \
		echo "Keeping the .env you already have."; \
	fi
	@$(MAKE) docker-up

## docker-restart: restart the containers WITHOUT rebuilding.
##   Use after changing an env var in docker-compose.yml or .env. If you changed Python
##   or a Studio file, you need docker-reload — the image bakes those in at build time.
docker-restart:
	docker compose restart
	@echo "Restarted. Code changes need 'make docker-reload' instead — this reuses the image."

## docker-build-clean: rebuild from scratch, ignoring every cached layer.
##   Slow, and only needed when a cached layer is the problem — a dependency that
##   resolved differently, or a stale package copied into the image.
docker-build-clean:
	DOCKER_BUILDKIT=1 docker compose build --no-cache

## test-docker: run the service tests against the CONTAINER, not an in-process app.
##   Proves the built image actually serves: it boots, binds, authenticates and streams.
##   A green `make test` cannot tell you that — it never builds the image.
test-docker:
	@curl -fsS http://localhost:7005/healthz >/dev/null 2>&1 || { \
		echo "Nothing is serving on :7005 — run 'make docker-up' first."; exit 1; }
	AGENTSHIP_BASE_URL=http://localhost:7005 $(VENV)/bin/pytest -q tests/test_container_smoke.py

## ask: give the multi-agent panel YOUR OWN task; watch each sub-agent get called live
##   and see the final merged response. Needs a real OPENAI_API_KEY.
##   usage: make ask INPUT="My bill is wrong and I feel dizzy — help?"
ASK_INPUT ?= My bill looks wrong and I feel dizzy — can you help?
ask:
	$(PY) demos/ask_multiagent.py "$(if $(INPUT),$(INPUT),$(ASK_INPUT))"

## run: run the demo agent for one real turn (needs a real OPENAI_API_KEY in .env)
##   usage: make run INPUT="Give one productivity tip."
INPUT ?=
run:
	$(VENV)/bin/agentship run agents/assistant.yaml --input "$(if $(INPUT),$(INPUT),Give one productivity tip.)"

## ui: open AgentShip Studio — the branded chat/debug UI the SERVICE serves at /studio.
##   Needs the service running (`make docker-up`). There is no UI in this repo: Studio ships
##   with agentship-service, so the demo never maintains its own.
ui:
	@echo "Opening AgentShip Studio at $(STUDIO_URL) (API key: dev)"
	@python3 -c "import webbrowser,sys; webbrowser.open(sys.argv[1])" $(STUDIO_URL)

## clean: remove the venv and caches
clean:
	rm -rf $(VENV) .pytest_cache tests/__pycache__
