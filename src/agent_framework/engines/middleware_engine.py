"""Middleware-wrapped engine.

This wrapper lets us add memory/RAG/tracing/safety without coupling to any
specific engine (ADK/OpenAI SDK/LangGraph).

Current scope:
- Non-streaming: middlewares run around the call, and the per-request
  `request_context` dict is threaded through to the inner engine.
- Streaming: still pass-through for now (future: add streaming hooks +
  final response capture). See design.md §4.6.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

from pydantic import BaseModel

from src.agent_framework.engines.base import AgentEngine, EngineCapabilities
from src.agent_framework.middleware.base import EngineMiddleware
from src.agent_framework.middleware.memory_middleware import MEMORY_RECALL_KEY


log = logging.getLogger(__name__)


class MiddlewareEngine(AgentEngine):
    """Wraps an inner engine and runs middlewares around each call.

    Holds a `_request_context_template` of values that apply for the
    lifetime of the engine (typically just `{"agent_name": ...}`). For
    each request, we build a fresh per-call dict by copying the template,
    hand that dict to every middleware and to the inner engine, and
    discard it when the call returns. The template itself is never
    mutated, so concurrent requests cannot leak state into each other
    through it.
    """

    def __init__(
        self,
        *,
        inner: AgentEngine,
        middlewares: List[EngineMiddleware],
        request_context_template: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Wrap an inner engine with a stack of middlewares.

        Args:
            inner: The real engine that runs the agent (ADK / LangGraph).
            middlewares: Ordered list invoked around every call. `before_run`
                runs front-to-back; `after_run` runs back-to-front so the
                wrapping is symmetric.
            request_context_template: Long-lived values copied into every
                per-call `request_context` dict. Use this for things that
                are the same for every request to this agent (the agent's
                name, build info). Do NOT use it for per-request state —
                that belongs in the per-call dict that middlewares write
                into.
        """
        self._inner = inner
        self._middlewares = middlewares
        # Long-lived template. We copy from this on every call; we never
        # mutate it. Keeping a separate name from the per-call local
        # (`request_context`) makes the lifetime difference visible to
        # readers.
        self._request_context_template: Dict[str, Any] = (
            dict(request_context_template) if request_context_template else {}
        )

    def engine_name(self) -> str:
        """Preserve the underlying engine identity for logging/config."""

        return self._inner.engine_name()

    def capabilities(self) -> EngineCapabilities:
        """Defer capability reporting to the inner engine."""

        return self._inner.capabilities()

    def rebuild(self) -> None:
        """Defer rebuild to the inner engine."""

        return self._inner.rebuild()

    async def run(
        self,
        user_id: str,
        session_id: str,
        input_data: BaseModel,
        request_context: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Run the inner engine with middlewares around the call.

        Builds a fresh `request_context` dict per call so concurrent
        requests cannot share state. The same dict is passed to every
        middleware (`before_run` and `after_run`) and to the inner
        engine, so a middleware can deposit data the engine then reads.

        Args:
            user_id, session_id, input_data: Forwarded to middlewares and
                the inner engine.
            request_context: Optional seed values from a caller that
                already has its own context (rare — typically None and
                we build a fresh dict).
        """
        active_context = request_context if request_context is not None else self._new_request_context()
        current_input = input_data
        agent_name = self._request_context_template.get("agent_name", "unknown")

        log.debug(
            "middleware_engine.run.start agent=%s middlewares=%d",
            agent_name,
            len(self._middlewares),
        )

        for mw in self._middlewares:
            current_input = await mw.before_run(
                user_id=user_id,
                session_id=session_id,
                input_data=current_input,
                request_context=active_context,
            )

        log.debug(
            "middleware_engine.run.invoking_inner agent=%s memory_recall=%s",
            agent_name,
            bool(active_context.get(MEMORY_RECALL_KEY)),
        )

        output = await self._inner.run(
            user_id=user_id,
            session_id=session_id,
            input_data=current_input,
            request_context=active_context,
        )

        for mw in reversed(self._middlewares):
            await mw.after_run(
                user_id=user_id,
                session_id=session_id,
                input_data=current_input,
                output_data=output,
                request_context=active_context,
            )

        log.debug("middleware_engine.run.done agent=%s", agent_name)
        return output

    async def run_stream(
        self,
        user_id: str,
        session_id: str,
        input_data: BaseModel,
        request_context: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream events from the inner engine with middlewares around the call.

        Same envelope as `run()`, with two streaming-specific behaviors
        (design.md §4.6):

        1. `content` events are accumulated as they pass through so the
           full assistant reply can be handed to `after_run` for the
           memory write. Other event types (`tool_call`, `tool_result`,
           `thinking`, `done`) pass through but are not part of the
           accumulated text.
        2. If the inner generator raises mid-stream, `after_run` is NOT
           called — saving half a response would pollute memory.
        """
        active_context = request_context if request_context is not None else self._new_request_context()
        agent_name = self._request_context_template.get("agent_name", "unknown")
        current_input = input_data

        log.debug(
            "middleware_engine.run_stream.start agent=%s middlewares=%d",
            agent_name,
            len(self._middlewares),
        )

        for mw in self._middlewares:
            current_input = await mw.before_run(
                user_id=user_id,
                session_id=session_id,
                input_data=current_input,
                request_context=active_context,
            )

        log.debug(
            "middleware_engine.run_stream.invoking_inner agent=%s memory_recall=%s",
            agent_name,
            bool(active_context.get(MEMORY_RECALL_KEY)),
        )

        # Accumulate `content` events into one string for `after_run`. Other
        # event types pass through but don't go into the write payload.
        # `stream_completed` flips to True only on a clean finish — a mid-
        # stream exception leaves it False and we skip `after_run` so we
        # never save half a turn.
        accumulated_text_parts: List[str] = []
        stream_completed = False
        try:
            async for event in self._inner.run_stream(
                user_id=user_id,
                session_id=session_id,
                input_data=current_input,
                request_context=active_context,
            ):
                if isinstance(event, dict) and event.get("type") == "content":
                    text_chunk = event.get("text") or event.get("content") or ""
                    if text_chunk:
                        accumulated_text_parts.append(text_chunk)
                yield event
            stream_completed = True
        finally:
            if stream_completed:
                output_text = "".join(accumulated_text_parts)
                for mw in reversed(self._middlewares):
                    await mw.after_run(
                        user_id=user_id,
                        session_id=session_id,
                        input_data=current_input,
                        output_data=output_text,
                        request_context=active_context,
                    )
                log.debug(
                    "middleware_engine.run_stream.done agent=%s output_chars=%d",
                    agent_name,
                    len(output_text),
                )
            else:
                log.debug(
                    "middleware_engine.run_stream.aborted agent=%s reason=mid_stream_exception",
                    agent_name,
                )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _new_request_context(self) -> Dict[str, Any]:
        """Build a fresh per-call request_context dict from the template.

        Returning a shallow copy is enough today because we only put flat
        values in here (strings, the recall block). If a future middleware
        starts nesting dicts inside the context and mutating them in place,
        this becomes a deep-copy candidate — for now, a shallow copy is
        the cheap, correct choice.
        """
        return dict(self._request_context_template)
