# Foundation & base classes

The vendor-free kernel of AgentShip: the small, stable seams every later pillar builds on — an agent spec you write in YAML or Python, an identity context every turn carries, an `Engine` base class, a tool type, and a middleware seam — with no vendor SDK in the import path.

## What it is

- **A vendor-free kernel.** Importing `agentship` pulls in Pydantic, PyYAML, and the stdlib — no LangGraph, LiteLLM, or `google-*`. Engines and other vendors are reached only through a registry, so core never imports an adapter (`test_packaging_isolation.py` guards this).
- **The seams.** `RunContext` (`agentship.context`) is the per-turn identity bag — `caller`, `session_id`, `run_id`, `mode` — held in the `current_run` contextvar so concurrent turns never collide. `Engine` (`agentship.engines.base`) is the ABC every engine implements; it must declare an `EngineCapabilities`, so it can never fake a capability it hasn't built. `Middleware` (`agentship.middleware`) is the ordered cross-cutting seam with no-op `on_request`/`on_response`/`on_error` hooks.
- **The spec model.** `AgentSpec` (`agentship.spec`) is the declarative definition of an agent — engine, model, prompt, tools, members, streaming. It is `extra="forbid"`, so an unknown key is a loud error, and it fails fast on incoherent field combinations.
- **The tool type.** `Tool` (`agentship.tools.tool`) is deliberately tiny — a name, a description, an optional Pydantic `args_schema`, and a callable. There is no base class to subclass; you build one by handing it a function.
- **The registry.** `Registry[T]` (`agentship.registry`) is the one extension mechanism, populated by `importlib.metadata` entry points. The `ENGINES` instance holds every installed engine; a vendor ships a class plus one `[project.entry-points."agentship.engines"]` line and it becomes discoverable — no kernel edits.

## How to use it

Define a spec, build it against its engine, and run a turn. `build_agent` resolves the engine by name from `ENGINES`, capability-validates the spec, and compiles it:

```python
from agentship import AgentSpec, build_agent

spec = AgentSpec(name="hello", engine="echo", prompt="You are a helpful assistant.")
agent = build_agent(spec)  # resolves engine via the ENGINES registry
result = await agent.run("hi there")  # RunnableAgent.run → a Result

# Look up which engines are installed via the registry directly:
from agentship.engines.base import ENGINES

print(ENGINES.names())  # e.g. ["echo"] (+ "langgraph" when installed)
```

Specs can also be loaded from YAML with `load_spec`, or authored in Python via a `code: "module:function"` reference resolved by `resolve_code`.

## One runnable example

`examples/hello.yaml` is a complete, dependency-free agent (the `echo` engine needs no API key):

```yaml
# examples/hello.yaml
name: hello
engine: echo
prompt: You are a helpful assistant.
```

```python
import asyncio
from agentship import build_agent, load_spec


async def main() -> None:
    agent = build_agent(load_spec("examples/hello.yaml"))
    result = await agent.run("hello", user_id="demo")
    print(result)


asyncio.run(main())
```

`build_agent` accepts a YAML path directly too, so `build_agent("examples/hello.yaml")` is equivalent to the `load_spec` line above.

## Status & limits

✅ delivered 2026-08-07 (panel-scored 9.7/10). Authoritative status: `.spec-dev/STATUS.md`.
