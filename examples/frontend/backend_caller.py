"""Server-to-server sample: call AgentShip with an API key, invoke then stream one turn.

This is the simplest integration — a trusted backend holding a service API key calls the
``/v1`` surface directly. Run the service first (``agentship serve --agents-dir examples``
or ``make docker-up``) with an API key in ``AGENTSHIP_API_KEYS``, then::

    AGENTSHIP_BASE_URL=http://localhost:8000 AGENTSHIP_API_KEY=dev \\
        python examples/frontend/backend_caller.py hello "hello there"

It prints the structured invoke response, then the SSE event frames of a streamed turn.
Never ship this key to a browser — for browser apps use the BFF pattern (see ``bff/``).
"""

from __future__ import annotations

import json
import os
import sys

import httpx


def invoke(base_url: str, api_key: str, agent: str, text: str) -> None:
    """Call ``POST /v1/agents/{agent}:invoke`` and print the structured response."""
    resp = httpx.post(
        f"{base_url}/v1/agents/{agent}:invoke",
        headers={"x-api-key": api_key, "content-type": "application/json"},
        json={"input": text},
        timeout=30.0,
    )
    resp.raise_for_status()
    print("invoke ->", json.dumps(resp.json(), indent=2))


def stream(base_url: str, api_key: str, agent: str, text: str) -> None:
    """Call ``POST /v1/agents/{agent}:stream`` and print each SSE event frame as it arrives."""
    print("stream ->")
    with httpx.stream(
        "POST",
        f"{base_url}/v1/agents/{agent}:stream",
        headers={"x-api-key": api_key, "accept": "text/event-stream"},
        json={"input": text},
        timeout=30.0,
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if line.startswith("data:"):
                print("  ", line[len("data:") :].strip())


def main() -> None:
    """Read the agent name + text from argv (defaults ``hello`` / ``hello there``) and run both."""
    base_url = os.environ.get("AGENTSHIP_BASE_URL", "http://localhost:8000")
    api_key = os.environ.get("AGENTSHIP_API_KEY", "dev")
    agent = sys.argv[1] if len(sys.argv) > 1 else "hello"
    text = sys.argv[2] if len(sys.argv) > 2 else "hello there"
    invoke(base_url, api_key, agent, text)
    stream(base_url, api_key, agent, text)


if __name__ == "__main__":
    main()
