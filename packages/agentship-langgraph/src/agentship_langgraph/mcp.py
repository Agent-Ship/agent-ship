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
import logging
import os
from typing import TYPE_CHECKING, Any

from agentship.errors import AgentShipError, CapabilityError

if TYPE_CHECKING:
    from agentship.spec import McpServerSpec
    from langchain_core.tools import BaseTool

_mcp_logger = logging.getLogger("agentship.mcp")


#: The supported ``mcp`` SDK range: ``>=1.28,<2``. v2 reshaped the client API the adapters target,
#: so an out-of-range install is flagged by ``agentship doctor`` (see :func:`mcp_version_ok`).
_MCP_MIN = (1, 28)
_MCP_MAX_EXCLUSIVE = (2, 0)


def mcp_version_ok() -> tuple[bool, str | None]:
    """Return ``(ok, installed_version)`` for the ``agentship doctor`` MCP version guard.

    ``ok`` is ``True`` only when the ``mcp`` SDK is installed *and* its version is within
    ``>=1.28,<2``. When ``mcp`` is not installed the version is ``None`` and ``ok`` is ``False``
    (an agent with ``mcp:`` servers cannot connect). Never raises.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            installed = version("mcp")
        except PackageNotFoundError:
            return (False, None)
    except Exception:  # noqa: BLE001 - a metadata hiccup must not crash doctor
        return (False, None)
    parts = tuple(int(p) for p in installed.split(".")[:2] if p.isdigit())
    return (_MCP_MIN <= parts < _MCP_MAX_EXCLUSIVE, installed)


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

    server_names = ", ".join(mcp)
    _mcp_logger.info("connecting to %d server(s): %s", len(mcp), server_names)
    client = MultiServerMCPClient(to_connections(mcp))
    tools = await client.get_tools()
    _mcp_logger.info(
        "discovered %d tool(s) across %d server(s): %s",
        len(tools),
        len(mcp),
        ", ".join(t.name for t in tools),
    )
    return tools


#: How long to wait for an MCP server to list its tools before giving up, in seconds.
#: Override with ``AGENTSHIP_MCP_DISCOVERY_TIMEOUT``.
#:
#: Bounded because discovery happens while the agent is BUILT, which for ``agentship serve`` is
#: before the socket binds: an unresponsive server used to leave every agent in the deployment
#: unstartable. A real container sat at "connecting to 1 server(s)" for five minutes with nine
#: agents behind it and no port open. Failing one spec loudly is strictly better — the doctor
#: gate then names it and serves the rest.
DISCOVERY_TIMEOUT_S = float(os.getenv("AGENTSHIP_MCP_DISCOVERY_TIMEOUT", "45"))


def _discovery_timed_out(mcp: dict[str, McpServerSpec], seconds: float) -> AgentShipError:
    """The actionable error for a server that accepted the connection and then went quiet."""
    return AgentShipError(
        f"MCP discovery timed out after {seconds:g}s for server(s): {', '.join(mcp)} — "
        f"the server started but never listed its tools. Check the command/url in the `mcp:` "
        f"block, run it by hand to see its output, or raise "
        f"AGENTSHIP_MCP_DISCOVERY_TIMEOUT if it is merely slow to start."
    )


def discover_mcp_tools_sync(mcp: dict[str, McpServerSpec]) -> list[BaseTool]:
    """Run :func:`discover_mcp_tools` from sync code (the engine's ``build`` is synchronous).

    Uses :func:`asyncio.run` when no event loop is running; when one is (e.g. discovery triggered
    from inside an async request), the coroutine runs to completion on a dedicated worker thread so
    it never conflicts with the caller's loop.

    Bounded by :data:`DISCOVERY_TIMEOUT_S` on both paths: a server that never answers fails this
    one agent instead of hanging the process that is building it.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:

        async def bounded() -> list[BaseTool]:
            """Await discovery under the timeout, inside the loop asyncio.run creates."""
            return await asyncio.wait_for(discover_mcp_tools(mcp), timeout=DISCOVERY_TIMEOUT_S)

        try:
            return asyncio.run(bounded())
        except TimeoutError as exc:
            raise _discovery_timed_out(mcp, DISCOVERY_TIMEOUT_S) from exc
    _mcp_logger.debug("event loop already running — using thread-pool bridge for discovery")
    # daemon threads: a wedged discovery must not keep the interpreter alive at shutdown.
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(lambda: asyncio.run(discover_mcp_tools(mcp)))
        try:
            return future.result(timeout=DISCOVERY_TIMEOUT_S)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise _discovery_timed_out(mcp, DISCOVERY_TIMEOUT_S) from exc
    finally:
        # wait=False: never block shutdown on a thread that is already stuck.
        pool.shutdown(wait=False)
