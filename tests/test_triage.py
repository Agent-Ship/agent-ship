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

import os
from pathlib import Path

from agentship import build_agent
from agentship.context import Caller, RunContext, RunMode
from agentship_langgraph.engine import LangGraphEngine
from conftest import requires_live_key

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT = str(REPO_ROOT / "agents" / "triage" / "triage.yaml")
_QUESTION = "My invoice looks wrong and I was double charged — who handles payments?"


@requires_live_key
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


@requires_live_key
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
