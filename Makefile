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

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: install test test-live record demo demo-multiagent demo-observability demo-service ask run ui docker-build docker-up docker-down docker-logs docker-reload clean

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
	docker compose up -d --build
	@echo ""
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

## ui: open a browser chat that drives the RUNNING SERVICE over HTTP — nothing runs in-process.
##   The agent picker is the service's own `GET /v1/agents`; a turn is `:stream` or `:invoke`;
##   a paused run is continued with `:resume`; a 401/403 is shown as its problem+json.
##   REQUIRES the service to be up first: `make docker-up` (http://localhost:7005).
##   Override with AGENTSHIP_BASE_URL / AGENTSHIP_API_KEY (defaults: that URL and the `dev` key).
ui:
	$(PY) demos/chat_ui.py

## clean: remove the venv and caches
clean:
	rm -rf $(VENV) .pytest_cache tests/__pycache__
