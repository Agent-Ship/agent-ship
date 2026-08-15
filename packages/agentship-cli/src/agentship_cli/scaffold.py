"""Scaffold templates for ``agentship init`` and ``agentship new-agent``.

These builders return the *text* of the files the scaffolding commands write —
kept out of ``main.py`` so the command code stays about I/O and error handling.
The scaffold is deliberately **single-tenant** (no auth/tenancy concepts): a fresh
project just runs, per the reusability plan. The starter agent uses the default
``langgraph`` engine over ``openai/gpt-4o-mini`` so ``agentship run`` works with a
single ``OPENAI_API_KEY``.

Every emitted spec is a valid :class:`~agentship.spec.AgentSpec` — the init tests
prove the generated ``assistant.yaml`` loads *and* builds, so these templates
cannot silently drift from the spec schema.
"""

from __future__ import annotations

#: The starter agent spec written by ``agentship init`` (default single-tenant).
ASSISTANT_YAML = """\
# A starter AgentShip agent. Run it with:
#   agentship run agents/assistant.yaml --input "hello"
name: assistant
engine: langgraph
model: openai/gpt-4o-mini
prompt: You are a concise, helpful assistant. Answer in one short sentence.
"""

#: The example environment file. Ships a commented placeholder, never a real key.
ENV_EXAMPLE = """\
# Copy this file to `.env` and fill in the provider key you use.
# The starter assistant uses OpenAI, so only this one is required for the quickstart.
# OPENAI_API_KEY=your-key-here
"""

#: The project README explaining the single-command run path.
README = """\
# My AgentShip project

A single-tenant AgentShip project scaffolded by `agentship init`.

## Layout

- `agents/` — your agent specs (YAML). One starter agent, `assistant.yaml`, is included.
- `.env.example` — copy to `.env` and add your provider key (e.g. `OPENAI_API_KEY`).

## Run the starter agent

```bash
cp .env.example .env          # then edit .env and set OPENAI_API_KEY
agentship run agents/assistant.yaml --input "hello"
```

Stream the response instead of waiting for the whole answer:

```bash
agentship run agents/assistant.yaml --input "hello" --stream
```

## Check your agents

Validate every agent spec against its engine's capabilities before running:

```bash
agentship doctor --agents-dir agents
```

## Add another agent

```bash
agentship new-agent researcher
```
"""


def new_agent_yaml(name: str, engine: str) -> str:
    """Return the text of a single starter agent spec named ``name`` on ``engine``.

    Written by ``agentship new-agent``. The spec is a valid
    :class:`~agentship.spec.AgentSpec`: a single agent with a prompt and, for the
    default ``langgraph`` engine, a ``model`` line so it runs as-is. Other engines
    omit the model line (they may not need one) and leave a comment pointing at it.
    """
    header = (
        f"# Agent {name!r}. Run it with:\n"
        f"#   agentship run agents/{name}.yaml --input \"hello\"\n"
    )
    if engine == "langgraph":
        return (
            f"{header}"
            f"name: {name}\n"
            f"engine: {engine}\n"
            f"model: openai/gpt-4o-mini\n"
            f"prompt: You are a helpful assistant.\n"
        )
    return (
        f"{header}"
        f"name: {name}\n"
        f"engine: {engine}\n"
        f"# model: <provider>/<model>   # add the model this engine should use\n"
        f"prompt: You are a helpful assistant.\n"
    )


def single_template_yaml(name: str) -> str:
    """Return a ``template: single`` spec named ``name`` (one model, zero author code).

    The ``single`` template yields a runnable ReAct agent from the YAML alone —
    ``create_react_agent(model, tools, prompt)`` under the hood — so this spec needs
    no companion ``agent.py``. It targets the ``langgraph`` engine (the only engine
    that ships templates today) over ``openai/gpt-4o-mini``.
    """
    return (
        f"# Agent {name!r} — the `single` template (one model, zero author Python).\n"
        f"#   agentship run agents/{name}.yaml --input \"hello\"\n"
        f"name: {name}\n"
        f"engine: langgraph\n"
        f"template: single\n"
        f"model: openai/gpt-4o-mini\n"
        f"prompt: You are a concise, helpful assistant.\n"
    )


def autonomous_template_yaml(name: str) -> str:
    """Return a ``template: autonomous`` spec named ``name`` (a single self-directing agent).

    The ``autonomous`` template configures one agent that plans and calls its own
    tools in a loop; it needs the optional ``agentship-langgraph[autonomous]`` extra
    and a tool-calling model, so the scaffold is a coherent, loadable spec that
    ``agentship doctor`` version-guards before it runs.
    """
    return (
        f"# Agent {name!r} — the `autonomous` template (a single self-directing agent).\n"
        f"# Needs the optional extra:  pip install 'agentship-langgraph[autonomous]'\n"
        f"#   agentship run agents/{name}.yaml --input \"hello\"\n"
        f"name: {name}\n"
        f"engine: langgraph\n"
        f"template: autonomous\n"
        f"model: openai/gpt-4o-mini\n"
        f"prompt: You are an autonomous assistant that plans and uses tools.\n"
    )


def graph_template_yaml(name: str, code_ref: str) -> str:
    """Return a ``graph``-template spec named ``name`` pointing ``code:`` at its ``agent.py``.

    The ``graph`` template is a *custom-authoring* scaffold: the fillable supervisor
    lives in a companion ``NAME/agent.py`` and the spec references it via ``code:``.
    (``template:`` and ``code:`` are mutually exclusive — a graph scaffold *is* an
    authored agent, so it uses ``code:``, not ``template: graph``.) ``code_ref`` is
    the ``"file.py:build_agent"`` reference the caller computes (an absolute path so
    the spec resolves regardless of the working directory it is loaded from — see
    :func:`agentship.spec.resolve_code`).
    """
    return (
        f"# Agent {name!r} — the `graph` template (a fillable supervisor scaffold).\n"
        f"# Open {name}/agent.py and follow the `# TODO(author)` markers.\n"
        f"#   agentship run agents/{name}.yaml --input \"hello\"\n"
        f"name: {name}\n"
        f"engine: langgraph\n"
        f"code: {code_ref}\n"
    )


def graph_template_agent_py(name: str) -> str:
    """Return the companion ``agent.py`` for a ``graph``-template scaffold named ``name``.

    Scaffolds a :class:`~agentship_langgraph.agent.LangGraphAgent` subclass whose
    ``build_graph(model, tools)`` is a compilable coordinator→worker supervisor with
    ``# TODO(author)`` markers where specialists, tools, and richer routing get
    filled in. A ``build_agent()`` factory (referenced by the YAML's ``code:``)
    returns the configured subclass instance, so the scaffold LOADS and BUILDS
    as-is over a real (or, in tests, a fake) model.
    """
    class_name = _class_name(name)
    return f'''"""The ``{name}`` agent — a fillable LangGraph supervisor scaffold.

Scaffolded by ``agentship new-agent {name} --template graph``. Fill in the
``# TODO(author)`` markers: add specialist workers, bind tools, and widen the
routing. The harness wires ``model``/``tools`` and drives ``run``/``stream``; you
own only the graph shape below.

Note: this module intentionally omits ``from __future__ import annotations`` so the
``TypedDict`` state annotations are real objects at class-creation time. LangGraph
reads them via ``get_type_hints``, and a spec loaded from a file path (see
``agentship.spec.resolve_code``) is not registered in ``sys.modules``, so a stringized
``Annotated[...]`` annotation could not be resolved later.
"""

from typing import Annotated

from agentship.spec import AgentSpec
from agentship_langgraph import LangGraphAgent
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class {class_name}State(TypedDict):
    """The supervisor's state: the message history plus the chosen route.

    ``messages`` uses the ``add_messages`` reducer so each node appends rather than
    replaces (the engine seeds ``[system, user]`` and reads the final message as the
    answer). ``route`` is the coordinator's decision the conditional edge switches on.
    """

    messages: Annotated[list, add_messages]
    #: TODO(author): widen to your specialist worker names.
    route: str


class {class_name}Agent(LangGraphAgent):
    """A fillable coordinator -> worker supervisor authored in native LangGraph."""

    def build_graph(self, model, tools) -> StateGraph:
        """Build the coordinator -> worker supervisor over the wired model and tools."""

        def coordinator(state: {class_name}State) -> dict:
            """Decide which worker handles this turn (the routing decision).

            TODO(author): inspect the request and pick among several specialists,
            setting ``route`` to the chosen worker's node name. The scaffold asks the
            model once and always falls through to the single ``worker``.
            """
            decision = model.invoke(state["messages"])
            route = (decision.content or "worker").strip().lower()
            if route != "done":
                route = "worker"
            return {{"route": route}}

        def worker(state: {class_name}State) -> dict:
            """Answer the request, appending the reply to the message history.

            TODO(author): add tools (``model.bind_tools(tools)``), specialist prompts,
            or a sub-graph here. The scaffold just invokes the wired model.
            """
            reply = model.invoke(state["messages"])
            return {{"messages": [reply]}}

        g = StateGraph({class_name}State)
        g.add_node("coordinator", coordinator)
        g.add_node("worker", worker)
        g.add_edge(START, "coordinator")
        # TODO(author): add more branches as you add specialists.
        g.add_conditional_edges(
            "coordinator",
            lambda state: state["route"],
            {{"worker": "worker", "done": END}},
        )
        g.add_edge("worker", END)
        return g


def build_agent() -> {class_name}Agent:
    """Return the configured ``{name}`` agent (referenced by ``{name}.yaml``'s ``code:``)."""
    return {class_name}Agent(
        AgentSpec(
            name="{name}",
            engine="langgraph",
            model="openai/gpt-4o-mini",
            prompt="Route the user request to the right specialist, then answer.",
        )
    )
'''


def _class_name(name: str) -> str:
    """Turn an agent ``name`` (``a-z0-9_``) into a CamelCase Python class prefix.

    ``new-agent`` validates ``name`` to ``[a-z][a-z0-9_]*`` before this runs, so
    splitting on ``_`` and title-casing each part yields a valid identifier prefix
    (e.g. ``ticket_router`` -> ``TicketRouter``).
    """
    return "".join(part.title() for part in name.split("_") if part)
