"""Agent configuration loaded from agent.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


@dataclass
class MCPServerConfig:
    name: str
    transport: str = "stdio"
    command: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)


@dataclass
class MemoryConfig:
    backend: str = "memory"  # "memory" | "postgres"
    url: Optional[str] = None


@dataclass
class ObservabilityConfig:
    backend: str = "none"  # "none" | "langfuse"
    endpoint: Optional[str] = None
    public_key: Optional[str] = None
    secret_key: Optional[str] = None


@dataclass
class AgentConfig:
    agent_name: str
    engine: str = "langgraph"
    model: str = "openai/gpt-4o-mini"
    temperature: float = 0.4
    system_prompt: str = "You are a helpful assistant."
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
    mcp_servers: List[MCPServerConfig] = field(default_factory=list)
    max_tool_rounds: int = 10

    @classmethod
    def from_yaml(cls, path: Path) -> "AgentConfig":
        with open(path) as f:
            data = yaml.safe_load(f) or {}

        memory_data = data.get("memory", {})
        memory = MemoryConfig(
            backend=memory_data.get("backend", "memory"),
            url=_resolve_env(memory_data.get("url")),
        )

        obs_data = data.get("observability", {})
        observability = ObservabilityConfig(
            backend=obs_data.get("backend", "none"),
            endpoint=_resolve_env(obs_data.get("endpoint")),
            public_key=_resolve_env(obs_data.get("public_key")),
            secret_key=_resolve_env(obs_data.get("secret_key")),
        )

        mcp_servers = []
        for s in data.get("mcp", {}).get("servers", []):
            cmd = s.get("command", [])
            if isinstance(cmd, str):
                cmd = cmd.split()
            env = {k: _resolve_env(v) for k, v in s.get("env", {}).items()}
            mcp_servers.append(MCPServerConfig(
                name=s.get("name", ""),
                transport=s.get("transport", "stdio"),
                command=cmd,
                env=env,
            ))

        return cls(
            agent_name=data.get("agent_name", path.parent.name),
            engine=data.get("engine", "langgraph"),
            model=data.get("model", "openai/gpt-4o-mini"),
            temperature=float(data.get("temperature", 0.4)),
            system_prompt=data.get("system_prompt", "You are a helpful assistant."),
            memory=memory,
            observability=observability,
            mcp_servers=mcp_servers,
            max_tool_rounds=int(data.get("max_tool_rounds", 10)),
        )


def _resolve_env(value: Any) -> Optional[str]:
    """Resolve ${VAR} tokens in config strings."""
    if not isinstance(value, str):
        return value
    if value.startswith("${") and value.endswith("}"):
        var_name = value[2:-1]
        return os.environ.get(var_name, "")
    return value
