"""Report which AgentShip build is running.

A deployment that cannot tell you what code it is running is a deployment you cannot
trust a bug report against. The package version alone is not enough here — every package
is pinned at ``0.0.1`` and does not move between builds — so the image also stamps a build
id at build time (``AGENTSHIP_BUILD``, typically a git sha or a timestamp).
"""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version

#: The distributions that make up a running service, in the order a reader cares about.
PACKAGES = (
    "agentship-core",
    "agentship-langgraph",
    "agentship-service",
    "agentship-cli",
    "agentship-observability",
)


def installed_versions() -> dict[str, str]:
    """Return ``{distribution: version}`` for every AgentShip package that is installed.

    A package that is not installed is simply absent, so the result also shows which
    extras a deployment actually has — an image without ``agentship-observability`` is
    visibly different from one that has it.
    """
    found: dict[str, str] = {}
    for name in PACKAGES:
        try:
            found[name] = version(name)
        except PackageNotFoundError:
            continue
    return found


def build_id() -> str:
    """The image's build stamp from ``AGENTSHIP_BUILD``, or ``"dev"`` when unset.

    Set it at image build time (a git sha, or a timestamp) so two images built from
    different source are distinguishable even though the package versions match.
    """
    return os.environ.get("AGENTSHIP_BUILD") or "dev"


def build_info() -> dict[str, object]:
    """The full build description: the build stamp plus every installed package version."""
    return {"build": build_id(), "packages": installed_versions()}
