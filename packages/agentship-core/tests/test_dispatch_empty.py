"""Dispatching nothing returns nothing — it must not crash the turn.

``dispatch("single", [], ...)`` did ``refs[0]`` and raised IndexError, taking down the whole
supervisor run. It is reachable in normal use: the dispatch node computes its specialists from
the retry helper on a second pass, and when nothing is retryable that list is empty. A real
two-turn conversation with the research team hit it.

Nothing to dispatch is a legitimate state — the resolver simply has no results to merge — so
every strategy must return an empty list rather than raise.
"""

from __future__ import annotations

import pytest
from agentship.context import Caller, RunContext, RunMode
from agentship.errors import CapabilityError
from agentship.primitives.dispatch import dispatch


def _ctx() -> RunContext:
    """A minimal context for driving a dispatch."""
    return RunContext(
        caller=Caller(user_id="u"),
        session_id="s",
        run_id="r",
        agent_name="sup",
        mode=RunMode.INVOKE,
    )


@pytest.mark.parametrize("strategy", ["single", "parallel", "sequential"])
async def test_dispatching_no_specialists_returns_no_results(strategy: str):
    """Every strategy tolerates an empty list; none of them raise."""
    assert await dispatch(strategy, [], "anything", _ctx()) == []


async def test_an_unknown_strategy_still_fails_loudly():
    """Tolerating an empty list must not turn a real misconfiguration into silence."""
    with pytest.raises(CapabilityError):
        await dispatch("nonsense", [], "anything", _ctx())
