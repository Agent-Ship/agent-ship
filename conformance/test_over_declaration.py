"""Meta-tests: prove the matrix catches a liar, and that no capability slips past.

The conformance matrix's whole value rests on two claims:

1. *A declared-but-not-implemented capability makes its cell fail.* We prove this
   **behaviourally**: a ``_LiarEngine`` declares ``streaming=True`` but its
   ``stream()`` never emits the terminal ``done`` event the streaming contract
   requires. Running that engine through the **real** ``_prove_streaming`` cell must
   fail — a real behavioural lie, caught by the real cell, not a hard-coded raise.

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
from agentship.engines.base import (
    ENGINES,
    Engine,
    EngineCapabilities,
    Event,
    Result,
)
from agentship.runtime import build_agent

from conformance.capabilities import (
    CAPABILITIES,
    DEFERRED_CAPABILITIES,
    covered_capability_names,
)
from conformance.engines import offline


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


def _streaming_capability():
    """Return the ``streaming`` descriptor from the shared catalogue."""
    for capability in CAPABILITIES:
        if capability.name == "streaming":
            return capability
    raise AssertionError("streaming capability missing from the catalogue")


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


def test_liar_engine_does_not_leak_after_the_meta_test() -> None:
    """After the guarded meta-test, the liar is gone — the registry is clean again.

    Runs after ``test_behavioural_liar_makes_the_streaming_cell_fail`` (which
    registered the liar under the snapshot guard) and asserts the throwaway engine
    did not survive into the shared registry, so the parametrized matrix never sees it.
    """
    assert "liar" not in ENGINES.names()


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
