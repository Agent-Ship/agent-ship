# Architecture

How AgentShip is put together, and why. Written for someone deciding whether to build on it
or contribute to it — so it states the rules the code is held to, and where it currently
falls short of them.

The project is documented with the [C4 model](https://c4model.com): context, then containers,
then one turn traced end to end. The same three figures appear in the
[JOSS paper](paper.md), rendered from the Mermaid sources under [`figures/`](figures/)
(`make figures`).

---

## The one rule

**The kernel imports no vendor library.**

`agentship-core` owns the spec, the run contract, identity and the tracing seam. It does not
import LangGraph, Pipecat, OpenTelemetry, FastAPI or any provider SDK. Everything vendor-shaped
sits behind a seam and is resolved at runtime through an entry-point registry.

This is not tidiness for its own sake. It buys three things:

- **A capability can be read without installing anything.** An evaluator, an audit hook or a
  CI check can import `agentship.observability.semconv` and read the frozen trace contract with
  only the kernel present.
- **A spec validates without a runtime.** `agentship verify` checks a spec naming Deepgram on a
  machine with no Deepgram SDK and no key, because the *name* is spec and the *SDK* is
  environment. (See [Verify vs doctor](#verify-vs-doctor).)
- **Swapping a vendor is a line of YAML**, not a migration, because nothing above the seam
  knows which implementation answered.

The guard is a test, not a convention: the kernel's dependency set is asserted in CI.

## Level 1 — Context

![System context](figures/architecture.png)

Solid borders are components that ship today. The dashed one is specified and not yet built —
drawn rather than omitted so the shape of the system is honest about where it is going, and
dashed rather than solid so no figure claims software that does not exist.

## Level 2 — Containers

![Container view](figures/capability-grid.png)

Seven published packages, in three layers.

| Package | Layer | What it owns |
|---|---|---|
| `agentship-core` | kernel | `AgentSpec`, `RunContext`, `Engine` ABC + `EngineCapabilities`, `Tool`, middleware, `Observer` seam, conformance grid |
| `agentship-langgraph` | adapter | compiles a spec into a LangGraph `StateGraph`; tools, MCP, checkpointing, HITL |
| `agentship-voice` | adapter | the speech cascade, behind a three-method seam with Pipecat and LiveKit implementations |
| `agentship-observability` | adapter | the OTel pipeline and its exporters |
| `agentship-service` | entry point | REST, SSE, WebSocket, auth, tenant scoping, Studio |
| `agentship-cli` | entry point | `run`, `serve`, `doctor`, `verify`, `voice` |
| `agentship-sdk` | meta | installs the stack |

They ship in **lockstep**: one version across all seven, siblings pinned exactly. A mixed set
is a combination nobody has tested, so the release tooling refuses to produce one.

## Level 3 — One turn

![Dynamic view](figures/turn-lifecycle.png)

The thing to notice is what is *not* in that diagram: a second code path for voice. A spoken
turn enters through `agentship-voice`, and from `RunnableAgent.stream()` onward it is the same
call the HTTP path makes — same memory, same tools, same tenancy, same trace. That claim is
checked by a conformance cell (`CONF-VOICE-4`) which asserts the spoken answer and the REST
answer are the same string.

---

## The seams

Each seam is deliberately small. The size is the design: a seam wide enough to be convenient
is wide enough to leak a vendor's model into the kernel.

**`Engine`** — `build`, `run`, `stream`, `resume`, `amend_conversation`. An engine also
publishes an `EngineCapabilities` record, and a spec requesting something the engine has not
declared is rejected at build time rather than failing mid-run.

**`Observer`** — `span`, `start_span`, `on_model`, `current_trace_id`. No OpenTelemetry types
cross it; the kernel speaks `SpanKind` and `Usage`, both of which it defines.

**`VoiceAdapter`** — `host`, `run`, `missing_dependency`. The shared contract is one sentence:
*given what the human said, stream back what the agent says.* Pipecat wants a `FrameProcessor`
in a frame graph and LiveKit wants an `llm.LLM` in a session; the seam is small enough that
both are honest implementations rather than one shape bent into the other's.

**`AuthProvider`** — resolves a request into a `Caller`. Tenant identity then rides a
contextvar that every store read consults, so cross-tenant access is a miss rather than a leak.

**`Tool`** — a name, a description, an optional argument schema, a callable. A native tool and
an MCP tool are indistinguishable to the agent, *including when they fail*.

## Declare, don't fake

A framework built on someone else's runtime has to defend its own claims, so two disciplines
are enforced continuously rather than reviewed.

**The conformance grid** drives every registered engine against every capability it declares.
An engine that claims durable resume but inherited the base implementation fails its own suite.
Structured output is switched **off** in the reference engine today for exactly this reason.

**`agentship verify`** runs that grid offline, with no provider keys, and reports honest
`SKIPPED` sections for anything it could not exercise:

```
engine×capability grid ...... 11/11 ✓
spec validation ............. 10/10 ✓
observability span-tree ..... 4/4   ✓
service contracts ........... 9/9   ✓
A2A interop ................. SKIPPED (no spec exposes a2a)
→ all declared capabilities proven, 0 over-claims
```

### Verify vs doctor

They answer different questions, and conflating them was a real bug:

| | `verify` | `doctor` / `serve` |
|---|---|---|
| Is the spec valid and honest? | ✅ | ✅ |
| Unknown provider or framework name | ✅ caught | ✅ caught |
| SDK installed **here**? | not checked | ✅ checked |
| API key set **here**? | not checked | ✅ checked |

A missing key is a deployment fact, not an over-claim. Reporting it as an invalid spec sent
readers to fix a file that was already correct, and turned the report red in CI.

## Observability contract

One interaction is one trace. The span names and attribute keys are a **frozen, versioned
contract** (`SEMCONV.md`, currently `0.2.0`) because downstream phases read traces by those
exact strings.

```
voice.turn                (INTERNAL)  — only on the voice channel
└── agent                 (AGENT)     — root of an ordinary turn
    ├── node.<name>       (INTERNAL)
    │   ├── model         (LLM)       — tokens, cost, latency
    │   └── tool.<name>   (TOOL)      — names its MCP server, if it came from one
    └── memory.write      (INTERNAL)
```

Content — prompts, replies, transcripts — is gated behind `capture_content`, off by default. A
transcript is exactly as sensitive as a prompt, so it rides the same switch rather than a
second one somebody has to remember.

## Where this falls short today

Stated here because a design document that only describes intentions is marketing.

- **Durable resume is unproven.** The seam is built and tested; nothing yet proves a run killed
  mid-execution resumes to an identical result. The test exists as a strict `xfail`.
- **The LiveKit adapter has never run against a live room.** It is unit-tested only.
- **A sub-agent's span is a sibling of `node.dispatch`, not a child.** Cosmetic — totals and
  every model/tool parentage are correct — and the real fix is costed in the phase notes rather
  than attempted a third time.
- **Long-term memory is not built**, and its spec needs rewriting first: it describes building a
  pgvector backend, which contradicts the integrate-don't-reinvent rule now that LangGraph ships
  `BaseStore` with vector search.

## Decisions

The reasoning behind the big choices lives in [`docs/decisions/`](docs/decisions/):

| | |
|---|---|
| [0001](docs/decisions/0001-integrate-not-invent.md) | Integrate, don't reinvent |
| [0002](docs/decisions/0002-plain-stategraph-supervisor.md) | A plain `StateGraph` supervisor, not a framework's |
| [0003](docs/decisions/0003-langgraph-checkpointer-as-the-durability-substrate.md) | LangGraph's checkpointer as the durability substrate |
| [0004](docs/decisions/0004-consume-langchain-mcp-adapters.md) | Consume `langchain-mcp-adapters`; never hand-roll an MCP client |
| [0005](docs/decisions/0005-lockstep-versioning-and-tag-triggered-releases.md) | Lockstep versioning, tag-triggered releases |
| [0006](docs/decisions/0006-voice-cascade-behind-one-tiny-seam.md) | A cascaded voice pipeline behind one tiny seam |
