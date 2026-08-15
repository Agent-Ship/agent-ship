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
from conftest import requires_live_key

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


@requires_live_key
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
