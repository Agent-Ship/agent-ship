"""Core implementation of `BaseAgent`.

This class is the public authoring surface for agents.

Key refactor: **ADK is no longer imported or constructed here**.
Instead, BaseAgent delegates execution to a pluggable runtime engine
(ADK engine by default). This isolates ADK behind a "sidecar runtime"
boundary and enables future runtimes (raw LiteLLM, LangGraph).
"""

import abc
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional, Type

from pydantic import BaseModel

from src.agent_framework.configs.agent_config import AgentConfig
from src.agent_framework.configs.loader import load_agent_config
from src.agent_framework.core.io import create_input_from_request
from src.agent_framework.core.types import AgentType
from src.agent_framework.engines import AgentEngine, create_engine
from src.agent_framework.engines.middleware_engine import MiddlewareEngine
from src.service.models.base_models import AgentChatRequest, AgentChatResponse, TextInput, TextOutput


logger = logging.getLogger(__name__)


class BaseAgent(abc.ABC):
    """Base class for all agents.

    Responsibilities (high-level):
    - Load `AgentConfig` from YAML (via `load_agent_config`).
    - Wire up session services and the Google ADK `Runner` (via engines).
    - Build tools from YAML configuration.
    - Provide a default `chat()` implementation.

    Subclasses typically only need to:
    - Provide `input_schema` / `output_schema` via `super().__init__`.
    - Optionally override `_create_input_from_request` for custom input
      mapping.
    """

    def __init__(
        self,
        agent_config: Optional[AgentConfig] = None,
        input_schema: Optional[Type[BaseModel]] = None,
        output_schema: Optional[Type[BaseModel]] = None,
        agent_type: Optional[AgentType] = None,
        config_path: Optional[str] = None,
        _caller_file: Optional[str] = None,
    ) -> None:
        # 1) Load configuration
        self.agent_config: AgentConfig = load_agent_config(
            agent_config=agent_config,
            config_path=config_path,
            caller_file=_caller_file,
        )
        logger.info("Agent config: %s", self.agent_config)

        # 2) Basic properties
        self.agent_type: Optional[AgentType] = agent_type
        self.input_schema: Type[BaseModel] = input_schema or TextInput
        self.output_schema: Type[BaseModel] = output_schema or TextOutput

        # 3) Create all components using clean factories
        from src.agent_framework.factories import EngineFactory, MemoryFactory, ObservabilityFactory
        from src.agent_framework.middleware import MemoryMiddleware

        # Create engine (required)
        base_engine: AgentEngine = EngineFactory.create(
            agent_config=self.agent_config,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
        )

        # Build the long-term memory middleware (only when the agent's YAML
        # has `memory.enabled: true`). The backend instance is not held on
        # the agent — the middleware is the only thing that talks to it.
        # When memory is off, `MemoryFactory.create` returns None and we
        # skip the middleware entirely; the agent behaves exactly as before.
        middlewares = []
        memory_backend = MemoryFactory.create(memory_config=self.agent_config.memory)
        if memory_backend is not None:
            middlewares.append(
                MemoryMiddleware(
                    memory=memory_backend,
                    config=self.agent_config.memory,
                    agent_name=self._get_agent_name(),
                )
            )

        # Create observer (optional)
        self.observer = ObservabilityFactory.create_observer(
            agent_config=self.agent_config
        )

        # Wrap the engine with the middleware stack. Adding more middlewares
        # (RAG, safety, tracing) means appending to `middlewares` above.
        # The `request_context_template` holds values shared by every call
        # to this agent; the per-call dict is built from a copy.
        self.engine = MiddlewareEngine(
            inner=base_engine,
            middlewares=middlewares,
            request_context_template={"agent_name": self._get_agent_name()},
        )

    # ------------------------------------------------------------------
    # Small helper accessors
    # ------------------------------------------------------------------

    def _get_agent_name(self) -> str:
        return self.agent_config.agent_name

    def _get_agent_description(self) -> str:
        return self.agent_config.description

    def _get_instruction_template(self) -> str:
        return self.agent_config.instruction_template

    def _get_agent_config(self) -> AgentConfig:
        return self.agent_config

    # ------------------------------------------------------------------
    # Backward-compatibility hooks
    # ------------------------------------------------------------------

    def _setup_agent(self) -> None:
        """Backward-compatible hook.

        Some older agents call `_setup_agent()` explicitly. With runtime
        engines, rebuilding is delegated to the runtime.
        """

        self.engine.rebuild()

    def _setup_runner(self) -> None:
        """Backward-compatible hook (no-op; kept for API stability)."""

        self.engine.rebuild()

    # ------------------------------------------------------------------
    # Public run + chat API
    # ------------------------------------------------------------------

    async def run(self, user_id: str, session_id: str, input_data: BaseModel) -> BaseModel:
        """Run the agent using the configured runtime."""

        logger.info("Running agent '%s' via execution engine '%s'", self._get_agent_name(), self.engine.engine_name())
        return await self.engine.run(user_id=user_id, session_id=session_id, input_data=input_data)

    # ------------------------------------------------------------------
    # Overridable hooks
    # ------------------------------------------------------------------

    def _create_input_from_request(self, request: AgentChatRequest) -> BaseModel:
        """Create input schema instance from `AgentChatRequest`.

        Subclasses may override this for custom input transformation. The
        default implementation delegates to `core.io.create_input_from_request`.
        """

        return create_input_from_request(self.input_schema, request)

    def _inject_output_ids(
        self, result: Any, user_id: str, session_id: str
    ) -> Any:
        """Overwrite placeholder session_id/user_id in the agent output with request context."""
        if not isinstance(result, BaseModel):
            return result
        if not hasattr(result, "session_id") or not hasattr(result, "user_id"):
            return result
        # Replace placeholders so logs and serialized response have real ids
        try:
            return result.model_copy(
                update={"session_id": session_id, "user_id": user_id}
            )
        except Exception:
            return result

    async def chat(self, request: AgentChatRequest) -> AgentChatResponse:
        """Default chat implementation.

        Steps:
        1. Build input schema from the request.
        2. Run the agent.
        3. Wrap the result in `AgentChatResponse` with basic error handling.
        """

        logger.debug("Chatting with the agent: %s", self._get_agent_name())

        try:
            input_data = self._create_input_from_request(request)

            result = await self.run(
                request.user_id,
                request.session_id,
                input_data,
            )

            # Inject request context into output when the LLM returns placeholder session_id/user_id
            result = self._inject_output_ids(result, request.user_id, request.session_id)

            logger.info("Result from %s: %s", self._get_agent_name(), result)

            return AgentChatResponse(
                agent_name=self._get_agent_name(),
                user_id=request.user_id,
                session_id=request.session_id,
                success=True,
                agent_response=result,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Error in %s: %s", self._get_agent_name(), exc, exc_info=True)
            return AgentChatResponse(
                agent_name=self._get_agent_name(),
                user_id=request.user_id,
                session_id=request.session_id,
                success=False,
                agent_response=f"Error: {str(exc)}",
            )

    # ------------------------------------------------------------------
    # Streaming API
    # ------------------------------------------------------------------

    async def run_stream(
        self, user_id: str, session_id: str, input_data: BaseModel
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Run the agent with streaming via the configured runtime."""

        async for event in self.engine.run_stream(user_id=user_id, session_id=session_id, input_data=input_data):
            yield event

    async def chat_stream(
        self, request: AgentChatRequest
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Streaming chat - yields events for SSE.

        This is the streaming equivalent of chat(). Use this for real-time
        updates showing tool calls, thinking states, and progressive responses.
        """
        logger.debug("Starting streaming chat with agent: %s", self._get_agent_name())

        try:
            input_data = self._create_input_from_request(request)

            async for event in self.run_stream(
                request.user_id,
                request.session_id,
                input_data,
            ):
                yield event

        except Exception as exc:
            logger.error("Streaming chat error: %s", exc, exc_info=True)
            yield {
                "type": "error",
                "agent": self._get_agent_name(),
                "message": str(exc),
            }
