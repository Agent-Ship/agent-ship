# Manual testing — Phase 03 (Tools & MCP) and everything before it

A hands-on checklist to exercise every shipped capability yourself. Each item is a real command
that calls OpenAI (so it costs a little). Work top to bottom, or jump to what you want to poke at.

## Setup (once)

```bash
cd agentship-demo
cp .env.example .env                       # put OPENAI_API_KEY=sk-... in it
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt        # installs the framework editable
pip install "langchain-mcp-adapters>=0.3" "mcp>=1.28,<2" "cryptography>=42" "deepagents==0.6.12"
set -a; source .env; set +a                # load your key into the shell
```

> **Important for MCP tests:** the local MCP servers are spawned as `python3` subprocesses, so keep
> the venv **activated** (so `python3` has the `mcp` package). All commands below assume the venv is
> active and `OPENAI_API_KEY` is set.

Sanity check the config of any agent before running it (no API call):

```bash
agentship doctor agents/           # validates every agent YAML; flags bad specs / missing extras
```

---

## 1. Tool calling — the calculator

The model calls a real tool instead of doing math itself. `--verbose` surfaces each tool
invocation on stderr so you can *see* that the model actually called the tool rather than answering
from its own knowledge.

```bash
agentship run agents/calculator.yaml --input "What is (45 * 3) - 17?" --verbose
# stdout: "... 118."
# stderr (interleaved): lines like:
#   · tool call: calculator({"expression": "45 * 3 - 17"})
#   ·   -> {"expression": "45 * 3 - 17", "result": 118}

agentship run agents/calculator.yaml --input "What is 2 to the power of 12, minus 96?"
# expect: 4000
```

Try to break it (the calculator only allows arithmetic — it must refuse code):
```bash
agentship run agents/calculator.yaml --input "Use the calculator to evaluate __import__('os').getcwd()"
# expect: it reports an error / refuses; no code executes
```

## 2. Built-in tools carried over from the old repo

Three built-ins ship: `calculator`, `http_request`, `web_search`. Point an agent at any of them via
`tools:`. `web_search` needs a `BRAVE_API_KEY` (else it returns a clear setup message — try it
without a key to see the graceful behaviour).

```bash
# quick unit check of all three (no OpenAI, no network for calculator; local server for http):
cd ../agentship && .venv/bin/python -m pytest packages/agentship-core/tests/test_tools.py -q ; cd ../agentship-demo
```

## 3. Local MCP server — the agent uses an MCP tool

`agents/mcp/agent.yaml` connects to a bundled **local stdio** MCP server (`time_server.py`) via
`langchain-mcp-adapters`. Its `days_between` tool is discovered and bound automatically.

```bash
agentship run agents/mcp/agent.yaml --input "How many days from 2026-01-01 to 2026-08-14?"
# expect: 225
agentship run agents/mcp/agent.yaml --input "How many days between 2026-03-01 and 2026-12-25?"
# expect: 299
```

Point at a **real public MCP server** to prove remote/streamable-HTTP works too — edit a copy of the
YAML to use e.g.:
```yaml
mcp:
  ctx7:
    transport: streamable_http
    url: https://mcp.context7.com/mcp     # any public MCP endpoint you have access to
```
then `agentship run <copy>.yaml --input "..."`.

## 4. Skills — teaching the agent HOW to use a tool/MCP

`agents/mcp/agent.yaml` also declares `skills: [agents/mcp/skills/date-math]`. That SKILL.md
(Agent Skills format) tells the model to call `days_between` for date questions. Edit the SKILL.md
body and re-run to see the guidance change behaviour:

```bash
cat agents/mcp/skills/date-math/SKILL.md      # read the how-to guidance
agentship run agents/mcp/agent.yaml --input "How long from today-ish 2026-05-05 until 2026-06-01?"
```

## 5. Tool allow-listing (the many-MCP overload guard)

Add `allowed_tools: [days_between]` to an agent with several MCP servers/tools to curate exactly
which tools the model sees. Verify by giving it a tool NOT on the list and confirming it can't use it.

## 6. HITL — confirm before a write

`agents/hitl/agent.yaml` has `confirm_writes: true` + a side-effecting `save_note` tool. A write
**pauses for approval** (the run returns a resume token; nothing is written) and fires only on
approval. This flow is interactive, so drive it from Python:

```bash
cd ../agentship
.venv/bin/python -m pytest ../agentship-demo/tests/test_hitl_write.py -q -s   # watch the pause→approve
cd ../agentship-demo
```

What to verify: the first turn returns `result.interrupt` set and **nothing saved**; resuming with
`{"approved": true}` fires the write exactly once; resuming with `{"approved": false}` never writes.

## 7. Exactly-once tools across a resume (idempotency)

A side-effecting tool wrapped in the write-ahead ledger fires **once** even if a resumed run replays
its invocation. Proven in the framework suite:

```bash
cd ../agentship
.venv/bin/python -m pytest conformance/test_phase02_durability.py::test_cell_replay_idempotency -q
cd ../agentship-demo
```

## 8. autonomous — a single self-directing agent that uses a tool

`agents/autonomous.yaml` runs one autonomous planning loop (the `autonomous` template, wrapping the
deepagents library) and uses the calculator for each step. This is ONE agent driving itself — not
the multi-agent supervisor (that is section 9).

```bash
agentship run agents/autonomous.yaml --input "What is (18 * 7) + (100 / 4)?"      # expect 151
agentship run agents/autonomous.yaml --input "If I save $250/month for 18 months, how much total? Then subtract a $400 fee."
```

## 9. Multi-agent (from Phase 02) — routing + fan-out + durable resume

```bash
agentship run agents/triage/triage.yaml --input "My invoice is wrong" --verbose         # routes to ONE sub-agent
agentship run agents/triage/panel.yaml  --input "My bill is wrong and I feel dizzy" --verbose   # fans out to ALL, merges
agentship run agents/triage/triage_declarative.yaml --input "who handles payments?" --verbose   # zero-Python supervisor
make ask INPUT="I was overcharged and I keep getting headaches"                          # watch sub-agents get called
make demo-multiagent                                                                     # red/green: all 3 sub-agents ran
```

## 10. Streaming, templates, custom graphs, router (Phase 00–01)

```bash
agentship run agents/streaming.yaml --input "Name the 8 planets, comma-separated." --stream
agentship run agents/assistant.yaml --input "Give one productivity tip."
agentship run agents/graph.yaml     --input "Plan a weekend trip."
agentship run agents/custom/custom.yaml --input "Name three primary colors."
make demo        # runs the whole labelled tour of every slice, live
```

## 11. Coordinator + quick vs deep research — the long-running, resumable agent

The flagship "many-speed ecosystem" demo: a **coordinator** classifies a request and routes it to a
fast single-turn **quick-search** agent (seconds, `durability: none`) or a long, iterative
**deep-research** agent that runs several rounds, **pauses to ask you "go deeper?"**, and is durable
(`durability: checkpoint`) so it survives a crash or a long wait and resumes from the exact round.

```bash
# One command that classifies, then runs the right agent end-to-end:
python demos/coordinated_research.py "Who won the 2026 Super Bowl?"                 # -> QUICK path
python demos/coordinated_research.py "Compare small modular reactor vendors in 2026" # -> DEEP path
# On the deep path, approve extra rounds instead of stopping at the first pause:
DEEP_APPROVE_ROUNDS=2 python demos/coordinated_research.py "State of EU AI regulation"
# or: make research INPUT="Compare small modular reactor vendors in 2026"
```

Run the agents directly too:

```bash
agentship run agents/quick_search.yaml --input "Who is the CEO of OpenAI?" --verbose  # one turn
agentship run agents/coordinator.yaml  --input "Compare every EU AI regulation"       # prints: deep
```

Real web results need `BRAVE_API_KEY` (without it, each search returns a clearly-labelled stub and
the loop still runs). Tune how many rounds run automatically before the pause with
`DEEP_RESEARCH_AUTO_ROUNDS` (default 2).

**What to verify (the long-running guarantees):**
- The deep run **pauses** — `agent.run(...)` returns with `result.interrupt` set (the "go deeper?"
  payload) and `result.output is None`; **no report yet**.
- Resuming with `{"go_deeper": false}` synthesizes the report; `{"go_deeper": true}` runs another
  round and pauses again (depth is unbounded, human-gated).
- **Crash/restart survival:** with `AGENT_SESSION_STORE_URI` set to Postgres, the resume token
  round-trips through the DB — a *fresh process* can resume the paused run. Proven deterministically
  offline in `tests/test_deep_research.py` (in-memory + fresh-engine resume) and live in
  `tests/test_coordinated_research.py`:

```bash
cd ../agentship && .venv/bin/python -m pytest ../agentship-demo/tests/test_deep_research.py -q
cd ../agentship-demo && pytest tests/test_coordinated_research.py -q -s   # live: pause -> resume -> report
```

---

## Run all the automated tests (the safety net under all of the above)

```bash
# framework + conformance (offline, fast):
cd ../agentship && .venv/bin/python -m pytest packages/ conformance/ -q

# every demo slice, live (needs the key + venv active):
cd ../agentship-demo && pytest -q
```

## What is NOT covered here (known follow-ups)

- **Interactive remote-MCP OAuth** (browser redirect for a server that requires OAuth 2.1) — the flow
  is driven by the `mcp` SDK's `OAuthClientProvider`; our encrypted `TokenStorage` persistence is
  unit-tested, but the end-to-end browser consent is a manual step against a real OAuth MCP server.
- **Persistent MCP sessions** — MCP tools currently open a fresh session per call (functional; a
  long-lived-session optimization is a follow-up).
- **The service / gateway / A2A** — those are Phases 04–05, not built yet.
