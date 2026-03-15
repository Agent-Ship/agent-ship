"""STDIO transport MCP client.

Spawns the server process and communicates over stdin/stdout using the official MCP SDK.

IMPORTANT: MCP ClientSession MUST be used with 'async with' to work properly.
See: https://github.com/modelcontextprotocol/python-sdk/pull/1849

KNOWN ISSUE: During garbage collection, you may see a RuntimeError about "cancel scope"
being exited in a different task. This is a harmless cleanup error caused by anyio's
strict TaskGroup requirements and does not affect functionality. The error occurs
because the MCP SDK's stdio_client uses anyio TaskGroups, which must be entered and
exited in the same async task. During garbage collection, Python may cleanup in a
different task, causing this error. It can be safely ignored.
"""

import logging
import sys
from typing import Any, List, Optional

from mcp import ClientSession, StdioServerParameters, stdio_client

from src.agent_framework.mcp.clients.base import BaseMCPClient
from src.agent_framework.mcp.models import MCPServerConfig, MCPToolInfo, MCPTransport

logger = logging.getLogger(__name__)


class StdioMCPClient(BaseMCPClient):
    """MCP client over STDIO: spawns server process and holds one session.

    Note: This implementation maintains connection across tool calls.
    The session is initialized on first use and reused for subsequent calls.
    """

    def __init__(self, config: MCPServerConfig) -> None:
        if config.transport != MCPTransport.STDIO or not config.command:
            raise ValueError("StdioMCPClient requires transport=stdio and command")
        super().__init__(config)
        self._session = None  # The actual session returned from __aenter__
        self._session_cm = None  # The ClientSession context manager
        self._stdio_context = None  # async context manager, held for lifecycle
        self._is_connected = False
        self._event_loop = None  # Track which event loop created the connection

    def _server_params(self) -> StdioServerParameters:
        """Build MCP SDK stdio parameters from our config."""
        import os
        cmd_list = self.config.command or []
        command = cmd_list[0] if cmd_list else ""
        args = cmd_list[1:] if len(cmd_list) > 1 else []
        # Always merge with the current process environment so the subprocess
        # inherits PATH, HOME, NODE_PATH, etc. Extra keys from config.env
        # are overlaid on top, so they take precedence when set.
        env = {**os.environ, **(self.config.env or {})}
        return StdioServerParameters(
            command=command,
            args=args,
            env=env,
            cwd=None,
            encoding="utf-8",
        )

    async def _ensure_connected(self) -> ClientSession:
        """Connect and initialize session on first use; reuse afterward.

        CRITICAL: ClientSession must be used with 'async with' to avoid deadlocks.
        We enter both contexts (stdio_client and ClientSession) and hold them
        for the lifetime of this client instance.

        EVENT LOOP HANDLING: If we detect the event loop has changed (e.g., from
        initialization to request handling), we close the old connection and create
        a new one in the current event loop.
        """
        import asyncio

        current_loop = asyncio.get_event_loop()

        # Check if we're connected AND in the same event loop
        if self._is_connected and self._session is not None:
            if self._event_loop is current_loop:
                logger.debug("Session already connected for %s, reusing", self.config.id)
                return self._session
            else:
                # Event loop changed - need to reconnect
                logger.warning(
                    "Event loop changed for %s (old=%s, new=%s), reconnecting...",
                    self.config.id, id(self._event_loop), id(current_loop)
                )
                await self.close()
                self._is_connected = False

        logger.info("Connecting to STDIO MCP server %s...", self.config.id)
        params = self._server_params()
        logger.debug("Server params: command=%s, args=%s", params.command, params.args)

        # Enter stdio_client context (spawns process, creates streams)
        self._stdio_context = stdio_client(params, errlog=sys.stderr)
        read_stream, write_stream = await self._stdio_context.__aenter__()
        logger.debug("Got read/write streams from stdio_client")

        # CRITICAL FIX: Enter ClientSession context (required for proper operation)
        # Store BOTH the context manager AND the session result
        self._session_cm = ClientSession(read_stream, write_stream)
        self._session = await self._session_cm.__aenter__()
        logger.debug("Entered ClientSession context")

        # Initialize the session
        await self._session.initialize()
        self._is_connected = True
        self._event_loop = current_loop  # Store the event loop for this connection
        logger.info("MCP STDIO client connected and initialized for server %s", self.config.id)

        return self._session

    async def list_tools(self) -> List[MCPToolInfo]:
        """Return tools from the server."""
        logger.debug("Listing tools from MCP server %s", self.config.id)
        session = await self._ensure_connected()
        result = await session.list_tools()

        return [
            MCPToolInfo(
                name=t.name,
                description=t.description or None,
                input_schema=t.inputSchema or {},
            )
            for t in result.tools
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Invoke a tool and return its result content."""
        session = await self._ensure_connected()
        # MCP SDK expects {} for tools with no params, not None
        result = await session.call_tool(name, arguments=arguments if arguments is not None else {})
        if result.isError:
            # Surface error from content if present
            msg = "Tool call failed"
            if result.content:
                for block in result.content:
                    if hasattr(block, "text") and block.text:
                        msg = block.text
                        break
            raise RuntimeError(msg)
        # Collect all text from content blocks (text, embedded resource, etc.)
        # Prefer structuredContent only when there is no text content to surface
        parts = []
        if result.content:
            for block in result.content:
                if hasattr(block, "text") and block.text:
                    parts.append(block.text)
                elif hasattr(block, "resource") and block.resource:
                    res = block.resource
                    text = getattr(res, "text", None) or getattr(res, "blob", None)
                    if text:
                        parts.append(str(text))
        if parts:
            return "\n".join(parts)
        if result.structuredContent is not None:
            return result.structuredContent
        return None

    async def close(self) -> None:
        """Close the session and terminate the server process.

        Note: Due to anyio's strict task group requirements, cleanup errors
        may occur if close() is called from a different async task than where
        the connection was established. These are suppressed as they're
        harmless cleanup errors that don't affect functionality.
        """
        logger.info("Closing MCP STDIO client for %s", self.config.id)

        # Exit ClientSession context first
        if self._session_cm is not None:
            try:
                # Call __aexit__ on the context manager, not the session result
                await self._session_cm.__aexit__(None, None, None)
            except RuntimeError as e:
                # Suppress anyio cancel scope errors - harmless cleanup issues
                if "cancel scope" not in str(e).lower():
                    logger.warning("Error closing ClientSession for %s: %s", self.config.id, e)
            except Exception as e:
                logger.warning("Error closing ClientSession for %s: %s", self.config.id, e)
            finally:
                self._session = None
                self._session_cm = None

        # Then exit stdio_client context
        if self._stdio_context is not None:
            try:
                await self._stdio_context.__aexit__(None, None, None)
            except RuntimeError as e:
                # Suppress anyio cancel scope errors - harmless cleanup issues
                if "cancel scope" not in str(e).lower():
                    logger.warning("Error closing stdio_client for %s: %s", self.config.id, e)
            except Exception as e:
                logger.warning("Error closing stdio_client for %s: %s", self.config.id, e)
            finally:
                self._stdio_context = None

        self._is_connected = False
        self._event_loop = None
