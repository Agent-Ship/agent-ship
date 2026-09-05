# Contributing to AgentShip

Thanks for your interest in contributing. AgentShip's design rule is
*integrate best-of-breed libraries behind small, stable seams — never reinvent
them*, and its correctness rule is *declare, don't fake*: every capability an
engine advertises must be provable by the shipped conformance grid. Contributions
that respect both rules are the easiest to merge.

## Ways to contribute

- Report a bug or a spec violation the drift guards missed.
- Add a capability cell to `agentship/conformance/`.
- Add or improve an engine adapter (LangGraph, ADK, Pydantic AI, or a new one).
- Improve documentation under `docs/`.

## Development setup

Requires Python 3.13+.

```bash
git clone https://github.com/<your-fork>/agentship.git
cd agentship
python -m venv .venv && source .venv/bin/activate
pip install -e "packages/agentship-core[dev]" \
            -e "packages/agentship-langgraph[dev]" \
            -e "packages/agentship-cli"
```

Run the full test suite (packages + conformance grid):

```bash
make test        # equivalent to: pytest packages conformance
make lint        # ruff
agentship verify # offline, keyless honesty check
```

## Pull request checklist

- [ ] Tests cover the new behaviour, and `make test` passes locally.
- [ ] `agentship verify` still exits `0` (or, if you added an engine capability
      declaration, the corresponding `prove` cell is green).
- [ ] Public surface changes are reflected in `docs/capabilities/` and, where
      relevant, an ADR in `docs/decisions/`.
- [ ] `ruff` clean.

## Reporting security issues

Please do not open a public issue. Email the maintainer directly (address in
`pyproject.toml`).

## Code of conduct

By participating in this project you agree to abide by the
[Code of Conduct](CODE_OF_CONDUCT.md).
