"""Watch a real AgentShip service boot and answer over all three transports (Phases 06-09).

This is the narrated version of ``tests/test_service_endpoints.py``. It launches the
shipped ``agentship serve`` command as a real subprocess on a loopback port, then calls
the same agent three ways and prints what comes back:

1. ``POST :invoke``  — one JSON result;
2. ``POST :stream``  — Server-Sent Events, printed frame by frame as they arrive;
3. ``/live`` WebSocket — the same turn over a socket that stays open for the next one.

Then it shows the security envelope around those calls: a request with no credential is
401, a valid key without the right scope is 403, and a malformed body comes back as an
RFC-9457 problem document.

It needs **no provider key**. The served agent (``agents/service/support.yaml``) runs on
the ``echo`` engine, because what this demo is about is the service surface, not the
model. The API keys below are the demo's own dev key table, handed to ``serve`` through
the environment variable the API-key auth provider reads.

Usage::

    python demos/serve_and_call.py
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from websockets.sync.client import connect as connect_websocket

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = REPO_ROOT / "agents" / "service"
AGENT = "support"

#: The dev key table `agentship serve`'s API-key provider reads from the environment.
#: `full-key` may invoke; `reader-key` is a valid credential that was never granted the
#: invoke scope — that is what makes the 403 below a scope failure, not a bad password.
API_KEYS = json.dumps(
    [
        {"key": "full-key", "user": "amy", "tenant": "acme", "scopes": ["agent:*:invoke"]},
        {"key": "reader-key", "user": "rita", "tenant": "acme", "scopes": ["agent:support:read"]},
    ]
)
CALLER = {"x-api-key": "full-key"}


def main() -> int:
    """Boot the server, exercise all three transports and the error envelope, then shut down."""
    port = free_port()
    print(f"\n$ agentship serve --agents-dir agents/service --port {port}\n")
    server = start_server(port)
    try:
        base_url = f"http://127.0.0.1:{port}"
        wait_until_live(base_url)
        print(f"The service is live on {base_url} (loopback only — serve's default host).\n")

        show_invoke(base_url)
        show_stream(base_url)
        show_websocket(base_url)
        show_error_envelope(base_url)
    finally:
        server.terminate()
        server.wait(timeout=10)
    print("\nServer stopped. Everything above ran with no provider key and no network.")
    return 0


def free_port() -> int:
    """Ask the OS for an unused loopback port so the demo never collides with a running app."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def start_server(port: int) -> subprocess.Popen:
    """Launch the shipped ``agentship serve`` command as a subprocess on ``port``.

    Running the real CLI (rather than building the app in-process) is the point: it
    proves the documented launch path works, doctor gate and all.
    """
    return subprocess.Popen(
        [
            str(Path(sys.executable).with_name("agentship")),
            "serve",
            "--agents-dir",
            str(AGENTS_DIR),
            "--port",
            str(port),
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "AGENTSHIP_API_KEYS": API_KEYS},
    )


def wait_until_live(base_url: str, timeout: float = 30.0) -> None:
    """Poll the unauthenticated liveness probe until the server answers, or give up."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base_url}/healthz", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.1)
    raise RuntimeError(f"the service did not come up within {timeout}s")


def show_invoke(base_url: str) -> None:
    """Call `POST :invoke` and print the single JSON result."""
    print("--- 1. POST /v1/agents/support:invoke — one JSON result ---")
    response = httpx.post(
        f"{base_url}/v1/agents/{AGENT}:invoke", headers=CALLER, json={"input": "hello"}
    )
    body = response.json()
    print(f"  {response.status_code}  output={body['output']!r}  session_id={body['session_id']}")
    print(f"  trace id (echoed in every response header): {response.headers['x-trace-id']}\n")


def show_stream(base_url: str) -> None:
    """Call `POST :stream` and print each SSE frame as it arrives."""
    print("--- 2. POST /v1/agents/support:stream — Server-Sent Events, frame by frame ---")
    with httpx.stream(
        "POST",
        f"{base_url}/v1/agents/{AGENT}:stream",
        headers=CALLER,
        json={"input": "hello"},
    ) as response:
        for line in response.iter_lines():
            if line.startswith("data:"):
                frame = json.loads(line.removeprefix("data:").strip())
                print(f"  seq={frame['seq']}  {frame['type']:<8} {frame['data']}")
    print()


def show_websocket(base_url: str) -> None:
    """Open the `/live` socket, send one turn, and print the frames streamed back."""
    print("--- 3. WebSocket /v1/agents/support/live — same turn, over one open socket ---")
    url = f"{base_url.replace('http://', 'ws://')}/v1/agents/{AGENT}/live"
    with connect_websocket(url, additional_headers=CALLER) as socket_client:
        socket_client.send(json.dumps({"input": "hello"}))
        while True:
            frame = json.loads(socket_client.recv(timeout=10))
            print(f"  seq={frame['seq']}  {frame['type']:<8} {frame['data']}")
            if frame["type"] in ("done", "error"):
                break
    print()


def show_error_envelope(base_url: str) -> None:
    """Print the three refusals a client must be able to tell apart: 401, 403 and 422."""
    print("--- 4. The envelope: who may call, and what a failure looks like ---")
    invoke_url = f"{base_url}/v1/agents/{AGENT}:invoke"

    no_key = httpx.post(invoke_url, json={"input": "hello"})
    print(f"  no credential            -> {no_key.status_code} {no_key.json()['code']}")

    wrong_scope = httpx.post(
        invoke_url, headers={"x-api-key": "reader-key"}, json={"input": "hello"}
    )
    print(f"  valid key, wrong scope   -> {wrong_scope.status_code} "
          f"{wrong_scope.json()['code']}")

    malformed = httpx.post(invoke_url, headers=CALLER, json={"not_the_input_field": 1})
    problem = malformed.json()
    print(f"  malformed body           -> {malformed.status_code} {problem['code']}")
    print(f"    content-type: {malformed.headers['content-type']}")
    print(f"    {problem['title']}: {problem['detail']}")


if __name__ == "__main__":
    raise SystemExit(main())
