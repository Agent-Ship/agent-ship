# 0003 — Use LangGraph's checkpointer as the durability substrate; keep `durability:` vendor-free

**Status:** accepted · **Scope:** P03 (checkpointing & HITL), consumed by P11 (durable resume)

## Context

Agents need to survive a crash and to pause for human approval before an irreversible action.
Both need the same thing underneath: state saved at every step, in a store that outlives the
process.

The tempting move is to build a `SessionStore` of our own — we already have a tenant model and a
Postgres dependency, and "save the conversation" looks like a small table. It is not. Doing it
properly means per-node snapshots, a resume frontier, concurrent-write reduction, and a pause
primitive that can suspend and rehydrate a running graph. LangGraph already ships all of that,
tested, in `InMemorySaver` / `AsyncPostgresSaver` and `interrupt()`.

The competing constraint: `durability` must stay a **portable promise**. If it becomes a
LangGraph setting, then P27 (ADK) and P28 (Pydantic AI) either cannot honour it, or honour it by
accident with different semantics — and a user's crash-safety expectation silently changes when
they switch engines.

## Decision

**Consume LangGraph's checkpointers and `interrupt()` wholesale. Build only two thin things:**

1. **`open_checkpointer(conninfo, *, setup=False)`** — an async context manager that picks the
   saver from the environment: blank → the shared process-wide `InMemorySaver`, a Postgres URI →
   `AsyncPostgresSaver` holding a live pool for the duration of the run. It is a context manager
   rather than a factory precisely because the Postgres saver owns a pool that must stay open for
   as long as the graph runs.
2. **The `confirm_write` node** — the *policy* that a side-effecting tool pauses before running.
   The suspension itself is `interrupt()`; we only decide when to call it.

And hold two lines that keep the promise portable:

- **`durability: none | checkpoint | workflow` is a spec field the capability gate enforces.**
  An engine declaring `durability="none"` fails at build time when a spec asks for `checkpoint`.
  A crash-recovery promise is never silently downgraded to nothing.
- **LangGraph's flush mode (`sync`/`async`/`exit`) is deliberately NOT a spec field.** A user
  writing `durability: checkpoint` is asking for crash safety, not a flush-timing knob. The
  engine picks the strongest mode (`sync`) internally, and the vendor's vocabulary never reaches
  portable YAML.

Schema DDL is opt-in (`setup=True`, invoked from `agentship doctor` / first boot), never on
process start — the project's never-migrate-silently rule.

## Consequence

**Bought:** durability is roughly 30 lines of ours plus a battle-tested library, instead of a
persistence layer we would own forever. Dev needs no database and production needs no code
change — the same YAML, a different environment variable. HITL comes almost free, because a
pause is just a checkpoint plus `interrupt()`.

**Cost:** `InMemorySaver` is single-process, so the no-database path cannot demonstrate real
crash recovery — cross-process durability genuinely requires Postgres. This is a real limit and
the capability page states it rather than implying otherwise.

**Do not break:** the shared module-level `_MEMORY_SAVER` must stay shared. A fresh saver per
`open_checkpointer` call would make an in-process `run` → `resume` forget its checkpoints, and
the failure would look like a resume bug rather than a lifetime bug. And do not add the flush
mode to `AgentSpec` — the moment it appears, portable YAML carries a LangGraph concept and P27/P28
inherit a field they cannot honour.
