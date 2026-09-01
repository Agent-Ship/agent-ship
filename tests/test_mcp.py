"""Live slice: an agent uses a local MCP server's tool (Phase 03 — MCP).

Loads ``agents/mcp/agent.yaml``, which connects to a bundled local **stdio** MCP server
(``agents/mcp/time_server.py``) via ``langchain-mcp-adapters``. The MCP tool ``days_between`` is
discovered and bound automatically; the model calls it and answers. Live: it calls OpenAI. Skips
cleanly without a key; also skips if the ``[mcp]`` extra isn't installed.

Run it (with a key set)::

    set -a; source ../agentship/.env; set +a
    pytest tests/test_mcp.py -q
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from agentship import build_agent
from agentship.skills import render_agent_prompt
from conftest import live_only

pytest.importorskip("langchain_mcp_adapters", reason="needs agentship-langgraph[mcp]")

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT = str(REPO_ROOT / "agents" / "mcp" / "agent.yaml")
SKILL = str(REPO_ROOT / "agents" / "mcp" / "skills" / "date-math")


def test_skill_teaches_the_agent_how_to_use_the_mcp_tool():
    """The date-math skill's how-to guidance is injected into the agent's system prompt (offline)."""
    prompt = render_agent_prompt("You are a precise assistant.", [SKILL])
    assert "date-math" in prompt
    assert "days_between" in prompt  # the skill tells the model which MCP tool to call
    assert "You are a precise assistant." in prompt


@live_only
async def test_agent_uses_a_local_mcp_tool():
    """The agent discovers the local MCP server's `days_between` tool and answers 225.

    Skips cleanly if the stdio server can't start (e.g. the `python3` on PATH lacks the `mcp`
    extra because the venv isn't active) — the framework's own MCP integration test proves the
    wiring with a guaranteed interpreter.
    """
    cwd = os.getcwd()
    os.chdir(REPO_ROOT)  # the mcp args path is repo-root-relative
    try:
        try:
            agent = build_agent(AGENT)
        except Exception as exc:  # noqa: BLE001 - the stdio server couldn't be spawned/imported
            pytest.skip(f"local MCP server unavailable (activate the venv + install [mcp]): {exc}")
        assert "days_between" in agent.compiled.bound_tools
        result = await agent.run("How many days from 2026-01-01 to 2026-08-14?")
    finally:
        os.chdir(cwd)
    assert "225" in result.output


# ---- one turn, both kinds of tool -------------------------------------------------------------

COMBINED = str(REPO_ROOT / "agents" / "mcp" / "combined.yaml")


def test_a_native_tool_and_an_mcp_tool_are_bound_side_by_side():
    """Both sources end up in one tool list the model cannot tell apart.

    ``calculator`` ships with the framework; ``days_between`` is discovered from the local
    stdio MCP server. The phase's headline claim is that they are indistinguishable to the
    agent, and nothing in this repo showed both together before. Keyless: binding happens at
    build time, no model call.
    """
    agent = build_agent(COMBINED)
    bound = set(agent.compiled.bound_tools)

    assert "calculator" in bound, f"native tool missing from {bound}"
    assert "days_between" in bound, f"MCP tool missing from {bound}"


# Both live turns below are `live_only`, not cassette-replayed. MCP tool discovery does not
# return its tools in a stable order, so the request body differs between record and replay and
# VCR never matches. Recording them anyway would leave two permanently-red tests; pretending
# they replay would be worse. The deterministic half — that a native tool and an MCP tool are
# bound side by side — IS keyless, and it is the claim that matters.

@live_only
async def test_one_turn_calls_the_mcp_tool_then_the_native_tool():
    """A single question needing both: a date difference (MCP) times three (native).

    Asserts the run used BOTH sources in one turn — the claim the capability page opens with.
    """
    agent = build_agent(COMBINED)
    used: list[str] = []
    async for event in agent.stream(
        "How many days from 2026-01-01 to 2026-08-14, and what is that times 3?"
    ):
        if event.type == "tool_call":
            used.append(event.data["tool"])

    assert "days_between" in used, f"the MCP tool was never called; used {used}"
    assert "calculator" in used, f"the native tool was never called; used {used}"
