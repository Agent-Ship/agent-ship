"""The capability catalogue the conformance matrix runs over.

This module is the single, small edit-point that makes the matrix extensible:
adding a capability = adding one :class:`Capability` entry to :data:`CAPABILITIES`;
adding an engine = registering it (entry point) plus, if it needs a model, one
line in :mod:`conformance.engines`.

Each :class:`Capability` teaches the matrix three things about one row of the
engine×capability grid:

* how to build an :class:`~agentship.spec.AgentSpec` that *requests* the capability
  (``request_spec``) — used to prove the capability gate rejects it when the engine
  does **not** declare it;
* how to read the engine's declaration (``declared``) — so the matrix knows whether
  to run the positive "it really works" cell or the negative "it is rejected" cell;
* how to *prove* the capability actually works (``prove``) — the positive cell, run
  only when the engine declares the capability. A declared capability whose ``prove``
  fails is a build failure: that is the whole point (*declare, don't fake*).

Capabilities whose ``request_spec`` is ``None`` have no spec field that the
capability gate keys on yet (e.g. ``tool_calling`` — tools are not an ``AgentSpec``
field until phase 03). For those the matrix skips the negative rejection cell (there
is nothing to reject through) but still runs the positive cell when declared.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agentship.engines.base import EngineCapabilities
from agentship.runtime import RunnableAgent, build_agent
from agentship.spec import AgentSpec, MemberSpec


@dataclass(frozen=True)
class Capability:
    """One row of the conformance grid: how to request, read, and prove a capability.

    ``name`` is the :class:`~agentship.engines.base.EngineCapabilities` field this
    row covers. ``declared`` reads that field off an engine's capabilities and
    returns whether the engine claims the capability. ``request_spec`` builds a spec
    that asks for the capability (or ``None`` if no spec field gates it yet).
    ``prove`` is the positive cell: given a *built* agent on an engine that declares
    the capability, it asserts the capability genuinely works (raising on failure).
    """

    name: str
    declared: Callable[[EngineCapabilities], bool]
    request_spec: Callable[[str], AgentSpec] | None
    prove: Callable[[RunnableAgent], Awaitable[None]] | None


async def _prove_streaming(agent: RunnableAgent) -> None:
    """Positive cell for ``streaming``: the agent yields >=1 chunk then a terminal ``done``.

    Drives the built agent's :meth:`~agentship.runtime.RunnableAgent.stream` and
    asserts the streaming contract every streaming engine must honour: at least one
    ``content`` event arrives, and the final event is exactly one terminal ``done``.
    Offline — the engine fixture injects a fake model where a real one is needed.
    """
    events = [event async for event in agent.stream("ping", user_id="conformance")]
    content = [event for event in events if event.type == "content"]
    assert content, f"engine {agent.spec.engine!r} declares streaming but yielded no content"
    assert events, f"engine {agent.spec.engine!r} streamed nothing at all"
    assert events[-1].type == "done", (
        f"engine {agent.spec.engine!r} did not terminate the stream with a `done` event "
        f"(got {events[-1].type!r})"
    )
    assert sum(1 for e in events if e.type == "done") == 1, (
        f"engine {agent.spec.engine!r} emitted more than one terminal `done` event"
    )


async def _prove_structured_output(agent: RunnableAgent) -> None:
    """Positive cell for ``structured_output``: the engine really validates a target.

    Not yet implemented by any shipped engine (phase 04). It exists so that the
    moment an engine declares ``structured_output != "none"``, the matrix demands a
    real proof rather than accepting the claim. Until a shipped engine declares it,
    this cell is unreached for the real engines; the over-declaration meta-test
    (:mod:`conformance.test_over_declaration`) exercises the "declared-but-unbuilt →
    cell fails" path directly.
    """
    raise AssertionError(
        f"engine {agent.spec.engine!r} declares structured_output but no conformance "
        f"cell proves it — structured output is a phase-04 capability; either it is "
        f"genuinely implemented (write the real proof here) or the declaration is an "
        f"over-claim (set structured_output back to 'none')"
    )


async def _prove_multi_agent(agent: RunnableAgent) -> None:
    """Positive cell for ``multi_agent`` — unbuilt (phase 06); demands proof if declared."""
    raise AssertionError(
        f"engine {agent.spec.engine!r} declares multi_agent but no conformance cell "
        f"proves it — multi-agent coordination is a later-phase capability; implement "
        f"the real proof or drop the declaration"
    )


async def _prove_durability(agent: RunnableAgent) -> None:
    """Positive cell for ``durability`` — unbuilt (phase 02/09); demands proof if declared."""
    raise AssertionError(
        f"engine {agent.spec.engine!r} declares durability but no conformance cell "
        f"proves it — durable resume is a later-phase capability; implement the real "
        f"proof (mint token → kill → resume) or drop the declaration"
    )


#: The engine×capability grid, as a list of capability descriptors. Each is one
#: column the matrix walks for every registered engine. **This is the extension
#: point:** add a capability by appending a :class:`Capability` here; the matrix
#: picks it up automatically. Only capabilities with a spec field the gate keys on
#: carry a ``request_spec`` (so the negative rejection cell can run); the rest set
#: it to ``None`` and are proved positively only.
CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        name="streaming",
        declared=lambda caps: caps.streaming,
        request_spec=lambda engine: AgentSpec(
            name="cell", engine=engine, model="x", prompt="p", streaming=True
        ),
        prove=_prove_streaming,
    ),
    Capability(
        name="structured_output",
        declared=lambda caps: caps.structured_output != "none",
        request_spec=lambda engine: AgentSpec(
            name="cell", engine=engine, model="x", output_schema="mypkg:Answer"
        ),
        prove=_prove_structured_output,
    ),
    Capability(
        name="multi_agent",
        declared=lambda caps: caps.multi_agent,
        request_spec=lambda engine: AgentSpec(
            name="cell", engine=engine, model="x", members=[MemberSpec(name="m1")]
        ),
        prove=_prove_multi_agent,
    ),
    Capability(
        name="durability",
        declared=lambda caps: caps.durability != "none",
        request_spec=lambda engine: AgentSpec(
            name="cell", engine=engine, model="x", durability="checkpoint"
        ),
        prove=_prove_durability,
    ),
)


def request_capability(capability: Capability, engine_name: str) -> RunnableAgent:
    """Build an agent whose spec requests ``capability`` on ``engine_name``.

    Used by the negative rejection cell: on an engine that does not declare the
    capability, :func:`~agentship.runtime.build_agent` must raise
    :class:`~agentship.errors.CapabilityError` here (fail-fast). Only valid when the
    capability has a ``request_spec``.
    """
    assert capability.request_spec is not None
    return build_agent(capability.request_spec(engine_name))
