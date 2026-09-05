"""The engine×capability conformance matrix — the "declare, don't fake" backstop.

For every registered engine and every capability in the catalogue, exactly one cell
runs (DESIGN §11):

* **declared → positive cell.** The engine claims the capability, so the matrix
  *proves* it works: it builds a real agent on that engine (offline, via a fake
  model where one is needed) and runs the capability's ``prove`` function. A
  declared capability whose proof fails is a **build failure** — that is the point.
* **not declared → negative cell.** The engine does not claim the capability, so a
  spec that *requests* it must be rejected at build time with
  :class:`~agentship.errors.CapabilityError` (fail-fast, never a silent degrade).
  Skipped only for capabilities that have no ``AgentSpec`` field the gate keys on
  yet (nothing to reject through).

Adding an engine (register it) or a capability (append to
:data:`conformance.capabilities.CAPABILITIES`) extends the matrix with no edit here.
"""

from __future__ import annotations

import pytest
from agentship.conformance import CAPABILITIES, Capability, request_capability
from agentship.engines.base import ENGINES
from agentship.errors import CapabilityError
from agentship.runtime import build_agent
from agentship_langgraph.testing import offline

from conformance.conftest import REGISTERED_ENGINE_NAMES


def _engine_capabilities(engine_name: str):
    """Return the declared capabilities of the registered engine ``engine_name``."""
    engine_cls = ENGINES.get(engine_name)
    assert engine_cls is not None, f"engine {engine_name!r} vanished from the registry"
    return engine_cls.capabilities


def _build_offline_agent(engine_name: str, capability: Capability):
    """Build an agent on ``engine_name`` that requests ``capability`` (offline), or ``None``.

    Uses the capability's own ``request_spec`` so the built agent genuinely has the
    capability's spec field set (e.g. ``streaming: true``), then the positive cell
    proves the capability against that agent. The engine's offline harness is
    entered so a model-backed engine never touches the network. When the capability
    has **no** ``request_spec`` (no gate-checked spec field expresses it — e.g.
    ``hitl``, whose interrupt contract is expressed by a custom-authored graph), this
    returns ``None`` and the capability's ``prove`` cell builds its own agent.
    """
    if capability.request_spec is None:
        return None
    return build_agent(capability.request_spec(engine_name))


#: The full grid, materialised as (engine_name, capability) pairs so each cell is a
#: separately-reported test with a readable id.
_GRID = [
    (engine_name, capability)
    for engine_name in REGISTERED_ENGINE_NAMES
    for capability in CAPABILITIES
]


@pytest.mark.parametrize(
    ("engine_name", "capability"),
    _GRID,
    ids=[f"{engine_name}-{capability.name}" for engine_name, capability in _GRID],
)
async def test_conformance_cell(engine_name: str, capability: Capability) -> None:
    """One cell of the matrix: prove a declared capability, or prove one is rejected.

    Declared → run the positive ``prove`` cell (must pass). Not declared → building a
    spec that requests the capability must raise :class:`CapabilityError` (fail-fast).
    Capabilities with no gate-checked spec field skip the negative branch.
    """
    caps = _engine_capabilities(engine_name)

    if capability.declared(caps):
        # Positive cell: the engine claims it, so it must genuinely work.
        assert capability.prove is not None, (
            f"capability {capability.name!r} has no `prove` cell but is declared by "
            f"{engine_name!r} — every declarable capability needs a positive proof"
        )
        with offline(engine_name):
            agent = _build_offline_agent(engine_name, capability)
            await capability.prove(agent)
    else:
        # Negative cell: requesting an undeclared capability must fail fast at build.
        if capability.request_spec is None:
            pytest.skip(
                f"capability {capability.name!r} has no spec field the gate keys on yet "
                f"— nothing to reject through on {engine_name!r}"
            )
        with pytest.raises(CapabilityError):
            request_capability(capability, engine_name)


def test_matrix_covers_at_least_the_known_engines() -> None:
    """Guard: the matrix really runs over echo and langgraph (not an empty grid).

    A silently-empty parametrization would make the whole suite vacuously green, so
    assert the two engines we ship are present. New engines only add rows.
    """
    assert "echo" in REGISTERED_ENGINE_NAMES
    assert "langgraph" in REGISTERED_ENGINE_NAMES
    assert _GRID, "the conformance grid is empty — no cells would run"


def test_echo_and_langgraph_have_a_real_streaming_cell() -> None:
    """Guard: both shipped engines declare streaming, so both get the positive cell.

    Documents (and enforces) the requirement that echo(streaming) and
    langgraph(streaming) are real proved cells, not rejection cells.
    """
    for engine_name in ("echo", "langgraph"):
        caps = _engine_capabilities(engine_name)
        assert caps.streaming, f"{engine_name!r} must declare streaming for a real cell"
