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

# One stamp per `make` invocation, baked into the image and reported by /healthz, so you
# can tell which build is answering without shelling into the container. `:=` so every
# target in one run agrees on it. It is the LAST thing the Dockerfile sets, so a new
# stamp invalidates only that layer — passing a fresh one does not force a real rebuild.
#
# Every target that builds must pass it. docker-build and docker-reload did not, so a
# reload left /healthz reporting `build: dev` and the staleness question unanswerable —
# which is the whole reason the stamp exists.
AGENTSHIP_BUILD := $(shell date +%Y%m%d-%H%M%S)

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
	AGENTSHIP_BUILD=$(AGENTSHIP_BUILD) docker compose up -d --build
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
	AGENTSHIP_BUILD=$(AGENTSHIP_BUILD) DOCKER_BUILDKIT=1 docker compose build

## docker-reload: hard reload — rebuild the image and restart everything.
docker-reload:
	docker compose down
	AGENTSHIP_BUILD=$(AGENTSHIP_BUILD) DOCKER_BUILDKIT=1 docker compose build
	AGENTSHIP_BUILD=$(AGENTSHIP_BUILD) docker compose up -d
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
	AGENTSHIP_BUILD=$(AGENTSHIP_BUILD) DOCKER_BUILDKIT=1 docker compose build --no-cache

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
##   Starts the service first if it is not already up. There is no UI in this repo: Studio
##   ships with agentship-service, so the demo never maintains its own.
##
##   This used to open a browser unconditionally. With nothing listening on :7005 that is a
##   dead tab and no explanation — the target appeared broken when the only problem was that
##   nobody had run `make docker-up` yet.
##
##   It then used to SKIP the build whenever anything answered on :7005, printing "Service
##   already up." A container from three days ago answers that check exactly as well as a
##   current one, so the target happily opened Studio on a stale image — a framework change
##   you had just made, or even a released version bump, was simply invisible. It now always
##   builds. The image bakes the framework in at build time, so "is something listening?"
##   was never the question worth asking; "is it running what is in the checkout?" is.
##
##   What that costs, measured, when nothing changed: the build itself is ~1s because every
##   layer is cached, but the stamp below is new on each run, so the image is new, so compose
##   recreates the container — about 4s — and the service then takes ~11s to answer /healthz.
##   Call it 15-30s to open Studio, against 0s before.
##
##   That is the deliberate trade: the old behaviour cost nothing and was wrong, serving a
##   three-day-old image with no hint it was doing so. If the restart becomes annoying while
##   iterating on agents rather than the framework, `make docker-restart` still reuses the
##   image, and deriving the stamp from the sibling checkout's content instead of the clock
##   would make an unchanged run a true no-op.
ui:
	@echo "Building and starting the service (cached build ~1s; the service takes ~15s to boot)…"
	@$(MAKE) --no-print-directory docker-up
	@printf "Waiting for the service"
	@for i in $$(seq 1 60); do \
		if curl -fsS --max-time 2 http://localhost:7005/healthz >/dev/null 2>&1; then \
			echo " ready."; break; \
		fi; \
		printf "."; sleep 2; \
	done
	@if ! curl -fsS --max-time 2 http://localhost:7005/healthz >/dev/null 2>&1; then \
		echo ""; \
		echo "The service did not come up. See what it said:  make docker-logs"; \
		exit 1; \
	fi
	@printf "  Serving build "; \
	curl -fsS --max-time 2 http://localhost:7005/healthz \
		| sed -n 's/.*"build":"\([^"]*\)".*/\1/p'
	@echo ""
	@echo "  Studio   $(STUDIO_URL)"
	@echo "  API key  dev        <- paste this when Studio asks"
	@echo ""
	@python3 -c "import webbrowser,sys; webbrowser.open(sys.argv[1])" $(STUDIO_URL)

## gateway: run an agent whose model is served by YOUR OWN inference server.
##   Needs an OpenAI-compatible endpoint on :8080 — vLLM, a LiteLLM proxy, TGI, SGLang —
##   and the demo container running (`make docker-up`).
##   Nothing in AgentShip was added for this: `params.api_base` has always pointed LiteLLM
##   at a custom endpoint. See agents/local_gateway.yaml.
gateway:
	@if ! curl -fsS --max-time 3 http://127.0.0.1:8080/v1/models >/dev/null 2>&1; then \
		echo "Nothing is serving on http://127.0.0.1:8080/v1"; \
		echo "Start an OpenAI-compatible server, e.g.:"; \
		echo "  vllm serve Qwen/Qwen2.5-7B-Instruct-AWQ --port 8080"; \
		exit 1; \
	fi
	@echo "Models the gateway advertises:"
	@curl -s --max-time 5 http://127.0.0.1:8080/v1/models | python3 -c "import sys,json;[print('  '+m['id']) for m in json.load(sys.stdin)['data']]"
	@echo ""
	@# Run INSIDE the container, because that is where the spec's api_base resolves:
	@# host.docker.internal reaches your machine from Docker, and means nothing in your
	@# shell. One URL that works in both places does not exist, so the command moves
	@# rather than the config.
	docker compose exec -T demo agentship run agents/local_gateway.yaml \
		--input "$(if $(INPUT),$(INPUT),In one sentence: what runs you?)"
.PHONY: gateway

## clean: remove the venv and caches
clean:
	rm -rf $(VENV) .pytest_cache tests/__pycache__
