"""Live slice: the durable multi-agent triage supervisor answers a routed request for real.

Loads the demo's own ``agents/triage/triage.yaml``, asks a billing question, and asserts a real
non-empty answer plus a minted resume token (the run was durable). The supervisor classifies with
``gpt-4o-mini``, routes to the billing specialist, and resolves the answer — a real end-to-end
multi-agent turn. Live: it calls OpenAI. Without a key the test skips cleanly.

Run it (with a key set)::

    set -a; source ../agentship/.env; set +a
    pytest tests/test_triage.py -q
"""

from __future__ import annotations

import os
from pathlib import Path

from agentship import build_agent
from conftest import requires_live_key

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT = str(REPO_ROOT / "agents" / "triage" / "triage.yaml")


@requires_live_key
async def test_triage_routes_and_answers_a_billing_question_live():
    """The triage supervisor classifies, routes to a specialist, and answers — durably."""
    cwd = os.getcwd()
    os.chdir(REPO_ROOT)  # the code: path is repo-root-relative
    try:
        agent = build_agent(AGENT)
        assert agent.spec.name == "triage"
        result = await agent.run(
            "My invoice looks wrong and I was double charged — who handles payments?"
        )
    finally:
        os.chdir(cwd)

    # A real specialist answered (non-empty), and the durable run minted a resume token.
    assert isinstance(result.output, str) and result.output.strip()
    assert result.resume_token is not None
    assert result.resume_token.engine == "langgraph"
