"""One broken agent does not take down the other eight.

Agents are built when the service starts. A spec that fails to build — an MCP server that
never answers, a missing tool, an uninstalled extra — used to raise out of the app factory, so
uvicorn never bound a port and EVERY agent became unreachable. A real deployment returned
ERR_CONNECTION_RESET for nine agents because one of them could not reach its MCP server.

A deployment is more useful degraded than dead: the broken agent is reported loudly and left
out of the catalogue, the rest are served.
"""

from __future__ import annotations

import logging

from agentship_service.serving import load_agents


def _spec(path, name: str, body: str = "") -> None:
    """Write a minimal agent spec to ``path``."""
    path.write_text(f"name: {name}\nengine: echo\n{body}")


def test_a_spec_that_cannot_be_built_is_skipped_not_fatal(tmp_path, caplog):
    """The healthy agents are served and the broken one is named in the log."""
    _spec(tmp_path / "good.yaml", "good")
    # `engine: nonexistent` fails at build time, standing in for any build-time failure
    # (a wedged MCP server, a missing extra, an unresolvable tool).
    (tmp_path / "broken.yaml").write_text("name: broken\nengine: nonexistent-engine\n")
    _spec(tmp_path / "also_good.yaml", "also_good")

    with caplog.at_level(logging.ERROR):
        registry = load_agents(tmp_path)

    served = {a.spec.name for a in registry}
    assert served == {"good", "also_good"}, f"healthy agents must still be served, got {served}"
    assert "broken" in caplog.text, "the skipped agent must be named so it can be fixed"


def test_every_spec_failing_still_yields_a_running_service(tmp_path, caplog):
    """Even with nothing servable the app starts — an empty catalogue beats a dead port.

    /healthz still answers, so an operator can reach the deployment and read why it is empty.
    """
    (tmp_path / "broken.yaml").write_text("name: broken\nengine: nonexistent-engine\n")

    with caplog.at_level(logging.ERROR):
        registry = load_agents(tmp_path)

    assert list(registry) == []
