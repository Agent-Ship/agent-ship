"""MCP tool discovery: list tools from a server with optional name filter."""

import logging
from typing import List

from src.agent_framework.mcp.client_manager import MCPClientManager
from src.agent_framework.mcp.models import MCPServerConfig, MCPToolInfo, MCPTransport

logger = logging.getLogger(__name__)


def _filter_tools(tools: List[MCPToolInfo], server_config: MCPServerConfig) -> List[MCPToolInfo]:
    """Apply server_config.tools name filter if set."""
    if not server_config.tools:
        return tools
    allowed = set(server_config.tools)
    filtered = [t for t in tools if t.name in allowed]
    if len(filtered) < len(allowed):
        missing = allowed - {t.name for t in filtered}
        logger.debug("MCP server %s: requested tools %s not in server list", server_config.id, missing)
    return filtered


class MCPToolDiscovery:
    """Discovers tools from an MCP server and optionally filters by name."""

    def __init__(self) -> None:
        self._manager = MCPClientManager.get_instance()

    async def discover_tools(self, server_config: MCPServerConfig) -> List[MCPToolInfo]:
        """Discover tools from the server (uses cached client); if server_config.tools is set, filter to those names."""
        client = self._manager.get_client(server_config)
        tools = await client.list_tools()
        return _filter_tools(tools, server_config)

    async def discover_tools_temporary(self, server_config: MCPServerConfig) -> List[MCPToolInfo]:
        """Discover tools using a one-off client that is closed after listing.
        Use this when discovery runs on a different event loop (e.g. a worker thread)
        so the main loop can create its own client when the tool is invoked.
        """
        if server_config.transport in (MCPTransport.STDIO, MCPTransport.UVX):
            from src.agent_framework.mcp.clients.stdio import StdioMCPClient
            client = StdioMCPClient(server_config)
            try:
                tools = await client.list_tools()
                return _filter_tools(tools, server_config)
            finally:
                await client.close()

        if server_config.transport in (MCPTransport.SSE, MCPTransport.HTTP):
            from src.agent_framework.mcp.clients.sse import SSEMCPClient
            from src.agent_framework.mcp.client_manager import get_mcp_user_id
            client = SSEMCPClient(server_config, get_mcp_user_id())
            async with client:
                tools = await client.list_tools()
                return _filter_tools(tools, server_config)

        raise ValueError(
            f"discover_tools_temporary: unsupported transport '{server_config.transport}' "
            f"for server '{server_config.id}'"
        )
