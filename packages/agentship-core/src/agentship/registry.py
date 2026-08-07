"""``Registry[T]`` — the one extension mechanism, populated by entry points.

A single generic registry serves every pluggable concern. Today it holds engines;
later phases register adapters (models, tools, observability) the same way, with
no kernel edits. A vendor ships a class plus one ``[project.entry-points]`` line
and it becomes discoverable. Discovery is thread-safe and atomic: a concurrent
first-time lookup either sees discovery not yet run (and blocks on the lock) or
sees a fully populated registry — never the half-populated in-between (no TOCTOU).
"""

from __future__ import annotations

import logging
import threading
from importlib.metadata import entry_points

logger = logging.getLogger(__name__)


class Registry[T]:
    """A named registry of providers for one pluggable concern.

    ``entry_point_group`` is the packaging group external adapters register under
    (e.g. ``agentship.engines``); ``label`` names the concern in log/error text.
    Providers are usually a class or factory callable — the caller decides how to
    instantiate what :meth:`get` returns.
    """

    def __init__(self, entry_point_group: str, *, label: str) -> None:
        """Create a registry bound to an entry-point group and a human label."""
        self._group = entry_point_group
        self._label = label
        self._providers: dict[str, T] = {}
        self._discovered = False
        #: Serialises discovery so concurrent first-time lookups never observe a
        #: half-populated registry. ``RLock`` so a discovery-triggered re-entrant
        #: ``register`` on the same thread cannot deadlock.
        self._lock = threading.RLock()

    def register(self, name: str, provider: T) -> T:
        """Register ``provider`` under ``name`` (in-code registration). Returns it.

        Warns on a genuine name collision: re-registering an already-known name
        with a *different* provider overwrites it (in-code registration wins over
        an entry point of the same name), but silently shadowing a provider is a
        footgun, so the overwrite is logged rather than swallowed.
        """
        with self._lock:
            existing = self._providers.get(name)
            if existing is not None and existing is not provider:
                logger.warning(
                    "%s %r is already registered; overwriting (%r -> %r)",
                    self._label,
                    name,
                    existing,
                    provider,
                )
            self._providers[name] = provider
        return provider

    def get(self, name: str) -> T | None:
        """Return the provider under ``name``, or ``None`` if absent (after discovery)."""
        if name not in self._providers:
            self._discover()
        return self._providers.get(name)

    def names(self) -> list[str]:
        """List all registered provider names (after entry-point discovery)."""
        self._discover()
        return sorted(self._providers)

    def __contains__(self, name: str) -> bool:
        """Whether a provider is registered (or discoverable) under ``name``."""
        return self.get(name) is not None

    def _discover(self) -> None:
        """Scan the entry-point group once and register any installed adapters.

        Atomic under ``_lock``: ``_discovered`` is only flipped once every entry
        point has been loaded into ``_providers``, so a concurrent lookup either
        blocks on the lock or sees a fully populated registry — never the
        half-populated in-between. A broken plugin is logged with its entry-point
        name and error instead of being silently dropped, and does not abort the
        discovery of the others.
        """
        if self._discovered:
            return
        with self._lock:
            # Re-check inside the lock: another thread may have finished discovery
            # while this one waited for it.
            if self._discovered:
                return
            for ep in entry_points(group=self._group):
                if ep.name in self._providers:
                    continue
                try:
                    self._providers[ep.name] = ep.load()
                except Exception as exc:  # noqa: BLE001 - a broken plugin must not kill discovery
                    logger.warning(
                        "failed to load %s entry point %r (from %r): %s — skipping it",
                        self._label,
                        ep.name,
                        getattr(ep, "value", "?"),
                        exc,
                    )
            # Only now, with every entry point loaded, publish that discovery ran.
            self._discovered = True
