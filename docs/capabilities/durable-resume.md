# Durable resume

Survive a `kill -9` mid-run: continue from the last checkpoint instead of starting over, without
re-running completed work and without firing a side effect twice.

> **Prerequisite:** this page builds on [checkpointing-and-hitl.md](checkpointing-and-hitl.md).
> That page gives you the checkpoints; this one is what brings a dead run back to life.

## What you get

- **Resume.** A durable run mints a `ResumeToken` (`{engine, blob}`). `engine.resume(token)`
  rehydrates state and continues from the frontier — completed nodes are **not** re-run.
- **Exactly-once side effects.** `call_once` plus the canonical
  `idem_key(thread_id, node_id, tool_name, args)` ensure a replayed or reclaimed run fires a side
  effect once. A write-ahead `pending` ledger entry closes the crash-between-effect-and-record
  window.
- **Single ownership.** `ThreadLock` on `(tenant, thread)` means a replay or a second worker can
  never double-execute the same thread.

## What we consume vs. what we build

| Concern | Who does it | Why |
|---|---|---|
| Continuing from a checkpoint | **LangGraph** (consumed) | Resuming a graph from its frontier is the runtime's job. |
| The `ResumeToken` shape | **We build it** — vendor-free | A token must be portable across engines, so it cannot be a LangGraph object. |
| Idempotency ledger | **We build it** — `call_once`, `idem_key` | Exactly-once over *our* tool boundary; no library knows where our side effects are. |
| Thread ownership | **We build it** — `ThreadLock` | Tenant-scoped single ownership is app logic. |

## How to use it

Resuming after a crash is the same call as resuming from a HITL pause, with no `resume_value`:

```python
result = await agent.engine.resume(agent.compiled, token, ctx, resume_value=None)
```

LangGraph continues from the last checkpoint; completed nodes do not run again. The resume holds
the single-owner `ThreadLock` for `(tenant, thread)` throughout.

Making a tool exactly-once:

```python
from agentship.primitives.idempotency import call_once, idem_key

key = idem_key(thread_id, node_id, "charge_card", args)
result = await call_once(ledger, key, lambda: charge_card(**args))
```

## Real crash recovery needs Postgres

`InMemorySaver` is single-process, so it can only demonstrate an in-process `run` → `resume`.
Surviving actual process death requires `AGENT_SESSION_STORE_URI` pointing at Postgres. The
capability page for checkpointing says the same thing — it is a genuine limit, not a caveat.

## Where the pieces live

- `agentship.primitives.idempotency` — `idem_key`, `call_once`, `IdempotencyLedger`
- `agentship.thread_lock` — `ThreadLock`, `resolve_thread_lock`
- `agentship_langgraph.durability` — `open_checkpointer`

## Status & limits

🟨 in-flight, 11/13 — **and the headline proof is missing.** Read this before relying on it:

- There is **no integrated test** of supervisor + `durability=checkpoint` + mid-run failure +
  resume producing identical output. The conformance cell `durable_resume_after_kill` is
  currently **xfail**.
- Exactly-once is proven today for a **single tool call**, not across a full supervisor dispatch.
- Known gap: a tool dying between its side effect and the ledger write can double-fire. The
  options are a write-ahead intent log or accepting the window; the call has not been made.

So: the seam works and is tested, the end-to-end guarantee is not yet proven. This is the single
most important missing proof in the project, and it is tracked as known-gap #1.

Authoritative status: [`.spec-dev/STATUS.md`](../../../.spec-dev/STATUS.md).
