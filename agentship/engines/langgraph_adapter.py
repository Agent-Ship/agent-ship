"""LangGraph + LiteLLM execution engine for AgentShip agents."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from litellm import acompletion
from litellm.exceptions import RateLimitError

from agentship.mcp.stdio_client import MCPTool, StdioMCPClient

logger = logging.getLogger(__name__)

_RATE_LIMIT_RETRIES = 4
_RATE_LIMIT_BACKOFF = 10


class _State(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]


class LangGraphAdapter:
    """Thin LangGraph + LiteLLM adapter.

    Handles multi-round tool calling against MCP servers, session persistence
    via a LangGraph checkpointer, and OTEL spans.
    """

    def __init__(
        self,
        *,
        model: str,
        temperature: float,
        system_prompt: str,
        mcp_clients: List[StdioMCPClient],
        checkpointer,
        max_tool_rounds: int = 10,
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._system_prompt = system_prompt
        self._mcp_clients = mcp_clients
        self._checkpointer = checkpointer
        self._max_tool_rounds = max_tool_rounds

        # Build tool index from connected MCP clients
        self._mcp_tools: Dict[str, tuple[StdioMCPClient, MCPTool]] = {}
        for client in mcp_clients:
            for tool in client.tools:
                self._mcp_tools[tool.name] = (client, tool)

        self._graph = self._build_graph()

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    async def run(self, message: str, thread_id: str) -> str:
        """Run one turn and return the assistant reply."""
        from agentship.observability import otel

        with otel.span("llm_turn", model=self._model, thread_id=thread_id):
            result = await self._graph.ainvoke(
                {"messages": [HumanMessage(content=message)]},
                config={"configurable": {"thread_id": thread_id}},
            )
        last = result["messages"][-1]
        return last.content if isinstance(last.content, str) else str(last.content)

    # -------------------------------------------------------------------------
    # Graph construction
    # -------------------------------------------------------------------------

    def _build_graph(self) -> Any:
        graph = StateGraph(_State)

        async def llm_node(state: _State) -> _State:
            messages = self._build_messages(state["messages"])
            tools_schema = self._tools_schema() if self._mcp_tools else None
            response = await self._call_llm(messages, tools_schema)

            msg = response.choices[0].message
            content = msg.content or ""
            tool_calls = getattr(msg, "tool_calls", None) or []

            ai_msg = AIMessage(content=content)
            if tool_calls:
                ai_msg.tool_calls = [
                    {
                        "id": tc.id,
                        "name": tc.function.name,
                        "args": json.loads(tc.function.arguments or "{}"),
                    }
                    for tc in tool_calls
                ]
            return {"messages": [ai_msg]}

        async def tools_node(state: _State) -> _State:
            last = state["messages"][-1]
            tool_calls = getattr(last, "tool_calls", []) or []
            results = []
            for tc in tool_calls:
                name = tc.get("name", "")
                args = tc.get("args", {})
                result = await self._call_tool(name, args)
                results.append(ToolMessage(content=result, tool_call_id=tc.get("id", ""), name=name))
            return {"messages": results}

        def router(state: _State) -> str:
            if not state["messages"]:
                return "end"
            last = state["messages"][-1]
            if getattr(last, "tool_calls", None):
                return "tools"
            return "end"

        graph.add_node("llm", llm_node)
        if self._mcp_tools:
            graph.add_node("tools", tools_node)
            graph.add_conditional_edges("llm", router, {"tools": "tools", "end": END})
            graph.add_edge("tools", "llm")
        else:
            graph.add_edge("llm", END)

        graph.set_entry_point("llm")
        return graph.compile(checkpointer=self._checkpointer)

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _build_messages(self, history: List[BaseMessage]) -> List[dict]:
        """Build the message list for LiteLLM, prepending the system prompt."""
        result = []
        if self._system_prompt:
            result.append({"role": "system", "content": self._system_prompt})
        for msg in history:
            if isinstance(msg, HumanMessage):
                result.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                entry: Dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
                tcs = getattr(msg, "tool_calls", None)
                if tcs:
                    entry["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {"name": tc["name"], "arguments": json.dumps(tc["args"])},
                        }
                        for tc in tcs
                    ]
                result.append(entry)
            elif isinstance(msg, ToolMessage):
                result.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id,
                    "content": msg.content,
                })
        return result

    def _tools_schema(self) -> List[dict]:
        schemas = []
        for name, (_, tool) in self._mcp_tools.items():
            schemas.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.description,
                    "parameters": tool.input_schema or {"type": "object", "properties": {}},
                },
            })
        return schemas

    async def _call_llm(self, messages: List[dict], tools: Optional[List[dict]]) -> Any:
        kwargs: Dict[str, Any] = dict(
            model=self._model,
            messages=messages,
            temperature=self._temperature,
            stream=False,
        )
        if tools:
            kwargs["tools"] = tools

        last_err = None
        for attempt in range(_RATE_LIMIT_RETRIES):
            try:
                return await acompletion(**kwargs)
            except RateLimitError as exc:
                last_err = exc
                wait = _RATE_LIMIT_BACKOFF * (attempt + 1)
                logger.warning("Rate limit, retrying in %ds (attempt %d)", wait, attempt + 1)
                await asyncio.sleep(wait)
        raise last_err

    async def _call_tool(self, name: str, args: Dict[str, Any]) -> str:
        from agentship.observability import otel
        entry = self._mcp_tools.get(name)
        if not entry:
            return f"Error: unknown tool '{name}'"
        client, _ = entry
        with otel.span("tool_call", tool=name):
            try:
                return await client.call_tool(name, args)
            except Exception as exc:
                logger.error("Tool %s failed: %s", name, exc)
                return f"Error calling tool '{name}': {exc}"
