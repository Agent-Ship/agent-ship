# Engine & agent

The first real, model-backed engine: a LangGraph graph over LiteLLM, plus the base class you subclass to author a native LangGraph agent — driven through one `run`/`stream`/`resume` seam whichever engine backs it.

## What it is

- **The engine seam made concrete.** `LangGraphEngine` (`agentship_langgraph.engine`) is the first shipping `Engine` — it resolves the chat model and (Phase 03) tools, compiles a `StateGraph`, and honestly declares its `EngineCapabilities` (`streaming`, `durability="checkpoint"`, `tool_calling`, `multi_agent`, and its LiteLLM `providers`). A spec that asks for more than it declares fails fast at build.
- **A LangGraph-backed custom agent.** `LangGraphAgent` (`agentship_langgraph.agent`) is the base class you subclass. You implement one abstract method, `build_graph(model, tools) -> StateGraph`, in native LangGraph — nodes, edges, subgraphs, `interrupt()`. The engine hands you an already-wired `model` and `tools`; you never wire a vendor and never lose native power.
- **Templates for the zero-code paths.** `template: single` (a prebuilt ReAct agent from YAML alone), `graph` (a fillable supervisor scaffold), and `autonomous` (a deepagents agent) generate the graph for you — no Python. The custom `build_graph` path is the only one you write code for.
- **One run surface.** You never call the engine directly. `build_agent(spec)` returns a `RunnableAgent` (`agentship.runtime`) whose `run` / `stream` methods drive the middleware pipeline, publish the per-turn `RunContext` on the `current_run` contextvar, then delegate to the engine's `run` / `stream` / `resume`.
- **Durable resume.** The engine mints a `ResumeToken` on each turn; `RunnableAgent`/`Engine.resume` replays a crashed or paused run from its checkpoint (the seam lands here; the real checkpoint saver is Phase 02).

## How to use it

`build_agent` resolves the engine by name, capability-validates the spec, and compiles it. `RunnableAgent.run` returns a `Result`; `RunnableAgent.stream` yields `Event`s:

```python
import asyncio
from agentship import AgentSpec, build_agent

# `template: single` — a prebuilt ReAct agent, zero author code.
spec = AgentSpec(
    name="quickstart",
    engine="langgraph",
    template="single",
    model="openai/gpt-4o-mini",
    prompt="You are a helpful assistant.",
)


async def main() -> None:
    agent = build_agent(spec)  # RunnableAgent

    result = await agent.run("What is 2 + 2?")  # -> Result
    print(result.output)

    async for event in agent.stream("Tell me a joke."):
        print(event.type, event.data)  # token / content / done ...


asyncio.run(main())
```

To author a native graph, subclass `LangGraphAgent` and implement `build_graph`:

```python
from agentship_langgraph import LangGraphAgent
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict


class _State(TypedDict):
    messages: list


class MyAgent(LangGraphAgent):
    def build_graph(self, model, tools) -> StateGraph:
        def answer(state: _State) -> dict:
            reply = model.invoke(state["messages"])  # the wired, cost-traced model
            return {"messages": [*state["messages"], reply]}

        g = StateGraph(_State)
        g.add_node("answer", answer)
        g.add_edge(START, "answer")
        g.add_edge("answer", END)
        return g
```

A YAML spec points its `code: "module:function"` at a builder that returns a configured instance; `build_agent` threads the authored object to the engine's `build`, which calls your `build_graph`.

## One runnable example

`examples/custom/agent.py` is the full custom-authoring path above (backed by `examples/custom/custom.yaml`); the zero-code `examples/quickstart.yaml` is a `template: single` agent. Both run keyless offline:

```bash
agentship run examples/quickstart.yaml --input "hello"
agentship run examples/custom/custom.yaml --input "hello"
```

All four authoring paths are documented in `examples/README.md` and proven by `packages/agentship-langgraph/tests/test_authoring_examples.py`.

## Status & limits

✅ delivered. Authoritative status: `.spec-dev/STATUS.md`.
