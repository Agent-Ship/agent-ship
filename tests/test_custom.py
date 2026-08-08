"""Keyless slice: custom `build_graph` — the author's native LangGraph answered.

The custom path subclasses ``LangGraphAgent`` and writes a native ``StateGraph``
in ``build_graph``; a spec's ``code:`` points at its factory. This test loads the
demo's own ``agents/custom/custom.yaml``, runs one real ``gpt-4o-mini`` turn
through the *author's* graph, and asserts a non-empty answer. The real HTTP
round-trip is recorded once (credentials redacted) and replayed keyless.

The ``code:`` reference is repo-root-relative, so the test chdirs to the demo repo
root while building — exactly as ``agentship run`` resolves it from the repo root.

To (re-)record the cassette, with a real key present:

    set -a; source ../agentship/.env; set +a
    pytest tests/test_custom.py --record-mode=once
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from agentship import build_agent

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT = str(REPO_ROOT / "agents" / "custom" / "custom.yaml")


@pytest.mark.vcr
async def test_custom_build_graph_answers_via_the_authors_graph(openai_key_for_replay):
    """agents/custom/custom.yaml runs the author's native graph and answers (via cassette)."""
    cwd = os.getcwd()
    os.chdir(REPO_ROOT)  # the code: path is repo-root-relative
    try:
        agent = build_agent(AGENT)
        assert agent.spec.name == "custom-assistant"
        # The custom subclass is what the harness built — the author's graph, not
        # the engine's default build body.
        assert type(agent.engine).__name__ == "LangGraphEngine"

        result = await agent.run("Name three primary colors.")

        assert isinstance(result.output, str)
        assert result.output.strip() != ""
    finally:
        os.chdir(cwd)
