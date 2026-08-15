"""Live slice: the coordinator routes quick vs deep, and the deep path pauses then resumes.

This is the end-to-end counterpart to the offline ``test_deep_research`` /
``test_coordinated_routing`` tests: it runs the *real* model so the coordinator actually
classifies, and drives the durable
deep-research agent through a real pause→resume to a synthesized report. Live — it calls OpenAI (and
Brave if ``BRAVE_API_KEY`` is set, else the deep agent's search returns labelled stubs and the loop
still completes). Skips cleanly without a key.

Run it (with a key set)::

    set -a; source ../agentship/.env; set +a
    pytest tests/test_coordinated_research.py -q -s
"""

from __future__ import annotations

import sys
from pathlib import Path

from agentship.context import Caller, RunContext, RunMode

from agentship import build_agent
from conftest import requires_live_key

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from demos.coordinated_research import normalize_label  # noqa: E402

COORDINATOR = str(REPO_ROOT / "agents" / "coordinator.yaml")
DEEP = str(REPO_ROOT / "agents" / "deep_research.yaml")


@requires_live_key
async def test_coordinator_routes_a_simple_lookup_to_quick():
    """A single-fact question is classified 'quick' by the real coordinator."""
    coordinator = build_agent(COORDINATOR)
    decision = await coordinator.run(
        "Who is the current CEO of OpenAI?", session_id="coord-quick-test"
    )
    assert normalize_label(str(decision.output)) == "quick"


@requires_live_key
async def test_coordinator_routes_a_broad_investigation_to_deep():
    """A broad, comparative investigation is classified 'deep' by the real coordinator."""
    coordinator = build_agent(COORDINATOR)
    decision = await coordinator.run(
        "Give me a thorough comparison of the leading small modular nuclear reactor vendors, "
        "their designs, funding, and regulatory status in 2026.",
        session_id="coord-deep-test",
    )
    assert normalize_label(str(decision.output)) == "deep"


@requires_live_key
async def test_deep_path_pauses_after_a_round_then_resumes_to_a_report(monkeypatch):
    """The durable deep agent runs a round, pauses for the human, and resumes to a real report.

    Forces a single automatic round (so the live test stays cheap), then declines to go deeper on
    resume; the run must come back with a synthesized, non-empty report and no lingering interrupt.
    """
    monkeypatch.setenv("DEEP_RESEARCH_AUTO_ROUNDS", "1")
    session = "coord-deep-live"
    agent = build_agent(DEEP)
    paused = await agent.run("The state of small modular reactors in 2026.", session_id=session)

    assert paused.interrupt is not None, "the deep agent should pause to ask 'go deeper?'"
    assert paused.output is None and paused.resume_token is not None

    ctx = RunContext(
        caller=Caller(user_id="demo"),
        session_id=session,
        run_id="resume-1",
        agent_name=agent.spec.name,
        mode=RunMode.INVOKE,
    )
    done = await agent.engine.resume(
        agent.compiled, paused.resume_token, ctx, resume_value={"go_deeper": False}
    )
    assert done.interrupt is None
    assert isinstance(done.output, str) and done.output.strip()
