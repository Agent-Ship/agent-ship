"""Agents served by the app inherit the observability seam through ``build_agent`` (P07 · #40).

The service builds every spec with :func:`~agentship.runtime.build_agent`, so a spec that declares
an ``observability:`` block must come out of :func:`~agentship_service.serving.load_agents` already
carrying a real tracer — with no observer plumbing in the service layer. A spec without the block
stays on the no-op observer, so tracing is strictly opt-in.
"""

from __future__ import annotations

from agentship.observability import NoOpObserver
from agentship_service.serving import load_agents

_TRACED_YAML = """\
name: traced
engine: echo
observability:
  provider: otel
  exporters: [console]
"""

_PLAIN_YAML = """\
name: plain
engine: echo
"""


def test_served_agent_with_a_block_is_traced(tmp_path) -> None:
    """A YAML spec with an ``observability`` block loads as an agent with a real observer."""
    (tmp_path / "traced.yaml").write_text(_TRACED_YAML)
    registry = load_agents(tmp_path)
    assert not isinstance(registry.get("traced").observer, NoOpObserver)


def test_served_agent_without_a_block_stays_untraced(tmp_path) -> None:
    """A spec with no ``observability`` block keeps the no-op observer — tracing is opt-in."""
    (tmp_path / "plain.yaml").write_text(_PLAIN_YAML)
    registry = load_agents(tmp_path)
    assert isinstance(registry.get("plain").observer, NoOpObserver)
