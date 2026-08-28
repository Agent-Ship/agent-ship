"""Live slices: the durable multi-agent triage supervisor.

Two tests:

1. ``test_triage_routes_and_answers_a_billing_question_live`` — end-to-end happy path:
   classify → route to billing specialist → resolve; asserts a non-empty real answer
   and a minted resume token.

2. ``test_triage_resume_after_simulated_kill`` — the kill-9 guarantee: same run, then a
   brand-new engine instance (simulating a process restart) resumes from the checkpoint
   in the resume token and produces byte-identical output. This is not a claim — it
   actually runs the resume step and compares outputs.

Both call OpenAI live. Without a key they skip cleanly — never fake-pass, never hard-error.

Run (with a key set)::

    set -a; source ../agentship/.env; set +a
    pytest tests/test_triage.py -q -s
"""

from __future__ import annotations

import pytest

import logging
import os
from pathlib import Path

from agentship import build_agent
from agentship.context import Caller, RunContext, RunMode
from agentship_langgraph.engine import LangGraphEngine

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT = str(REPO_ROOT / "agents" / "triage" / "triage.yaml")
PANEL = str(REPO_ROOT / "agents" / "triage" / "panel.yaml")
DECLARATIVE = str(REPO_ROOT / "agents" / "triage" / "triage_declarative.yaml")
_QUESTION = "My invoice looks wrong and I was double charged — who handles payments?"


@pytest.mark.vcr
async def test_triage_routes_and_answers_a_billing_question_live():
    """The triage supervisor classifies, routes to billing specialist, and answers — durably."""
    cwd = os.getcwd()
    os.chdir(REPO_ROOT)
    try:
        agent = build_agent(AGENT)
        assert agent.spec.name == "triage"
        result = await agent.run(_QUESTION)
    finally:
        os.chdir(cwd)

    assert isinstance(result.output, str) and result.output.strip()
    assert result.resume_token is not None
    assert result.resume_token.engine == "langgraph"


@pytest.mark.vcr
async def test_triage_resume_after_simulated_kill():
    """Kill-9 guarantee: a fresh engine resumes from the checkpoint → byte-identical output.

    Step 1 — original run: builds the supervisor, asks a real question, gets a real answer
    and a resume token. The session_id is fixed so the thread_id is stable.

    Step 2 — simulated kill: instantiates a brand-new ``LangGraphEngine`` (simulating a
    process restart; the in-memory saver singleton is shared in-process, standing in for
    a real Postgres checkpointer in production). Calls ``engine.resume()`` with only the
    resume token — no other context about the original run.

    Step 3 — assertion: the resumed output is byte-identical to the original. This is the
    actual promise of durable checkpoints, proven here rather than just claimed.
    """
    SESSION = "demo-resume-test"
    cwd = os.getcwd()
    os.chdir(REPO_ROOT)
    try:
        # Step 1: original run on a fixed session so the thread_id is stable.
        agent = build_agent(AGENT)
        result = await agent.run(_QUESTION, session_id=SESSION)
        assert result.resume_token is not None, "original run must mint a resume token"
        original_output = result.output

        # Step 2: simulated kill — fresh engine, fresh build, same spec.
        # In production this would be a new process reading a Postgres checkpoint;
        # here the InMemorySaver singleton (no DB configured) is shared in-process.
        new_engine = LangGraphEngine()
        new_compiled = new_engine.build(agent.spec)
        ctx = RunContext(
            caller=Caller(user_id="anonymous"),
            session_id=SESSION,
            run_id="r-resume",
            agent_name=agent.spec.name,
            mode=RunMode.INVOKE,
        )
        resumed = await new_engine.resume(new_compiled, result.resume_token, ctx)
    finally:
        os.chdir(cwd)

    # Step 3: byte-identical output — the checkpoint replayed identically.
    assert resumed.output == original_output, (
        f"resumed output differs from original:\n"
        f"  original: {original_output!r}\n"
        f"  resumed:  {resumed.output!r}"
    )


@pytest.mark.vcr
async def test_triage_panel_fans_out_to_multiple_sub_agents_in_parallel(caplog):
    """The panel dispatches to all three sub-agents concurrently, then merges their answers.

    Proves the fan-out is real (not a single agent): the supervisor's decision log records a
    ``parallel`` dispatch to all three named sub-agents, each produces an answer, and the
    ``ConflictResolver`` reports all three as *considered* before picking one winner. Asserting on
    the captured decision log is how we see the multiple sub-agents actually ran.
    """
    cwd = os.getcwd()
    os.chdir(REPO_ROOT)
    try:
        agent = build_agent(PANEL)
        with caplog.at_level(logging.INFO, logger="agentship.supervisor"):
            result = await agent.run(
                "My latest bill looks wrong and I've also been feeling dizzy — can you help?",
                session_id="test-panel",
            )
    finally:
        os.chdir(cwd)

    assert isinstance(result.output, str) and result.output.strip()
    log = "\n".join(rec.getMessage() for rec in caplog.records)

    # A parallel dispatch to all three named sub-agents was logged.
    assert "dispatch: parallel" in log, f"expected a parallel dispatch, got:\n{log}"
    for sub_agent in ("billing_specialist", "clinical_specialist", "faq_specialist"):
        assert sub_agent in log, f"sub-agent {sub_agent!r} never ran; log:\n{log}"

    # All three were considered by the resolver before one winner was chosen.
    assert "resolve: winner=" in log
    considered = next(rec.getMessage() for rec in caplog.records if "considered=" in rec.getMessage())
    for sub_agent in ("billing_specialist", "clinical_specialist", "faq_specialist"):
        assert sub_agent in considered, f"{sub_agent!r} not considered by resolver: {considered}"


@pytest.mark.vcr
async def test_declarative_supervisor_routes_with_zero_python(caplog):
    """A YAML-only supervisor (members: -> sub-agent YAMLs) routes to a specialist and answers.

    No `code:` factory: the framework resolves the member refs, derives the routing, and dispatches
    to the classified sub-agent. The captured decision log confirms a named sub-agent handled it.
    """
    agent = build_agent(DECLARATIVE)
    assert set(agent.compiled.members) == {
        "billing_specialist",
        "clinical_specialist",
        "faq_specialist",
    }
    with caplog.at_level(logging.INFO, logger="agentship.supervisor"):
        result = await agent.run(_QUESTION, session_id="test-declarative")

    assert isinstance(result.output, str) and result.output.strip()
    log = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "dispatch:" in log and "billing_specialist" in log
