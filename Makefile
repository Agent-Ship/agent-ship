# AgentShip demo — dev workflow.
#
# Until the framework is published to PyPI, `make install` installs it editable from
# the sibling monorepo checkout (../agentship/packages/*). Once published, the
# pinned form is `pip install "agentship[langgraph]==0.0.1"` (see pyproject.toml).

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: install test run clean

## install: create .venv and install the framework editable-local + test tooling
install:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-dev.txt

## test: run the keyless cassette smoke test (no API key needed)
test:
	env -u OPENAI_API_KEY $(VENV)/bin/pytest -q

## run: run the demo agent for one turn (needs a real OPENAI_API_KEY in .env)
##   usage: make run INPUT="Give one productivity tip."
INPUT ?= Give one productivity tip.
run:
	$(VENV)/bin/agentship run agents/assistant.yaml --input "$(INPUT)"

## clean: remove the venv and caches
clean:
	rm -rf $(VENV) .pytest_cache tests/__pycache__
