"""The runtime scopes a turn to the caller it is handed — tenant and scopes included.

This is the completion of tenant isolation: the runtime service authenticates a request
into a :class:`Caller` (tenant + scopes) and must thread *that whole identity* into the
run, not just a user id. These tests capture the ``RunContext`` the engine sees and assert
its caller is exactly the one passed — and that omitting a caller still yields the
single-tenant dev default so an un-plumbed project keeps working.
"""

from __future__ import annotations

from agentship.context import Caller, RunContext
from agentship.middleware import Middleware
from agentship.runtime import build_agent
from agentship.spec import AgentSpec


class _CaptureCaller(Middleware):
    """Middleware that stashes the turn's ``RunContext`` so a test can inspect its caller."""

    def __init__(self) -> None:
        """Start with no captured context."""
        self.ctx: RunContext | None = None

    async def on_request(self, ctx: RunContext) -> None:
        """Capture the context for this turn."""
        self.ctx = ctx


async def test_run_threads_full_caller_tenant_and_scopes() -> None:
    """A caller passed to ``run`` scopes the turn — tenant and scopes reach the context."""
    capture = _CaptureCaller()
    agent = build_agent(AgentSpec(name="a", engine="echo"), middlewares=[capture])
    caller = Caller(
        tenant_id="acme", user_id="u1", scopes=frozenset({"agent:a:invoke"}), auth_method="api_key"
    )

    await agent.run("hi", caller=caller)

    assert capture.ctx is not None
    assert capture.ctx.caller == caller
    assert capture.ctx.tenant_id == "acme"
    assert capture.ctx.memory_scope == ("acme", "u1")
    assert "agent:a:invoke" in capture.ctx.caller.scopes


async def test_stream_threads_full_caller() -> None:
    """A caller passed to ``stream`` scopes the streamed turn the same way."""
    capture = _CaptureCaller()
    agent = build_agent(AgentSpec(name="a", engine="echo"), middlewares=[capture])
    caller = Caller(tenant_id="acme", user_id="u1")

    async for _ in agent.stream("hi", caller=caller):
        pass

    assert capture.ctx is not None
    assert capture.ctx.tenant_id == "acme"
    assert capture.ctx.user_id == "u1"


async def test_caller_wins_over_user_id() -> None:
    """When both are given, the authenticated caller wins over the bare ``user_id``."""
    capture = _CaptureCaller()
    agent = build_agent(AgentSpec(name="a", engine="echo"), middlewares=[capture])
    caller = Caller(tenant_id="acme", user_id="real")

    await agent.run("hi", caller=caller, user_id="ignored")

    assert capture.ctx is not None
    assert capture.ctx.user_id == "real"


async def test_no_caller_falls_back_to_single_tenant_default() -> None:
    """Omitting a caller keeps the dev default: single ``"default"`` tenant, given user id."""
    capture = _CaptureCaller()
    agent = build_agent(AgentSpec(name="a", engine="echo"), middlewares=[capture])

    await agent.run("hi", user_id="dev")

    assert capture.ctx is not None
    assert capture.ctx.tenant_id == "default"
    assert capture.ctx.user_id == "dev"
    assert capture.ctx.caller.scopes == frozenset()
