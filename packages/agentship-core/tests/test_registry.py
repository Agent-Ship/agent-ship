"""Tests for the generic Registry: register/get/names and entry-point discovery."""

from __future__ import annotations

import logging

from agentship.registry import Registry


def test_register_and_get():
    """A registered provider is retrievable by name; an unknown name returns None."""
    reg: Registry[str] = Registry("agentship.nonexistent", label="widget")
    reg.register("a", "provider-a")
    assert reg.get("a") == "provider-a"
    assert reg.get("missing") is None


def test_names_is_sorted():
    """names() lists every registered provider, sorted."""
    reg: Registry[int] = Registry("agentship.nonexistent", label="widget")
    reg.register("z", 1)
    reg.register("a", 2)
    assert reg.names() == ["a", "z"]


def test_collision_is_logged_not_swallowed(caplog):
    """Re-registering a name with a DIFFERENT provider overwrites, and warns."""
    reg: Registry[str] = Registry("agentship.nonexistent", label="widget")
    reg.register("x", "first")
    with caplog.at_level(logging.WARNING):
        reg.register("x", "second")
    assert reg.get("x") == "second"
    assert any("already registered" in r.message for r in caplog.records)


def test_broken_plugin_is_logged_not_swallowed(caplog, monkeypatch):
    """A broken entry point is logged (with its name) and skipped — discovery survives.

    We inject a fake entry point whose ``load()`` raises, plus a healthy one, and
    assert the healthy provider is still discovered while the failure is logged.
    """
    from agentship import registry as registry_mod

    class _BrokenEP:
        name = "broken"
        value = "does.not:exist"

        def load(self):
            raise ImportError("boom")

    class _GoodEP:
        name = "good"
        value = "fine:Provider"

        def load(self):
            return "good-provider"

    def fake_entry_points(*, group):
        assert group == "agentship.test-discovery"
        return [_BrokenEP(), _GoodEP()]

    monkeypatch.setattr(registry_mod, "entry_points", fake_entry_points)

    reg: Registry[str] = Registry("agentship.test-discovery", label="widget")
    with caplog.at_level(logging.WARNING):
        names = reg.names()

    # The healthy plugin is discovered despite the broken one.
    assert "good" in names
    assert reg.get("good") == "good-provider"
    # The broken plugin was logged, not silently swallowed.
    assert any("broken" in r.message and "failed to load" in r.message for r in caplog.records)
