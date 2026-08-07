"""The ``echo`` engine — the zero-dependency walking-skeleton engine.

:class:`EchoEngine` implements the :class:`~agentship.engines.base.Engine` contract
with no external dependencies, so the whole spine (author → build → run/stream →
CLI) is exercisable without an LLM, an API key, or a heavy runtime. It stays
forever as the conformance target every real engine is tested against.

**Neutrality proof.** Echo is the vendor-free, non-LangGraph engine that proves the
base classes hold when you swap the engine: it implements every base-class method
with zero LangChain/LangGraph/LiteLLM import. ``conformance/test_echo_vendor_free.py``
locks that in — it goes red the instant echo imports a vendor library.
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

    def build(self, spec: AgentSpec, authored: object = None) -> AgentSpec:
        """Compile the spec — for echo there is nothing to compile, so return it.

        Echo has no custom-authoring path, so it ignores ``authored`` entirely and
        builds from the spec alone.
        """
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
