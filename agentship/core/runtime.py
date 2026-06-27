"""FastAPI app factory — auto-discovers agents and mounts chat endpoints."""

from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Pre-import the LangGraph adapter at module load time so the heavy litellm
# import runs synchronously during server startup, not inside an async request
# handler where it would block uvicorn's event loop.
import agentship.engines.langgraph_adapter  # noqa: F401

logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str
    user_id: str = "default"
    session_id: str = "default"


class ChatResponse(BaseModel):
    agent: str
    response: str


def create_app(project_root: Path) -> FastAPI:
    """Glob agents/*/agent.yaml and register a POST /agents/<name>/chat route for each."""
    app = FastAPI(
        title="AgentShip",
        description="Production-ready AI agent runtime",
        version="0.1.0",
    )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    agents_dir = project_root / "agents"
    if not agents_dir.exists():
        logger.warning("No agents/ directory found at %s", project_root)
        return app

    # Add project root to sys.path so agent.py imports work
    project_root_str = str(project_root)
    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)

    registered = []
    for yaml_path in sorted(agents_dir.glob("*/agent.yaml")):
        agent_dir = yaml_path.parent
        agent_name = agent_dir.name
        agent_py = agent_dir / "agent.py"

        if not agent_py.exists():
            logger.warning("agents/%s/agent.py missing — skipping", agent_name)
            continue

        try:
            agent_instance = _load_agent(agent_dir, yaml_path)
            _register_route(app, agent_name, agent_instance)
            registered.append(agent_name)
            logger.info("Registered agent: %s → POST /agents/%s/chat", agent_name, agent_name)
        except Exception as exc:
            logger.error("Failed to load agent '%s': %s", agent_name, exc, exc_info=True)

    if registered:
        logger.info("AgentShip: %d agent(s) registered: %s", len(registered), registered)
    else:
        logger.warning("AgentShip: no agents were registered")

    return app


def _load_agent(agent_dir: Path, yaml_path: Path):
    """Import agent.py, find the Agent subclass, and return an instance."""
    from agentship.core.agent import Agent

    module_name = f"_agentship_agent_{agent_dir.name}"
    spec = importlib.util.spec_from_file_location(module_name, agent_dir / "agent.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {agent_dir / 'agent.py'}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module

    # Inject the yaml path so Agent.__init__ doesn't need to search
    Agent._yaml_path_override = yaml_path
    try:
        spec.loader.exec_module(module)

        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, Agent) and obj is not Agent and obj.__module__ == module_name:
                return obj()
    finally:
        Agent._yaml_path_override = None

    raise RuntimeError(f"No Agent subclass found in {agent_dir}/agent.py")


def _register_route(app: FastAPI, agent_name: str, agent) -> None:
    route_path = f"/agents/{agent_name}/chat"

    async def chat_endpoint(request: ChatRequest, _agent=agent, _name=agent_name) -> ChatResponse:
        try:
            response = await _agent.chat(
                message=request.message,
                user_id=request.user_id,
                session_id=request.session_id,
            )
            return ChatResponse(agent=_name, response=response)
        except Exception as exc:
            logger.error("Agent '%s' error: %s", _name, exc, exc_info=True)
            raise HTTPException(status_code=500, detail=str(exc))

    app.add_api_route(
        route_path,
        chat_endpoint,
        methods=["POST"],
        response_model=ChatResponse,
        tags=[agent_name],
        summary=f"Chat with agent '{agent_name}'",
    )
