"""Unit tests for MCPServerRegistry."""

import json
import os
import tempfile
from pathlib import Path

import pytest
import yaml

from src.agent_framework.mcp.models import MCPTransport
from src.agent_framework.mcp.registry import MCPServerRegistry


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset singleton before and after each test so tests don't share state."""
    MCPServerRegistry.reset_instance()
    yield
    MCPServerRegistry.reset_instance()


def test_registry_loads_json(tmp_path):
    config_file = tmp_path / ".mcp.settings.json"
    config_file.write_text(
        json.dumps({
            "servers": {
                "fs": {
                    "transport": "stdio",
                    "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem"],
                    "env": {"MCP_FILESYSTEM_ROOT": "/tmp"},
                },
                "github": {
                    "transport": "sse",
                    "url": "https://mcp.github.com/sse",
                    "auth": {"type": "oauth", "client_id_env": "GITHUB_CLIENT_ID", "client_secret_env": "GITHUB_CLIENT_SECRET"},
                },
            }
        }),
        encoding="utf-8",
    )
    reg = MCPServerRegistry(config_path=str(config_file))
    assert reg.get_server("fs") is not None
    assert reg.get_server("fs").transport == MCPTransport.STDIO
    assert reg.get_server("fs").command == ["npx", "-y", "@modelcontextprotocol/server-filesystem"]
    assert reg.get_server("github") is not None
    assert reg.get_server("github").url == "https://mcp.github.com/sse"
    assert reg.list_server_ids() == ["fs", "github"]


def test_registry_loads_yaml(tmp_path):
    config_file = tmp_path / "mcp_servers.yaml"
    config_file.write_text(
        yaml.dump({
            "servers": {
                "db": {
                    "transport": "http",
                    "url": "https://mcp.db.example.com",
                    "auth": {"type": "bearer_token", "token_var": "DB_TOKEN"},
                    "timeout": 60,
                },
            }
        }),
        encoding="utf-8",
    )
    reg = MCPServerRegistry(config_path=str(config_file))
    db = reg.get_server("db")
    assert db is not None
    assert db.transport == MCPTransport.HTTP
    assert db.url == "https://mcp.db.example.com"
    assert db.auth.type.value == "bearer_token"
    assert db.timeout == 60


def test_registry_get_server_missing():
    reg = MCPServerRegistry(config_path=None)
    assert reg.get_server("nonexistent") is None


def test_registry_get_servers_filters_missing():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"servers": {"one": {"transport": "stdio", "command": ["echo"]}}}, f)
        path = f.name
    try:
        reg = MCPServerRegistry(config_path=path)
        result = reg.get_servers(["one", "missing", "one"])
        assert len(result) == 2
        assert result[0].id == "one"
        assert result[1].id == "one"
    finally:
        os.unlink(path)


def test_registry_singleton():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"servers": {"s1": {"transport": "stdio", "command": ["echo"]}}}, f)
        path = f.name
    try:
        a = MCPServerRegistry.get_instance(config_path=path)
        b = MCPServerRegistry.get_instance(config_path="/other/path")
        assert a is b
        assert a.get_server("s1") is not None
    finally:
        os.unlink(path)


def test_registry_invalid_json_raises(tmp_path):
    config_file = tmp_path / "bad.json"
    config_file.write_text("not json", encoding="utf-8")
    with pytest.raises((ValueError, json.JSONDecodeError)):
        MCPServerRegistry(config_path=str(config_file))


def test_registry_invalid_root_raises(tmp_path):
    config_file = tmp_path / "bad.json"
    config_file.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    with pytest.raises(ValueError):
        MCPServerRegistry(config_path=str(config_file))


def test_registry_skips_invalid_server_entry(tmp_path):
    config_file = tmp_path / "partial.json"
    config_file.write_text(
        json.dumps({
            "servers": {
                "good": {"transport": "stdio", "command": ["echo"]},
                "bad": {"transport": "invalid_transport"},
                "also_bad": "not a dict",
            }
        }),
        encoding="utf-8",
    )
    reg = MCPServerRegistry(config_path=str(config_file))
    assert reg.get_server("good") is not None
    assert reg.get_server("bad") is None
    assert reg.list_server_ids() == ["good"]


def test_registry_nonexistent_path_empty():
    reg = MCPServerRegistry(config_path="/nonexistent/path/mcp.json")
    assert reg.list_server_ids() == []
    assert reg.get_server("any") is None


def test_registry_uvx_explicit_transport(tmp_path):
    """Explicit transport=uvx with args builds correct command."""
    config_file = tmp_path / "mcp.json"
    config_file.write_text(
        json.dumps({
            "servers": {
                "sqlite": {
                    "transport": "uvx",
                    "args": ["mcp-server-sqlite", "--db-path", "test.db"],
                }
            }
        }),
        encoding="utf-8",
    )
    reg = MCPServerRegistry(config_path=str(config_file))
    srv = reg.get_server("sqlite")
    assert srv is not None
    assert srv.transport == MCPTransport.UVX
    assert srv.command == ["uvx", "mcp-server-sqlite", "--db-path", "test.db"]


def test_registry_uvx_auto_detect_from_command_string(tmp_path):
    """command='uvx' auto-detects transport as uvx."""
    config_file = tmp_path / "mcp.json"
    config_file.write_text(
        json.dumps({
            "servers": {
                "sqlite": {
                    "command": "uvx",
                    "args": ["mcp-server-sqlite", "--db-path", "test.db"],
                }
            }
        }),
        encoding="utf-8",
    )
    reg = MCPServerRegistry(config_path=str(config_file))
    srv = reg.get_server("sqlite")
    assert srv is not None
    assert srv.transport == MCPTransport.UVX
    assert srv.command == ["uvx", "mcp-server-sqlite", "--db-path", "test.db"]


def test_registry_uvx_auto_detect_from_command_list(tmp_path):
    """command=['uvx', ...] auto-detects transport as uvx."""
    config_file = tmp_path / "mcp.json"
    config_file.write_text(
        json.dumps({
            "servers": {
                "sqlite": {
                    "command": ["uvx", "mcp-server-sqlite", "--db-path", "test.db"],
                }
            }
        }),
        encoding="utf-8",
    )
    reg = MCPServerRegistry(config_path=str(config_file))
    srv = reg.get_server("sqlite")
    assert srv is not None
    assert srv.transport == MCPTransport.UVX
    assert srv.command == ["uvx", "mcp-server-sqlite", "--db-path", "test.db"]


def test_registry_uvx_env_var_resolution(tmp_path, monkeypatch):
    """uvx config resolves ${VAR} in args."""
    monkeypatch.setenv("TEST_DB_PATH", "/data/test.db")
    config_file = tmp_path / "mcp.json"
    config_file.write_text(
        json.dumps({
            "servers": {
                "sqlite": {
                    "transport": "uvx",
                    "args": ["mcp-server-sqlite", "--db-path", "${TEST_DB_PATH}"],
                }
            }
        }),
        encoding="utf-8",
    )
    reg = MCPServerRegistry(config_path=str(config_file))
    srv = reg.get_server("sqlite")
    assert srv.command == ["uvx", "mcp-server-sqlite", "--db-path", "/data/test.db"]
