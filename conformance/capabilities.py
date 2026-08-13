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
from pydantic import BaseModel


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
    """Positive cell for ``structured_output``: the engine really returns a validated model.

    Inspects real behaviour rather than hard-coding a failure: it runs the built
    agent and asserts the turn's ``output`` is a validated pydantic model instance,
    which is what a genuine structured-output engine must produce. An engine that
    declares ``structured_output != "none"`` but returns a plain string (as every
    shipped engine does today — structured output lands in a later phase) fails this
    cell, catching the over-claim. When a real structured-output engine ships it
    already passes, no edit needed.
    """
    result = await agent.run("ping", user_id="conformance")
    assert isinstance(result.output, BaseModel), (
        f"engine {agent.spec.engine!r} declares structured_output but its run returned "
        f"{type(result.output).__name__} (expected a validated pydantic model) — either "
        f"implement structured output or set structured_output back to 'none'"
    )


async def _prove_multi_agent(agent: RunnableAgent) -> None:
    """Positive cell for ``multi_agent``: the built agent really coordinates its members.

    Inspects the built agent rather than hard-coding a failure: the request spec
    declares members, so a genuine multi-agent engine must surface a compiled team
    (its coordinator/members) on the built artifact. This cell asserts the engine
    exposes an inspectable ``members`` structure on the compiled agent. No shipped
    engine coordinates members yet (a later phase), so a declaration today fails
    here — catching the over-claim while passing automatically once real
    coordination ships.
    """
    members = getattr(agent.compiled, "members", None)
    assert members, (
        f"engine {agent.spec.engine!r} declares multi_agent but its built agent exposes "
        f"no coordinated `members` — either implement multi-agent coordination or drop "
        f"the declaration"
    )


async def _prove_durability(agent: RunnableAgent) -> None:
    """Positive cell for ``durability``: the engine really implements the resume seam.

    A genuinely durable engine must **override** :meth:`Engine.resume` with a real replay — the
    base default raises, so an engine that declared durability but inherited it would be faking.
    This cell asserts the override exists (the anti-over-declaration check); the full kill → resume
    → byte-identical guarantee is proven per-engine in its durability suite (P02
    ``test_langgraph_durable.py`` / ``test_phase02_durability.py``). The checkpointer itself is
    opened per-run (owner-approved lifecycle), so it is deliberately *not* an attribute on the
    compiled artifact.
    """
    from agentship.engines.base import Engine

    assert type(agent.engine).resume is not Engine.resume, (
        f"engine {agent.spec.engine!r} declares durability but did not override resume() — it "
        f"would raise the base default. Implement durable resume (mint token → kill → resume) or "
        f"set durability back to 'none'."
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


#: Capabilities that have **no** conformance cell yet because they land in a later
#: phase, mapped to the phase that builds them. This is the explicit allowlist the
#: coverage guard (:mod:`conformance.test_over_declaration`) checks: every
#: :class:`~agentship.engines.base.EngineCapabilities` field must be *either* covered
#: by a :data:`CAPABILITIES` cell *or* listed here. A newly-declared capability field
#: with neither a cell nor an entry here fails the guard, so it cannot slip past
#: unproven. ``providers`` is gated separately (at build, against the ``model:``
#: prefix — see :meth:`EngineCapabilities._assert_provider_supported`), not by a cell.
DEFERRED_CAPABILITIES: dict[str, str] = {
    "tool_calling": "P03",
    "hitl": "P02",
    "cycles": "P02",
    "multimodal_in": "P03",
    "live_bidi": "P13",
    "providers": "gated at build (model prefix), not by a matrix cell",
}


def covered_capability_names() -> frozenset[str]:
    """Return the set of capability field names that have a :data:`CAPABILITIES` cell."""
    return frozenset(capability.name for capability in CAPABILITIES)


def request_capability(capability: Capability, engine_name: str) -> RunnableAgent:
    """Build an agent whose spec requests ``capability`` on ``engine_name``.

    Used by the negative rejection cell: on an engine that does not declare the
    capability, :func:`~agentship.runtime.build_agent` must raise
    :class:`~agentship.errors.CapabilityError` here (fail-fast). Only valid when the
    capability has a ``request_spec``.
    """
    assert capability.request_spec is not None
    return build_agent(capability.request_spec(engine_name))
