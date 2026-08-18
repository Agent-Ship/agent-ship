"""``generate_langgraph_json`` builds a valid Studio manifest from an ``agents/`` dir (P07 · §4.8).

Pins the glue P12/Studio consume: one manifest graph per discovered LangGraph agent, a generated
wrapper module that binds each spec to the graph factory, and a clear error when there is nothing to
render. Non-LangGraph agents are skipped (Studio can render only ``engine: langgraph``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agentship.errors import AgentShipError
from agentship.observability.studio import (
    WRAPPER_MODULE,
    discover_langgraph_agents,
    generate_langgraph_json,
)

_LANGGRAPH = "name: {name}\nengine: langgraph\nmodel: openai/gpt-4o-mini\nprompt: hi\n"
_ECHO = "name: {name}\nengine: echo\n"


def _agents_dir(tmp_path: Path, specs: dict[str, str]) -> Path:
    """Write the given ``name → yaml`` specs into an ``agents/`` dir and return it."""
    agents = tmp_path / "agents"
    agents.mkdir()
    for name, body in specs.items():
        (agents / f"{name}.yaml").write_text(body, encoding="utf-8")
    return agents


def test_discovery_keeps_only_langgraph_agents(tmp_path: Path) -> None:
    """Discovery returns LangGraph agents by name and skips other engines."""
    agents = _agents_dir(
        tmp_path,
        {"single": _LANGGRAPH.format(name="single"), "noisy": _ECHO.format(name="noisy")},
    )
    found = discover_langgraph_agents(agents)
    assert set(found) == {"single"}
    assert found["single"] == (agents / "single.yaml").resolve()


def test_manifest_has_one_graph_per_agent(tmp_path: Path) -> None:
    """The manifest maps each LangGraph agent to a ``./wrapper:name`` graph ref."""
    agents = _agents_dir(
        tmp_path,
        {"single": _LANGGRAPH.format(name="single"), "planner": _LANGGRAPH.format(name="planner")},
    )
    out = generate_langgraph_json(agents, tmp_path / "langgraph.json")
    manifest = json.loads(out.read_text())
    assert manifest["graphs"] == {
        "single": f"./{WRAPPER_MODULE}:single",
        "planner": f"./{WRAPPER_MODULE}:planner",
    }
    assert manifest["dependencies"] == ["."]


def test_wrapper_module_binds_each_spec(tmp_path: Path) -> None:
    """The generated wrapper binds each agent name to the graph factory over its spec path."""
    agents = _agents_dir(tmp_path, {"single": _LANGGRAPH.format(name="single")})
    out = generate_langgraph_json(agents, tmp_path / "langgraph.json")
    wrapper = (out.parent / WRAPPER_MODULE).read_text()
    assert "from agentship_langgraph.studio import build_studio_graph" in wrapper
    assert "single = build_studio_graph(" in wrapper
    assert "single.yaml" in wrapper


def test_no_langgraph_agents_is_an_error(tmp_path: Path) -> None:
    """A directory with no LangGraph agent fails clearly rather than launching empty Studio."""
    agents = _agents_dir(tmp_path, {"noisy": _ECHO.format(name="noisy")})
    with pytest.raises(AgentShipError):
        generate_langgraph_json(agents, tmp_path / "langgraph.json")
