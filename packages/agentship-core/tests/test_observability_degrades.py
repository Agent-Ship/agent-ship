"""A missing tracing adapter must not stop an agent from running.

Found by installing the published packages from PyPI and running the quickstart:

    pip install "agentship-sdk[starter]"
    agentship run hello.yaml
    Error: observability provider 'otel' is not installed

`provider` defaults to "otel" and an agent with no observability block is still traced, so
a stack that does not include agentship-observability could not run ANY agent — including
the keyless echo example the docs open with. The whole install was unusable.

Raising is right when someone asked for a backend and it is absent: a silently untraced
run is worse than a loud failure. It is wrong when nobody asked, and the value came from a
default they never wrote. Tracing is meant to be fail-open — a tracing fault is logged and
swallowed so a run never breaks because of observability — and that principle was being
violated by the one code path that runs before any tracing exists.
"""

from __future__ import annotations

import pytest
from agentship.errors import CapabilityError
from agentship.observability.registry import OBSERVERS, resolve_observer
from agentship.spec import ObservabilitySpec


@pytest.fixture
def no_adapters(monkeypatch):
    """A process where no observability adapter is installed."""
    # Pretend discovery ran and found nothing, which is what a stack without
    # agentship-observability installed actually looks like.
    monkeypatch.setattr(OBSERVERS, "_providers", {})
    monkeypatch.setattr(OBSERVERS, "_discovered", True)
    return OBSERVERS


def test_an_agent_with_no_observability_block_still_runs(no_adapters):
    """The default provider must degrade, not raise — nobody asked for otel here."""
    assert resolve_observer(None) is None, (
        "an agent that never mentioned observability cannot be stopped by a missing adapter"
    )


def test_a_block_that_does_not_name_a_provider_also_degrades(no_adapters):
    """`capture_content: true` alone must not make the run depend on an adapter.

    A spec can set an observability option without choosing a backend; `provider` is then
    still a default, so the same reasoning applies.
    """
    spec = ObservabilitySpec(capture_content=True)

    assert resolve_observer(spec) is None


def test_explicitly_asking_for_a_missing_backend_still_raises(no_adapters):
    """`provider: otel` written by hand is a request, and an unmet request must be loud.

    This is the case the error was built for: the operator wants their traces in a backend,
    and quietly dropping them would mean debugging a production incident with nothing.
    """
    spec = ObservabilitySpec(provider="otel")

    with pytest.raises(CapabilityError, match="not installed"):
        resolve_observer(spec)
