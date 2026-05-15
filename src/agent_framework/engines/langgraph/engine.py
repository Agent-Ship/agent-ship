"""LangGraph-based execution engine with LiteLLM integration.

This engine provides:
- True token-by-token streaming via LiteLLM
- Proper tool calling with multiple rounds support
- Tool call/result event emission for UI visibility
- Conversation memory via LangGraph checkpointer
- Structured input/output handling
- Opik observability via LangGraphOpikObserver
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Annotated, Any, AsyncGenerator, Dict, List, Optional, Type, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from litellm import acompletion
from litellm.exceptions import RateLimitError
from pydantic import BaseModel

from src.agent_framework.configs.agent_config import AgentConfig, StreamingMode
from src.agent_framework.core.io import build_schema_prompt, extract_display_text
from src.agent_framework.core.types import AgentType
from src.agent_framework.engines.base import AgentEngine, EngineCapabilities
from src.agent_framework.engines.json_object_response_format import (
    provider_supports_json_object_response_format,
)
from src.agent_framework.session.base import SessionStoreFactory
from src.agent_framework.session.adapters.langgraph import LangGraphSessionStore
from src.agent_framework.factories.observability_factory import ObservabilityFactory
from src.agent_framework.engines.langgraph.callback_handler import create_langgraph_callback_config

logger = logging.getLogger(__name__)

# Default maximum tool execution rounds
DEFAULT_MAX_TOOL_ROUNDS = 10

# Retry 429 rate limit: max attempts, initial backoff seconds
RATE_LIMIT_MAX_RETRIES = 4
RATE_LIMIT_BACKOFF_SEC = 10


async def _acompletion_with_retry(**kwargs: Any) -> Any:
    """Call LiteLLM acompletion with retry on 429 rate limit."""
    last_error = None
    for attempt in range(RATE_LIMIT_MAX_RETRIES):
        try:
            return await acompletion(**kwargs)
        except RateLimitError as e:
            last_error = e
            if attempt < RATE_LIMIT_MAX_RETRIES - 1:
                wait = RATE_LIMIT_BACKOFF_SEC * (attempt + 1)
                logger.warning("Rate limit (429), retrying in %ds (attempt %d/%d): %s", wait, attempt + 1, RATE_LIMIT_MAX_RETRIES, e)
                await asyncio.sleep(wait)
            else:
                raise
    if last_error:
        raise last_error
    raise RuntimeError("Unexpected retry loop exit")

# UUID v4 pattern (8-4-4-4-12 hex)
_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


def _is_placeholder_user_id(value: Any) -> bool:
    """Return True if value looks like a placeholder, not a real user UUID."""
    if not isinstance(value, str) or not value.strip():
        return True
    s = value.strip()
    if _UUID_PATTERN.match(s):
        return False
    # Common placeholders the model might emit (exact and partial)
    placeholders = (
        "user_id",
        "<user_id>",
        "<actual-user-id>",
        "the exact user id string from input",
        "the exact user id string from the input",
        "user id from input",
    )
    if s in placeholders or s.lower() in {p.lower() for p in placeholders}:
        return True
    if "user_id" in s.lower() and "<" in s and ">" in s:
        return True
    # Any phrase that says "from input" or "from the input" re user id
    lower = s.lower()
    if ("user" in lower and "id" in lower) and ("from input" in lower or "from the input" in lower):
        return True
    return True  # any non-UUID string we treat as placeholder


def _merge_user_id_into_tool_args(arguments: Dict[str, Any], user_id: Optional[str]) -> Dict[str, Any]:
    """Return a copy of arguments with context user_id injected when the value is a placeholder. Used so stream/Opik see real user_id."""
    if not user_id or "user_id" not in arguments:
        return arguments
    if _is_placeholder_user_id(arguments.get("user_id")):
        return {**arguments, "user_id": user_id}
    return arguments


class LangGraphState(TypedDict):
    """State schema for LangGraph agent graph."""
    messages: Annotated[List[BaseMessage], add_messages]


class LangGraphEngine(AgentEngine):
    """Execute an agent using LangGraph + LiteLLM.

    Features:
    - Token-by-token streaming via LiteLLM's native streaming
    - Multi-round tool calling with configurable max rounds
    - Proper tool_call and tool_result event emission
    - Conversation memory via LangGraph checkpointer
    - Clean structured input/output handling
    """

    # =========================================================================
    # Initialization
    # =========================================================================

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

        # Configuration
        self._max_tool_rounds = agent_config.max_tool_rounds

        # Observability - observer provides LangGraph and LiteLLM callbacks
        self.observer = ObservabilityFactory.create_observer(agent_config)
        if hasattr(self.observer, 'get_callback_config'):
            self.callback_config = self.observer.get_callback_config()
        else:
            self.callback_config = create_langgraph_callback_config(self.observer)

        # Tools
        from src.agent_framework.tools.tool_manager import ToolManager
        self._tools: List[StructuredTool] = ToolManager.create_tools(agent_config, "langgraph")
        self._tools_by_name: Dict[str, StructuredTool] = {t.name: t for t in self._tools}

        if self._tools:
            logger.info(
                "LangGraphEngine: Loaded %d tools for agent '%s': %s",
                len(self._tools),
                agent_config.agent_name,
                [t.name for t in self._tools],
            )

        # Session store (provides checkpointer access)
        session_store = SessionStoreFactory.create(
            engine_name=self.engine_name(),
            agent_name=self.agent_config.agent_name,
        )
        assert isinstance(session_store, LangGraphSessionStore)
        self.session_store = session_store

        # Graph builder (compiled lazily with checkpointer)
        self._graph_builder = self._build_graph_builder()
        self._compiled_graph = None

    def engine_name(self) -> str:
        return "langgraph"

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            supported_providers=frozenset(
                {"openai", "claude", "gemini", "vllm", "groq", "openrouter"}
            ),
            supports_sse_streaming=True,
            supports_tool_calling=bool(self._tools),
            supports_bidi_streaming=False,
            supports_multimodal=False,
            notes="LangGraph + LiteLLM with token streaming, multi-round tool calling, and checkpointer memory.",
        )

    # =========================================================================
    # Graph Building
    # =========================================================================

    def _build_graph_builder(self) -> StateGraph:
        """Build LangGraph StateGraph builder (compiled lazily with checkpointer)."""
        graph = StateGraph(LangGraphState)

        async def call_llm(state: LangGraphState) -> LangGraphState:
            """LLM node - calls LiteLLM with current messages."""
            model_str, temperature, api_base = self._resolve_litellm_model()
            llm_messages = self._convert_to_litellm_messages(state["messages"])
            tools_schema = self._get_tools_schema() if self._tools else None

            completion_kwargs: Dict[str, Any] = dict(
                model=model_str,
                messages=llm_messages,
                temperature=temperature,
                stream=False,
                tools=tools_schema,
                response_format=self._get_response_format(),
            )
            if api_base:
                completion_kwargs["api_base"] = api_base

            response = await _acompletion_with_retry(**completion_kwargs)

            message = response.choices[0].message if response.choices else None
            if not message:
                return {"messages": [AIMessage(content="No response from LLM")]}

            content = message.content or ""
            tool_calls = getattr(message, 'tool_calls', None) or []

            ai_message = AIMessage(content=content)
            if tool_calls:
                # Convert LiteLLM tool calls to LangChain format
                ai_message.tool_calls = [
                    {
                        "id": tc.id,
                        "name": tc.function.name,
                        "args": json.loads(tc.function.arguments) if tc.function.arguments else {},
                    }
                    for tc in tool_calls
                ]

            return {"messages": [ai_message]}

        async def call_tools(state: LangGraphState) -> LangGraphState:
            """Tool execution node."""
            last_message = state["messages"][-1]
            tool_calls = getattr(last_message, 'tool_calls', [])

            if not tool_calls:
                return {"messages": []}

            tool_results = []
            for tool_call in tool_calls:
                tool_name = tool_call.get("name", "unknown")
                tool_id = tool_call.get("id", "")
                arguments = tool_call.get("args", {})

                logger.info("Executing tool: %s with args: %s", tool_name, arguments)
                result = await self._execute_tool(tool_name, arguments)

                tool_results.append(ToolMessage(
                    content=result,
                    tool_call_id=tool_id,
                    name=tool_name
                ))

            return {"messages": tool_results}

        def should_continue(state: LangGraphState) -> str:
            """Determine if we should continue to tools or end."""
            if not state["messages"]:
                return "end"

            last_message = state["messages"][-1]
            tool_calls = getattr(last_message, 'tool_calls', [])

            if tool_calls:
                return "tools"
            return "end"

        # Build graph
        graph.add_node("llm", call_llm)
        if self._tools:
            graph.add_node("tools", call_tools)
            graph.add_conditional_edges("llm", should_continue, {"tools": "tools", "end": END})
            graph.add_edge("tools", "llm")
        else:
            graph.add_edge("llm", END)

        graph.set_entry_point("llm")
        return graph

    async def _get_compiled_graph(self):
        """Get compiled graph with checkpointer (lazy async init)."""
        if self._compiled_graph is None:
            checkpointer = await self.session_store.get_checkpointer()
            self._compiled_graph = self._graph_builder.compile(checkpointer=checkpointer)
            logger.info("LangGraph compiled with checkpointer: %s", type(checkpointer).__name__)
        return self._compiled_graph

    def _get_thread_config(self, user_id: str, session_id: str) -> dict:
        """Generate config with thread_id for checkpointer."""
        thread_id = f"{user_id}:{session_id}"
        return {"configurable": {"thread_id": thread_id}}

    # =========================================================================
    # LLM Interaction
    # =========================================================================

    def _resolve_litellm_model(self) -> tuple[str, float, Optional[str]]:
        """Return (model_string, temperature, api_base) for LiteLLM calls.

        api_base is non-None only for self-hosted providers (e.g. vLLM).
        """
        provider = self.agent_config.model_provider
        model_enum = self.agent_config.model
        model_str = provider.get_model_string(model_enum.value)
        temperature = self.agent_config.temperature or provider.temperature
        return model_str, temperature, provider.api_base

    def _get_response_format(self) -> Optional[dict]:
        """Get response_format for structured output (OpenAI-compatible providers)."""
        provider = self.agent_config.model_provider.name.value
        if provider_supports_json_object_response_format(provider):
            return {"type": "json_object"}
        return None

    def _build_system_prompt(self) -> str:
        """Build system prompt with tool documentation and output schema instructions."""
        # Auto-generate tool documentation and inject into prompt
        from src.agent_framework.prompts.tool_documentation import PromptBuilder

        prompt_with_tools = PromptBuilder.build_system_prompt(
            base_instruction=self.agent_config.instruction_template,
            tools=self._tools,
            engine_type="langgraph"
        )

        schema_prompt = build_schema_prompt(self.output_schema)
        return f"{prompt_with_tools}\n{schema_prompt}"

    def _convert_to_litellm_messages(self, messages: List[BaseMessage]) -> List[Dict[str, Any]]:
        """Convert LangChain messages to LiteLLM format."""
        llm_messages = [
            {"role": "system", "content": self._build_system_prompt()}
        ]

        for msg in messages:
            if isinstance(msg, HumanMessage):
                llm_messages.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                msg_dict = {"role": "assistant", "content": msg.content or ""}
                # Include tool calls if present
                tool_calls = getattr(msg, 'tool_calls', [])
                if tool_calls:
                    msg_dict["tool_calls"] = [
                        {
                            "id": tc.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": tc.get("name", ""),
                                "arguments": json.dumps(tc.get("args", {})),
                            }
                        }
                        for tc in tool_calls
                    ]
                llm_messages.append(msg_dict)
            elif isinstance(msg, ToolMessage):
                llm_messages.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id,
                    "content": msg.content,
                })

        return llm_messages

    # =========================================================================
    # Tool Execution
    # =========================================================================

    def _get_tools_schema(self) -> List[Dict[str, Any]]:
        """Convert tools to OpenAI-compatible schema for LiteLLM."""
        if not self._tools:
            return []

        tools_schema = []
        for tool in self._tools:
            if hasattr(tool, 'args_schema') and tool.args_schema:
                schema = tool.args_schema.model_json_schema()
                # Clean up schema for OpenAI format
                parameters = {
                    "type": "object",
                    "properties": schema.get("properties", {}),
                    "required": schema.get("required", []),
                }
                # Remove $defs if present (not needed for function calling)
                if "$defs" in parameters:
                    del parameters["$defs"]
            else:
                parameters = {"type": "object", "properties": {}}

            tools_schema.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or f"Call the {tool.name} tool",
                    "parameters": parameters,
                }
            })

        return tools_schema

    async def _execute_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        session_id: str = "default",
        user_id: Optional[str] = None,
    ) -> str:
        """Execute a tool and return the result as string.

        When user_id is provided (from run context), any tool argument "user_id"
        that looks like a placeholder is replaced with the real user_id so the
        backend receives the actual UUID.
        """
        tool = self._tools_by_name.get(tool_name)
        if not tool:
            return f"Error: Tool '{tool_name}' not found"

        # Inject run-context user_id when the LLM passed a placeholder
        if user_id and "user_id" in arguments and _is_placeholder_user_id(arguments.get("user_id")):
            arguments = {**arguments, "user_id": user_id}
            logger.debug("Injected context user_id into tool %s", tool_name)

        tool_input = json.dumps(arguments, default=str) if arguments else ""

        # Determine tool type from metadata
        is_agent_tool = (getattr(tool, 'metadata', None) or {}).get('is_agent_tool', False)
        tool_type = 'agent' if is_agent_tool else 'function'

        # Call before_tool_callback for tracing
        self.observer.before_tool_callback({
            "tool_name": tool_name,
            "tool_input": tool_input,
            "session_id": session_id,
            "tool_type": tool_type,
        })

        try:
            result = await self._invoke_tool(tool, arguments)

            if isinstance(result, str):
                result_str = result
            else:
                result_str = json.dumps(result, indent=2, default=str)

            # Call after_tool_callback for tracing
            self.observer.after_tool_callback({
                "tool_name": tool_name,
                "output": result_str,
                "session_id": session_id,
                "tool_type": tool_type,
            })

            return result_str
        except Exception as e:
            logger.error("Error executing tool %s: %s", tool_name, e, exc_info=True)
            error_msg = f"Error executing tool {tool_name}: {str(e)}"

            # Call after_tool_callback with error
            self.observer.after_tool_callback({
                "tool_name": tool_name,
                "output": error_msg,
                "error": str(e),
                "session_id": session_id,
                "tool_type": tool_type,
            })

            return error_msg

    async def _invoke_tool(self, tool: StructuredTool, arguments: Dict[str, Any]) -> Any:
        """Invoke a tool (sync or async)."""
        if hasattr(tool, 'ainvoke'):
            return await tool.ainvoke(arguments)
        return tool.invoke(arguments)

    # =========================================================================
    # Streaming Tool Loop
    # =========================================================================

    def _get_litellm_metadata(self) -> Dict[str, Any]:
        """Get metadata for LiteLLM calls including Opik span context."""
        # Use observer's method if available (LangGraphOpikObserver)
        if hasattr(self.observer, 'get_litellm_metadata'):
            return self.observer.get_litellm_metadata()
        # Fallback to basic metadata
        return {
            "agent_name": self.agent_config.agent_name,
            "engine": "langgraph",
        }

    def _get_litellm_callbacks(self) -> List[Any]:
        """Get callbacks for LiteLLM including OpikLogger."""
        # Use observer's method if available (LangGraphOpikObserver)
        if hasattr(self.observer, 'get_litellm_callbacks'):
            return self.observer.get_litellm_callbacks()
        return []

    async def _run_tool_loop_stream(
        self,
        messages: List[Dict[str, Any]],
        model_str: str,
        temperature: float,
        session_id: str = "default",
        user_id: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Run LLM with streaming and tool calling loop.

        Yields events:
        - {"type": "content", "agent": ..., "text": token}
        - {"type": "tool_call", "agent": ..., "tool_name": ..., "arguments": ...}
        - {"type": "tool_result", "agent": ..., "tool_name": ..., "result": ...}

        user_id is passed so tools get the real UUID injected when the LLM sends a placeholder.
        """
        tools_schema = self._get_tools_schema() if self._tools else None
        agent_name = self.agent_config.agent_name
        litellm_callbacks = self._get_litellm_callbacks()

        for iteration in range(self._max_tool_rounds):
            logger.debug("Tool loop iteration %d/%d", iteration + 1, self._max_tool_rounds)

            # Stream LLM response
            accumulated_content = ""
            accumulated_tool_calls: List[Dict[str, Any]] = []
            tool_call_accumulators: Dict[int, Dict[str, Any]] = {}

            # Build acompletion kwargs with Opik integration
            completion_kwargs = {
                "model": model_str,
                "messages": messages,
                "temperature": temperature,
                "stream": True,
                "tools": tools_schema,
                "response_format": self._get_response_format(),
                "metadata": self._get_litellm_metadata(),
            }
            if api_base:
                completion_kwargs["api_base"] = api_base
            if litellm_callbacks:
                completion_kwargs["callbacks"] = litellm_callbacks

            # Call before_model_callback for LLM tracing
            self.observer.before_model_callback({
                "model": model_str,
                "messages": messages,
                "session_id": session_id,
                "provider": self.agent_config.model_provider.name.value,
                "iteration": iteration + 1,
            })

            stream = await _acompletion_with_retry(**completion_kwargs)

            async for chunk in stream:
                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                # Handle content tokens
                if delta and delta.content:
                    token = delta.content
                    accumulated_content += token
                    yield {
                        "type": "content",
                        "agent": agent_name,
                        "text": token,
                    }

                # Handle streaming tool calls
                if delta and delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index

                        if idx not in tool_call_accumulators:
                            tool_call_accumulators[idx] = {
                                "id": "",
                                "name": "",
                                "arguments": "",
                            }

                        acc = tool_call_accumulators[idx]
                        if tc_delta.id:
                            acc["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                acc["name"] = tc_delta.function.name
                            if tc_delta.function.arguments:
                                acc["arguments"] += tc_delta.function.arguments

                # Check for finish
                if choice.finish_reason:
                    break

            # Finalize accumulated tool calls
            for idx in sorted(tool_call_accumulators.keys()):
                acc = tool_call_accumulators[idx]
                if acc["name"]:
                    try:
                        args = json.loads(acc["arguments"]) if acc["arguments"] else {}
                    except json.JSONDecodeError:
                        args = {}
                    accumulated_tool_calls.append({
                        "id": acc["id"],
                        "name": acc["name"],
                        "args": args,
                    })

            # Determine decision: tool calls or final response
            if accumulated_tool_calls:
                decision = f"call tools: {', '.join([tc['name'] for tc in accumulated_tool_calls])}"
            else:
                decision = "final response"

            # Call after_model_callback for LLM tracing
            self.observer.after_model_callback({
                "model": model_str,
                "response_content": accumulated_content,
                "session_id": session_id,
                "usage": {},  # Usage info not available in streaming mode
                "decision": decision,
                "tool_calls": [tc["name"] for tc in accumulated_tool_calls] if accumulated_tool_calls else None,
            })

            # If no tool calls, we're done
            if not accumulated_tool_calls:
                logger.debug("No tool calls, ending loop after iteration %d", iteration + 1)
                return

            # Add assistant message with tool calls to history
            messages.append({
                "role": "assistant",
                "content": accumulated_content,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["args"]),
                        }
                    }
                    for tc in accumulated_tool_calls
                ]
            })

            # Execute tools and yield events (inject context user_id so stream/Opik see real id)
            for tool_call in accumulated_tool_calls:
                tool_name = tool_call["name"]
                tool_args = _merge_user_id_into_tool_args(tool_call["args"], user_id)
                tool_id = tool_call["id"]

                # Yield tool_call event with injected args so observability sees real user_id
                yield {
                    "type": "tool_call",
                    "agent": agent_name,
                    "tool_name": tool_name,
                    "arguments": tool_args,
                }

                # Execute the tool (args already have user_id injected)
                result = await self._execute_tool(
                    tool_name, tool_args, session_id, user_id=user_id
                )

                # Yield tool_result event AFTER execution
                yield {
                    "type": "tool_result",
                    "agent": agent_name,
                    "tool_name": tool_name,
                    "result": result[:500] if len(result) > 500 else result,
                }

                # Add tool result to messages
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": result,
                })

        logger.warning(
            "Max tool iterations (%d) reached for agent %s",
            self._max_tool_rounds,
            agent_name,
        )

    async def _run_tool_loop(
        self,
        messages: List[Dict[str, Any]],
        model_str: str,
        temperature: float,
        session_id: str = "default",
        user_id: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> str:
        """Run LLM with tool calling loop (non-streaming).

        Returns the final content text.
        user_id is passed so tools that need it get the real UUID injected when
        the LLM sends a placeholder.
        """
        tools_schema = self._get_tools_schema() if self._tools else None
        litellm_callbacks = self._get_litellm_callbacks()

        for iteration in range(self._max_tool_rounds):
            # Build acompletion kwargs with Opik integration
            completion_kwargs = {
                "model": model_str,
                "messages": messages,
                "temperature": temperature,
                "stream": False,
                "tools": tools_schema,
                "response_format": self._get_response_format(),
                "metadata": self._get_litellm_metadata(),
            }
            if api_base:
                completion_kwargs["api_base"] = api_base
            if litellm_callbacks:
                completion_kwargs["callbacks"] = litellm_callbacks

            # Call before_model_callback for LLM tracing
            self.observer.before_model_callback({
                "model": model_str,
                "messages": messages,
                "session_id": session_id,
                "provider": self.agent_config.model_provider.name.value,
                "iteration": iteration + 1,
            })

            response = await _acompletion_with_retry(**completion_kwargs)

            # Extract usage info for tracing
            usage_info = {}
            if hasattr(response, 'usage') and response.usage:
                usage_info = {
                    "prompt_tokens": getattr(response.usage, 'prompt_tokens', 0),
                    "completion_tokens": getattr(response.usage, 'completion_tokens', 0),
                    "total_tokens": getattr(response.usage, 'total_tokens', 0),
                }

            if not response.choices:
                self.observer.after_model_callback({
                    "model": model_str,
                    "response_content": "",
                    "session_id": session_id,
                    "usage": usage_info,
                    "error": "No response from LLM",
                    "decision": "error",
                })
                return "No response from LLM"

            message = response.choices[0].message
            content = message.content or ""
            tool_calls = getattr(message, 'tool_calls', None) or []

            # Determine decision
            if tool_calls:
                decision = f"call tools: {', '.join([tc.function.name for tc in tool_calls])}"
                tool_names = [tc.function.name for tc in tool_calls]
            else:
                decision = "final response"
                tool_names = None

            # Call after_model_callback for LLM tracing
            self.observer.after_model_callback({
                "model": model_str,
                "response_content": content,
                "session_id": session_id,
                "usage": usage_info,
                "decision": decision,
                "tool_calls": tool_names,
            })

            if not tool_calls:
                return content

            # Add assistant message with tool calls
            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        }
                    }
                    for tc in tool_calls
                ]
            })

            # Execute tools (inject context user_id so backend and observability see real id)
            for tc in tool_calls:
                tool_name = tc.function.name
                try:
                    tool_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    tool_args = {}
                tool_args = _merge_user_id_into_tool_args(tool_args, user_id)

                result = await self._execute_tool(
                    tool_name, tool_args, session_id, user_id=user_id
                )

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

        return "Max tool iterations reached. Please try again."

    # =========================================================================
    # Input/Output Handling
    # =========================================================================

    def _extract_input_text(self, input_data: BaseModel) -> str:
        """Extract input text from input data.

        Checks common field names in order, falls back to JSON serialization.
        """
        for field in ['message', 'query', 'text', 'input', 'content']:
            if hasattr(input_data, field):
                value = getattr(input_data, field)
                if value:
                    return str(value)

        # Fallback to JSON serialization
        return input_data.model_dump_json()

    def _parse_response(self, content: str) -> BaseModel:
        """Parse LLM response into output schema.

        Handles markdown code blocks and JSON parsing.
        """
        if not content:
            logger.error("Empty content received for parsing")
            # Try to return a minimal valid instance
            try:
                return self.output_schema()
            except:
                raise ValueError("Cannot create output from empty content")

        # Strip markdown code blocks
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        elif content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        # Parse JSON and validate
        try:
            data = json.loads(content)
            return self.output_schema.model_validate(data)
        except json.JSONDecodeError as e:
            logger.warning("JSON decode error: %s. Content: %s", e, content[:200])
            # Try single-field fallback
            if hasattr(self.output_schema, 'model_fields'):
                fields = list(self.output_schema.model_fields.keys())
                if len(fields) == 1:
                    try:
                        return self.output_schema(**{fields[0]: content})
                    except:
                        pass
            raise ValueError(f"Failed to parse response as JSON: {e}")
        except Exception as e:
            logger.error("Failed to validate output: %s", e)
            raise ValueError(f"Failed to validate output: {e}")

    # =========================================================================
    # History Management
    # =========================================================================

    async def _load_history(self, user_id: str, session_id: str) -> List[BaseMessage]:
        """Load conversation history from checkpointer."""
        config = self._get_thread_config(user_id, session_id)

        try:
            graph = await self._get_compiled_graph()
            state = await graph.aget_state(config)

            if state and state.values and "messages" in state.values:
                messages = state.values["messages"]
                logger.debug(
                    "Loaded %d messages from checkpointer for %s:%s",
                    len(messages), user_id, session_id
                )
                return messages
        except Exception as e:
            logger.debug("No existing state for %s:%s: %s", user_id, session_id, e)

        return []

    async def _save_to_history(
        self,
        user_id: str,
        session_id: str,
        user_message: HumanMessage,
        assistant_message: AIMessage,
    ) -> None:
        """Save messages to checkpointer."""
        config = self._get_thread_config(user_id, session_id)

        try:
            graph = await self._get_compiled_graph()
            await graph.aupdate_state(
                config,
                {"messages": [user_message, assistant_message]},
                as_node="llm"
            )
            logger.debug("Saved messages to checkpointer for %s:%s", user_id, session_id)
        except Exception as e:
            logger.error("Failed to save to checkpointer: %s", e)

    # =========================================================================
    # Main Execution
    # =========================================================================

    async def run(
        self,
        user_id: str,
        session_id: str,
        input_data: BaseModel,
    ) -> BaseModel:
        """Non-streaming execution."""
        await self.session_store.ensure_session_exists(user_id, session_id)

        input_text = self._extract_input_text(input_data)
        config = self._get_thread_config(user_id, session_id)
        thread_id = config["configurable"]["thread_id"]

        logger.info(
            "LangGraphEngine.run: agent=%s model=%s thread_id=%s tools=%d",
            self.agent_config.agent_name,
            self._resolve_litellm_model()[0],
            thread_id,
            len(self._tools),
        )

        # Load history and build messages
        history = await self._load_history(user_id, session_id)
        messages = self._convert_to_litellm_messages(history)
        messages.append({"role": "user", "content": input_text})

        model_str, temperature, api_base = self._resolve_litellm_model()

        # Call before_agent_callback for tracing (starts trace)
        self.observer.before_agent_callback({
            "query": input_text,
            "session_id": session_id,
            "thread_id": thread_id,
            "user_id": user_id,
        })

        try:
            final_content = await self._run_tool_loop(
                messages, model_str, temperature, session_id, user_id=user_id, api_base=api_base
            )

            # Call after_agent_callback for tracing (ends trace)
            self.observer.after_agent_callback({
                "result": final_content,
                "session_id": session_id,
            })
        except Exception as e:
            # Call after_agent_callback with error
            self.observer.after_agent_callback({
                "result": "",
                "error": str(e),
                "session_id": session_id,
            })
            raise

        # Save to history
        await self._save_to_history(
            user_id, session_id,
            HumanMessage(content=input_text),
            AIMessage(content=final_content),
        )

        return self._parse_response(final_content)

    async def run_stream(
        self,
        user_id: str,
        session_id: str,
        input_data: BaseModel,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Streaming execution with token-by-token output and tool events."""
        await self.session_store.ensure_session_exists(user_id, session_id)

        input_text = self._extract_input_text(input_data)
        config = self._get_thread_config(user_id, session_id)
        agent_name = self.agent_config.agent_name

        logger.info(
            "LangGraphEngine.run_stream: agent=%s model=%s thread_id=%s tools=%d",
            agent_name,
            self._resolve_litellm_model()[0],
            config["configurable"]["thread_id"],
            len(self._tools),
        )

        # Check streaming mode - if none, use non-streaming path
        streaming_mode = self.agent_config.streaming_mode
        if isinstance(streaming_mode, StreamingMode):
            normalized_mode = streaming_mode.value
        else:
            normalized_mode = streaming_mode or StreamingMode.NONE.value

        yield {
            "type": "thinking",
            "agent": agent_name,
            "message": "Processing your request...",
        }

        if normalized_mode == "none":
            # Non-streaming mode
            try:
                final_output = await self.run(user_id, session_id, input_data)
                yield {
                    "type": "content",
                    "agent": agent_name,
                    "text": extract_display_text(self.output_schema, final_output.model_dump_json()),
                }
            except Exception as e:
                logger.error("Error in non-streaming run: %s", e, exc_info=True)
                yield {"type": "error", "agent": agent_name, "message": str(e)}
            yield {"type": "done"}
            return

        try:
            # Load history and build messages
            history = await self._load_history(user_id, session_id)
            messages = self._convert_to_litellm_messages(history)
            messages.append({"role": "user", "content": input_text})

            thread_id = config["configurable"]["thread_id"]
            model_str, temperature, api_base = self._resolve_litellm_model()
            accumulated_content = ""

            # Call before_agent_callback for tracing (starts trace)
            self.observer.before_agent_callback({
                "query": input_text,
                "session_id": session_id,
                "thread_id": thread_id,
                "user_id": user_id,
            })

            async for event in self._run_tool_loop_stream(
                messages, model_str, temperature, session_id, user_id=user_id, api_base=api_base
            ):
                yield event
                if event.get("type") == "content":
                    accumulated_content += event.get("text", "")

            # Call after_agent_callback for tracing (ends trace)
            self.observer.after_agent_callback({
                "result": accumulated_content,
                "session_id": session_id,
            })

            # Save to history
            if accumulated_content:
                await self._save_to_history(
                    user_id, session_id,
                    HumanMessage(content=input_text),
                    AIMessage(content=accumulated_content),
                )

            logger.info(
                "Stream completed. Content length: %d, thread_id=%s",
                len(accumulated_content),
                thread_id,
            )

        except Exception as e:
            logger.error("LangGraphEngine.run_stream error: %s", e, exc_info=True)
            # Call after_agent_callback with error
            self.observer.after_agent_callback({
                "result": "",
                "error": str(e),
                "session_id": session_id,
            })
            yield {"type": "error", "agent": agent_name, "message": str(e)}

        yield {"type": "done"}
