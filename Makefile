# AgentShip developer entry points. See .spec-dev/ for the phase plan.
# This is a monorepo of packages under packages/*/ — install the three editable
# and test/lint across the whole tree from the repo root.
.PHONY: venv install test run lint

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

# Create the dev venv and install every package editable + dev tooling.
venv:
	python3.13 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(MAKE) install
	$(PIP) install 'pytest>=8' 'pytest-asyncio>=0.23' 'pytest-recording>=0.13' 'ruff>=0.6'

# Install the three packages editable (core + langgraph engine + CLI).
install:
	$(PIP) install -e packages/agentship-core -e packages/agentship-langgraph -e packages/agentship-cli

# Run the full offline test suite across every package.
test:
	$(VENV)/bin/pytest packages -q

# Run the walking-skeleton example end to end.
run:
	$(VENV)/bin/agentship run examples/hello.yaml --input "hi"

# Lint with ruff across the whole tree.
lint:
	$(VENV)/bin/ruff check .
