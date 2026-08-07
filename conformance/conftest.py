"""Conformance-suite fixtures: engine discovery + a registry snapshot guard.

Two responsibilities:

* :func:`registered_engines` — the list of engine names the matrix parametrizes
  over, taken live from the plugin registry so any installed engine is covered
  automatically (no hand-maintained list).
* :func:`registry_snapshot` (autouse) — snapshots the engine registry before each
  test and restores it after, so a test that registers a throwaway engine (the
  over-declaration meta-test registers a deliberate liar) can never leak it into
  another test's parametrization or into the real registry.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from agentship.engines.base import ENGINES


@pytest.fixture
def registry_snapshot() -> Iterator[None]:
    """Snapshot and restore the engine registry around a test.

    Copies the registry's provider map (and its discovered flag) before the test
    and restores both afterwards, so registering a throwaway engine inside a test
    cannot leak. Reaches the registry's internals deliberately — this guard is the
    one sanctioned place that does so, keeping the public registry API clean.
    """
    saved_providers = dict(ENGINES._providers)
    saved_discovered = ENGINES._discovered
    try:
        yield
    finally:
        ENGINES._providers = saved_providers
        ENGINES._discovered = saved_discovered


def _discover_engine_names() -> list[str]:
    """Return every registered engine name (triggers entry-point discovery once)."""
    return ENGINES.names()


#: Collected at import time so it can drive ``pytest.mark.parametrize`` (which needs
#: the values during collection, before fixtures run). Discovery is idempotent and
#: cached in the registry, so this is cheap and stable.
REGISTERED_ENGINE_NAMES: list[str] = _discover_engine_names()
