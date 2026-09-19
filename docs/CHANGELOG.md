# Changelog

All notable, user-facing changes to AgentShip, grouped by delivery phase. This file is a
**Definition-of-Done gate**: a phase is not "done" until its capability page, this entry, and an ADR
(when a non-obvious decision was made) all land. See `.spec-dev/operating-model.md` (DOCS gate) and
`.spec-dev/STATUS.md` (the authoritative board).

The format is loosely based on [Keep a Changelog](https://keepachangelog.com).

**This file is the source of the GitHub release notes.** `scripts/changelog.py` extracts the
`## [<version>]` section for a tag and the release workflow publishes it verbatim, so the release
page and this file cannot drift. A tag whose version has no section here **fails the release
before anything is published** — see [Releasing](RELEASING.md#release-notes).

Work in progress accumulates under `## [Unreleased]`; cutting a release renames that heading to
the version and dates it. Entries within a release are grouped by delivery phase, which is how
they were written before the project cut tagged releases.

<!-- BACKFILLED 2026-08-23 from code+tests during the status-reconciliation pass, not written at
     ship time. Headings were migrated to the post-renumber phase numbers as each phase's docs were
     finished (P02/P03/P04/P11 done 2026-08-28). From here on, an entry lands WITH its phase as a
     DoD gate. See the phase-number note at the bottom. -->

## [Unreleased]

### Fixed
- **Streaming an agent with a human approval gate crashed.** LangGraph reports a pending
  interrupt on its update stream under `__interrupt__`, whose value is a *tuple* where every
  other update is a node's dict — so the loop reading node updates died on `'tuple' object has
  no attribute 'get'` and the caller got an error frame naming a type they had never heard of.
- **A streamed run that paused could never be resumed.** Even without the crash, the turn ended
  with a bare `done` and **no resume token was ever minted**: the client was told the turn had
  finished while the run sat waiting in the checkpointer, unresumable by anyone. `:stream` now
  emits a `paused` event carrying the question and a working token, and the terminal `done`
  says `paused: true` so a client written before this still terminates and still notices.
- **`agentship verify` failed a valid spec over this machine's setup.** It shares doctor's
  per-spec checks, and doctor rightly refuses to start an agent whose provider SDK is absent or
  whose key is unset — but `verify` asks whether a spec is valid and honest, not whether it can
  run *here*. A correct voice agent was reported as `invalid spec ... needs DEEPGRAM_API_KEY`,
  sending a reader to fix a file that was already right and turning the report red on any
  machine missing any provider extra, CI included. The split is now by what kind of wrong it
  is: an unknown provider or framework NAME is wrong in the file and is always reported; a
  missing SDK or unset key is wrong only here, and belongs to `doctor` and `serve`.
- **Every MCP tool looked like local code in every trace.** `agentship.tool.mcp_server` was in
  the frozen contract, documented, and stamped by code that genuinely ran — onto a map nothing
  ever populated, because the callback accepted `mcp_servers` and no caller passed one. A slow
  or failing remote server was indistinguishable from slow code of our own, which is the one
  thing the attribute exists to tell apart.

### Added
- **A landing page that leads with proof.** The docs front door was a file index; it now opens
  with the `agentship verify` report, one architecture diagram, and an honest per-capability
  status table that says `seam only` and `unproven live` where those are the truth.
- **Studio badges describe the agent, not the engine.** They were drawn from engine
  capabilities, so all nine demo agents showed the identical six chips and `assistant`
  advertised `tools` and `team` while declaring neither. They now show the model, the tools by
  name, voice, durability and team size — what this agent actually asks for.
- **Studio suggests what to say.** An agent with no messages showed a blank rectangle; it now
  shows what the agent is for and three starters derived from the tools it declares, so the
  suggestions cannot drift into offering a search to an agent that cannot search.
- **Every tracing backend proves delivery in CI.** Each of Phoenix, Opik, LangSmith and Langfuse
  now exports a real span to a throwaway in-process collector, asserting it POSTs to that
  backend's documented path with its own credential. "Works across all four" had rested on
  read-back tests that are live-only and skip in CI — asserted, never verified.

## [0.0.3] — 2026-09-15

**Voice, and the conversation memory that never worked.** Adds a seventh distribution,
`agentship-voice`, and fixes two things in the kernel that were wrong long before it.

### Added
- **`agentship-voice`** — talk to an agent. A vendor-free seam (`VoiceTurn`: text in, spoken
  text out) with adapters for **Pipecat** and **LiveKit**, and 16 speech providers behind a
  registry (Deepgram, ElevenLabs, Cartesia, AssemblyAI, Gladia, Groq, Speechmatics, OpenAI and
  more). `pip install "agentship-sdk[voice]"`, then a framework extra.
- **`WS /v1/agents/{name}/voice`** — voice as a capability of the service you already run: same
  auth, same registry, same tenant scoping. A browser authenticates over the `bearer`
  subprotocol, because it cannot set headers on a WebSocket handshake.
- **`voice:` block on an agent spec** — framework, providers, models, language, endpointing and
  speech rate, beside `observability:` where the kernel owns the authoring surface.
- **Studio is a playground** — a microphone above the conversation, per-turn latency with a
  per-stage breakdown, the agent's declared spec, tool steps that expand to their arguments and
  results, and a per-turn model override that never touches the spec.
- **`agentship voice serve` and `agentship voice providers`** — the second answers a question a
  spec cannot: whether this machine can actually reach the provider you named.
- **Per-turn timings on every response**, `ttft_ms` kept apart from `total_ms`.
- **A spoken turn is traced like any other turn.** A `voice.turn` span wraps the ordinary
  `agent <name>` tree — same inner tree a REST call produces, one honest layer on top — carrying
  the providers in play, the per-stage timings, and the stage that took the largest share. A
  barge-in is marked `agentship.voice.cancelled` rather than errored, because being interrupted
  is the feature working. SEMCONV `0.2.0`, additive: every `0.1.0` name and key is unchanged.
- **Six voice conformance cells** (`CONF-VOICE-1..6`) — the 850 ms first-audio budget, the
  latency trace, the span tree, REST/voice parity, framework parity, and barge-in honesty. All
  keyless, driving a real pipeline with stand-ins that subclass Pipecat's own service classes.
- **`agent.amend_history()`** — rewrite what an agent is recorded as having said. On LangGraph
  this replaces the last reply by message id, so `add_messages` updates rather than appends.
- **`greeting`, `max_session_seconds`, `speak_field` and `fallback_text`** on `voice:`. A voice
  agent that waits in silence reads as broken; an abandoned browser tab otherwise holds its
  socket and provider connections forever; a schema'd agent would read its own JSON aloud.
- **Voice adapters register through `agentship.voice_frameworks` entry points**, the mechanism
  engines already use, so a third framework is a package rather than a patch here.
- **A local dev key.** `agentship serve` with no keys configured used to 401 everything,
  including Studio's own calls. On loopback it now mints one and prints it.

### Fixed
- **An agent had no conversation memory — on any path.** The graph's message channel was a
  plain list with no reducer, so every turn replaced the conversation and the agent began from
  nothing. P03 had been marked done.
- **Two tenants could read each other's conversations.** The checkpoint thread was the
  client-supplied `session_id` alone; it is now keyed by tenant and agent as well. Reachable
  only once memory worked, and found by adversarially reviewing the fix above.
- **`[cancelled by user]` never reached the conversation.** The marker was built, tested, and
  read by nothing, so history kept the full intended reply — the model then believed it had
  said a sentence the human heard three words of, and opened the next turn with "as I
  mentioned…". An interrupted turn now rewrites history to what was actually spoken.
- **Only the first utterance of a session was timed.** A session holds one `VoiceTurn`, and the
  per-utterance state was never cleared: `llm_ttft_ms` is stamped only while unset, so the
  latency panel showed one real measurement and then froze, while the transcript accumulated
  the whole conversation and the barge-in clip point drifted with every exchange.
- **Recognition and synthesis went unmeasured.** The trace carried the model's two numbers and
  nothing else, so a turn that felt slow because the recogniser was slow appeared as a fast
  model and an unexplained wait. Both stages are now attributed, and `total_ms` measures the
  only thing a human feels: silence to first audio.
- **Streamed turns were never checkpointed** — `astream` ran with no thread config, so a voice
  caller had no memory while a text caller did.
- **The system prompt was stored once per turn**, twelve copies after twelve turns.
- **A Python MCP server now runs under the interpreter that spawned it**, instead of whatever
  `python3` PATH resolves to — which outside a virtualenv is a system Python with no `mcp`.
- **A malformed `resume_token` is a 422, not a 500**, and an unexpected 500 keeps its security
  headers and trace id.

### Changed
- **Seven distributions in lockstep**, not six. `agentship-voice` was held back while the phase
  moved; holding it back also meant nobody could install a working voice agent from an index.

## [0.0.2] — 2026-09-07

**The first release that can actually run an agent.** `0.0.1` claimed the six names on PyPI and
shipped a build whose quickstart failed on install; this replaces it. `0.0.1` has been yanked.

### Fixed
- **An agent runs without the observability adapter installed.** `provider` defaults to `otel`
  and every agent is traced, so `pip install "agentship-sdk[starter]"` — which does not include
  `agentship-observability` — could not run *any* agent, including the keyless echo example the
  README opens with. A provider that was explicitly asked for still fails loudly; a defaulted one
  now logs at debug and runs untraced.

### Release engineering (2026-09-07)
- **A tag now publishes.** `release.yml`'s tag trigger had been commented out, so `git push --tags`
  did nothing. Re-enabled as `push: tags: ["v*"]` on the live `on:` block, keeping the
  `packages`/`target`/`auth` inputs that the manual rehearsal path depends on.
- **Fixed the install-back-out gate**, which could not have passed: it installed
  `agentship[starter]` (the meta-package is `agentship-sdk`) pinned to `${GITHUB_REF_NAME#v}`
  (the string `main` on a manual run). The version now comes from the packages.
- **The gate now runs the README quickstart** — keyless, in a clean directory, with provider keys
  emptied — before PyPI is touched. `--help` plus an import passed against `0.0.1`, which could
  not run an agent at all; the check has to reproduce what a user does.
- **Documented the release strategy**: ADR 0005 (lockstep versioning, tag-triggered releases from
  `main`, no release branches, `0.0.x` while pre-release) and a rewritten `RELEASING.md` covering
  branching, cadence, and hotfix-from-a-tag.
- **Recorded the actual state of both indexes.** PyPI has all six projects and working trusted
  publishers; **TestPyPI has none of the six**, and no `testpypi-*` environments or pending
  publishers exist. A tag would therefore fail at the `testpypi` job before reaching PyPI —
  correctly, but it means TestPyPI must be set up before the first tag.
- **Note on `0.0.1`:** it is live on PyPI and cannot run an agent. PyPI versions are immutable, so
  it will be superseded by `0.0.2` and yanked, never replaced.

## [0.0.1] — 2026-09-06 — YANKED

The name-claiming upload: the six distributions were published to reserve their names on PyPI.
The build itself was unusable — `agentship run` failed on the README's own quickstart — so this
version has been **yanked** and is skipped by every resolver. Use `0.0.2` or later. The number
cannot be reused; PyPI versions are immutable.

Everything below shipped in this release. Entries are grouped by delivery phase rather than by
version, because the project had not cut a tagged release when they were written.

### Testing & verification tooling (2026-08-25)
- Promoted the conformance capability catalogue into the shipped, vendor-free `agentship.conformance`
  module (importable from `agentship-core`, no longer test-only): `CAPABILITIES`, `Capability`,
  `run_capability_grid`, `CellResult`, and `DEFERRED_CAPABILITIES`. Imports only `agentship.*` /
  `pydantic` / stdlib — never langchain/langgraph/litellm.
- Moved the LangGraph offline harness into `agentship_langgraph.testing` (the `offline` model-seam
  provider + `build_hitl_agent` factory), passed *into* the grid so the catalogue stays engine-neutral.
- Added the `hitl` conformance cell; `langgraph` now declares `hitl="interrupt"` and proves it by
  surfacing a real interrupt pause rather than running straight through.
- Added the `agentship verify [--agents-dir DIR] [--live/--offline]` CLI — the user-facing "verifiable
  agents" honesty report over the same grid, plus spec-validation, observability, service-contract, and
  A2A-interop sections that report SKIPPED (with a reason) when their optional dependency or target is
  absent. Runs fully offline; no provider keys.
- Fixed stale phase numbers in the conformance surface.
- Offline baseline: **687 passed / 14 skipped / 2 xfailed**.

### P05 — Observability (delivered 2026-08-18, `feat/phase-07-observability`)
- OpenTelemetry-based tracing behind a vendor-free `Observer` seam; the kernel never imports OTel.
- Full trace tree: AGENT root span with model / tool / MCP / graph-node child spans (in-flight — see
  STATUS.md for the remaining real-MCP-server full-trace-tree test).
- OTLP/HTTP export (Phoenix-compatible) with no vendor SDK lock-in; `RecordingObserver` for offline
  trace assertions, proven at parity with the real OTel path (`test_observer_parity.py`).
- Vendor-free semantic-convention keys guarded against OTel-GenAI / OpenInference upstream
  (`test_semconv_upstream.py`).

### P18–P19 — Agent gateway / A2A (in-flight, 15/24 reconciled)
- Agent-to-Agent (A2A) wire models and AgentCard emission, conformance-guarded against `a2a-sdk`
  (`test_a2a_conformance.py`). Core A2A path done (39 tests). Task-bridge + push blocked on P12's
  `on_state_change` hook — see STATUS.md.

### P06–P09 — Service & security (in-flight, 40/44 aggregate)
- FastAPI service exposing agents over REST + SSE streaming (`sse-starlette`) + WebSocket.
- `AuthProvider` seam with ForwardedHeader / ApiKey / JWT (PyJWT `PyJWKClient`) adapters; scope-based
  `authorize()`. Per-tenant isolation with `TenantViolation` on cross-tenant access.
- HTTP security posture: CORS, security headers, optional rate-limit (off by default, no Redis
  required). Open: `agentship deploy` CLI, Postman collection verify, dev-ingress lockdown cell.

### P04 — Tools & MCP (in-flight, 26/28 reconciled)
- Vendor-free `Tool` type (name + description + Pydantic `args_schema` + callable) in the kernel,
  plus four built-ins: `calculator`, `http_request`, `web_search`, `scrape_url`. A `tools:` entry
  is a registered name or a `module:function`.
- MCP via `langchain-mcp-adapters`' `MultiServerMCPClient` — local `stdio` and remote
  `streamable_http` servers in one `mcp:` block. **Not hand-rolled**: protocol, transports, and
  OAuth come from the library on the official `mcp` SDK
  ([ADR 0004](decisions/0004-consume-langchain-mcp-adapters.md)). Pinned `mcp>=1.28,<2`, guarded
  by `agentship doctor`; optional and lazily imported, so a bare install carries no MCP dependency.
- MCP tools are bound as the same `Tool` type as native ones — the model cannot tell them apart.
- `skills:` (a `SKILL.md` folder teaching the model *how* to use a tool) and `allowed_tools:`
  (an allow-list over native + MCP tools, the guard against many servers flooding the model).
- deepagents-backed `template: autonomous` for planner-executor tool loops with zero author code.
- Capability page: [tools-and-mcp.md](capabilities/tools-and-mcp.md).
- Open (both proof gaps, not missing features): a graceful tool-error test — a raising tool should
  degrade into an error the model can recover from, not a crashed run; and a demo of one agent
  using a native tool **and** an MCP tool in the same turn.

### P02 — Multi-agent supervisors (in-flight, 18/19 reconciled)
- Supervisor orchestration over plain LangGraph `StateGraph`:
  `classify → lookup_route → dispatch → resolve → confirm_write? → safety_gate`. Routing is a pure
  lookup, not an LLM handoff, so the same input always takes the same path — the property P11's
  identical-resume guarantee depends on. `langgraph-supervisor` deliberately not adopted:
  [ADR 0002](decisions/0002-plain-stategraph-supervisor.md).
- Three member shapes the supervisor treats identically: **inline**, **`ref:`** (another agent's
  YAML), and **`a2a:`** (an agent on another host). Local → networked is a one-line change.
- Fully declarative supervisors — `members:` with `ref:` and `description:`, **zero Python**.
  A `code:` factory remains available when custom routing is wanted.
- `ConflictResolver` merges competing specialist answers by priority list with an explicit
  tie-break (`first_by_priority` / `highest_confidence`); pure — no model call, no I/O.
- Capability page: [multi-agent.md](capabilities/multi-agent.md).
- Open: `classify` does not yet stamp `TaskHint(model=…)`, so an easy branch cannot be routed to a
  cheaper model.

> **Durability moved out of P02.** Checkpointing/HITL is now P03 and crash-resume is P11 — see the
> renumber note at the bottom of this file.

### P03 — Checkpointing & HITL (in-flight, 8/9 reconciled)
- `durability: none | checkpoint | workflow` on `AgentSpec`, enforced by the capability gate: an
  engine declaring `durability="none"` fails at **build time** rather than silently downgrading a
  crash-recovery promise to nothing.
- Backend chosen from the environment, not the spec — `InMemorySaver` with no database,
  `AsyncPostgresSaver` when `AGENT_SESSION_STORE_URI` is set. The same YAML runs both ways.
  Schema DDL is opt-in (`agentship doctor` / first boot), never on process start.
- LangGraph's checkpoint **flush mode** deliberately kept OUT of the spec: `durability: checkpoint`
  means "I want crash safety", not a flush-timing knob, so the engine picks `sync` internally and
  the vendor's vocabulary never reaches portable YAML.
- `confirm_writes: true` — side-effecting tools pause via LangGraph `interrupt()` before running;
  the turn returns a resume token with nothing written, and the write fires only on
  `{"approved": true}`. Rejected at build time without `durability: checkpoint`, so a pause is
  never non-durable by accident.
- Capability page: [checkpointing-and-hitl.md](capabilities/checkpointing-and-hitl.md) ·
  [ADR 0003](decisions/0003-langgraph-checkpointer-as-the-durability-substrate.md).
- Open: the `reclaim_mid_flight` conformance cell (a worker dies holding a thread; a second worker
  picks it up).

### P11 — Durable resume (in-flight, 11/13 reconciled)
- `ResumeToken` + `engine.resume` seam; idempotency ledger (`call_once` / `idem_key`) with a
  write-ahead `pending` entry; `ThreadLock` for single ownership of `(tenant, thread)`.
- Capability page: [durable-resume.md](capabilities/durable-resume.md).
- **Open, and the project's most important missing proof:** no integrated test of supervisor +
  checkpoint + mid-run failure + resume yielding identical output; `durable_resume_after_kill` is
  xfail; exactly-once is proven for a single tool call, not across a supervisor dispatch.

### P01 — Engine & LangGraph agent (delivered)
- `LangGraphAgent` behind the `Engine.build` seam (`build_agent(spec)` → `RunnableAgent`) with `run` / `stream` / `resume`.

### P00 — Foundation & contracts (delivered 2026-08-07, panel-scored 9.7/10)
- Vendor-free kernel: core base classes (`RunContext`, `Tool`, the `Engine` ABC), the agent/spec model,
  and the registry. Everything above the kernel is a thin adapter behind these seams.

---

## Note on phase numbers (renumbered 2026-08-23)

Phases were renumbered so that **one number = one feature**. Entries above are being migrated to the
new numbering as each phase's docs are finished; a heading not yet migrated still carries its old
number. The mapping for the headings in this file:

| Old | Was | New |
|---|---|---|
| P02 | Multi-agent & durability | **02** Multi-Agent Supervisors · **03** Checkpointing & HITL · **11** Durable Resume |
| P03 | Tools & MCP | **04** Tools & MCP |
| P04 | Service & security | **06** Service Endpoints · **07** Auth · **08** Tenant Isolation · **09** HTTP Posture & Deploy |
| P05 | Agent gateway | **18** A2A Core · **19** A2A Push |
| P07 | Observability | **05** Observability |

Full crosswalk and the authoritative board: `.spec-dev/STATUS.md`.
