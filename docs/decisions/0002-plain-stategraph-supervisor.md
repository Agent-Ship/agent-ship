# 0002 — Build the supervisor on plain `StateGraph`, not `langgraph-supervisor`

**Status:** accepted · **Scope:** P02 (multi-agent supervisors)

## Context

AgentShip's rule is *integrate best-of-breed libraries behind small, stable seams — never
reinvent them* ([ADR 0001](0001-integrate-not-invent.md)). A supervisor that routes a turn to
specialist agents is exactly the kind of thing a library should own, and LangGraph ships one:
`langgraph-supervisor`. Not adopting it needs a real justification, because "we wrote our own
orchestrator" is the failure mode this project exists to avoid.

Three constraints decided it:

1. **Determinism.** A resumed run must reproduce byte-identical output (P11). That requires the
   route decision and the merge step to be reproducible. `langgraph-supervisor` models handoff
   as the LLM calling a transfer tool — the routing decision *is* a model call, so the same
   input can take a different path on replay.
2. **Engine neutrality.** `members:` / `conflict:` are `AgentSpec` fields that a second engine
   adapter (P27 ADK, P28 Pydantic AI) must be able to honour. Adopting a LangGraph-specific
   supervisor shape would push a vendor's handoff protocol into the spec that is supposed to be
   vendor-free, and the `multi_agent` conformance cell would only ever be satisfiable by one
   engine.
3. **Fit.** What we needed was five nodes and a lookup table. The library's value is the handoff
   protocol and its state plumbing — precisely the parts we cannot use.

## Decision

Build `SupervisorAgent` from plain LangGraph `StateGraph` nodes
(`classify → lookup_route → dispatch → resolve → confirm_write? → safety_gate`), and do **not**
depend on `langgraph-supervisor`.

The split that makes this honest:

- **Consumed:** LangGraph `StateGraph` for graph execution, state reduction, parallel fan-out,
  checkpointing, and `interrupt`. We write no graph runtime.
- **Built (thin):** the five node functions and `ConflictResolver`. `classify` is the only model
  call; `lookup_route` is a dictionary lookup over member `description:`s, and `ConflictResolver`
  is a pure function — no model, no I/O.

This is not "we reimplemented LangGraph". It is "we used LangGraph and wrote our five nodes",
which is what building on a graph runtime looks like.

## Consequence

**Bought:** routing is a pure lookup, so an identical input always takes an identical path, which
is what makes P11's identical-resume guarantee provable at all. `members:` stays engine-neutral
data, so the `multi_agent` conformance cell is satisfiable by any engine rather than by LangGraph
alone. And the supervisor is one readable file — a contributor can see the whole control flow
without learning a handoff protocol.

**Cost:** we own those five nodes, and we do not get `langgraph-supervisor`'s future features for
free. Accepted, because the feature we would most want from it — LLM-driven handoff — is the one
we deliberately rejected.

**Do not break:** `lookup_route` and `ConflictResolver` must stay pure. Introducing a model call
or any I/O into either silently breaks identical-resume, and the failure will surface far away
from the change, in P11's resume proof.
