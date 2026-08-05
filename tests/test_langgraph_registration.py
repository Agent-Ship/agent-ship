"""T1 proof: the ``langgraph`` engine is discoverable via its entry point.

The kernel populates :data:`agentship.engines.base.ENGINES` from the
``agentship.engines`` entry-point group. Once the ``[langgraph]`` extra is
installed and the entry point registered in ``pyproject.toml``, the registry must
resolve ``langgraph`` to :class:`~agentship.engines.langgraph.engine.LangGraphEngine`.
"""

from __future__ import annotations

from agentship.engines.base import ENGINES


def test_langgraph_engine_is_registered_via_entry_point():
    """The registry resolves ``langgraph`` (entry-point discovery), not just ``echo``."""
    engine_cls = ENGINES.get("langgraph")
    assert engine_cls is not None, f"langgraph not discovered; available: {ENGINES.names()}"
    assert engine_cls.name == "langgraph"


def test_langgraph_appears_in_available_engine_names():
    """``langgraph`` shows up alongside ``echo`` in the discovered engine names."""
    names = ENGINES.names()
    assert "langgraph" in names
    assert "echo" in names
