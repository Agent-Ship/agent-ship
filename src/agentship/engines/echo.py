"""The ``echo`` engine — the zero-dependency walking-skeleton engine.

:class:`EchoEngine` implements the :class:`~agentship.engines.base.Engine` contract
with no external dependencies, so the whole spine (author → build → run/stream →
CLI) is exercisable without an LLM, an API key, or a heavy runtime. It stays
forever as the conformance target every real engine is tested against.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from .base import Engine, EngineCapabilities, Event, Result

if TYPE_CHECKING:
    from ..context import RunContext
    from ..spec import AgentSpec


class EchoEngine(Engine):
    """A trivial engine that echoes its input back.

    Declares only ``streaming`` — it can neither call tools, produce structured
    output, coordinate members, nor resume — so the capability gate rejects any
    spec that asks for those, proving the gate works end-to-end.
    """

    name = "echo"
    capabilities = EngineCapabilities(streaming=True)

    def build(self, spec: AgentSpec) -> AgentSpec:
        """Compile the spec — for echo there is nothing to compile, so return it."""
        return spec

    async def run(self, compiled: AgentSpec, text: str, ctx: RunContext) -> Result:
        """Return ``echo: <input>`` as the turn's output."""
        return Result(output=f"echo: {text}")

    async def stream(
        self, compiled: AgentSpec, text: str, ctx: RunContext
    ) -> AsyncIterator[Event]:
        """Yield one content chunk (``echo: <input>``), then a terminal ``done`` event."""
        yield Event(type="content", data=f"echo: {text}")
        yield Event(type="done")
