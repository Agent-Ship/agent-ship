"""SSE MCP client for remote OAuth-based servers."""

import logging
import os
from typing import Any, Dict, List, Optional
from datetime import datetime

import httpx

from ..models import MCPToolInfo, MCPServerConfig, MCPTransport
from .base import BaseMCPClient
from ..db_operations import MCPDatabaseOperations

logger = logging.getLogger(__name__)


class SSEMCPClient(BaseMCPClient):
    """MCP client for remote SSE servers with OAuth authentication."""

    def __init__(self, config: MCPServerConfig, user_id: str):
        """Initialize SSE MCP client.

        Args:
            config: MCP server configuration
            user_id: User identifier for token lookup
        """
        super().__init__(config)
        self.user_id = user_id
        self.base_url = config.url
        self._session: Optional[httpx.AsyncClient] = None
        self._tools_cache: Optional[List[MCPToolInfo]] = None

        # Get database operations
        database_url = os.getenv("AGENTSHIP_AUTH_DB_URI")
        if not database_url:
            raise ValueError("AGENTSHIP_AUTH_DB_URI not configured")
        self.db = MCPDatabaseOperations(database_url)

    async def _get_auth_token(self) -> str:
        """Retrieve OAuth token for this user and server.

        Returns:
            Decrypted access token

        Raises:
            ValueError: If no token found or token expired
        """
        token_data = self.db.get_oauth_token(self.user_id, self.config.id)

        if not token_data:
            raise ValueError(
                f"No OAuth token found for server {self.config.id}. "
                f"Connect first with: agentship mcp connect {self.config.id}"
            )

        # Check if token expired
        if token_data.get("expires_at"):
            expires_at = token_data["expires_at"]
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(expires_at)

            if expires_at < datetime.now():
                # Token expired
                if token_data.get("refresh_token"):
                    logger.info(f"Token expired for {self.config.id}, attempting refresh...")
                    # TODO: Implement token refresh
                    raise ValueError(
                        f"OAuth token expired for {self.config.id}. "
                        f"Reconnect with: agentship mcp reconnect {self.config.id}"
                    )
                else:
                    raise ValueError(
                        f"OAuth token expired for {self.config.id} (no refresh token). "
                        f"Reconnect with: agentship mcp reconnect {self.config.id}"
                    )

        return token_data["access_token"]

    async def _ensure_session(self) -> httpx.AsyncClient:
        """Ensure HTTP client session exists.

        Returns:
            HTTP client session
        """
        if self._session is None or self._session.is_closed:
            token = await self._get_auth_token()

            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "text/event-stream, application/json",
                "Content-Type": "application/json",
            }

            self._session = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=30.0,
            )

        return self._session

    def _parse_sse_response(self, text: str) -> Any:
        """Parse an SSE-formatted response body into a JSON-RPC result.

        GitHub Copilot MCP (Streamable HTTP) returns responses as SSE:
            event: message
            data: {"jsonrpc":"2.0","id":1,"result":{...}}

        Args:
            text: Raw response body text

        Returns:
            Parsed JSON object (full JSON-RPC envelope)
        """
        import json as _json

        for line in text.splitlines():
            if line.startswith("data:"):
                payload = line[len("data:"):].strip()
                if payload:
                    return _json.loads(payload)

        # Fallback: try parsing entire body as JSON
        import json as _json
        return _json.loads(text)

    async def _jsonrpc(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Send a JSON-RPC 2.0 request to the MCP server.

        Args:
            method: JSON-RPC method name (e.g. "tools/list")
            params: Optional method parameters

        Returns:
            The "result" field from the JSON-RPC response

        Raises:
            ValueError: On JSON-RPC error or HTTP error
        """
        session = await self._ensure_session()
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "method": method, "id": 1}
        if params:
            payload["params"] = params

        response = await session.post("", json=payload)

        if response.status_code == 401:
            raise ValueError(
                f"Authentication failed for {self.config.id}. "
                f"Reconnect with: agentship mcp reconnect {self.config.id}"
            )
        response.raise_for_status()

        data = self._parse_sse_response(response.text)
        if "error" in data:
            raise ValueError(f"MCP error: {data['error']}")
        return data.get("result", {})

    async def list_tools(self) -> List[MCPToolInfo]:
        """List available tools from MCP server.

        Returns:
            List of tool definitions
        """
        if self._tools_cache is not None:
            return self._tools_cache

        try:
            result = await self._jsonrpc("tools/list")

            tools = []
            for tool_data in result.get("tools", []):
                tool_info = MCPToolInfo(
                    name=tool_data["name"],
                    description=tool_data.get("description", ""),
                    input_schema=tool_data.get("inputSchema", {}),
                )
                tools.append(tool_info)

            self._tools_cache = tools
            self.db.update_last_used(self.user_id, self.config.id)

            logger.info(f"Listed {len(tools)} tools from {self.config.id}")
            return tools

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error listing tools from {self.config.id}: {e}")
            raise ValueError(f"Failed to list tools: {e}")

        except ValueError:
            raise

        except Exception as e:
            logger.error(f"Error listing tools from {self.config.id}: {e}")
            raise ValueError(f"Failed to list tools: {e}")

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Call a tool on the MCP server.

        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments

        Returns:
            Tool execution result as string
        """
        try:
            result = await self._jsonrpc("tools/call", {"name": tool_name, "arguments": arguments})

            # MCP protocol returns content array
            content = result.get("content", [])
            if not content:
                return ""

            result_parts = []
            for item in content:
                if isinstance(item, dict):
                    item_type = item.get("type", "")
                    if item_type == "resource":
                        # Resource blocks (e.g. file contents from GitHub MCP)
                        resource = item.get("resource", {})
                        text = resource.get("text") or resource.get("blob") or ""
                        if text:
                            result_parts.append(str(text))
                    elif "text" in item:
                        result_parts.append(item["text"])
                    elif "data" in item:
                        result_parts.append(str(item["data"]))
                else:
                    result_parts.append(str(item))

            self.db.update_last_used(self.user_id, self.config.id)
            logger.info(f"Called tool {tool_name} on {self.config.id}")
            return "\n".join(result_parts)

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error calling tool {tool_name}: {e}")
            raise ValueError(f"Tool execution failed: {e}")

        except ValueError:
            raise

        except Exception as e:
            logger.error(f"Error calling tool {tool_name}: {e}")
            raise ValueError(f"Tool execution failed: {e}")

    async def close(self):
        """Close the HTTP client session."""
        if self._session and not self._session.is_closed:
            await self._session.aclose()
            self._session = None

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()


class SSEMCPClientFactory:
    """Factory for creating SSE MCP clients."""

    @staticmethod
    def create_client(server_id: str, user_id: str) -> "SSEMCPClient":
        """Create an SSE/HTTP MCP client for a server.

        Args:
            server_id: MCP server identifier
            user_id: User identifier

        Returns:
            Configured SSE MCP client

        Raises:
            ValueError: If server not found or not an HTTP/SSE server
        """
        from ..registry import MCPServerRegistry

        registry = MCPServerRegistry.get_instance()
        server_config = registry.get_server(server_id)

        if not server_config:
            raise ValueError(f"Server {server_id} not found in registry")

        if server_config.transport not in ("sse", "http"):
            raise ValueError(f"Server {server_id} is not an SSE/HTTP server (transport={server_config.transport})")

        return SSEMCPClient(server_config, user_id)
