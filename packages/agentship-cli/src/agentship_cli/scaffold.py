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
