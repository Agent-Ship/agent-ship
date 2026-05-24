"""Engine interface for agent execution.

This is the seam where we isolate framework specifics:
- ADK runner execution
- (future) OpenAI Agents SDK execution
- (future) LangGraph execution

Engines receive an optional `request_context` dict on every call. It is
populated by middlewares (via `MiddlewareEngine`) with per-request side-
channel data that the engine may use when building prompts — e.g. the
`MEMORY_RECALL_KEY` block written by `MemoryMiddleware`. The dict is
created fresh per request and discarded when the call returns; it is
never shared between concurrent requests.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Dict, FrozenSet, Optional

from pydantic import BaseModel


@dataclass(frozen=True)
class EngineCapabilities:
    """Capabilities contract for an execution engine.

    This is used to fail-fast when an agent is configured with an engine
    that cannot support its requested model/provider/features.
    """

    supported_providers: FrozenSet[str]
    supports_sse_streaming: bool = True
    supports_tool_calling: bool = True
    supports_bidi_streaming: bool = False
    supports_multimodal: bool = False
    notes: Optional[str] = None


class AgentEngine(abc.ABC):
    """Abstract engine for executing an agent."""

    @abc.abstractmethod
    def engine_name(self) -> str:
        """Human-readable engine name (e.g. 'adk', 'openai_sdk')."""

    @abc.abstractmethod
    def capabilities(self) -> EngineCapabilities:
        """Return the engine capability contract."""

    @abc.abstractmethod
    async def run(
        self,
        user_id: str,
        session_id: str,
        input_data: BaseModel,
        request_context: Optional[Dict[str, Any]] = None,
    ) -> BaseModel:
        """Run the agent (non-streaming).

        Args:
            user_id: The end-user identifier.
            session_id: The conversation/session identifier.
            input_data: Strongly-typed input matching the agent's schema.
            request_context: Optional per-request side-channel populated
                by middlewares before the call. Engines read keys like
                `MEMORY_RECALL_KEY` from this to enrich prompts. Defaults
                to None when no middleware has anything to contribute,
                which engines should treat as an empty dict.
        """

    @abc.abstractmethod
    async def run_stream(
        self,
        user_id: str,
        session_id: str,
        input_data: BaseModel,
        request_context: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Run the agent and yield standardized stream events.

        Args:
            user_id: The end-user identifier.
            session_id: The conversation/session identifier.
            input_data: Strongly-typed input matching the agent's schema.
            request_context: Same per-request side-channel contract as `run`.
        """

    def rebuild(self) -> None:
        """Optional hook to rebuild underlying engine state."""

        return None
