# Contributing to AgentShip

Thanks for your interest in contributing. AgentShip's design rule is
*integrate best-of-breed libraries behind small, stable seams — never reinvent
them*, and its correctness rule is *declare, don't fake*: every capability an
engine advertises must be provable by the shipped conformance grid. Contributions
that respect both rules are the easiest to merge.

## Reporting a problem, or asking for help

Three doors, and picking the right one is most of what makes a report useful:

| | |
|---|---|
| **Something is broken** | [Open a bug report](https://github.com/Agent-Ship/agent-ship/issues/new?template=bug_report.yml). The form asks for the output of `agentship doctor` and `agentship verify` — those two answer most of what we would otherwise have to come back and ask. |
| **A question, or not sure it is a bug** | [Discussions](https://github.com/Agent-Ship/agent-ship/discussions). Usage, "is this possible", design arguments. No question is too small, and a question that turns out to be a bug gets promoted to one. |
| **A security vulnerability** | [Report it privately](https://github.com/Agent-Ship/agent-ship/security/advisories/new) — never as a public issue. See [Reporting security issues](#reporting-security-issues). |

Documentation that is wrong or describes code that has since changed is a **bug**, not a
chore, and has [its own form](https://github.com/Agent-Ship/agent-ship/issues/new?template=documentation.yml).
Drift between the docs and the code is the failure this project hits most often.

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

`main` is protected: every change lands by pull request, needs one approval from another
maintainer, and needs both CI checks green. Nobody pushes to `main` directly.

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

## Releasing

Several distributions, one version, released together. See [docs/RELEASING.md](docs/RELEASING.md).

```bash
python scripts/versions.py --check   # never hand-edit a version
```

While AgentShip is on `0.x` the API can break between minor versions. Pin exactly if that
matters to you.
