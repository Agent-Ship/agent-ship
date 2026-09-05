"""The shipped, vendor-neutral conformance capability catalogue.

This module is the honesty backstop for AgentShip's multi-engine promise
(DESIGN §11, "declare, don't fake") promoted into a **shipped, importable
surface** so "verifiable agents" is a real product: the ``agentship verify`` CLI
consumes :func:`run_capability_grid` to prove, for any registered engine, that
*every capability it declares genuinely works and every capability it does not
declare is rejected fail-fast*.

The catalogue teaches the matrix three things about each row of the
engine×capability grid, via a :class:`Capability`:

* how to build an :class:`~agentship.spec.AgentSpec` that *requests* the
  capability (``request_spec``) — used by the negative cell to prove the gate
  rejects an engine that does not declare it;
* how to read the engine's declaration (``declared``) — so the matrix knows
  whether to run the positive "it really works" cell or the negative "it is
  rejected" cell;
* how to *prove* the capability actually works (``prove``) — the positive cell,
  run only when the engine declares the capability. A declared capability whose
  ``prove`` fails is a build failure: that is the whole point (*declare, don't
  fake*).

Capabilities whose ``request_spec`` is ``None`` have no gate-checked
``AgentSpec`` field yet, so the matrix skips their negative rejection cell (there
is nothing to reject through) but still runs the positive cell when declared.

**Vendor-free by construction.** This module imports only ``agentship.*``,
``pydantic``, and the standard library — never langchain/langgraph/litellm — so
the catalogue itself stays engine-neutral. Vendor-specific offline harnesses
live in their engine package (e.g. ``agentship_langgraph.testing``) and are
passed in through the ``offline`` provider argument to :func:`run_capability_grid`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from agentship.engines.base import ENGINES, EngineCapabilities
from agentship.errors import CapabilityError
from agentship.runtime import RunnableAgent, build_agent
from agentship.spec import AgentSpec, MemberSpec


@dataclass(frozen=True)
class Capability:
    """One row of the conformance grid: how to request, read, and prove a capability.

    ``name`` is the :class:`~agentship.engines.base.EngineCapabilities` field this
    row covers. ``declared`` reads that field off an engine's capabilities and
    returns whether the engine claims it. ``request_spec`` builds an
    :class:`~agentship.spec.AgentSpec` that *requests* the capability (or ``None``
    when no gate-checked spec field expresses it yet). ``prove`` is the positive
    cell: given a built agent on an engine that declares the capability (or ``None``
    when the cell builds its own agent), it asserts the capability genuinely works,
    raising on failure.
    """

    name: str
    declared: Callable[[EngineCapabilities], bool]
    request_spec: Callable[[str], AgentSpec] | None
    prove: Callable[[RunnableAgent | None], Awaitable[None]] | None


async def _prove_streaming(agent: RunnableAgent | None) -> None:
    """Positive cell for ``streaming``: agent yields >=1 chunk then a terminal ``done``.

    Drives the built agent's :meth:`~agentship.runtime.RunnableAgent.stream` and
    asserts the streaming contract every streaming engine must honour: at least one
    ``content`` event arrives, and the final event is exactly one terminal ``done``.
    Offline — the engine fixture injects a fake model where a real one is needed.
    """
    assert agent is not None
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


async def _prove_structured_output(agent: RunnableAgent | None) -> None:
    """Positive cell for ``structured_output``: the engine really returns a validated model.

    Inspects real behaviour rather than hard-coding a failure: it runs the built
    agent and asserts the turn's ``output`` is a validated pydantic model instance,
    which is what a genuine structured-output engine must produce. An engine that
    declares ``structured_output != "none"`` but returns a plain string fails this
    cell, catching the over-claim. When a real structured-output engine ships it
    already passes, no edit needed.
    """
    assert agent is not None
    result = await agent.run("ping", user_id="conformance")
    assert isinstance(result.output, BaseModel), (
        f"engine {agent.spec.engine!r} declares structured_output but its run returned "
        f"{type(result.output).__name__} (expected a validated pydantic model) — either "
        f"implement structured output or set structured_output back to 'none'"
    )


async def _prove_multi_agent(agent: RunnableAgent | None) -> None:
    """Positive cell for ``multi_agent``: the built agent really coordinates its members.

    The request spec declares members, so a genuine multi-agent engine surfaces a
    compiled team (its coordinator + members) on the built artifact. This cell
    asserts the engine exposes an inspectable, non-empty ``members`` structure on
    the compiled agent — the build-time contract that a declarative ``members:``
    spec is wired into a real coordinated team rather than silently dropped. The
    full live team-coordination behaviour is exercised per-engine in its own suite
    (the multi-agent demo slice); this cell pins the build-time contract offline.
    An engine that declared ``multi_agent`` but compiled no members fails here,
    catching the over-claim.
    """
    assert agent is not None
    members = getattr(agent.compiled, "members", None)
    assert members, (
        f"engine {agent.spec.engine!r} declares multi_agent but its built agent exposes "
        f"no coordinated `members` — either implement multi-agent coordination or drop "
        f"the declaration"
    )


async def _prove_tool_calling(agent: RunnableAgent | None) -> None:
    """Positive cell for ``tool_calling``: a declared tool is really bound to the built agent.

    The request spec declares ``tools: [calculator]``, so a genuine tool-calling
    engine resolves and binds it — the built artifact exposes the bound tool names.
    This cell asserts that build-time contract: a declared tool is resolved and
    bound into the compiled agent rather than silently dropped. The full *tool
    calling by a live model* behaviour is proven per-engine in its suite (the tool
    demo slice); this cell pins the build-time contract offline. An engine that
    declared ``tool_calling`` but bound nothing fails here, catching the over-claim.
    """
    assert agent is not None
    bound = getattr(agent.compiled, "bound_tools", None)
    assert bound, (
        f"engine {agent.spec.engine!r} declares tool_calling but bound no tools for a spec that "
        f"declares one — implement tool resolution/execution or set tool_calling back to False"
    )


async def _prove_durability(agent: RunnableAgent | None) -> None:
    """Positive cell for ``durability``: the engine really implements the resume seam.

    A genuinely durable engine must **override** :meth:`Engine.resume` with a real
    replay — the base default raises, so an engine that declared durability but
    inherited it would be faking. This cell asserts the override exists (the
    anti-over-declaration check); the full kill → resume → byte-identical guarantee
    is proven per-engine in its durability suite. The checkpointer itself is opened
    per-run (owner-approved lifecycle), so it is deliberately *not* an attribute on
    the compiled artifact.
    """
    assert agent is not None
    from agentship.engines.base import Engine

    assert type(agent.engine).resume is not Engine.resume, (
        f"engine {agent.spec.engine!r} declares durability but did not override resume() — it "
        f"would raise the base default. Implement durable resume (mint token → kill → resume) or "
        f"set durability back to 'none'."
    )


#: A ``code:`` reference to a factory that builds a HITL confirm/write agent on the
#: engine under test. The catalogue holds only the string (staying vendor-free); the
#: factory lives in the engine package's testing helper (e.g.
#: ``agentship_langgraph.testing:build_hitl_agent``). Resolved by ``build_agent`` at
#: prove time, so no vendor type is named here.
_HITL_CODE_REF = "agentship_langgraph.testing:build_hitl_agent"


async def _prove_hitl(agent: RunnableAgent | None) -> None:
    """Positive cell for ``hitl``: a run that hits ``interrupt()`` pauses instead of completing.

    Human-in-the-loop is not gated by a scalar :class:`~agentship.spec.AgentSpec`
    field, so this cell is self-contained: it builds a HITL confirm/write agent via
    a ``code:`` reference (:data:`_HITL_CODE_REF`) into the engine package's testing
    helper, then drives one turn. A genuine HITL engine must *surface the interrupt*
    — the run pauses at ``interrupt(payload)`` with ``output`` left ``None``, the
    ``interrupt`` payload set, and a resume token flagged as interrupted — rather
    than running straight through the side effect. This is exactly what the engine's
    own HITL suite proves (``test_langgraph_hitl.py``); the cell pins that contract
    offline. An engine that declared ``hitl`` but completed the run (never pausing)
    fails here, catching the over-claim.

    The passed ``agent`` is ignored: ``hitl`` has no ``request_spec``, so the matrix
    hands ``None`` and the cell builds the HITL agent itself.
    """
    spec = AgentSpec(name="hitl-cell", engine="langgraph", code=_HITL_CODE_REF)
    built = build_agent(spec)
    result = await built.run("send it", user_id="conformance", session_id="conformance-hitl")
    assert result.output is None, (
        f"engine {built.spec.engine!r} declares hitl but the run completed with output "
        f"{result.output!r} instead of pausing at interrupt() — HITL must surface the pause"
    )
    assert result.interrupt, (
        f"engine {built.spec.engine!r} declares hitl but surfaced no interrupt payload — a HITL "
        f"run must pause with the confirmation payload the node passed to interrupt()"
    )
    assert result.resume_token is not None and result.resume_token.blob.get("interrupt") is True, (
        f"engine {built.spec.engine!r} declares hitl but minted no interrupt-flagged resume token "
        f"— the paused run must be resumable with the human's decision"
    )


#: The engine×capability grid, as a tuple of capability descriptors. Each is one
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
    Capability(
        name="tool_calling",
        declared=lambda caps: caps.tool_calling,
        request_spec=lambda engine: AgentSpec(
            name="cell", engine=engine, model="x", tools=["calculator"]
        ),
        prove=_prove_tool_calling,
    ),
    Capability(
        name="hitl",
        declared=lambda caps: caps.hitl != "none",
        # No scalar AgentSpec field gates hitl through the capability check — the
        # interrupt/confirm contract is expressed by a custom-authored graph, not a
        # gate-checked field — so there is no negative rejection cell to run.
        request_spec=None,
        prove=_prove_hitl,
    ),
)


#: Capabilities that have **no** conformance cell yet because they land in a later
#: phase, mapped to the phase that builds them. This is the explicit allowlist the
#: coverage guard checks: every :class:`~agentship.engines.base.EngineCapabilities`
#: field must be *either* covered by a :data:`CAPABILITIES` cell *or* listed here. A
#: newly-declared capability field with neither a cell nor an entry here fails the
#: guard, so it cannot slip past unproven. ``providers`` is gated separately (at
#: build, against the ``model:`` prefix), not by a cell.
DEFERRED_CAPABILITIES: dict[str, str] = {
    "cycles": "P02",
    "multimodal_in": "P25",
    "live_bidi": "P10",
    "providers": "gated at build (model prefix), not by a matrix cell",
}


#: A cell result: which engine×capability was checked, whether it was a positive
#: ``prove`` cell or a negative ``reject`` cell, whether it passed, and a human-
#: readable detail (the failure reason, or a one-line summary on success).
@dataclass(frozen=True)
class CellResult:
    """The outcome of one conformance cell in the grid.

    ``engine`` and ``capability`` name the cell; ``kind`` is ``"prove"`` (the engine
    declares the capability, so it must genuinely work) or ``"reject"`` (the engine
    does not declare it, so requesting it must fail fast). ``passed`` is whether the
    cell held; ``detail`` is a short human-readable explanation (the assertion/error
    message on failure, or a summary on success) for the ``agentship verify`` report.
    """

    engine: str
    capability: str
    kind: Literal["prove", "reject"]
    passed: bool
    detail: str


def covered_capability_names() -> frozenset[str]:
    """Return the set of capability field names that a :data:`CAPABILITIES` cell covers."""
    return frozenset(capability.name for capability in CAPABILITIES)


def request_capability(capability: Capability, engine_name: str) -> RunnableAgent:
    """Build an agent from a spec that requests ``capability`` on ``engine_name``.

    Used by the negative rejection cell: on an engine that does not declare the
    capability, :func:`~agentship.runtime.build_agent` raises
    :class:`~agentship.errors.CapabilityError` (fail-fast). Only valid for a
    capability that carries a ``request_spec``.
    """
    assert capability.request_spec is not None
    return build_agent(capability.request_spec(engine_name))


def _engine_capabilities(engine_name: str) -> EngineCapabilities:
    """Return the declared capabilities of the registered engine ``engine_name``."""
    engine_cls = ENGINES.get(engine_name)
    assert engine_cls is not None, f"engine {engine_name!r} is not registered"
    return engine_cls.capabilities


async def run_capability_grid(
    engine_names: list[str] | None = None,
    *,
    offline: Callable[[str], AbstractContextManager] | None = None,
) -> list[CellResult]:
    """Run one conformance cell per (engine, capability) and return the results.

    For each engine (default: every registered engine) and each capability in
    :data:`CAPABILITIES`, exactly one cell runs:

    * **declared → positive ``prove`` cell.** The engine claims the capability, so
      the cell builds a real agent (from the capability's ``request_spec`` when it
      has one, else the ``prove`` cell builds its own) and runs ``prove``. The build
      + prove run inside ``offline(engine_name)`` when an ``offline`` provider is
      supplied (so a model-backed engine never touches the network), else as-is.
    * **not declared → negative ``reject`` cell.** The engine does not claim it, so
      building a spec that *requests* it must raise
      :class:`~agentship.errors.CapabilityError` — but only when a ``request_spec``
      exists. A capability with no ``request_spec`` and not declared contributes no
      cell (nothing to reject through).

    Every cell is caught: an assertion or unexpected exception is recorded as a
    failing :class:`CellResult`, never raised out of the grid, so the caller (the
    ``agentship verify`` CLI) always gets a complete report.

    ``offline`` maps an engine name to a context manager entered around a positive
    cell (the vendor offline harness, e.g. ``agentship_langgraph.testing.offline``);
    it is not applied to negative cells, which fail at build before any run.
    """
    names = engine_names if engine_names is not None else ENGINES.names()
    results: list[CellResult] = []
    for engine_name in names:
        caps = _engine_capabilities(engine_name)
        for capability in CAPABILITIES:
            cell = await _run_cell(engine_name, caps, capability, offline)
            if cell is not None:
                results.append(cell)
    return results


async def _run_cell(
    engine_name: str,
    caps: EngineCapabilities,
    capability: Capability,
    offline: Callable[[str], AbstractContextManager] | None,
) -> CellResult | None:
    """Run one grid cell for ``(engine_name, capability)`` and return its result.

    Returns a ``CellResult`` for a positive or negative cell; returns ``None`` only
    when the engine neither declares the capability nor has a ``request_spec`` (no
    cell to run). Never raises: any failure is captured into the result's ``detail``.
    """
    if capability.declared(caps):
        return await _run_prove_cell(engine_name, capability, offline)
    if capability.request_spec is not None:
        return _run_reject_cell(engine_name, capability)
    return None


async def _run_prove_cell(
    engine_name: str,
    capability: Capability,
    offline: Callable[[str], AbstractContextManager] | None,
) -> CellResult:
    """Run the positive ``prove`` cell for a declared capability, capturing any failure."""
    ctx = offline(engine_name) if offline is not None else nullcontext()
    try:
        with ctx:
            agent = (
                build_agent(capability.request_spec(engine_name))
                if capability.request_spec is not None
                else None
            )
            assert capability.prove is not None, (
                f"capability {capability.name!r} is declared by {engine_name!r} but has no "
                f"prove cell"
            )
            await capability.prove(agent)
    except Exception as exc:  # noqa: BLE001 - the grid records every failure, never raises
        detail = str(exc) or type(exc).__name__
        return CellResult(engine_name, capability.name, "prove", False, detail)
    return CellResult(engine_name, capability.name, "prove", True, "declared capability proved")


def _run_reject_cell(engine_name: str, capability: Capability) -> CellResult:
    """Run the negative ``reject`` cell: requesting an undeclared capability must fail fast."""
    try:
        request_capability(capability, engine_name)
    except CapabilityError as exc:
        return CellResult(engine_name, capability.name, "reject", True, str(exc))
    except Exception as exc:  # noqa: BLE001 - a non-CapabilityError is a failure, recorded
        return CellResult(
            engine_name,
            capability.name,
            "reject",
            False,
            f"expected CapabilityError, got {type(exc).__name__}: {exc}",
        )
    return CellResult(
        engine_name,
        capability.name,
        "reject",
        False,
        "requesting an undeclared capability did not raise CapabilityError (silent degrade)",
    )


__all__ = [
    "CAPABILITIES",
    "Capability",
    "CellResult",
    "DEFERRED_CAPABILITIES",
    "covered_capability_names",
    "request_capability",
    "run_capability_grid",
]
