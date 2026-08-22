"""See a real agent emit a full OpenTelemetry trace — turned on by one YAML block (Phase 07).

This runs ``agents/observability.yaml`` — the calculator agent with an ``observability:`` block — for
one live turn. The block is the whole trick: the runtime resolves it to a real OTel observer, so the
tool-calling turn exports a nested span tree (agent → node → model → tool) carrying tokens, cost, and
latency. With the default ``console`` exporter those spans print to **stderr**, so you can watch the
tree while stdout stays the clean answer.

To ship the same trace to a hosted backend (Opik / LangFuse / LangSmith) instead, add its exporter to
the YAML and set that backend's keys — see ``tests/test_observability.py`` and ``.env.example``.

Usage (needs a real OPENAI_API_KEY in .env or the shell)::

    python demos/observability.py
    # spans stream to stderr; the final answer prints to stdout
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import litellm
from agentship import build_agent
from agentship.observability import NoOpObserver

litellm.disable_aiohttp_transport = True
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT = REPO_ROOT / "agents" / "observability.yaml"


async def main() -> int:
    """Run the traced calculator agent for one live turn and print its answer."""
    if not os.environ.get("OPENAI_API_KEY"):
        print("This is live — set OPENAI_API_KEY in .env (or the shell) to run it.")
        return 1

    agent = build_agent(str(AGENT))
    # The observability: block resolved to a real observer — tracing is on, no code required.
    traced = not isinstance(agent.observer, NoOpObserver)
    print(f"\nTracing is {'ON (observability: block resolved)' if traced else 'OFF'} — "
          "watch the span tree print to stderr below.\n", file=sys.stderr)

    result = await agent.run("What is 21 * 2? Use the calculator.", session_id="obs-demo")

    print(f"\nANSWER: {result.output.strip()}")
    print(
        "\nThe span tree above (stderr) is the full trace: agent → node → model → tool, with "
        "tokens/cost/latency. Point it at Opik/LangFuse/LangSmith by editing the YAML's "
        "`exporters:` and setting that backend's keys — see tests/test_observability.py.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    import asyncio

    sys.exit(asyncio.run(main()))
