"""Agent base class — subclass this in your agent.py files."""

from __future__ import annotations

import inspect
import logging
from pathlib import Path
from typing import Optional

from agentship.core.config import AgentConfig
from agentship.mcp.stdio_client import StdioMCPClient
from agentship.skills.loader import load_skills

logger = logging.getLogger(__name__)


class Agent:
    """Base class for AgentShip agents.

    Subclass with zero required arguments — configuration is loaded automatically
    from ``agent.yaml`` in the same directory as the subclass's source file.

    Override :meth:`before_message` to preprocess incoming messages.
    """

    # Set by agentship serve before instantiation when it already knows the path.
    _yaml_path_override: Optional[Path] = None

    def __init__(self) -> None:
        yaml_path = self._resolve_yaml_path()
        self._config = AgentConfig.from_yaml(yaml_path)
        self._agent_dir = yaml_path.parent
        self._engine: Optional[object] = None  # built lazily on first chat()

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    async def chat(self, message: str, user_id: str = "default", session_id: str = "default") -> str:
        """Process one turn and return the assistant reply."""
        message = await self.before_message(message, {"user_id": user_id, "session_id": session_id})
        engine = await self._get_engine()
        thread_id = f"{self._config.agent_name}:{user_id}:{session_id}"
        return await engine.run(message, thread_id)

    async def before_message(self, message: str, ctx: dict) -> str:
        """Hook: override to preprocess or validate the incoming message."""
        return message

    @property
    def config(self) -> AgentConfig:
        return self._config

    # -------------------------------------------------------------------------
    # Lazy engine initialization
    # -------------------------------------------------------------------------

    async def _get_engine(self):
        if self._engine is None:
            self._engine = await _build_engine(self._config, self._agent_dir)
        return self._engine

    # -------------------------------------------------------------------------
    # YAML path resolution
    # -------------------------------------------------------------------------

    def _resolve_yaml_path(self) -> Path:
        # Allow serve to inject path (avoids needing the file on the importable path)
        override = type(self).__dict__.get("_yaml_path_override") or Agent._yaml_path_override
        if override and Path(override).exists():
            return Path(override)

        # Auto-discover relative to the subclass's source file
        try:
            source_file = inspect.getfile(type(self))
            yaml_path = Path(source_file).parent / "agent.yaml"
            if yaml_path.exists():
                return yaml_path
        except (TypeError, OSError):
            pass

        raise FileNotFoundError(
            f"agent.yaml not found for {type(self).__name__}. "
            "Run 'agentship new-agent <name>' to scaffold the files."
        )


async def _build_engine(config: AgentConfig, agent_dir: Path):
    """Wire up MCP clients, skills, session, and the LangGraph adapter."""
    from agentship.engines.langgraph_adapter import LangGraphAdapter
    from agentship.memory.postgres_session import get_checkpointer
    from agentship.observability import otel

    # Observability
    otel.setup(
        agent_name=config.agent_name,
        backend=config.observability.backend,
        endpoint=config.observability.endpoint,
        public_key=config.observability.public_key,
        secret_key=config.observability.secret_key,
    )

    # MCP servers
    mcp_clients = []
    for srv in config.mcp_servers:
        if srv.transport == "stdio" and srv.command:
            client = StdioMCPClient(name=srv.name, command=srv.command, env=srv.env)
            try:
                await client.connect()
                mcp_clients.append(client)
            except Exception as exc:
                logger.error("Failed to connect MCP server '%s': %s", srv.name, exc)

    # Skills
    skills_content = load_skills(agent_dir)
    system_prompt = config.system_prompt
    if skills_content:
        system_prompt = f"{system_prompt}\n\n{skills_content}"

    # Tool docs injected into system prompt
    all_tools = [t for c in mcp_clients for t in c.tools]
    if all_tools:
        tool_section = _build_tool_docs(all_tools)
        system_prompt = f"{system_prompt}\n\n{tool_section}"

    # Session / checkpointer
    db_url = config.memory.url if config.memory.backend == "postgres" else None
    checkpointer = await get_checkpointer(db_url)

    return LangGraphAdapter(
        model=config.model,
        temperature=config.temperature,
        system_prompt=system_prompt,
        mcp_clients=mcp_clients,
        checkpointer=checkpointer,
        max_tool_rounds=config.max_tool_rounds,
    )


def _build_tool_docs(tools) -> str:
    lines = ["## Available Tools\n"]
    for t in tools:
        lines.append(f"### {t.name}")
        if t.description:
            lines.append(f"**Description:** {t.description}")
        props = (t.input_schema or {}).get("properties", {})
        required = (t.input_schema or {}).get("required", [])
        if props:
            lines.append("**Parameters:**")
            for pname, pdef in props.items():
                req = " (**required**)" if pname in required else ""
                ptype = pdef.get("type", "any")
                pdesc = pdef.get("description", "")
                lines.append(f"- `{pname}` ({ptype}{req}): {pdesc}")
        lines.append("")
    return "\n".join(lines)
