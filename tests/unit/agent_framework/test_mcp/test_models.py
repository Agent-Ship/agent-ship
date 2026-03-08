"""Unit tests for MCP configuration models."""

import pytest
from pydantic import ValidationError

from src.agent_framework.mcp.models import (
    MCPAuthConfig,
    MCPAuthType,
    MCPServerConfig,
    MCPTransport,
)


def test_mcp_transport_enum():
    assert MCPTransport.STDIO.value == "stdio"
    assert MCPTransport.SSE.value == "sse"
    assert MCPTransport.HTTP.value == "http"
    assert MCPTransport.UVX.value == "uvx"


def test_mcp_auth_type_enum():
    assert MCPAuthType.NONE.value == "none"
    assert MCPAuthType.OAUTH.value == "oauth"


def test_mcp_auth_config_defaults():
    auth = MCPAuthConfig()
    assert auth.type == MCPAuthType.NONE
    assert auth.token_var is None
    assert auth.client_id_env is None
    assert auth.scopes == []


def test_mcp_auth_config_oauth():
    auth = MCPAuthConfig(
        type=MCPAuthType.OAUTH,
        client_id_env="GITHUB_CLIENT_ID",
        client_secret_env="GITHUB_CLIENT_SECRET",
        scopes=["repo:read", "issues:write"],
    )
    assert auth.type == MCPAuthType.OAUTH
    assert auth.client_id_env == "GITHUB_CLIENT_ID"
    assert auth.scopes == ["repo:read", "issues:write"]


def test_mcp_server_config_stdio_minimal():
    config = MCPServerConfig(
        id="fs",
        transport=MCPTransport.STDIO,
        command=["npx", "-y", "@modelcontextprotocol/server-filesystem"],
    )
    assert config.id == "fs"
    assert config.transport == MCPTransport.STDIO
    assert config.command == ["npx", "-y", "@modelcontextprotocol/server-filesystem"]
    assert config.url is None
    assert config.env == {}
    assert config.auth.type == MCPAuthType.NONE
    assert config.tools is None
    assert config.timeout == 30
    assert config.max_retries == 3


def test_mcp_server_config_from_dict():
    raw = {
        "id": "github",
        "transport": "sse",
        "url": "https://mcp.github.com/sse",
        "auth": {
            "type": "oauth",
            "client_id_env": "GITHUB_CLIENT_ID",
            "client_secret_env": "GITHUB_CLIENT_SECRET",
            "scopes": ["repo:read"],
        },
        "timeout": 60,
    }
    config = MCPServerConfig(**raw)
    assert config.id == "github"
    assert config.transport == MCPTransport.SSE
    assert config.url == "https://mcp.github.com/sse"
    assert config.auth.type == MCPAuthType.OAUTH
    assert config.auth.client_id_env == "GITHUB_CLIENT_ID"
    assert config.timeout == 60
    assert config.max_retries == 3  # default


def test_mcp_server_config_tools_filter():
    config = MCPServerConfig(
        id="db",
        transport=MCPTransport.HTTP,
        url="https://mcp.example.com",
        tools=["query", "execute"],
    )
    assert config.tools == ["query", "execute"]


def test_mcp_server_config_timeout_validation():
    with pytest.raises(ValidationError):
        MCPServerConfig(
            id="x",
            transport=MCPTransport.STDIO,
            command=["echo"],
            timeout=0,
        )


def test_mcp_server_config_max_retries_validation():
    MCPServerConfig(
        id="x",
        transport=MCPTransport.STDIO,
        command=["echo"],
        max_retries=0,
    )
    with pytest.raises(ValidationError):
        MCPServerConfig(
            id="x",
            transport=MCPTransport.STDIO,
            command=["echo"],
            max_retries=-1,
        )
