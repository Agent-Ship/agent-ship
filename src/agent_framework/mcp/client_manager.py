"""Singleton manager for MCP server connections.

Returns a shared client per server config (by id). Clients are created on first use.
"""

import logging
import os
from typing import Dict, Optional

from src.agent_framework.mcp.clients.base import BaseMCPClient
from src.agent_framework.mcp.clients.stdio import StdioMCPClient
from src.agent_framework.mcp.models import MCPServerConfig, MCPTransport

logger = logging.getLogger(__name__)

_DEFAULT_MCP_USER_ID = "agentship"


def get_mcp_user_id() -> str:
    """Return the configured MCP user ID (default: 'agentship')."""
    return os.getenv("MCP_DEFAULT_USER_ID", _DEFAULT_MCP_USER_ID)


class MCPClientManager:
    """Manages MCP client instances per server (singleton, cache by config.id)."""

    _instance: Optional["MCPClientManager"] = None

    def __init__(self) -> None:
        self._clients: Dict[str, BaseMCPClient] = {}

    def get_client(self, server_config: MCPServerConfig, owner: str = "") -> BaseMCPClient:
        """Get or create an MCP client for the given server config and owner.

        Clients are cached by (server_id, owner) so that different agents each
        get their own subprocess + session and cannot interfere with each other.
        """
        cache_key = f"{server_config.id}:{owner}" if owner else server_config.id
        if cache_key not in self._clients:
            self._clients[cache_key] = self._create_client(server_config)
            logger.debug("Created MCP client for server %s (owner=%s)", server_config.id, owner or "<shared>")
        return self._clients[cache_key]

    def _create_client(self, config: MCPServerConfig) -> BaseMCPClient:
        """Create a new client for the given config (by transport)."""
        if config.transport in (MCPTransport.STDIO, MCPTransport.UVX):
            return StdioMCPClient(config)
        if config.transport in (MCPTransport.SSE, MCPTransport.HTTP):
            from src.agent_framework.mcp.clients.sse import SSEMCPClient
            user_id = get_mcp_user_id()
            logger.debug("Creating SSE MCP client for server %s (user=%s)", config.id, user_id)
            return SSEMCPClient(config, user_id)
        raise ValueError(
            f"Unsupported MCP transport '{config.transport.value}' for server '{config.id}'."
        )

    async def close_all(self) -> None:
        """Close all cached clients (e.g. on shutdown)."""
        for sid, client in list(self._clients.items()):
            try:
                await client.close()
            except Exception as e:
                logger.warning("Error closing MCP client %s: %s", sid, e)
        self._clients.clear()

    @classmethod
    def get_instance(cls) -> "MCPClientManager":
        """Return the singleton manager."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (for tests)."""
        cls._instance = None
