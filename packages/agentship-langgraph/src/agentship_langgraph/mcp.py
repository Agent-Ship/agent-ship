"""MCP tools via ``langchain-mcp-adapters`` — the OSS client, not a hand-rolled one (Phase 03 · C3).

An agent's ``mcp:`` block (a dict of :class:`~agentship.spec.McpServerSpec`) becomes a
``MultiServerMCPClient`` connections dict; ``get_tools()`` discovers each server's tools as
LangChain tools the engine binds alongside native skills — a local (stdio) and a remote (HTTP)
server look identical to the agent. We write only this thin translation + a sync bridge; the
protocol, transports, and OAuth come from ``langchain-mcp-adapters`` (on the official ``mcp`` SDK).
Install the ``agentship-langgraph[mcp]`` extra; imported lazily so a bare install needs neither.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import TYPE_CHECKING, Any

from agentship.errors import CapabilityError

if TYPE_CHECKING:
    from agentship.spec import McpServerSpec
    from langchain_core.tools import BaseTool


def to_connections(mcp: dict[str, McpServerSpec]) -> dict[str, dict[str, Any]]:
    """Translate the spec's ``mcp:`` block into a ``MultiServerMCPClient`` connections dict.

    Each server becomes one connection keyed by its name: a ``stdio`` server carries
    ``command``/``args``; a ``streamable_http`` server carries ``url`` and any ``headers``. ``None``
    fields are dropped so the library keeps its own defaults.
    """
    connections: dict[str, dict[str, Any]] = {}
    for name, server in mcp.items():
        if server.transport == "stdio":
            connections[name] = {
                "transport": "stdio",
                "command": server.command,
                "args": list(server.args),
            }
        else:  # streamable_http
            conn: dict[str, Any] = {"transport": "streamable_http", "url": server.url}
            if server.headers:
                conn["headers"] = dict(server.headers)
            connections[name] = conn
    return connections


async def discover_mcp_tools(mcp: dict[str, McpServerSpec]) -> list[BaseTool]:
    """Connect to every declared MCP server and return its tools as LangChain tools.

    Raises :class:`~agentship.errors.CapabilityError` with an actionable message if the optional
    ``[mcp]`` extra is not installed, rather than a raw ``ImportError``.
    """
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError as exc:
        raise CapabilityError(
            "this agent declares `mcp:` servers but the MCP extra is not installed — "
            "`pip install agentship-langgraph[mcp]`"
        ) from exc

    client = MultiServerMCPClient(to_connections(mcp))
    return await client.get_tools()


def discover_mcp_tools_sync(mcp: dict[str, McpServerSpec]) -> list[BaseTool]:
    """Run :func:`discover_mcp_tools` from sync code (the engine's ``build`` is synchronous).

    Uses :func:`asyncio.run` when no event loop is running; when one is (e.g. discovery triggered
    from inside an async request), the coroutine runs to completion on a dedicated worker thread so
    it never conflicts with the caller's loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(discover_mcp_tools(mcp))
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(discover_mcp_tools(mcp))).result()
