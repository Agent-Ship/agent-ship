# Multi-agent supervisors

Fan a turn out to specialist agents and reconcile their answers deterministically — declared
in YAML, with no orchestration framework to learn and no Python to write.

## What you get

A **supervisor**: one agent that owns a team of specialists and decides which of them should
answer. Its graph is fixed and readable:

```
classify → lookup_route → dispatch → resolve → (confirm_write?) → safety_gate
```

- **`classify`** asks the model which specialist the input belongs to.
- **`lookup_route`** turns that answer into a route by **plain lookup, not an LLM guess** — so
  the same input always takes the same path.
- **`dispatch`** runs the chosen specialist(s), in parallel when more than one applies.
- **`resolve`** merges competing answers through `ConflictResolver` — a pure function with no
  model call and no I/O, which is what lets a resumed run reproduce byte-identical output.

Members come in three shapes and the supervisor cannot tell them apart: **inline** (defined in
the same file), **`ref:`** (a pointer to another agent's YAML), and **`a2a:`** (an agent running
on another host, reached over the wire). Swapping a local specialist for a networked one is a
one-line change.

## What we consume vs. what we build

This is the whole design rule: *integrate best-of-breed libraries behind small, stable seams —
never reinvent them.* For supervisors that comes out as:

| Concern | Who does it | Why |
|---|---|---|
| Graph execution, state, parallelism | **LangGraph `StateGraph`** (consumed) | It is the best graph runtime available and we are not going to write a better one. |
| Supervisor orchestration | **We build it** — ~one file of plain `StateGraph` nodes | `langgraph-supervisor` was deliberately not adopted; see [ADR 0002](../decisions/0002-plain-stategraph-supervisor.md). |
| Conflict resolution | **We build it** — `ConflictResolver`, pure | Must be deterministic for identical-resume; a library that calls a model here would break that. |
| Model calls | **LiteLLM** (consumed) | One seam, ~100 providers. |

The part we build is small on purpose. If you can read `graph_supervisor.py`, you know exactly
what your supervisor does — there is no framework doing something clever behind your back.

## How to use it

A supervisor with three specialists, each its own agent file. **No Python at all** — the
framework resolves the members and derives routing from their `description:`s:

```yaml
# triage.yaml
name: triage
engine: langgraph
model: openai/gpt-4o-mini
members:
  - name: billing_specialist
    ref: specialists/billing.yaml
    description: billing, invoices, double charges, payments, and refunds
  - name: clinical_specialist
    ref: specialists/clinical.yaml
    description: health, symptoms, and medical questions
  - name: faq_specialist
    ref: specialists/faq.yaml
    description: general questions about the service
```

```bash
agentship run triage.yaml --input "My invoice looks wrong — who handles payments?"
```

The `description:` is load-bearing: it is what `classify` matches against, so write it as the
set of things that specialist owns.

### When you want more control

Point `code:` at a Python factory instead, and you build the `StateGraph` yourself while keeping
every other AgentShip guarantee (tracing, durability, tenancy). Declarative and code-built
supervisors run on the same engine — the demo ships both against the same specialists.

### Tuning the merge

```yaml
conflict:
  priority: [clinical_specialist, billing_specialist, faq_specialist]
  on_tie: highest_confidence      # or first_by_priority (the default)
```

## Swapping the vendor

`Engine` is the contract; `langgraph` is one implementation of it. A supervisor spec is engine
data, not LangGraph data — the same `members:`/`conflict:` block is what a second engine adapter
consumes. That claim is enforced, not asserted: the conformance grid runs the `multi_agent` cell
against every registered engine, and an engine that declares support it does not have fails
`test_over_declaration.py`.

## One runnable example

`agentship-demo/agents/triage` ships the same team three
ways, so you can see the trade-off directly:

| File | Wiring | Use when |
|---|---|---|
| `triage_declarative.yaml` | zero Python, `ref:` members | the default — start here |
| `triage.yaml` | a `code:` factory | you need custom routing logic |
| `panel.yaml` | `code:` factory, parallel fan-out | every specialist should answer |

```bash
cd agentship-demo
make test                     # replays cassettes — no key, no network
python demos/ask_multiagent.py   # live: watch all three specialists answer
```

## Where the pieces live

- `agentship.primitives.conflict_resolver` — `ConflictResolver`, `ConflictPolicy`, `SpecialistResult`
- `agentship_langgraph.templates.graph_supervisor` — `SupervisorAgent`, `build_supervisor_graph`
- `agentship_langgraph.templates.graph_config` — the declarative `members:` → route mapping

## Status & limits

🟨 in-flight, 18/19. One open item: `classify` does not yet stamp a `TaskHint(model=…)`, so an
easy branch cannot be routed to a cheaper model. Everything else on this page is shipped and
tested.

**Not on this page:** surviving a crash mid-run is a separate capability —
checkpointing and human-in-the-loop live in [checkpointing-and-hitl.md](checkpointing-and-hitl.md),
and crash recovery in [durable-resume.md](durable-resume.md).

Authoritative status: `.spec-dev/STATUS.md`.
