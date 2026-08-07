# The demo repo (`agentship-demo/`)

A **separate, forkable project** that uses AgentShip strictly as a **pinned pip
dependency** — the end-to-end proof that the *published packages* actually work (it catches
packaging bugs the in-tree suite can't: a missing entry-point, an unwired extra, an import
that only works from the source tree). Lives at
`/Users/harshuljain/work/projects/agentship/agentship-demo` as **its own git repo**.

## examples/ vs the demo repo (no duplication)
- **Framework `examples/`** (in the `agentship` repo): one tiny artifact per capability,
  each cassette-tested. "Here is capability X in isolation."
- **Demo repo**: one cohesive, realistic app composed from many capabilities. "Here is a
  real app built from X+Y+Z." It never re-teaches a single capability; examples never grow
  into an app.

## Structure
```
agentship-demo/                 # its own git repo
  pyproject.toml                # pins agentship==X.Y.Z (+[langgraph]); a dev extra may use ../agentship editable
  agents/*.yaml                 # the demo's agents (single agent now; supervisor at framework ≥ P6)
  README.md                     # run instructions, matched to the pinned version
  .env.example                  # committed; real .env gitignored
  .gitignore
  tests/
    cassettes/
    test_smoke.py               # runs the demo's agents against recorded cassettes — no key, deterministic
  .github/workflows/ci.yml      # pip install agentship[...]==<pin>; pytest → replays cassette
```

## Rules (so it can't rot)
- **Install by pin**: users `pip install "agentship[langgraph]==X.Y.Z"`. A dev/editable mode
  may install `-e ../agentship/packages/*` to dogfusing unreleased changes.
- **Smoke test in CI**: `test_smoke.py` loads the demo's agents, runs them, and replays a
  committed cassette — keyless, deterministic, on every push. If a package rename,
  entry-point drop, or spec change breaks the demo, its CI goes red. (Nightly keyed job
  re-records against real providers for drift.)
- **Capability rule**: the demo may only use capabilities shipped in its **pinned** framework
  version. A supervisor demo can't ship until framework ≥ Phase 6.
- **Per-phase sync**: each framework phase that adds a user-visible capability adds a demo
  slice *when it belongs in a real app*, always with a cassette test, and bumps the pin. The
  demo smoke test staying green is part of that phase's Definition of Done.

## Current state to fix (first demo task)
Today `agentship-demo/` is **not a git repo** and holds files copied from the dead old repo:
a stale `.env` (references an agent-directory scanner that doesn't exist), a `team.yaml`
using `template: supervisor` (a **Phase-6** feature, on a Phase-1 framework), and a README
whose `agentship run team` command can't work (the CLI needs a file path). First task:
`git init`, add `pyproject`(pin)/`.env.example`/`.gitignore`, **rewrite to a single-agent
demo** matching the current framework, and add the cassette smoke test.
