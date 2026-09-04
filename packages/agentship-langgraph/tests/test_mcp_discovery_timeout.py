"""A hung MCP server fails its own agent — it must not hang the whole service.

MCP tools are discovered while the agent is BUILT, which for `agentship serve` happens before
the socket binds. Discovery used to wait forever, so one unresponsive server left every agent
in the deployment unstartable: a real container sat at "connecting to 1 server(s): notes" for
five minutes with nine agents behind it and no port open.

Bounded instead: discovery gives up and raises something actionable naming the server, and
`serve`'s doctor gate then reports that one spec as bad while the rest are served.
"""

from __future__ import annotations

import time

import pytest
from agentship.errors import AgentShipError
from agentship_langgraph import mcp as mcp_module


def test_a_quiet_server_raises_instead_of_hanging_the_caller(monkeypatch):
    """No event loop running: an awaiting-forever server is cut off by wait_for."""
    import asyncio

    async def _never_returns(_mcp):
        """A server that accepts the connection and then never answers."""
        await asyncio.sleep(30)

    monkeypatch.setattr(mcp_module, "discover_mcp_tools", _never_returns)
    monkeypatch.setattr(mcp_module, "DISCOVERY_TIMEOUT_S", 0.5)

    started = time.monotonic()
    with pytest.raises(AgentShipError) as err:
        mcp_module.discover_mcp_tools_sync({"notes": object()})
    elapsed = time.monotonic() - started

    assert elapsed < 10, f"discovery blocked for {elapsed:.1f}s instead of giving up"
    assert "notes" in str(err.value), f"the error must name the server: {err.value}"


async def test_a_wedged_server_cannot_hang_a_running_service(monkeypatch):
    """An event loop IS running — the path `agentship serve` takes — and the client is WEDGED.

    This is the case that actually broke a container: discovery ran on the thread-pool bridge
    and blocked, so no `await` could ever cancel it. Only a bounded `future.result` gets the
    caller back, which is why the timeout guards both paths and not just the async one.
    """

    async def _blocks_the_thread(_mcp):
        """Blocking, not awaiting — nothing can interrupt this from outside."""
        time.sleep(30)

    monkeypatch.setattr(mcp_module, "discover_mcp_tools", _blocks_the_thread)
    monkeypatch.setattr(mcp_module, "DISCOVERY_TIMEOUT_S", 0.5)

    started = time.monotonic()
    with pytest.raises(AgentShipError) as err:
        mcp_module.discover_mcp_tools_sync({"notes": object()})
    elapsed = time.monotonic() - started

    assert elapsed < 10, f"the service stayed blocked for {elapsed:.1f}s"
    assert "notes" in str(err.value)


def test_a_responsive_server_is_unaffected(monkeypatch):
    """The timeout does not change the normal path."""

    async def _responds(_mcp):
        """Return promptly, as a healthy server does."""
        return ["a-tool"]

    monkeypatch.setattr(mcp_module, "discover_mcp_tools", _responds)
    assert mcp_module.discover_mcp_tools_sync({"notes": object()}) == ["a-tool"]
