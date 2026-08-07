"""Packaging isolation: importing agentship-core must NOT drag in vendor libraries.

The kernel is required to be dependency-light — its only runtime deps are pydantic
and pyyaml (see agentship-core/pyproject.toml). If a vendor import ever leaks into
core (e.g. someone imports langgraph at module top-level in the kernel), the light-core
promise in DESIGN §9 breaks: every `import agentship` would then pull an engine.

This test enforces that in a *fresh* subprocess (so it is immune to whatever the rest
of the test session has already imported): it imports the whole public surface of
`agentship` and asserts none of the heavy engine/service libraries landed in
`sys.modules`. It goes red the moment a vendor import leaks into the kernel.
"""

from __future__ import annotations

import subprocess
import sys

# Libraries the kernel must never import transitively. These belong to the engine,
# gateway, and service packages — not the vendor-free core.
FORBIDDEN = ("langgraph", "langchain", "litellm", "fastapi")

_PROBE = f"""
import sys
import agentship  # noqa: F401  (import for side effects — the whole public surface)

# Touch the re-exported public names so a lazy import can't hide the leak.
from agentship import (  # noqa: F401
    AgentSpec,
    RunContext,
    RunnableAgent,
    build_agent,
    Engine,
    EngineCapabilities,
)

forbidden = {FORBIDDEN!r}
leaked = sorted(
    name
    for name in sys.modules
    for prefix in forbidden
    if name == prefix or name.startswith(prefix + ".")
)
print(",".join(leaked))
"""


def test_core_import_pulls_in_no_vendor_modules():
    """A fresh `import agentship` (core) loads no langgraph/langchain/litellm/fastapi."""
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        f"probe subprocess failed:\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )
    leaked = [name for name in proc.stdout.strip().split(",") if name]
    assert not leaked, (
        "agentship-core leaked vendor modules into sys.modules "
        f"(the kernel must stay dependency-light): {leaked}"
    )
