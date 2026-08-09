"""Live slice: the `graph` template — a real routed coordinator -> worker turn.

The ``graph`` template generates a real, compilable supervisor ``StateGraph`` that
routes a coordinator to one worker. This test loads the demo's own
``agents/graph.yaml`` through the framework and runs one real ``gpt-4o-mini`` turn
(coordinator routes -> worker answers), asserting a real non-empty answer. There is
no recording: this makes a live call to OpenAI.

HONEST LABEL: this is the authoring SCAFFOLD only — a full durable multi-agent
runtime is Phase 02. The test proves the scaffold is a real routed graph that
answers, not that a durable multi-agent runtime exists.

Run it (with a key set):

    set -a; source ../agentship/.env; set +a
    pytest tests/test_graph.py -q

Without a key the test skips cleanly.
"""

from __future__ import annotations

from pathlib import Path

from agentship import build_agent
from conftest import requires_live_key

AGENT = str(Path(__file__).resolve().parents[1] / "agents" / "graph.yaml")


@requires_live_key
async def test_graph_scaffold_routes_and_returns_a_non_empty_answer():
    """agents/graph.yaml (template: graph) routes coordinator -> worker and answers live."""
    agent = build_agent(AGENT)
    assert agent.spec.template == "graph"

    result = await agent.run("Help me plan a weekend trip to the mountains.")

    assert isinstance(result.output, str)
    assert result.output.strip() != ""
