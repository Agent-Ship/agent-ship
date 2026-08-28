# Checkpointing & human-in-the-loop

Save an agent's progress at every step, and pause it mid-run to ask a human before it does
something irreversible. Both are one line of YAML.

## What you get

- **Checkpointing.** `durability: checkpoint` makes the engine save state after every node.
  A run that dies part-way can be continued instead of restarted.
- **Human-in-the-loop.** `confirm_writes: true` makes any side-effecting tool stop *before* it
  runs and hand control back to you. The run returns a resume token and nothing has happened
  yet; the write fires only when you resume with `{"approved": true}`.

Because HITL is built on checkpointing, a paused run is durable — the human can take an hour,
or the process can restart, and the pending write is still there waiting.

## What we consume vs. what we build

| Concern | Who does it | Why |
|---|---|---|
| Per-step state saving | **LangGraph checkpointers** (consumed) | `InMemorySaver` in dev, `AsyncPostgresSaver` in production. We write no persistence layer. |
| The pause mechanism | **LangGraph `interrupt()`** (consumed) | It already knows how to suspend a graph and resume it with a value. |
| Deciding *when* to pause | **We build it** — the `confirm_write` node | The policy "pause before a side-effecting tool" is ours; the suspension machinery is not. |
| Choosing the backend | **We build it** — `open_checkpointer` | ~30 lines that pick a saver from the environment. See [ADR 0003](../decisions/0003-langgraph-checkpointer-as-the-durability-substrate.md). |

The honest summary: **we did not build durability, we configured it.** What we added is the
vendor-free `durability:` field so the promise is portable, and the policy for when to interrupt.

## How to use it

### Checkpointing

```yaml
name: assistant
engine: langgraph
model: openai/gpt-4o-mini
durability: checkpoint      # save state after every step
```

That's the whole surface. `durability` takes `none` (default), `checkpoint`, or `workflow`.

Where state goes is an **environment** decision, not a spec decision — the same YAML runs both
ways, which is the point:

| `AGENT_SESSION_STORE_URI` | Backend | Survives |
|---|---|---|
| unset | `InMemorySaver` | in-process `run` → `resume` only |
| a Postgres URI | `AsyncPostgresSaver` | process death, machine death |

Postgres needs its tables once. That is a migration, so it never runs silently — opt in via
`agentship doctor` or first boot, per the project's never-migrate-silently rule.

Note what is deliberately *not* a spec field: LangGraph's checkpoint **flush mode**
(`sync`/`async`/`exit`). Declaring `durability: checkpoint` means "I want crash safety", not
"let me tune flush timing", so the engine picks the strongest mode (`sync`) internally and the
vendor's vocabulary never reaches your YAML.

### Human-in-the-loop

```yaml
name: note-taker
engine: langgraph
template: single
model: openai/gpt-4o-mini
durability: checkpoint      # required — HITL is built on checkpointing
confirm_writes: true        # side-effecting tools pause for approval
tools:
  - save_note
```

```python
from agentship import build_agent

agent = build_agent("note-taker.yaml")
result = await agent.run("Save a note: renew the domain in March")

if result.interrupt is not None:      # paused — nothing has been written yet
    result = await agent.engine.resume(
        agent.compiled, result.resume_token, ctx, resume_value={"approved": True},
    )
```

Resuming with `{"approved": false}` declines, and the tool never runs. The spec rejects
`confirm_writes: true` without `durability: checkpoint` at build time, rather than letting you
discover at runtime that your pause was not durable.

## Swapping the vendor

`durability: checkpoint` is a **promise**, not a LangGraph setting — which is why the capability
gate rejects it at build time on any engine declaring `durability="none"`. A crash-recovery
promise is never silently downgraded to nothing. An engine that wants to satisfy this field
backs it with whatever it has (P28's Pydantic AI adapter uses Temporal/DBOS `reattach`); the YAML
does not change.

## One runnable example

`agentship-demo/agents/hitl/agent.yaml` — a
note-taker whose `save_note` tool pauses for approval:

```
run                       → pauses (result.interrupt set, nothing written)
resume {"approved": true} → the write fires exactly once
```

```bash
cd agentship-demo
make test    # replays cassettes — no key, no network
```

The proof that matters is in `tests/test_hitl_write.py`: it asserts a *real* interrupt was
raised and that nothing was written before approval — not a simulated pause.

## Where the pieces live

- `agentship_langgraph.durability` — `open_checkpointer` (the backend choice)
- `agentship_langgraph.templates.graph_supervisor` — `make_confirm_write` (the pause policy)
- `agentship.spec` — the `durability` and `confirm_writes` fields and their coherence check

## Status & limits

🟨 in-flight, 8/9. One open item: the `reclaim_mid_flight` conformance cell — a worker dying
while holding a thread, and a second worker picking it up.

**Not on this page:** recovering from a crash *across processes* — that is
[durable-resume.md](durable-resume.md). This page gives you the checkpoints; P11 is what drives
them back to life automatically.

Authoritative status: `.spec-dev/STATUS.md`.
