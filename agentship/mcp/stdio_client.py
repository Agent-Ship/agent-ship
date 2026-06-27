"""Minimal STDIO MCP client: spawn server, discover tools, call tools."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class MCPTool:
    name: str
    description: str
    input_schema: Dict[str, Any] = field(default_factory=dict)


class StdioMCPClient:
    """Connects to an MCP server over STDIO, discovers tools, and calls them."""

    def __init__(self, name: str, command: List[str], env: Optional[Dict[str, str]] = None):
        self._name = name
        self._command = command
        self._env = env or {}
        self._session = None
        self._stdio_ctx = None
        self._session_ctx = None
        self._tools: List[MCPTool] = []
        self._connected = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def tools(self) -> List[MCPTool]:
        return self._tools

    async def connect(self) -> None:
        """Spawn the server process and initialize the MCP session."""
        if self._connected:
            current = asyncio.get_event_loop()
            if current is self._loop:
                return
            # Event loop changed — reconnect
            logger.warning("Event loop changed for MCP server %s, reconnecting", self._name)
            await self._close()

        from mcp import ClientSession, StdioServerParameters, stdio_client

        cmd = self._command
        server_params = StdioServerParameters(
            command=cmd[0],
            args=cmd[1:] if len(cmd) > 1 else [],
            env=self._env or None,
        )

        logger.info("Connecting to MCP server %s: %s", self._name, " ".join(cmd))
        self._stdio_ctx = stdio_client(server_params)
        read, write = await self._stdio_ctx.__aenter__()
        self._session_ctx = ClientSession(read, write)
        self._session = await self._session_ctx.__aenter__()
        await self._session.initialize()

        self._connected = True
        self._loop = asyncio.get_event_loop()
        logger.info("MCP server %s connected", self._name)

        await self._discover_tools()

    async def _discover_tools(self) -> None:
        result = await self._session.list_tools()
        self._tools = [
            MCPTool(
                name=t.name,
                description=t.description or "",
                input_schema=t.inputSchema if isinstance(t.inputSchema, dict) else {},
            )
            for t in (result.tools or [])
        ]
        logger.info("MCP server %s: discovered %d tools: %s", self._name, len(self._tools), [t.name for t in self._tools])

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Call a tool on the MCP server and return the result as a string."""
        if not self._connected:
            await self.connect()

        logger.debug("Calling MCP tool %s.%s with args: %s", self._name, tool_name, arguments)
        result = await self._session.call_tool(tool_name, arguments)
        if result.content:
            parts = []
            for block in result.content:
                if hasattr(block, "text"):
                    parts.append(block.text)
                else:
                    parts.append(str(block))
            return "\n".join(parts)
        return ""

    async def _close(self) -> None:
        self._connected = False
        self._loop = None
        try:
            if self._session_ctx:
                await self._session_ctx.__aexit__(None, None, None)
        except Exception:
            pass
        try:
            if self._stdio_ctx:
                await self._stdio_ctx.__aexit__(None, None, None)
        except Exception:
            pass
        self._session = None
        self._session_ctx = None
        self._stdio_ctx = None
