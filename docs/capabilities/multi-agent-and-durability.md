# Multi-agent & durability

Fan a turn out to several specialist agents, reconcile their answers deterministically, and survive a `kill -9` mid-run — resuming from the last durable checkpoint so completed work is never redone.

## What it is

> **Supervisors moved.** Deterministic routing, member resolution, and `ConflictResolver` now have
> their own page: **[multi-agent.md](multi-agent.md)**. This page covers durability only —
> checkpointing, HITL, resume, and idempotency.

- **Checkpoint durability.** A spec declaring `durability: checkpoint` runs under LangGraph per-node checkpointing (in-memory in dev, `AsyncPostgresSaver` when `AGENT_SESSION_STORE_URI` is set), so a crashed run can continue from its last durable step.
- **Resume.** A durable run mints a `ResumeToken` (`{engine, blob}`); `engine.resume(token)` re-hydrates state and continues the frontier — completed nodes are not re-run.
- **Idempotency.** `call_once` + the canonical `idem_key(thread_id, node_id, tool_name, args)` ensure a replayed or reclaimed run fires a side effect **exactly once** (a write-ahead `pending` ledger entry closes the crash-between-effect-and-record window).
- **Human-in-the-loop (HITL).** A `confirm_write` node calls LangGraph's `interrupt(...)` to pause before a side-effecting write; the run persists a checkpoint and returns an `interrupt` payload + resume token. The human's decision flows back via `resume_value`.

## How to use it

Declare a supervisor with two sub-agents and durable checkpointing (`durability: checkpoint`):

```yaml
# team.yaml
name: triage
engine: langgraph
template: graph
model: openai/gpt-4o-mini
durability: checkpoint
members:
  - name: billing_specialist
    prompt: Answer billing and invoice questions.
  - name: clinical_specialist
    prompt: Answer symptom and medication questions.
```

Run it, and if the run pauses on a HITL `interrupt`, resume it with the human's decision:

```python
from agentship.runtime import build_agent

agent = build_agent("team.yaml")
result = await agent.run("cancel my subscription")

if result.interrupt is not None:          # paused at confirm_write
    token = result.resume_token           # ResumeToken(engine="langgraph", blob={...})
    result = await agent.engine.resume(
        agent.compiled, token, ctx, resume_value={"approved": True},
    )

print(result.output)
```

A plain crash-resume (no interrupt) is the same call with `resume_value=None`: LangGraph continues from the last checkpoint and does not re-run completed nodes. The resume holds the single-owner `ThreadLock` for `(tenant, thread)` so a replay or a second worker can never double-execute.

## One runnable example

The `graph` authoring scaffold ships at [`examples/graph.yaml`](../../examples/graph.yaml) — the smallest compilable multi-agent starting point (coordinator → one worker). Follow the `# TODO(author)` markers in `agentship_langgraph/templates/graph.py` to add specialists and routing. The primitives live at:

- `agentship.primitives.conflict_resolver` — `ConflictResolver`, `ConflictPolicy`, `SpecialistResult`
- `agentship.primitives.idempotency` — `idem_key`, `call_once`, `IdempotencyLedger`
- `agentship.thread_lock` — `ThreadLock`, `resolve_thread_lock`
- `agentship_langgraph.templates.graph_supervisor` — `SupervisorAgent`, `build_supervisor_graph`
- `agentship_langgraph.durability` — `open_checkpointer`

## Status & limits

🟨 in-flight, 40/42. Real remaining gap: no integrated test yet of supervisor + checkpoint + mid-run failure + resume; idempotency across a supervisor dispatch is unproven (exactly-once is proven today only for a single tool call). The auto-resume **trigger** (crash detection, reaper, `/tasks`) lives in P11 — P02 only exposes `engine.resume` + the lock seam that P11 drives. Authoritative status: [`.spec-dev/STATUS.md`](../../../.spec-dev/STATUS.md).
