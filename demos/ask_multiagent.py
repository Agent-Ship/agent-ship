"""Ask the multi-agent panel your own question and watch the sub-agents get called.

One command, your own task: this fans the question out to all three specialist sub-agents
(billing / clinical / faq) concurrently, prints each named sub-agent's live answer as it
arrives (via the supervisor's decision log), then prints the resolver's final merged response.

Usage (needs a real OPENAI_API_KEY in .env or the shell)::

    python demos/ask_multiagent.py "My bill looks wrong and I feel dizzy — help?"
    # or: make ask INPUT="..."
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import litellm
from agentship import build_agent

litellm.disable_aiohttp_transport = True
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

REPO_ROOT = Path(__file__).resolve().parents[1]
PANEL = REPO_ROOT / "agents" / "triage" / "panel.yaml"


def _enable_supervisor_log() -> None:
    """Route the supervisor's classify/route/dispatch/resolve decisions to stdout."""
    log = logging.getLogger("agentship.supervisor")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("  | %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False


async def main() -> int:
    """Run the panel on the CLI question and print the sub-agent trace + final answer."""
    if not os.environ.get("OPENAI_API_KEY"):
        print("This is live — set OPENAI_API_KEY in .env (or the shell) to run it.")
        return 1

    question = " ".join(sys.argv[1:]) or "My bill looks wrong and I feel dizzy — can you help?"
    _enable_supervisor_log()

    print(f"\nTASK: {question}\n")
    print("Dispatching to all sub-agents in parallel — watch each one get called:\n")
    os.chdir(REPO_ROOT)  # the code: path in panel.yaml is repo-root-relative
    agent = build_agent(str(PANEL))
    result = await agent.run(question, session_id="ask-multiagent")

    print(f"\nFINAL RESPONSE (resolver's pick):\n  {result.output.strip()}\n")
    return 0


if __name__ == "__main__":
    import asyncio

    sys.exit(asyncio.run(main()))
