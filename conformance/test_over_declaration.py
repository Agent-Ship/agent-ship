"""Meta-test: prove the matrix actually catches a liar (an over-declaring engine).

The conformance matrix's whole value rests on one claim: *a capability that is
declared but not implemented makes its cell fail.* This test proves that claim by
registering a throwaway engine that lies — it declares ``structured_output="native"``
but implements no structured output at all — and asserting that running that
engine's ``structured_output`` cell **fails**. If the matrix could not catch this,
the "declare, don't fake" guarantee would be hollow.

The liar engine is registered only inside the ``registry_snapshot`` guard (autouse
via the fixture argument), so it is fully removed afterwards and never leaks into
the real registry or the parametrized matrix.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from agentship.engines.base import ENGINES, Engine, EngineCapabilities, Event, Result
from agentship.runtime import build_agent

from conformance.capabilities import CAPABILITIES
from conformance.engines import offline


class _LiarEngine(Engine):
    """A deliberately dishonest engine: declares native structured output, delivers none.

    It can stream (so the matrix builds and drives it), but it declares
    ``structured_output="native"`` while its ``run``/``stream`` produce a plain
    string — no validated structured target anywhere. Its ``structured_output``
    conformance cell must therefore fail, which is exactly what this meta-test asserts.
    """

    name = "liar"
    capabilities = EngineCapabilities(streaming=True, structured_output="native")

    def build(self, spec: Any) -> Any:
        """Nothing to compile — hand the spec straight back like the echo engine."""
        return spec

    async def run(self, compiled: Any, text: str, ctx: Any) -> Result:
        """Return a plain string — no structured validation, contradicting the declaration."""
        return Result(output=f"liar: {text}")

    async def stream(self, compiled: Any, text: str, ctx: Any) -> AsyncIterator[Event]:
        """Yield one content chunk then a terminal ``done`` (so streaming itself is honest)."""
        yield Event(type="content", data=f"liar: {text}")
        yield Event(type="done")


def _structured_output_capability():
    """Return the ``structured_output`` descriptor from the shared catalogue."""
    for capability in CAPABILITIES:
        if capability.name == "structured_output":
            return capability
    raise AssertionError("structured_output capability missing from the catalogue")


async def test_over_declaration_makes_the_cell_fail(registry_snapshot: None) -> None:
    """The liar's structured_output cell fails — proving the matrix catches over-claims.

    Registers the liar (auto-removed by ``registry_snapshot``), then runs the exact
    positive cell the matrix would run for ``structured_output`` on it. Because the
    engine declares the capability but implements nothing, the cell must raise —
    demonstrating that declaring-without-building is caught as a failure.
    """
    ENGINES.register(_LiarEngine.name, _LiarEngine)
    capability = _structured_output_capability()

    # Sanity: the liar really does declare the capability, so the matrix would take
    # the positive-cell branch (not the rejection branch) for it.
    assert capability.declared(_LiarEngine.capabilities)

    with offline(_LiarEngine.name):
        agent = build_agent(capability.request_spec(_LiarEngine.name))
        with pytest.raises(AssertionError):
            await capability.prove(agent)


def test_liar_engine_does_not_leak_after_the_meta_test() -> None:
    """After the guarded meta-test, the liar is gone — the registry is clean again.

    Runs after ``test_over_declaration_makes_the_cell_fail`` (which registered the
    liar under the snapshot guard) and asserts the throwaway engine did not survive
    into the shared registry, so the parametrized matrix never sees it.
    """
    assert "liar" not in ENGINES.names()
