# Conformance matrix — `engine × capability`

The honesty backstop for AgentShip's multi-engine promise (DESIGN §11, principle
"declare, don't fake"). It proves that **every capability an engine declares
actually works, and every capability it does not declare is rejected fail-fast** —
so a spec is never silently degraded and an engine can never over-claim.

## The rule

For each registered engine and each capability, exactly one cell runs:

| Engine declares the capability? | Cell that runs | Passes when… |
|---|---|---|
| **yes** | positive "prove" cell | the capability genuinely works (build a real agent, drive it) |
| **no** | negative "reject" cell | requesting it in a spec raises `CapabilityError` at build time |

A **declared-but-red** cell is a build failure. That is the entire point: it is
impossible to ship an engine that claims a capability it does not implement.

## Layout

| File | Role |
|---|---|
| `capabilities.py` | the capability catalogue (`CAPABILITIES`) — **the main extension point** |
| `engines.py` | per-engine offline harness (fake models) so cells never hit the network |
| `test_matrix.py` | the parametrized grid: one cell per (engine, capability) |
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
   entry to `OFFLINE_HARNESSES` in `engines.py` mapping its name to a context
   manager that swaps in a fake model. A model-free engine (like `echo`) needs
   nothing — the default is a no-op.

That is the whole change. The engine's declared capabilities now each get a real
positive cell, and everything it does not declare gets a rejection cell.

## Adding a capability

Append one `Capability(...)` to `CAPABILITIES` in `capabilities.py`:

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

```bash
env -u OPENAI_API_KEY -u ANTHROPIC_API_KEY -u GEMINI_API_KEY -u GOOGLE_API_KEY \
    pytest conformance -q
```

Fully offline — no network, no API keys. Unsetting the keys above is a belt-and-
braces guard that a cell never silently reaches a provider.
