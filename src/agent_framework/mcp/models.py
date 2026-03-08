"""MCP configuration models.

Pydantic models for global MCP server configuration and auth.
Used by MCPServerRegistry and (later) AgentConfig resolution.
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MCPToolInfo(BaseModel):
    """Lightweight descriptor for an MCP tool (engine-agnostic)."""

    name: str = Field(..., description="Tool name")
    description: Optional[str] = Field(default=None, description="Human-readable description")
    input_schema: Dict[str, Any] = Field(default_factory=dict, description="JSON Schema for tool arguments")


class MCPTransport(str, Enum):
    """Transport type for MCP server connection."""

    STDIO = "stdio"
    SSE = "sse"
    HTTP = "http"
    UVX = "uvx"


class MCPAuthType(str, Enum):
    """Authentication method for MCP server."""

    NONE = "none"
    ENV_VAR = "env_var"
    BEARER_TOKEN = "bearer_token"
    OAUTH = "oauth"
    API_KEY = "api_key"


class MCPAuthConfig(BaseModel):
    """Authentication configuration for an MCP server.

    Per-server env var names are specified here (e.g. GITHUB_TOKEN, DATABASE_MCP_TOKEN).
    For OAuth servers, also includes provider-specific URLs and configuration.
    """

    type: MCPAuthType = MCPAuthType.NONE
    token_var: Optional[str] = None  # For env_var / bearer_token
    api_key_var: Optional[str] = None  # For api_key

    # OAuth configuration
    provider: Optional[str] = None  # OAuth provider name (e.g., "github", "slack")
    client_id_env: Optional[str] = None  # Env var name for client ID
    client_secret_env: Optional[str] = None  # Env var name for client secret
    authorize_url: Optional[str] = None  # OAuth authorization endpoint
    token_url: Optional[str] = None  # OAuth token exchange endpoint
    scopes: List[str] = Field(default_factory=list)  # OAuth scopes


class MCPServerConfig(BaseModel):
    """Full configuration for one MCP server (from global registry or after resolution)."""

    id: str = Field(..., description="Unique server identifier")
    transport: MCPTransport = Field(..., description="Connection transport")
    description: Optional[str] = Field(default=None, description="Human-readable server description")
    command: Optional[List[str]] = Field(default=None, description="STDIO: command + args")
    url: Optional[str] = Field(default=None, description="SSE/HTTP: server URL")
    env: Dict[str, str] = Field(default_factory=dict, description="Environment variables for the server")
    auth: MCPAuthConfig = Field(
        default_factory=lambda: MCPAuthConfig(type=MCPAuthType.NONE),
        description="Authentication configuration",
    )
    tools: Optional[List[str]] = Field(
        default=None,
        description="Optional list of tool names to expose; None means all tools",
    )
    timeout: int = Field(default=30, ge=1, description="Connection timeout in seconds")
    max_retries: int = Field(default=3, ge=0, description="Max retry attempts")


class MCPServerReference(BaseModel):
    """Per-agent reference to an MCP server with optional overrides.

    Used in agent YAML under mcp_servers. The id must exist in the global registry;
    other fields override the registry config for this agent only.
    """

    id: str = Field(..., description="Server ID from global MCP registry")
    tools: Optional[List[str]] = Field(
        default=None,
        description="Tool names to expose; None = all tools from server",
    )
    env: Optional[Dict[str, str]] = Field(
        default=None,
        description="Environment overrides for this agent",
    )
    timeout: Optional[int] = Field(default=None, ge=1, description="Timeout override in seconds")
