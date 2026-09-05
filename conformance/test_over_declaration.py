"""Meta-tests: prove the matrix catches a liar, and that no capability slips past.

The conformance matrix's whole value rests on two claims:

1. *A declared-but-not-implemented capability makes its cell fail.* We prove this
   **behaviourally** for every positive prove-cell: one throwaway "liar" engine per
   capability declares the capability but does not deliver it, and running it through
   the **real** prove cell must fail — a real behavioural lie, caught by the real cell,
   not a hard-coded raise. The liars are:

   - ``_LiarEngine`` declares ``streaming=True`` but its ``stream()`` never emits the
     terminal ``done`` event the streaming contract requires;
   - ``_StructuredOutputLiar`` declares ``structured_output="native"`` but its ``run()``
     returns a plain ``str`` instead of a validated pydantic model;
   - ``_MultiAgentLiar`` declares ``multi_agent=True`` but its built agent exposes no
     coordinated ``members`` structure;
   - ``_DurabilityLiar`` declares ``durability="checkpoint"`` but its built agent exposes
     no ``checkpointer``/resume seam.

   Each is non-vacuous: had the liar actually delivered the capability, its cell would
   pass and the ``pytest.raises`` guarding the test would itself fail.

2. *No capability field can be declared without being proven or explicitly deferred.*
   The coverage guard walks every ``EngineCapabilities`` field and asserts it is
   either covered by a ``CAPABILITIES`` cell or listed in the
   ``DEFERRED_CAPABILITIES`` allowlist. A newly-added capability with neither cannot
   slip past unproven.

The liar engine is registered only inside the ``registry_snapshot`` guard, so it is
fully removed afterwards and never leaks into the real registry or the matrix.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from agentship.conformance import (
    CAPABILITIES,
    DEFERRED_CAPABILITIES,
    covered_capability_names,
)
from agentship.engines.base import (
    ENGINES,
    Engine,
    EngineCapabilities,
    Event,
    Result,
)
from agentship.runtime import build_agent
from agentship_langgraph.testing import offline


class _LiarEngine(Engine):
    """A behaviourally dishonest engine: declares ``streaming`` but breaks the contract.

    It *declares* ``streaming=True``, so the matrix takes the positive-cell branch and
    drives its ``stream()``. But its ``stream()`` yields content and then **stops
    without a terminal ``done`` event** — violating the streaming contract every
    honest engine must honour. The real ``_prove_streaming`` cell therefore fails on
    this engine's actual behaviour, which is exactly what the meta-test asserts. This
    is a behavioural lie caught by a real cell, not a hard-coded raise.
    """

    name = "liar"
    capabilities = EngineCapabilities(streaming=True)

    def build(self, spec: Any) -> Any:
        """Nothing to compile — hand the spec straight back like the echo engine."""
        return spec

    async def run(self, compiled: Any, text: str, ctx: Any) -> Result:
        """Return a plain string — echo-like; unused by the streaming cell."""
        return Result(output=f"liar: {text}")

    async def stream(self, compiled: Any, text: str, ctx: Any) -> AsyncIterator[Event]:
        """Yield content but **never** a terminal ``done`` — breaking the contract."""
        yield Event(type="content", data=f"liar: {text}")
        # No `done` event: the stream ends here, violating the streaming contract.


class _StructuredOutputLiar(Engine):
    """Declares native ``structured_output`` but its run returns a plain ``str``.

    The gate passes at build time (the capability is declared), so the matrix takes
    the positive branch and drives ``run()``. But ``run()`` returns a plain string, not
    a validated pydantic model — exactly the over-claim ``_prove_structured_output``
    exists to catch. If this engine actually returned a validated model, that cell would
    pass and the ``pytest.raises`` guarding the test would itself fail; the raise proves
    the cell is non-vacuous (it catches a real lie, not a hard-coded one).
    """

    name = "structured_output_liar"
    capabilities = EngineCapabilities(structured_output="native")

    def build(self, spec: Any) -> Any:
        """Nothing to compile — hand the spec straight back like the echo engine."""
        return spec

    async def run(self, compiled: Any, text: str, ctx: Any) -> Result:
        """Return a plain string, breaking the structured-output promise."""
        return Result(output=f"liar: {text}")


class _MultiAgentLiar(Engine):
    """Declares ``multi_agent=True`` but its built agent exposes no coordinated members.

    The request spec declares members and the gate passes (the capability is declared),
    so the matrix drives the positive cell, which inspects the compiled artifact. This
    engine's ``build`` returns a bare object with no ``members`` structure — the over-claim
    ``_prove_multi_agent`` catches. Had ``build`` surfaced a real coordinated team, the
    cell would pass and the ``pytest.raises`` would fail; the raise proves the cell is
    non-vacuous.
    """

    name = "multi_agent_liar"
    capabilities = EngineCapabilities(multi_agent=True)

    def build(self, spec: Any) -> Any:
        """Return a bare object with no ``members`` — nothing coordinated to inspect."""
        return object()

    async def run(self, compiled: Any, text: str, ctx: Any) -> Result:
        """Echo-like; unused by the multi-agent cell (which inspects the artifact)."""
        return Result(output=f"liar: {text}")


class _DurabilityLiar(Engine):
    """Declares ``durability="checkpoint"`` but never overrides :meth:`Engine.resume`.

    The request spec asks for checkpoint durability and the gate passes (the capability
    is declared), so the matrix drives the positive cell, which checks the engine really
    implements the resume seam. This engine inherits the base ``resume`` (which raises) —
    the over-claim ``_prove_durability`` catches. Had it overridden ``resume``, the cell
    would pass and the ``pytest.raises`` would fail; the raise proves the cell is
    non-vacuous.
    """

    name = "durability_liar"
    capabilities = EngineCapabilities(durability="checkpoint")

    def build(self, spec: Any) -> Any:
        """Return a bare object — the liar builds fine; the lie is the unimplemented resume."""
        return object()

    async def run(self, compiled: Any, text: str, ctx: Any) -> Result:
        """Echo-like; unused by the durability cell (which inspects the engine's resume)."""
        return Result(output=f"liar: {text}")


class _ToolCallingLiar(Engine):
    """Declares ``tool_calling=True`` but its built agent binds no tools.

    The request spec declares ``tools: [calculator]`` and the gate passes (the capability is
    declared), so the matrix drives the positive cell, which inspects the built artifact. This
    engine's ``build`` returns a bare object with no ``bound_tools`` — the over-claim
    ``_prove_tool_calling`` catches. Had ``build`` bound the tool, the cell would pass and the
    ``pytest.raises`` would fail; the raise proves the cell is non-vacuous.
    """

    name = "tool_calling_liar"
    capabilities = EngineCapabilities(tool_calling=True)

    def build(self, spec: Any) -> Any:
        """Return a bare object with no ``bound_tools`` — nothing executable to inspect."""
        return object()

    async def run(self, compiled: Any, text: str, ctx: Any) -> Result:
        """Echo-like; unused by the tool-calling cell (which inspects the artifact)."""
        return Result(output=f"liar: {text}")


def _capability(name: str):
    """Return the descriptor named ``name`` from the shared catalogue."""
    for capability in CAPABILITIES:
        if capability.name == name:
            return capability
    raise AssertionError(f"{name} capability missing from the catalogue")


def _streaming_capability():
    """Return the ``streaming`` descriptor from the shared catalogue."""
    return _capability("streaming")


async def test_behavioural_liar_makes_the_streaming_cell_fail(registry_snapshot: None) -> None:
    """A liar that declares streaming but omits ``done`` fails the real streaming cell.

    Registers the liar (auto-removed by ``registry_snapshot``), then runs the exact
    positive cell the matrix would run for ``streaming`` on it. Because the engine
    declares the capability but its behaviour violates the contract (no terminal
    ``done``), the real ``_prove_streaming`` cell must raise — proving the matrix
    catches a behavioural over-claim, not just a hard-coded one.
    """
    ENGINES.register(_LiarEngine.name, _LiarEngine)
    capability = _streaming_capability()

    # Sanity: the liar really declares streaming, so the matrix takes the positive
    # branch (drives stream) rather than the rejection branch.
    assert capability.declared(_LiarEngine.capabilities)
    assert capability.prove is not None

    with offline(_LiarEngine.name):
        agent = build_agent(capability.request_spec(_LiarEngine.name))
        with pytest.raises(AssertionError, match="done"):
            await capability.prove(agent)


async def test_behavioural_liar_makes_the_structured_output_cell_fail(
    registry_snapshot: None,
) -> None:
    """A liar that declares structured_output but returns a plain ``str`` fails the cell.

    Registers the liar (auto-removed by ``registry_snapshot``), then runs the exact
    positive cell the matrix would run for ``structured_output``. Because the engine
    declares the capability (so the gate passes and the positive branch is taken) but
    its ``run`` returns a plain string instead of a validated pydantic model, the real
    ``_prove_structured_output`` cell must raise. Non-vacuity check: if the liar had
    returned a validated model, the cell would pass and this ``pytest.raises`` would
    fail — so a red here proves the cell catches a real behavioural lie.
    """
    ENGINES.register(_StructuredOutputLiar.name, _StructuredOutputLiar)
    capability = _capability("structured_output")

    # Sanity: the liar really declares the capability, so the matrix takes the positive
    # branch (drives run) rather than the rejection branch.
    assert capability.declared(_StructuredOutputLiar.capabilities)
    assert capability.prove is not None

    with offline(_StructuredOutputLiar.name):
        agent = build_agent(capability.request_spec(_StructuredOutputLiar.name))
        with pytest.raises(AssertionError, match="structured_output"):
            await capability.prove(agent)


async def test_behavioural_liar_makes_the_multi_agent_cell_fail(
    registry_snapshot: None,
) -> None:
    """A liar that declares multi_agent but exposes no coordinated members fails the cell.

    Registers the liar (auto-removed by ``registry_snapshot``), then runs the exact
    positive cell the matrix would run for ``multi_agent``. The engine declares the
    capability (so the gate passes and the positive branch is taken) but its ``build``
    returns an artifact with no ``members`` structure, so the real ``_prove_multi_agent``
    cell must raise. Non-vacuity check: if ``build`` had surfaced a real coordinated team,
    the cell would pass and this ``pytest.raises`` would fail — so a red here proves the
    cell catches a real over-claim.
    """
    ENGINES.register(_MultiAgentLiar.name, _MultiAgentLiar)
    capability = _capability("multi_agent")

    assert capability.declared(_MultiAgentLiar.capabilities)
    assert capability.prove is not None

    with offline(_MultiAgentLiar.name):
        agent = build_agent(capability.request_spec(_MultiAgentLiar.name))
        with pytest.raises(AssertionError, match="members"):
            await capability.prove(agent)


async def test_behavioural_liar_makes_the_durability_cell_fail(
    registry_snapshot: None,
) -> None:
    """A liar that declares durability but exposes no resume seam fails the cell.

    Registers the liar (auto-removed by ``registry_snapshot``), then runs the exact
    positive cell the matrix would run for ``durability``. The engine declares the
    capability (so the gate passes and the positive branch is taken) but its ``build``
    returns an artifact with no ``checkpointer`` seam, so the real ``_prove_durability``
    cell must raise. Non-vacuity check: if ``build`` had exposed a real checkpointer, the
    cell would pass and this ``pytest.raises`` would fail — so a red here proves the cell
    catches a real over-claim.
    """
    ENGINES.register(_DurabilityLiar.name, _DurabilityLiar)
    capability = _capability("durability")

    assert capability.declared(_DurabilityLiar.capabilities)
    assert capability.prove is not None

    with offline(_DurabilityLiar.name):
        agent = build_agent(capability.request_spec(_DurabilityLiar.name))
        with pytest.raises(AssertionError, match="resume"):
            await capability.prove(agent)


async def test_behavioural_liar_makes_the_tool_calling_cell_fail(
    registry_snapshot: None,
) -> None:
    """A liar that declares tool_calling but binds no tools fails the cell.

    Registers the liar (auto-removed by ``registry_snapshot``), then runs the exact positive cell
    the matrix would run for ``tool_calling``. The engine declares the capability (gate passes,
    positive branch taken) but its ``build`` binds no tools, so the real ``_prove_tool_calling``
    cell must raise. Non-vacuity: if ``build`` had bound the tool, the cell would pass and this
    ``pytest.raises`` would fail — a red here proves the cell catches a real over-claim.
    """
    ENGINES.register(_ToolCallingLiar.name, _ToolCallingLiar)
    capability = _capability("tool_calling")

    assert capability.declared(_ToolCallingLiar.capabilities)
    assert capability.prove is not None

    with offline(_ToolCallingLiar.name):
        agent = build_agent(capability.request_spec(_ToolCallingLiar.name))
        with pytest.raises(AssertionError, match="bound no tools"):
            await capability.prove(agent)


def test_liar_engines_do_not_leak_after_the_meta_tests() -> None:
    """After the guarded meta-tests, every throwaway liar is gone — the registry is clean.

    Runs after the behavioural-liar tests (each of which registered its liar under the
    snapshot guard) and asserts none of the throwaway engines survived into the shared
    registry, so the parametrized matrix never sees them.
    """
    names = ENGINES.names()
    for liar_name in (
        "liar",
        "structured_output_liar",
        "multi_agent_liar",
        "durability_liar",
        "tool_calling_liar",
    ):
        assert liar_name not in names, f"{liar_name!r} leaked into the shared registry"


def test_every_capability_field_is_covered_or_explicitly_deferred() -> None:
    """Coverage guard: no ``EngineCapabilities`` field may be declared unproven.

    Walks every field of :class:`~agentship.engines.base.EngineCapabilities` and
    asserts each is EITHER covered by a :data:`CAPABILITIES` cell OR listed in the
    :data:`DEFERRED_CAPABILITIES` allowlist (mapping field → the phase it lands in).
    Adding a new capability field therefore forces a deliberate choice — write a cell
    or add an allowlist entry — so a newly-declared capability can never slip past
    the matrix unproven.
    """
    fields = set(EngineCapabilities.model_fields)
    covered = covered_capability_names()
    deferred = set(DEFERRED_CAPABILITIES)

    uncovered = fields - covered - deferred
    assert not uncovered, (
        f"capability field(s) {sorted(uncovered)} have neither a conformance cell nor a "
        f"DEFERRED_CAPABILITIES entry — add a cell to CAPABILITIES or defer it explicitly "
        f"(field -> phase) so it cannot be declared unproven"
    )

    # A field must not be double-listed (both proved and deferred) — that would hide a
    # stale allowlist entry once a real cell lands.
    overlap = covered & deferred
    assert not overlap, (
        f"capability field(s) {sorted(overlap)} are both proved by a cell and listed in "
        f"DEFERRED_CAPABILITIES — remove the stale allowlist entry now that a real cell exists"
    )

    # Every allowlist key must be a real capability field (guard against typos/rot).
    stale = deferred - fields
    assert not stale, (
        f"DEFERRED_CAPABILITIES lists {sorted(stale)}, which are not EngineCapabilities "
        f"fields — remove the stale entries"
    )
