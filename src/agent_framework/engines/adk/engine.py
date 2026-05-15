"""ADK execution engine.

All Google ADK imports and ADK-specific execution live in this module so
the rest of the codebase can treat ADK as a swappable engine.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator, Dict, List, Optional, Type

from dotenv import load_dotenv
from google.adk import Agent
from google.adk.agents import LlmAgent, ParallelAgent, SequentialAgent
from google.adk.runners import Runner
from google.genai import types
from pydantic import BaseModel

from src.agent_framework.configs.agent_config import AgentConfig
from src.agent_framework.core.io import extract_display_text, parse_agent_response
from src.agent_framework.factories.observability_factory import ObservabilityFactory
from src.agent_framework.core.types import AgentType
from src.agent_framework.engines.base import AgentEngine, EngineCapabilities
from src.agent_framework.session.base import SessionStoreFactory
from src.agent_framework.session.adapters.adk import AdkSessionStore

load_dotenv()
logger = logging.getLogger(__name__)


class AdkEngine(AgentEngine):
    """Execute an agent using Google ADK Runner."""

    def __init__(
        self,
        *,
        agent_config: AgentConfig,
        input_schema: Type[BaseModel],
        output_schema: Type[BaseModel],
        agent_type: Optional[AgentType],
    ) -> None:
        self.agent_config = agent_config
        self.input_schema = input_schema
        self.output_schema = output_schema
        self.agent_type = agent_type
        
        # Engine-agnostic session store
        self.session_store = SessionStoreFactory.create(
            engine_name=self.engine_name(),
            agent_name=self.agent_config.agent_name,
        )

        # Observability callbacks (clean factory pattern)
        self.observer = ObservabilityFactory.create_observer(self.agent_config)

        # Underlying ADK objects
        self.agent: Agent | None = None
        self.runner: Runner | None = None

        self.rebuild()
    
    def _get_adk_model(self):
        """Create ADK LiteLlm model wrapper. Uses provider's get_model_string() so
        LiteLLM gets the right format (e.g. gemini/gemini-1.5-pro for Gemini API, not Vertex).
        For self-hosted providers like vLLM, api_base is passed through.
        """
        from google.adk.models.lite_llm import LiteLlm
        provider = self.agent_config.model_provider
        model_str = provider.get_model_string(self.agent_config.model.value)
        if provider.api_base:
            return LiteLlm(model=model_str, api_base=provider.api_base)
        return LiteLlm(model_str)

    def engine_name(self) -> str:
        return "adk"

    def capabilities(self) -> EngineCapabilities:
        # NOTE: ADK execution here relies on ADK's LiteLlm wrapper. In practice,
        # provider support is constrained by that integration and by how ADK
        # surfaces streaming for a given provider/model.
        return EngineCapabilities(
            supported_providers=frozenset({"openai", "claude", "gemini"}),
            supports_sse_streaming=True,
            supports_tool_calling=True,
            supports_bidi_streaming=False,  # not implemented in this codebase yet
            supports_multimodal=False,  # not implemented in this codebase yet
            notes="SSE streaming is supported via Runner.run() events; bidi/live is not wired yet.",
        )

    def rebuild(self) -> None:
        """(Re)build the underlying ADK agent + runner."""

        # Build tools using the unified tool manager
        from src.agent_framework.tools.tool_manager import ToolManager
        tools = ToolManager.create_tools(self.agent_config, "adk")

        # ADK exposes tools to the model via FunctionDeclaration — the LLM receives
        # full tool schemas through that mechanism, so prompt-based doc injection is
        # not needed. We use the instruction template directly.
        final_instruction = self.agent_config.instruction_template

        agent_kwargs: dict[str, Any] = {
            "model": self._get_adk_model(),
            "name": self.agent_config.agent_name,
            "description": self.agent_config.description,
            "instruction": final_instruction,  # Use enhanced instruction with tool docs
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "tools": tools,
        }

        if self.observer:
            # Check if using OpikTracer - pass its callbacks for automatic tracing
            if hasattr(self.observer, '_opik_tracer') and self.observer._opik_tracer:
                opik_tracer = self.observer._opik_tracer
                # Pass OpikTracer's own callbacks for proper trace/thread capture
                agent_kwargs.update(
                    {
                        "before_agent_callback": opik_tracer.before_agent_callback,
                        "after_agent_callback": opik_tracer.after_agent_callback,
                        "before_model_callback": opik_tracer.before_model_callback,
                        "after_model_callback": opik_tracer.after_model_callback,
                        "before_tool_callback": opik_tracer.before_tool_callback,
                        "after_tool_callback": opik_tracer.after_tool_callback,
                    }
                )
            else:
                # Pass manual callbacks for non-Opik observers
                agent_kwargs.update(
                    {
                        "before_agent_callback": self.observer.before_agent_callback,
                        "after_agent_callback": self.observer.after_agent_callback,
                        "before_model_callback": self.observer.before_model_callback,
                        "after_model_callback": self.observer.after_model_callback,
                        "before_tool_callback": self.observer.before_tool_callback,
                        "after_tool_callback": self.observer.after_tool_callback,
                    }
                )

        self.agent = self._create_agent_from_type(agent_kwargs)

        # Apply Opik's recursive tracing for proper hierarchical agent/tool tracing
        if self.observer and hasattr(self.observer, '_opik_tracer') and self.observer._opik_tracer:
            try:
                from opik.integrations.adk import track_adk_agent_recursive
                track_adk_agent_recursive(self.agent, self.observer._opik_tracer)
                logger.info("Applied Opik track_adk_agent_recursive for hierarchical tracing")
            except ImportError:
                logger.debug("track_adk_agent_recursive not available")
            except Exception as e:
                logger.warning(f"Failed to apply track_adk_agent_recursive: {e}")

        # ADK Runner still expects a concrete session_service. For the ADK engine,
        # the SessionStoreFactory always returns an AdkSessionStore, so it is
        # safe to downcast here.
        assert isinstance(self.session_store, AdkSessionStore)
        self.runner = Runner(
            agent=self.agent,
            app_name=self.agent_config.agent_name,
            session_service=self.session_store.session_service,
        )

    def _create_agent_from_type(self, agent_kwargs: dict[str, Any]) -> Agent:
        if self.agent_type == AgentType.LLM_AGENT:
            return LlmAgent(**agent_kwargs)
        if self.agent_type == AgentType.PARALLEL_AGENT:
            return ParallelAgent(**agent_kwargs)
        if self.agent_type == AgentType.SEQUENTIAL_AGENT:
            return SequentialAgent(**agent_kwargs)
        return Agent(**agent_kwargs)

    async def run(self, user_id: str, session_id: str, input_data: BaseModel) -> BaseModel:
        if self.runner is None:
            raise RuntimeError("ADK runner not initialized.")

        await self.session_store.ensure_session_exists(user_id, session_id)

        input_text = input_data.model_dump_json()
        content = types.Content(role="user", parts=[types.Part(text=input_text)])

        result_generator = self.runner.run(
            user_id=user_id,
            session_id=session_id,
            new_message=content,
        )

        result = None
        for response in result_generator:
            if (
                hasattr(response, "content")
                and response.content
                and response.content.parts
                and response.content.parts[0].text
            ):
                result = response

        return parse_agent_response(self.output_schema, result)

    async def run_stream(
        self, user_id: str, session_id: str, input_data: BaseModel
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Yield the same event schema as current ADK streaming implementation."""

        if self.runner is None:
            raise RuntimeError("ADK runner not initialized.")

        await self.session_store.ensure_session_exists(user_id, session_id)

        input_text = input_data.model_dump_json()
        content = types.Content(role="user", parts=[types.Part(text=input_text)])

        yield {
            "type": "thinking",
            "agent": self.agent_config.agent_name,
            "message": "Processing your request...",
        }

        try:
            result_generator = self.runner.run(
                user_id=user_id,
                session_id=session_id,
                new_message=content,
            )
        except Exception as e:
            yield {
                "type": "error",
                "agent": self.agent_config.agent_name,
                "message": f"Failed to start agent: {str(e)}",
            }
            return

        try:
            for event in result_generator:
                for stream_event in self._format_stream_event(event):
                    yield stream_event
        except Exception as e:
            logger.error("Error while processing runner events: %s", e, exc_info=True)
            yield {
                "type": "error",
                "agent": self.agent_config.agent_name,
                "message": f"Error processing events: {str(e)}",
            }

        yield {"type": "done"}

    def _format_stream_event(self, event) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []

        if not hasattr(event, "content") or not event.content:
            return events

        author = getattr(event, "author", self.agent_config.agent_name)

        for part in event.content.parts if event.content.parts else []:
            if hasattr(part, "function_call") and part.function_call:
                fc = part.function_call
                events.append(
                    {
                        "type": "tool_call",
                        "agent": author,
                        "tool_name": fc.name if hasattr(fc, "name") else str(fc),
                        "arguments": dict(fc.args) if hasattr(fc, "args") and fc.args else {},
                    }
                )
            elif hasattr(part, "function_response") and part.function_response:
                fr = part.function_response
                result_str = ""
                if hasattr(fr, "response"):
                    result_str = str(fr.response)[:500]
                events.append(
                    {
                        "type": "tool_result",
                        "agent": author,
                        "tool_name": fr.name if hasattr(fr, "name") else "unknown",
                        "result": result_str,
                    }
                )
            elif hasattr(part, "text"):
                text_value = part.text if part.text else ""
                if text_value:
                    # Extract plain display text, stripping any schema JSON wrapper
                    # (e.g. ADK output_schema causes LLM to emit {"response": "..."}).
                    # Falls back to raw text safely for partial streaming chunks.
                    display_text = extract_display_text(self.output_schema, text_value)
                    events.append({"type": "content", "agent": author, "text": display_text})

        return events
