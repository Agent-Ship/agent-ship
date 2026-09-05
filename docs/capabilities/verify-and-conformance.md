# Verify & conformance

AgentShip ships *verifiable agents*: every engine either genuinely honours a
capability it declares, or it fails fast when asked for one it does not. The
`agentship verify` command is the one user-facing command that proves this — a
themed honesty report you can run in CI with no provider keys.

## What "declare, don't fake" means

AgentShip is multi-engine: a spec names an `engine:` and requests capabilities
(`streaming`, `tool_calling`, `durability`, `hitl`, …). The framework's core
promise (DESIGN §11) is that these are never bluffed:

- **No over-claims.** If an engine's `EngineCapabilities` declares a capability,
  that capability must genuinely work — proven by driving a real agent, not by a
  green flag.
- **No silent degrade.** If an engine does *not* declare a capability, a spec that
  requests it must raise `CapabilityError` at build time — fail-fast, never a quiet
  no-op.

The honesty rule extends to the checks themselves: a check whose optional
dependency is not installed, or which has nothing to look at, reports **SKIPPED
with a reason** — never a fake green. Only a real over-claim, invalid spec, or
broken contract fails a run.

## The shipped `agentship.conformance` module

The capability catalogue used to be test-tree-only. It now ships as a
**vendor-free, importable** module in `agentship-core`:
`agentship.conformance`. It imports only `agentship.*`, `pydantic`, and the
standard library — never langchain/langgraph/litellm — so the catalogue stays
engine-neutral and any consumer (the `verify` CLI, an eval hook, your own CI)
can drive it with just the kernel installed.

### The engine × capability grid

For each registered engine and each capability, exactly one cell runs:

| Engine declares the capability? | Cell | Passes when… |
|---|---|---|
| **yes** | positive `prove` cell | the capability genuinely works (build a real agent, drive it) |
| **no** | negative `reject` cell | requesting it in a spec raises `CapabilityError` at build time |

A **declared-but-red** cell is a failure — that is the whole point: you cannot
ship an engine that claims a capability it does not implement. The grid is built
live from the plugin registry (`ENGINES.names()`), so every installed engine is
covered automatically — there is no hand-maintained engine list.

`CAPABILITIES` currently covers `streaming`, `structured_output`, `multi_agent`,
`durability`, `tool_calling`, and `hitl`. Capabilities with no cell yet are
listed explicitly in `DEFERRED_CAPABILITIES` (e.g. `cycles` → P02,
`multimodal_in` → P25, `live_bidi` → P10); a coverage guard requires every
`EngineCapabilities` field to be *either* covered by a cell *or* deferred here, so
a new field cannot slip past unproven.

### `run_capability_grid` and `CellResult`

```python
from agentship.conformance import run_capability_grid, CellResult

results: list[CellResult] = await run_capability_grid()
```

`run_capability_grid(engine_names=None, *, offline=None)` runs one cell per
`(engine, capability)` and returns a list of `CellResult`. Key guarantees:

- It **never raises** — every assertion or unexpected exception is captured into a
  failing `CellResult`, so the caller always gets a complete report.
- `engine_names` defaults to every registered engine; pass a subset to scope it.
- `offline` maps an engine name to a context manager entered around each *positive*
  cell — the vendor offline harness that swaps a real model for a fake so
  model-backed engines run with zero network calls. It is not applied to reject
  cells (those fail at build, before any run).

Each `CellResult` is a frozen dataclass:

| Field | Meaning |
|---|---|
| `engine` | engine name |
| `capability` | capability field name |
| `kind` | `"prove"` (declared → must work) or `"reject"` (undeclared → must fail fast) |
| `passed` | whether the cell held |
| `detail` | short human-readable reason (the assertion/error on failure, a summary on success) |

### The offline harness lives in the engine package

Because the catalogue is vendor-free, anything that needs a vendor type lives in
the engine package and is passed *into* the grid. For LangGraph that is
`agentship_langgraph.testing`:

- `offline(engine_name)` — the context-manager provider `run_capability_grid`
  takes as its `offline=` argument. It swaps `agentship_langgraph.models.resolve_model`
  for a deterministic `FakeListChatModel`. Model-free engines (`echo`) get a no-op.
- `build_hitl_agent()` — the `code:` factory the `hitl` cell builds through: a
  durable confirm/write graph that pauses at `interrupt()` before a side effect, so
  the cell can prove the engine genuinely surfaces the HITL interrupt rather than
  running straight through.

### Adding an engine or a capability

- **New engine:** register it via its `agentship.engines` entry-point; discovery
  adds a full column of cells with no edit to the catalogue. If it needs a model,
  add one entry to `OFFLINE_HARNESSES` in the engine package's `testing` module
  mapping its name to a fake-model context manager. A model-free engine needs
  nothing.
- **New capability:** append one `Capability(...)` to `CAPABILITIES` with `name`
  (the `EngineCapabilities` field), `declared` (reads that field), `request_spec`
  (builds a spec requesting it, or `None` if no gate-checked field expresses it
  yet — then the negative cell is skipped), and `prove` (the positive assertion;
  for a not-yet-built capability, make it raise so the moment an engine declares
  it the grid demands a real proof).

## The `agentship verify` command

```
agentship verify [--agents-dir DIR] [--live/--offline]
```

`verify` is the user-facing runner over the same grid, plus the framework's
cross-cutting contract checks. It assembles a `VerifyReport` of `Section`s and
exits non-zero only if a *present* section has a real failure; skipped sections
never fail the run. Today every section runs **fully offline** (the grid's fake-
model seam + the model-free `echo` engine), so it needs no provider keys.
`--live/--offline` is reserved for future live-provider checks and defaults to
`--offline`.

The sections:

- **`engine×capability grid`** — the conformance grid above; any failed `prove`
  cell is additionally named as an over-claim (`engine.capability`).
- **`spec validation`** — with `--agents-dir`, every `*.yaml` loads and passes the
  same capability gate `doctor` runs. Skipped (with a reason) when no dir is given.
- **`observability span-tree`** — one `echo` run emits the frozen root `agent`
  span (CONF-OBS-1). Skipped if the `agentship-observability` extra is absent.
- **`service contracts`** — with `--agents-dir` and the `agentship-service` extra,
  drives a `TestClient` over the real app to assert auth (401/403) and tenant
  isolation (a tenant cannot read/cancel another's task). Skipped otherwise.
- **`A2A interop`** — every `a2a.expose: true` spec renders a schema-valid, honest
  Agent Card whose `streaming` claim and `securitySchemes` match the engine.
  Skipped when nothing is exposed.

### Example report

Run bare (no project directory) — the grid and observability sections run; the
project-scoped sections skip honestly:

```
$ agentship verify
  engine×capability grid ...... 18/18 ✅
  spec validation ............. SKIPPED (no --agents-dir given (nothing to validate))
  observability span-tree ..... 4/4 ✅
  service contracts ........... SKIPPED (no --agents-dir given (nothing to serve))
  A2A interop ................. SKIPPED (no --agents-dir given (no agents to expose))
  → all declared capabilities proven, 0 over-claims
```

Point it at a project to light up the project sections:

```
$ agentship verify --agents-dir ./agents
  engine×capability grid ...... 18/18 ✅
  spec validation ............. 3/3 ✅
  observability span-tree ..... 4/4 ✅
  service contracts ........... 8/8 ✅
  A2A interop ................. SKIPPED (no spec exposes a2a (interop layer optional))
  → all declared capabilities proven, 0 over-claims
```

If an engine over-claims, the offending `prove` cell is named and the run exits
non-zero:

```
  engine×capability grid ...... 17/18 ❌
      - over-claim: langgraph.structured_output declared but not honoured
  ...
  → verification FAILED — 1 over-claim(s); see failures above
```

## How it differs from `make test` and from evals

- **`agentship verify` is a user-facing honesty check, offline by default.** It
  answers one question: "do my installed engines and wired project actually honour
  what they declare?" It is fast, keyless, and CI-friendly.
- **`make test` is the full pytest suite** — the developer-facing regression net
  across every package (the conformance test tree included). `verify` is a curated
  slice of the same guarantees surfaced as a themed report; `make test` is
  everything. The test-tree `conformance/` files are now thin wrappers over the
  shipped `agentship.conformance` grid.
- **P23 user-facing evals are a different thing** — scenario and regression
  evaluation of an agent's *task behaviour* (quality, not capability honesty).
  `verify` proves the engine does not lie about what it can do; evals judge how
  well an agent does its job. Do not conflate the two.

## One runnable example

Run the honesty check with no keys and no project, straight from the repo:

```bash
agentship verify
```

Or drive the same grid programmatically over just the engines you care about:

```python
import asyncio
from agentship.conformance import run_capability_grid
from agentship_langgraph.testing import offline


async def main() -> None:
    results = await run_capability_grid(["echo", "langgraph"], offline=offline)
    for cell in results:
        mark = "ok " if cell.passed else "FAIL"
        print(f"[{mark}] {cell.engine}.{cell.capability} ({cell.kind}) — {cell.detail}")


asyncio.run(main())
```

## Status & limits

✅ shipped 2026-08-25. The catalogue ships in `agentship.conformance`; the
LangGraph offline harness ships in `agentship_langgraph.testing`; `agentship
verify` is the user-facing runner over the grid. `--live` is reserved (all
sections run offline today). Deferred capability cells are tracked in
`DEFERRED_CAPABILITIES`. Authoritative status: `.spec-dev/STATUS.md`.
