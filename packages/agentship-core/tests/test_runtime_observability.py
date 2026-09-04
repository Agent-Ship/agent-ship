"""The runtime opens one root ``agent`` span per turn and stamps the trace id (P07 · C2).

The observer seam lives in :class:`~agentship.runtime.RunnableAgent`, not in any engine, so the span
tree is identical whichever engine runs. These assert the root span wraps the whole pipeline,
carries the frozen identity attributes, records ok/error status, and that ``ctx.trace_id`` is
visible to middleware while the turn runs.
"""

from __future__ import annotations

import pytest
from agentship.context import RunContext
from agentship.middleware import Middleware
from agentship.observability import RecordingObserver, semconv
from agentship.runtime import build_agent
from agentship.spec import AgentSpec


class _TraceIdProbe(Middleware):
    """Middleware that captures the trace id visible at ``on_request`` time."""

    def __init__(self) -> None:
        """Start with no captured trace id."""
        self.seen_trace_id: str | None = "unset"

    async def on_request(self, ctx: RunContext) -> None:
        """Record the trace id the runtime stamped before the engine ran."""
        self.seen_trace_id = ctx.trace_id


async def test_run_opens_one_root_agent_span_with_identity_attrs() -> None:
    """A turn opens exactly one root ``agent`` span carrying the frozen identity keys, status ok."""
    obs = RecordingObserver()
    agent = build_agent(AgentSpec(name="support", engine="echo"), observer=obs)
    await agent.run("hi", user_id="u1")
    (root,) = obs.roots
    assert root.name.startswith(semconv.SPAN_AGENT)
    assert root.status == "ok"
    assert root.attrs[semconv.AS_STATUS] == "ok"
    assert root.attrs[semconv.AS_AGENT_NAME] == "support"
    assert root.attrs[semconv.AS_RUN_MODE] == "invoke"
    assert root.attrs[semconv.AS_TENANT_ID] == "default"


async def test_trace_id_is_stamped_before_middleware_runs() -> None:
    """The runtime stamps ``ctx.trace_id`` from the observer before ``on_request`` fires."""
    obs = RecordingObserver()
    probe = _TraceIdProbe()
    agent = build_agent(AgentSpec(name="a", engine="echo"), middlewares=[probe], observer=obs)
    await agent.run("hi")
    assert probe.seen_trace_id is not None
    assert probe.seen_trace_id == obs.current_trace_id()


async def test_engine_failure_marks_root_span_error() -> None:
    """When the engine raises, the root span is errored (status + attr) and the error propagates."""
    obs = RecordingObserver()
    agent = build_agent(AgentSpec(name="a", engine="echo"), observer=obs)
    boom = RuntimeError("engine boom")

    async def fail(compiled, text, ctx):
        raise boom

    agent.engine.run = fail  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="engine boom"):
        await agent.run("hi")
    (root,) = obs.roots
    assert root.status == "error"
    assert root.attrs[semconv.AS_STATUS] == "error"
    assert root.attrs["exception.type"] == "RuntimeError"


async def test_stream_opens_one_root_span_over_the_whole_generator() -> None:
    """A fully consumed stream opens one root span and closes it ok after the last event."""
    obs = RecordingObserver()
    agent = build_agent(AgentSpec(name="a", engine="echo"), observer=obs)
    events = [event async for event in agent.stream("hi")]
    assert [e.type for e in events] == ["content", "done"]
    (root,) = obs.roots
    assert root.name.startswith(semconv.SPAN_AGENT)
    assert root.status == "ok"
    assert root.attrs[semconv.AS_RUN_MODE] == "stream"


async def test_default_observer_is_noop_so_untraced_agents_still_run() -> None:
    """Built without an observer, an agent runs on the NoOp observer — no tracing, no crash."""
    agent = build_agent(AgentSpec(name="a", engine="echo"))
    result = await agent.run("hi")
    assert result.output == "echo: hi"


async def test_stream_early_stop_leaves_span_ok() -> None:
    """Abandoning a stream after one event is a clean disconnect — the root span stays ok."""
    obs = RecordingObserver()
    agent = build_agent(AgentSpec(name="a", engine="echo"), observer=obs)
    stream = agent.stream("hi")
    assert (await stream.__anext__()).type == "content"
    await stream.aclose()  # caller stops early
    (root,) = obs.roots
    assert root.status == "ok"
