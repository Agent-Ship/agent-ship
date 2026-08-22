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

.PHONY: install test demo demo-multiagent demo-observability ask run ui clean

## install: create .venv and install the framework editable-local + test tooling
install:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-dev.txt

## test: run the LIVE test suite — every test calls the real API (needs OPENAI_API_KEY).
##   Without a key the tests skip cleanly.
test:
	$(VENV)/bin/pytest -q

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

## ui: open a browser chat to drive ANY agent — pick one and send a request. Real agents chat
##   and research on demand; the note-taker pauses for write approval (reply yes/no). No script.
##   Needs a real OPENAI_API_KEY; FIRECRAWL_API_KEY (free, firecrawl.dev) optional for real
##   web search + page scraping.
ui:
	$(PY) demos/chat_ui.py

## clean: remove the venv and caches
clean:
	rm -rf $(VENV) .pytest_cache tests/__pycache__
