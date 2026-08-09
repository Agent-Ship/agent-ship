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

.PHONY: install test demo run clean

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

## run: run the demo agent for one real turn (needs a real OPENAI_API_KEY in .env)
##   usage: make run INPUT="Give one productivity tip."
INPUT ?= Give one productivity tip.
run:
	$(VENV)/bin/agentship run agents/assistant.yaml --input "$(INPUT)"

## clean: remove the venv and caches
clean:
	rm -rf $(VENV) .pytest_cache tests/__pycache__
