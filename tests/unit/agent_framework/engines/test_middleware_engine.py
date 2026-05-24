"""Unit tests for `MiddlewareEngine`.

What's covered:
  - `run` calls middlewares in declared order; `after_run` in reverse
  - `run` builds a fresh per-request context (no class-level mutation)
  - `run` threads request_context to the inner engine
  - Concurrent calls don't share their per-request context
  - `run_stream` calls before_run, then streams events through, then
    calls after_run with the accumulated `content` text
  - `run_stream` skips after_run when the inner generator raises
    mid-stream (no half-saved turns)
  - `run_stream` passes non-content events through unchanged
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator, Dict, List, Optional

import pytest
from pydantic import BaseModel

from src.agent_framework.engines.base import AgentEngine, EngineCapabilities
from src.agent_framework.engines.middleware_engine import MiddlewareEngine
from src.agent_framework.middleware.base import EngineMiddleware


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _Input(BaseModel):
    text: str


class _RecordingMiddleware(EngineMiddleware):
    """Captures every call into a shared log so tests can assert order.

    `before_run` optionally writes a value into `request_context` so we
    can verify the per-call dict reaches the inner engine.
    """

    def __init__(self, name: str, call_log: list, *, write_key: Optional[str] = None, write_value: Any = None):
        self.name = name
        self.call_log = call_log
        self.write_key = write_key
        self.write_value = write_value

    async def before_run(self, *, user_id, session_id, input_data, request_context=None):
        self.call_log.append((self.name, "before"))
        if self.write_key and request_context is not None:
            request_context[self.write_key] = self.write_value
        return input_data

    async def after_run(self, *, user_id, session_id, input_data, output_data, request_context=None):
        self.call_log.append((self.name, "after", output_data))


class _RecordingInner(AgentEngine):
    """Minimal AgentEngine that returns a fixed output and records what it saw."""

    def __init__(
        self,
        *,
        run_output: Any = "inner_output",
        stream_events: Optional[List[Dict[str, Any]]] = None,
        raise_in_stream_after: Optional[int] = None,
    ):
        self.run_output = run_output
        self.stream_events = stream_events or []
        self.raise_in_stream_after = raise_in_stream_after
        self.last_request_context: Optional[Dict[str, Any]] = None
        self.run_call_count = 0

    def engine_name(self) -> str:
        return "fake"

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(supported_providers=frozenset({"openai"}))

    def rebuild(self) -> None:
        return None

    async def run(self, user_id, session_id, input_data, request_context=None):
        self.run_call_count += 1
        # Snapshot the context dict so concurrent tests can assert isolation.
        self.last_request_context = dict(request_context) if request_context is not None else None
        return self.run_output

    async def run_stream(
        self, user_id, session_id, input_data, request_context=None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        self.last_request_context = dict(request_context) if request_context is not None else None
        for index, event in enumerate(self.stream_events):
            if self.raise_in_stream_after is not None and index == self.raise_in_stream_after:
                raise RuntimeError("inner stream blew up")
            yield event


# ---------------------------------------------------------------------------
# `run` — non-streaming
# ---------------------------------------------------------------------------


class TestRunNonStreaming:
    @pytest.mark.asyncio
    async def test_middleware_called_in_order_before_and_reverse_after(self):
        log: list = []
        mw_a = _RecordingMiddleware("A", log)
        mw_b = _RecordingMiddleware("B", log)
        inner = _RecordingInner(run_output="OK")
        engine = MiddlewareEngine(inner=inner, middlewares=[mw_a, mw_b])

        await engine.run(user_id="u1", session_id="s1", input_data=_Input(text="hi"))

        # before: A then B; after: B then A (reverse).
        assert log == [
            ("A", "before"),
            ("B", "before"),
            ("B", "after", "OK"),
            ("A", "after", "OK"),
        ]

    @pytest.mark.asyncio
    async def test_request_context_reaches_inner_engine(self):
        log: list = []
        mw = _RecordingMiddleware("M", log, write_key="recall_block", write_value="<recalled>")
        inner = _RecordingInner()
        engine = MiddlewareEngine(inner=inner, middlewares=[mw])

        await engine.run(user_id="u1", session_id="s1", input_data=_Input(text="hi"))

        assert inner.last_request_context is not None
        assert inner.last_request_context["recall_block"] == "<recalled>"

    @pytest.mark.asyncio
    async def test_request_context_template_is_never_mutated(self):
        template = {"agent_name": "demo"}
        inner = _RecordingInner()
        mw = _RecordingMiddleware("M", [], write_key="recall_block", write_value="x")
        engine = MiddlewareEngine(
            inner=inner,
            middlewares=[mw],
            request_context_template=template,
        )

        await engine.run(user_id="u1", session_id="s1", input_data=_Input(text="hi"))

        # The template the caller passed in stays unchanged.
        assert template == {"agent_name": "demo"}
        # And the engine's internal copy also stays unchanged.
        assert engine._request_context_template == {"agent_name": "demo"}
        # The per-call context the inner saw had BOTH the template entry
        # and the per-call write.
        assert inner.last_request_context == {"agent_name": "demo", "recall_block": "x"}

    @pytest.mark.asyncio
    async def test_concurrent_calls_do_not_share_context(self):
        """Two concurrent `run` calls must each get their own context dict.

        Each middleware writes a different value under the same key; if
        the dict were shared, one call would see the other's value.
        """
        observed_contexts: List[Dict[str, Any]] = []

        class WriteAndSnapshot(EngineMiddleware):
            def __init__(self, value):
                self.value = value

            async def before_run(self, *, user_id, session_id, input_data, request_context=None):
                # Simulate some async work between write and read so the
                # two concurrent calls can interleave.
                request_context["mark"] = self.value
                await asyncio.sleep(0.01)
                observed_contexts.append(dict(request_context))
                return input_data

            async def after_run(self, **_):
                pass

        inner = _RecordingInner()
        engine_a = MiddlewareEngine(
            inner=inner, middlewares=[WriteAndSnapshot("A")],
            request_context_template={"agent_name": "demo"},
        )
        engine_b = MiddlewareEngine(
            inner=inner, middlewares=[WriteAndSnapshot("B")],
            request_context_template={"agent_name": "demo"},
        )

        await asyncio.gather(
            engine_a.run(user_id="u1", session_id="s1", input_data=_Input(text="x")),
            engine_b.run(user_id="u2", session_id="s2", input_data=_Input(text="y")),
        )

        marks = sorted(c["mark"] for c in observed_contexts)
        assert marks == ["A", "B"]   # neither saw the other's write

    def test_new_request_context_returns_distinct_dicts(self):
        engine = MiddlewareEngine(
            inner=_RecordingInner(),
            middlewares=[],
            request_context_template={"agent_name": "demo"},
        )
        c1 = engine._new_request_context()
        c2 = engine._new_request_context()
        assert c1 is not c2
        c1["leak"] = True
        assert "leak" not in c2


# ---------------------------------------------------------------------------
# `run_stream` — streaming
# ---------------------------------------------------------------------------


class TestRunStream:
    @pytest.mark.asyncio
    async def test_middleware_invoked_around_stream_with_accumulated_content(self):
        log: list = []
        mw = _RecordingMiddleware("M", log)
        events = [
            {"type": "thinking", "message": "..."},
            {"type": "content", "text": "Hello "},
            {"type": "content", "text": "world."},
            {"type": "done"},
        ]
        inner = _RecordingInner(stream_events=events)
        engine = MiddlewareEngine(inner=inner, middlewares=[mw])

        out_events = [
            e async for e in engine.run_stream(
                user_id="u1", session_id="s1", input_data=_Input(text="hi"),
            )
        ]

        # All inner events make it through unchanged.
        assert out_events == events
        # before_run ran first, then after_run with the accumulated text.
        assert log[0] == ("M", "before")
        assert log[-1] == ("M", "after", "Hello world.")

    @pytest.mark.asyncio
    async def test_request_context_reaches_inner_engine_on_stream(self):
        mw = _RecordingMiddleware("M", [], write_key="recall_block", write_value="<recalled>")
        inner = _RecordingInner(stream_events=[{"type": "content", "text": "ok"}, {"type": "done"}])
        engine = MiddlewareEngine(inner=inner, middlewares=[mw])

        _ = [e async for e in engine.run_stream(
            user_id="u1", session_id="s1", input_data=_Input(text="hi"),
        )]

        assert inner.last_request_context["recall_block"] == "<recalled>"

    @pytest.mark.asyncio
    async def test_after_run_skipped_when_stream_raises_mid_flight(self):
        """A half-streamed reply must not become a memory."""
        log: list = []
        mw = _RecordingMiddleware("M", log)
        # Three events, then raise on the third (index 2).
        events = [
            {"type": "content", "text": "partial-"},
            {"type": "content", "text": "answer-"},
            {"type": "content", "text": "boom"},
        ]
        inner = _RecordingInner(stream_events=events, raise_in_stream_after=2)
        engine = MiddlewareEngine(inner=inner, middlewares=[mw])

        with pytest.raises(RuntimeError, match="inner stream blew up"):
            async for _ in engine.run_stream(
                user_id="u1", session_id="s1", input_data=_Input(text="hi"),
            ):
                pass

        # before_run fired, after_run did NOT — the entire after_run side
        # of the middleware stack is skipped on mid-stream exception.
        assert ("M", "before") in log
        assert not any(entry[1] == "after" for entry in log)

    @pytest.mark.asyncio
    async def test_non_content_events_do_not_contribute_to_accumulated_text(self):
        log: list = []
        mw = _RecordingMiddleware("M", log)
        events = [
            {"type": "thinking", "message": "ignored"},
            {"type": "tool_call", "tool_name": "x", "arguments": {}},
            {"type": "content", "text": "the answer"},
            {"type": "tool_result", "tool_name": "x", "result": "noise"},
            {"type": "done"},
        ]
        inner = _RecordingInner(stream_events=events)
        engine = MiddlewareEngine(inner=inner, middlewares=[mw])

        _ = [e async for e in engine.run_stream(
            user_id="u1", session_id="s1", input_data=_Input(text="hi"),
        )]

        after_entry = next(entry for entry in log if entry[1] == "after")
        # Only the content event's text is in the accumulated output.
        assert after_entry[2] == "the answer"
