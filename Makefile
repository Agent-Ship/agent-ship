# AgentShip developer entry points. See .spec-dev/ for the phase plan.
.PHONY: venv test run lint

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

# Create the dev venv and install the package editable with all extras + dev tools.
venv:
	python3.13 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e '.[all,dev]'

# Run the full offline test suite.
test:
	$(VENV)/bin/pytest -q

# Run the walking-skeleton example end to end.
run:
	$(VENV)/bin/agentship run examples/hello.yaml --input "hi"

# Lint with ruff.
lint:
	$(VENV)/bin/ruff check .
