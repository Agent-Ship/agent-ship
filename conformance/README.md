# Conformance matrix — `engine × capability`

The honesty backstop for AgentShip's multi-engine promise (DESIGN §11, principle
"declare, don't fake"). It proves that **every capability an engine declares
actually works, and every capability it does not declare is rejected fail-fast** —
so a spec is never silently degraded and an engine can never over-claim.

The catalogue is no longer test-only: it now ships as the vendor-free, importable
`agentship.conformance` module in `agentship-core` (`CAPABILITIES`,
`run_capability_grid`, `CellResult`, `DEFERRED_CAPABILITIES`). The vendor offline
harness (fake models + the `hitl` factory) lives in `agentship_langgraph.testing`
and is passed *into* the grid, so the catalogue itself imports no
langchain/langgraph/litellm. The files in this `conformance/` test tree are now
thin wrappers that parametrize over that shipped grid. The user-facing runner over
the exact same grid is `agentship verify` (see the
[Verify & conformance](../docs/capabilities/verify-and-conformance.md) page).

## The rule

For each registered engine and each capability, exactly one cell runs:

| Engine declares the capability? | Cell that runs | Passes when… |
|---|---|---|
| **yes** | positive "prove" cell | the capability genuinely works (build a real agent, drive it) |
| **no** | negative "reject" cell | requesting it in a spec raises `CapabilityError` at build time |

A **declared-but-red** cell is a build failure. That is the entire point: it is
impossible to ship an engine that claims a capability it does not implement.

## Layout

The catalogue and offline harness now ship outside this tree:

| Ships in | Role |
|---|---|
| `agentship.conformance` (`agentship-core`) | the capability catalogue (`CAPABILITIES`), `run_capability_grid`, `CellResult`, `DEFERRED_CAPABILITIES` — **the main extension point**, vendor-free and importable |
| `agentship_langgraph.testing` | the per-engine offline harness (`OFFLINE_HARNESSES` / `offline`, fake models) + the `hitl` `build_hitl_agent` factory, passed into the grid so cells never hit the network |

The test tree here holds thin wrappers over that grid:

| File | Role |
|---|---|
| `test_matrix.py` | the parametrized grid: one cell per (engine, capability), over `run_capability_grid` |
| `test_engine_conformance.py` | engine-parametrized behavioural cells (P01 T7): `capability_fail_fast`, `run_stream_parity`, `context_isolation`, `router_purity`, and the P02-deferred `durable_resume_after_kill` (xfail with reason `P02: durability`) |
| `test_over_declaration.py` | meta-test: a throwaway liar engine proves the matrix catches over-claims |
| `test_echo_vendor_free.py` | the neutrality proof: `echo` is the vendor-free, non-LangGraph engine implementing every base class with zero langchain/langgraph/litellm import — swap the engine, the base classes don't move |
| `conftest.py` | live engine discovery + a registry snapshot guard (throwaway engines can't leak) |

The grid is built live from the plugin registry (`ENGINES.names()`), so any
installed engine is covered automatically — there is no hand-maintained engine list.

## Adding an engine

1. Register it via its `[project.entry-points."agentship.engines"]` line (as
   `echo` and `langgraph` do). Discovery picks it up; the matrix adds a full
   column of cells for it with no edit here.
2. If the engine needs a model (i.e. would otherwise reach the network), add one
   entry to `OFFLINE_HARNESSES` in the engine package's `testing` module (e.g.
   `agentship_langgraph.testing`) mapping its name to a context manager that swaps
   in a fake model. A model-free engine (like `echo`) needs nothing — the default
   is a no-op.

That is the whole change. The engine's declared capabilities now each get a real
positive cell, and everything it does not declare gets a rejection cell.

## Adding a capability

Append one `Capability(...)` to `CAPABILITIES` in `agentship.conformance`:

- `name` — the `EngineCapabilities` field this row covers.
- `declared` — reads that field and returns whether the engine claims it.
- `request_spec` — builds an `AgentSpec` that *requests* the capability (used by
  the rejection cell). Set to `None` if no spec field gates it yet — the matrix
  then skips the negative cell (nothing to reject through) but still runs the
  positive cell when the capability is declared.
- `prove` — the positive cell: given a built agent on an engine that declares the
  capability, assert it genuinely works (raise on failure). For a not-yet-built
  capability, make `prove` raise with an explanatory message; the moment an engine
  declares it, the matrix will demand a real proof (this is what stops premature
  declarations).

## Running

As a test (the full parametrized tree):

```bash
env -u OPENAI_API_KEY -u ANTHROPIC_API_KEY -u GEMINI_API_KEY -u GOOGLE_API_KEY \
    pytest conformance -q
```

As the user-facing report over the same grid:

```bash
agentship verify
```

Both run fully offline — no network, no API keys. Unsetting the keys above is a
belt-and-braces guard that a cell never silently reaches a provider. `agentship
verify` wraps the grid in a themed report (spec validation, observability, service
contracts, A2A interop) and honestly SKIPs any section whose optional dependency
or target is absent — see the
[Verify & conformance](../docs/capabilities/verify-and-conformance.md) page.
