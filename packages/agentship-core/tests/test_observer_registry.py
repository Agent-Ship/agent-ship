"""The observer registry and the ``resolve_observer`` seam that wires it into ``build_agent`` (P07).

An agent's declarative ``observability:`` block only means something if the runtime turns it into a
real observer. These pin that path end to end: ``resolve_observer`` maps a block to an observer (or
``None`` when tracing is off), a declared-but-uninstalled provider fails fast, and ``build_agent``
honours the block while an explicitly-passed observer still wins.

The seam is exercised against the real ``otel`` provider name — the only non-``none`` one the spec
allows today — with the registry's ``otel`` slot stubbed or emptied per test so the logic holds
without depending on the ``agentship-observability`` adapter being installed. One end-to-end test
resolves the true adapter and is skipped when it is absent.
"""

from __future__ import annotations

import pytest
from agentship.errors import CapabilityError
from agentship.observability import (
    OBSERVERS,
    NoOpObserver,
    RecordingObserver,
    resolve_observer,
)
from agentship.runtime import build_agent
from agentship.spec import AgentSpec, ObservabilitySpec


class _MarkerObserver(NoOpObserver):
    """A no-op observer we can identify by type, standing in for a real adapter observer."""


@pytest.fixture
def stub_otel():
    """Point the registry's ``otel`` slot at a marker factory for the test, then restore it."""
    OBSERVERS.names()  # force entry-point discovery so the real slot is captured
    saved = OBSERVERS._providers.get("otel")
    OBSERVERS._providers["otel"] = lambda spec: _MarkerObserver()
    yield
    if saved is None:
        OBSERVERS._providers.pop("otel", None)
    else:
        OBSERVERS._providers["otel"] = saved


@pytest.fixture
def no_otel():
    """Empty the registry's ``otel`` slot to simulate the adapter not being installed."""
    OBSERVERS.names()  # force discovery so we remove a real slot, not a to-be-discovered one
    saved = OBSERVERS._providers.pop("otel", None)
    yield
    if saved is not None:
        OBSERVERS._providers["otel"] = saved


def test_resolve_observer_returns_none_when_no_block() -> None:
    """No ``observability`` block means leave the agent on its default (no-op) observer."""
    assert resolve_observer(None) is None


def test_resolve_observer_returns_none_when_provider_is_none() -> None:
    """``provider: none`` explicitly disables tracing, so no observer is attached."""
    assert resolve_observer(ObservabilitySpec(provider="none")) is None


def test_resolve_observer_builds_the_named_provider(stub_otel) -> None:
    """A block naming an installed provider is turned into that provider's observer."""
    observer = resolve_observer(ObservabilitySpec(provider="otel"))
    assert isinstance(observer, _MarkerObserver)


def test_resolve_observer_fails_fast_on_missing_provider(no_otel) -> None:
    """A provider declared but not installed is a loud CapabilityError, not a silent skip."""
    with pytest.raises(CapabilityError, match="not installed"):
        resolve_observer(ObservabilitySpec(provider="otel"))


def test_build_agent_attaches_the_spec_observer(stub_otel) -> None:
    """``build_agent`` resolves the block's observer when the caller passes none."""
    spec = AgentSpec(name="a", engine="echo", observability=ObservabilitySpec(provider="otel"))
    agent = build_agent(spec)
    assert isinstance(agent.observer, _MarkerObserver)


def test_build_agent_keeps_noop_without_a_block() -> None:
    """No block, no explicit observer → the agent stays on the no-op observer."""
    agent = build_agent(AgentSpec(name="a", engine="echo"))
    assert isinstance(agent.observer, NoOpObserver)


def test_explicit_observer_wins_over_the_spec_block(stub_otel) -> None:
    """An observer passed to ``build_agent`` always wins, even when the spec asks for a provider."""
    explicit = RecordingObserver()
    spec = AgentSpec(name="a", engine="echo", observability=ObservabilitySpec(provider="otel"))
    agent = build_agent(spec, observer=explicit)
    assert agent.observer is explicit


def test_otel_provider_is_registered_via_entry_point() -> None:
    """The adapter contributes ``otel`` through the ``agentship.observers`` entry-point group."""
    pytest.importorskip("agentship_observability")
    assert OBSERVERS.get("otel") is not None


async def test_otel_provider_resolves_from_a_declarative_block() -> None:
    """The real ``otel`` provider is reachable through the block and traces a live turn."""
    pytest.importorskip("agentship_observability")
    spec = AgentSpec(
        name="a",
        engine="echo",
        observability=ObservabilitySpec(provider="otel", exporters=["console"]),
    )
    agent = build_agent(spec)
    assert not isinstance(agent.observer, NoOpObserver)
    result = await agent.run("hi")
    assert result.output == "echo: hi"
