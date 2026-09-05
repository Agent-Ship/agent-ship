"""Engine-parametrized conformance cells (Phase 01 · T7).

These cells are the cross-engine contract every adapter must satisfy. They are
written **parametrized over every registered engine** (``echo`` + ``langgraph``
today) so Phase 06's second engine reuses them unchanged — a cell runs for an
engine iff that engine declares the capability it exercises. Each cell is offline:
a model-backed engine gets its model seam swapped for a deterministic fake via the
:func:`conformance.engines.offline` harness, so no cell touches the network.

The cells:

* ``capability_fail_fast`` — requesting a capability the engine does not declare
  raises :class:`~agentship.errors.CapabilityError` at *build* (fail-fast, never a
  silent degrade). Runs per engine, over every gate-checked capability the engine
  does not declare.
* ``run_stream_parity`` — for a trivial turn, ``run()``'s output equals the
  reassembled ``content`` of ``stream()``. Runs per engine that declares streaming.
* ``context_isolation`` — many concurrent ``run``s on one compiled agent never bleed
  ``current_run`` across requests; each turn sees only its own caller/session. Runs
  per engine (the construct-once / read-per-request rule is a cross-engine seam).
* ``router_purity`` — the engine consumes ``RunContext.routed_model`` and never routes
  itself (never calls ``ModelRouter.pick``). Runs per engine; the model-resolution
  half is asserted for engines whose adapter resolves a model from the context.
* ``durable_resume_after_kill`` — **deferred to Phase 02** (no checkpointer yet).
  Present as an explicitly xfail cell so it is visible and un-skips when P02 lands —
  not a fake pass.
"""

from __future__ import annotations

import asyncio

import pytest
from agentship.conformance import CAPABILITIES
from agentship.context import current_run
from agentship.engines.base import ENGINES
from agentship.errors import CapabilityError
from agentship.primitives.model_router import DefaultModelRouter
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from agentship_langgraph.testing import offline

from conformance.conftest import REGISTERED_ENGINE_NAMES


def _capabilities(engine_name: str):
    """Return the declared capabilities of the registered engine ``engine_name``."""
    engine_cls = ENGINES.get(engine_name)
    assert engine_cls is not None, f"engine {engine_name!r} vanished from the registry"
    return engine_cls.capabilities


def _trivial_agent(engine_name: str):
    """Build the simplest runnable agent on ``engine_name`` (caller enters ``offline``).

    A minimal single-model spec: enough for ``run``/``stream`` to produce output on
    any engine. The caller is responsible for entering the engine's offline harness
    so a model-backed engine never reaches the network.
    """
    return build_agent(
        AgentSpec(name="cell", engine=engine_name, model="openai/gpt-4o-mini", prompt="p")
    )


# --------------------------------------------------------------------------- #
# Cell 1 — capability_fail_fast (per engine × each undeclared, gated capability)
# --------------------------------------------------------------------------- #

#: (engine_name, capability) pairs where the engine does NOT declare the capability
#: and the capability has a spec field the gate keys on — so requesting it must be
#: rejected at build. Materialised at import so each is a separately-reported cell.
_FAIL_FAST_GRID = [
    (engine_name, capability)
    for engine_name in REGISTERED_ENGINE_NAMES
    for capability in CAPABILITIES
    if capability.request_spec is not None and not capability.declared(_capabilities(engine_name))
]


@pytest.mark.parametrize(
    ("engine_name", "capability"),
    _FAIL_FAST_GRID,
    ids=[f"{engine}-{cap.name}" for engine, cap in _FAIL_FAST_GRID],
)
def test_capability_fail_fast(engine_name: str, capability) -> None:
    """Requesting an undeclared capability raises ``CapabilityError`` at build.

    Non-vacuous: the capability is one the engine genuinely does not declare, so a
    passing build (a silent degrade) would fail this assertion. The grid only holds
    undeclared, gate-checked capabilities, so every parametrization is a real
    rejection.
    """
    with pytest.raises(CapabilityError):
        build_agent(capability.request_spec(engine_name))


def test_capability_fail_fast_grid_is_non_empty() -> None:
    """Guard: the fail-fast grid actually has cells (else the cell is vacuous).

    Echo declares only ``streaming``, so it must contribute several rejection cells
    (structured_output / multi_agent / durability). An empty grid would mean the
    cell never runs — assert it is populated.
    """
    assert _FAIL_FAST_GRID, "capability_fail_fast has no cells — the gate is untested"
    assert any(engine == "echo" for engine, _ in _FAIL_FAST_GRID)


# --------------------------------------------------------------------------- #
# Cell 2 — run_stream_parity (per engine that declares streaming)
# --------------------------------------------------------------------------- #

_STREAMING_ENGINES = [
    engine_name for engine_name in REGISTERED_ENGINE_NAMES if _capabilities(engine_name).streaming
]


@pytest.mark.parametrize("engine_name", _STREAMING_ENGINES)
async def test_run_stream_parity(engine_name: str) -> None:
    """``run()`` output equals the reassembled ``content`` of ``stream()``.

    Drives the same trivial turn twice — once via ``run``, once via ``stream`` — and
    asserts the run's output equals the concatenation of the stream's ``content``
    events. Every streaming engine must keep the two paths consistent (the streamed
    tokens reassemble to exactly the non-streamed answer). Offline via the fake model
    harness. Non-vacuous: a divergent engine (streaming a different/incomplete answer)
    fails the equality.
    """
    with offline(engine_name):
        agent = _trivial_agent(engine_name)
        result = await agent.run("hello", user_id="conformance")
        chunks = [
            event.data
            async for event in agent.stream("hello", user_id="conformance")
            if event.type == "content" and event.data is not None
        ]
    reassembled = "".join(chunks)
    assert reassembled, f"engine {engine_name!r} streamed no content to reassemble"
    assert reassembled == result.output, (
        f"engine {engine_name!r} run/stream parity broken: run returned "
        f"{result.output!r} but the stream reassembled to {reassembled!r}"
    )


def test_run_stream_parity_covers_shipped_streaming_engines() -> None:
    """Guard: both shipped engines get a real parity cell (not an empty parametrization)."""
    assert "echo" in _STREAMING_ENGINES
    assert "langgraph" in _STREAMING_ENGINES


# --------------------------------------------------------------------------- #
# Cell 3 — context_isolation (per engine — the construct-once / read-per-request rule)
# --------------------------------------------------------------------------- #

#: How many concurrent turns cell 3 fans out. Enough to shake out any per-request
#: state accidentally shared on the compiled agent or leaked through the contextvar.
_CONCURRENCY = 20


@pytest.mark.parametrize("engine_name", REGISTERED_ENGINE_NAMES)
async def test_context_isolation(engine_name: str) -> None:
    """``_CONCURRENCY`` concurrent runs on one compiled agent never bleed ``current_run``.

    One agent is built once, then driven by many concurrent turns each with a
    distinct ``(user_id, session_id)``. Every turn reads back the active
    ``RunContext`` mid-run and must see *its own* caller/session — never a neighbour's
    — proving the compiled agent holds no per-request state and the contextvar is
    isolated per task (the C3 rule every adapter must satisfy). The ambient
    ``current_run`` is also asserted unset after the gather, proving no leak.

    Non-vacuous: an engine that stashed the last caller on the compiled agent, or a
    runtime that leaked the contextvar across tasks, would make some turn observe the
    wrong id and trip the assertion.
    """
    with offline(engine_name):
        agent = _trivial_agent(engine_name)

        async def one_turn(index: int) -> tuple[int, str, str]:
            """Run one turn and report the identity the run observed mid-flight."""
            user = f"user-{index}"
            session = f"session-{index}"
            seen: dict[str, str] = {}

            async def observing_hook(ctx) -> None:
                """Capture the caller/session the runtime published for this turn."""
                seen["user"] = ctx.caller.user_id
                seen["session"] = ctx.session_id

            await agent.run(
                f"turn {index}",
                user_id=user,
                session_id=session,
                middlewares=[_CapturingMiddleware(observing_hook)],
            )
            return index, seen["user"], seen["session"]

        results = await asyncio.gather(*(one_turn(i) for i in range(_CONCURRENCY)))

    for index, seen_user, seen_session in results:
        assert seen_user == f"user-{index}", (
            f"engine {engine_name!r} bled context: turn {index} saw caller {seen_user!r}"
        )
        assert seen_session == f"session-{index}", (
            f"engine {engine_name!r} bled context: turn {index} saw session {seen_session!r}"
        )
    # No leak into the ambient context after all turns complete.
    assert current_run.get(None) is None, (
        f"engine {engine_name!r} left current_run set after the run — a contextvar leak"
    )


class _CapturingMiddleware:
    """A minimal middleware that calls ``hook`` with the ctx on request.

    Used by ``context_isolation`` to read back the ``RunContext`` the runtime
    published for a turn *from inside* the pipeline (where the contextvar is active),
    without needing an engine-specific node. The other hooks are inert pass-throughs.
    """

    def __init__(self, hook) -> None:
        """Store the async ``hook(ctx)`` fired on each ``on_request``."""
        self._hook = hook

    async def on_request(self, ctx) -> None:
        """Fire the capture hook with this turn's context."""
        await self._hook(ctx)

    async def on_response(self, ctx, result):
        """Pass the result through unchanged."""
        return result

    async def on_error(self, ctx, exc) -> None:
        """Observe nothing on error (inert)."""


# --------------------------------------------------------------------------- #
# Cell 4 — router_purity (per engine — reads routed_model, never routes)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("engine_name", REGISTERED_ENGINE_NAMES)
async def test_router_purity(engine_name: str, monkeypatch) -> None:
    """The engine consumes ``RunContext.routed_model`` and never calls ``ModelRouter.pick``.

    Routing is decided upstream by the runtime's ``route`` step and stamped on
    ``RunContext.routed_model`` (DESIGN §13.5); the adapter must only *read* it. This
    cell spies on ``DefaultModelRouter.pick`` and drives a full build + run through
    the engine — but with the router's ``pick`` already spied so any adapter-side
    routing trips it. The runtime's own ``route`` step is the sanctioned caller, so
    the spy tolerates the single upstream call and only fails on an *extra*
    (adapter-side) one.

    Non-vacuous: an engine that re-derived its model by calling ``pick`` itself would
    push the call count past the one upstream route step and trip the assertion.

    This is the engine-parametrized *purity* half. The complementary langgraph
    read-half — that the adapter resolves the model from the *stamped* ``routed_model``
    value — lives in
    ``packages/agentship-langgraph/tests/test_router_purity.py`` (it is langgraph-
    specific because ``echo`` resolves no model); Phase 06's engine adds its own
    read-half test alongside this shared purity cell.
    """
    calls: list[str] = []
    original_pick = DefaultModelRouter.pick

    def spy_pick(self, spec, task=None):
        """Record every ``pick`` and delegate to the real deterministic router."""
        calls.append(spec.name)
        return original_pick(self, spec, task) if task is not None else original_pick(self, spec)

    monkeypatch.setattr(DefaultModelRouter, "pick", spy_pick)

    with offline(engine_name):
        agent = _trivial_agent(engine_name)
        await agent.run("hello", user_id="conformance")

    # The runtime's route step calls pick exactly once per run; the adapter must add
    # none. So at most one call is permitted — more means the adapter routed itself.
    assert len(calls) <= 1, (
        f"engine {engine_name!r} routed inside the adapter: ModelRouter.pick fired "
        f"{len(calls)} times (only the upstream route step may call it)"
    )


# --------------------------------------------------------------------------- #
# Cell 5 — durable_resume_after_kill (DEFERRED to Phase 02)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("engine_name", REGISTERED_ENGINE_NAMES)
@pytest.mark.xfail(reason="P11: durable resume", strict=False, run=False)
def test_durable_resume_after_kill(engine_name: str) -> None:
    """DEFERRED (P02: durability) — resume a killed run from its checkpoint.

    The durability pillar (checkpointer + ``ResumeToken`` round-trip) is built in
    Phase 02; no shipped engine checkpoints yet. This cell is registered as an
    explicit xfail (``run=False`` so it is reported without a fake pass) rather than
    silently omitted, so the conformance matrix *shows* the gap and this cell
    un-skips the moment P02 wires a real checkpointer. Filling it in: start a
    2-node graph, kill between nodes, ``resume(token)``, assert completion + replay
    idempotency.
    """
    pytest.fail("durable_resume_after_kill is not implemented until Phase 02")
