# Durable resume

Survive a `kill -9` mid-run: continue from the last checkpoint instead of starting over, without
re-running completed work and without firing a side effect twice.

> **Prerequisite:** this page builds on [checkpointing-and-hitl.md](checkpointing-and-hitl.md).
> That page gives you the checkpoints; this one is what brings a dead run back to life.

## What you get

- **Resume.** A durable run mints a `ResumeToken` (`{engine, blob}`), but the session is what
  identifies the run: `agent.resume(session_id=...)` continues it with or without the token. That
  matters after a crash, because a turn that died never returned a token. Completed nodes are
  **not** re-run.
- **Your own conversation only.** The resumed thread is derived from the caller's tenant, the
  agent and the session — never from the token, which the client holds and could edit. A token
  pointing at another tenant's or session's thread is refused (`ResumeError`, HTTP 409).
- **At-most-once side effects, across processes.** Every side-effecting tool call goes through
  `call_once`, which commits a `pending` row to the ledger *before* the effect fires and a `done`
  row with the result after. With `AGENT_SESSION_STORE_URI` set, that ledger is a Postgres table
  beside the checkpoints, so a run resumed in a new process replays the recorded result instead
  of firing again.
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

Resuming after a crash is the same call as resuming from a HITL pause, with no `resume_value`.
The session is enough:

```python
result = await agent.resume(session_id="chat-1", caller=caller)
```

Over HTTP, `resume_token` is optional for the same reason:

```bash
curl -X POST http://127.0.0.1:8000/v1/agents/payments:resume \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"session_id": "chat-1"}'
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

`InMemorySaver` and the in-memory ledger are single-process, so they can only demonstrate an
in-process `run` → `resume`. Surviving actual process death requires `AGENT_SESSION_STORE_URI`
pointing at Postgres, with both tables created once:

```bash
export AGENT_SESSION_STORE_URI=postgresql://user:pass@host:5432/agentship
agentship db upgrade --allow-migrations   # checkpoints + tool_idempotency_keys
```

`agentship doctor` and `agentship serve` refuse a `durability: checkpoint` agent when
`AGENT_SESSION_STORE_URI` is unset, so the in-memory fallback can no longer happen silently. For
local development pass `--allow-in-memory-durability` (or set
`AGENTSHIP_ALLOW_IN_MEMORY_DURABILITY=1`) to accept it knowingly.

### What makes two tool calls "the same call"

The ledger key is `idem_key(conversation_key, call_site, tool_name, args)`:

- `conversation_key` is `tenant/agent/session`, so two tenants who both use session `chat-1`
  never share entries;
- `call_site` is the graph step, the node path and the model's tool-call id. It is the same when a
  crashed step is resumed and different when the user asks for the same write in a later turn —
  which is a new write and fires.

### After a crash mid-write

If the process dies after the `pending` row but before `done`, the effect may or may not have
happened. The resumed run does **not** fire it again; the tool returns a message saying the prior
call may have completed and was not re-run, and the model sees that. For true exactly-once, pass
the idempotency key on to an API that accepts one (for example an `Idempotency-Key` header), so a
retry is safe on their side too.

## Where the pieces live

- `agentship.primitives.idempotency` — `idem_key`, `call_once`, `IdempotencyLedger`
- `agentship.primitives.ledger` — `PostgresLedger`, `open_ledger`, the `tool_idempotency_keys` DDL
- `agentship.thread_lock` — `ThreadLock`, `resolve_thread_lock`
- `agentship_langgraph.durability` — `open_checkpointer`, `open_turn_state` (checkpointer and
  ledger from one store, per turn)

## Status & limits

🟨 in-flight, 11/13 — **and the headline proof is missing.** Read this before relying on it:

- **Proven for a single agent, across real process death.**
  `test_crash_recovery_postgres.py` runs a turn in a child process against Postgres, `SIGKILL`s
  it inside a side-effecting tool, and resumes by session in a new process. It checks both kill
  points: mid-write (`pending`: not re-fired) and after the write but before LangGraph saved the
  step (`done`: the receipt is replayed). A control test deletes the ledger rows first and shows
  the same crash then fires twice. CI runs these against a Postgres service.
- There is still **no integrated test** of a *supervisor* + `durability=checkpoint` + mid-run
  failure + resume. The engine-grid cell `durable_resume_after_kill` is still **xfail**.
- Crash mid-write is **at-most-once**, not exactly-once: the effect is not repeated, but whether
  it happened is reported as unknown unless the downstream API is idempotent too.
- Ledger rows are never pruned; an operator can delete old `done` rows by `updated_at`.
- Known hazard (unchanged): concurrent turns on one agent share its compiled graph's
  checkpointer attribute under Postgres — see `_with_checkpointer` in the LangGraph engine.

Authoritative status: `.spec-dev/STATUS.md`.
