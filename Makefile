# AgentShip developer entry points. See .spec-dev/ for the phase plan.
# This is a monorepo of packages under packages/*/ — install them editable
# and test/lint across the whole tree from the repo root.
.PHONY: venv install test run serve lint docs-serve docs-build docs-clean docker-build docker-up docker-down

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

# Create the dev venv and install every package editable + dev tooling.
venv:
	python3.13 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(MAKE) install
	$(PIP) install 'pytest>=8' 'pytest-asyncio>=0.23' 'pytest-recording>=0.13' 'ruff>=0.6'
	$(PIP) install 'mkdocs-material>=9.5' 'mkdocs-exclude>=1.0'

# Install every package editable (core + langgraph engine + service + CLI + observability).
install:
	$(PIP) install -e packages/agentship-core -e packages/agentship-langgraph \
		-e packages/agentship-service -e packages/agentship-cli \
		-e packages/agentship-observability

# Run the full offline test suite across every package + the conformance matrix.
test:
	$(VENV)/bin/pytest packages conformance -q

# Run the walking-skeleton example end to end.
run:
	$(VENV)/bin/agentship run examples/hello.yaml --input "hi"

# Serve the agents in ./agents over the secure /v1 surface (doctor-gated). A convenience
# alias for `agentship serve`; the CLI command is the supported contract.
serve:
	$(VENV)/bin/agentship serve --agents-dir examples --host 127.0.0.1 --port 8000

# Lint with ruff across the whole tree.
lint:
	$(VENV)/bin/ruff check .

# --- Deployment (parity carry-forward from the old repo) -----------------------------
# Build the service image and bring it up / down. `docker-up` boots the API on :8000.
# docs-serve: browse the docs at http://localhost:8000 with live reload.
#   The old repo served Sphinx at :7001/docs; this is the same idea on MkDocs, since our
#   docs are already Markdown. Port 8000 stays clear of `make serve` (7001) and of the
#   Docker-published ports in docker-compose.yml (7002 is taken by a container).
#   Override if you need to: make docs-serve DOCS_PORT=8080
DOCS_PORT ?= 8000
docs-serve:
	$(VENV)/bin/mkdocs serve --dev-addr localhost:$(DOCS_PORT)

# docs-build: render the static site into site/. Runs in strict mode, so a broken
#   internal link fails the build instead of shipping a dead link to a reader.
docs-build:
	$(VENV)/bin/mkdocs build

docs-clean:
	rm -rf site

docker-build:
	DOCKER_BUILDKIT=1 docker compose build

docker-up:
	docker compose up -d

docker-down:
	docker compose down
