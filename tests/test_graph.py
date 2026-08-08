"""Keyless slice: the `graph` template — real routed coordinator -> worker turn.

The ``graph`` template generates a real, compilable supervisor ``StateGraph`` that
routes a coordinator to one worker. This test loads the demo's own
``agents/graph.yaml`` through the framework, runs one real ``gpt-4o-mini`` turn
(coordinator routes -> worker answers), and asserts a non-empty answer. The real
HTTP round-trip is recorded once (credentials redacted) and replayed keyless.

HONEST LABEL: this is the authoring SCAFFOLD only — a full DURABLE multi-agent
runtime is Phase 02. The test proves the scaffold is a real routed graph that
answers, not that a durable multi-agent runtime exists.

To (re-)record the cassette, with a real key present:

    set -a; source ../agentship/.env; set +a
    pytest tests/test_graph.py --record-mode=once
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agentship import build_agent

AGENT = str(Path(__file__).resolve().parents[1] / "agents" / "graph.yaml")


@pytest.mark.vcr
async def test_graph_scaffold_routes_and_returns_a_non_empty_answer(openai_key_for_replay):
    """agents/graph.yaml (template: graph) routes coordinator -> worker and answers (via cassette)."""
    agent = build_agent(AGENT)
    assert agent.spec.template == "graph"

    result = await agent.run("Help me plan a weekend trip to the mountains.")

    assert isinstance(result.output, str)
    assert result.output.strip() != ""
