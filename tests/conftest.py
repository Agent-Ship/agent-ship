"""Shared pytest setup for the demo suite — two tiers: keyless replay, and opt-in live.

The demo used to be live-only: every test called OpenAI for real, so a green run cost
money, needed network, and could never be a CI gate. That contradicted the operating
model, which requires every real-model path to be recorded once as a cassette and
replayed keylessly. This module fixes that by giving the suite two tiers:

**Replay (the default).** ``pytest`` replays committed VCR cassettes. No key, no
network, no spend — this is the tier CI gates on and the one a stranger who just
cloned the repo gets. A test whose cassette is missing FAILS loudly rather than
quietly reaching for the network.

**Live (opt-in).** ``pytest --live`` makes real calls, exactly as the demo always did,
so the "it really talks to OpenAI" showcase in the README is preserved. Re-record with
``pytest --live --record-mode=once``.

Both tiers run the *same* test bodies, so a passing replay is real evidence the demo
works — not a fake pass against a stub.
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import litellm
import pytest
import uvicorn
from agentship.auth import ApiKeyAuthProvider, EnvApiKeyStore
from agentship.runtime import build_agent
from agentship_service import AgentRegistry, create_app
from fastapi.testclient import TestClient

# LiteLLM's async path defaults to an aiohttp transport, which VCR cannot intercept.
# Forcing the plain httpx transport is what makes cassette replay possible at all, and
# it keeps real streaming turns consistent across environments.
litellm.disable_aiohttp_transport = True

# Keep the model cost map local so no extra HTTP fetch is needed at import time.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

# Captured before anything imports litellm, which loads a local `.env` and injects
# provider keys into os.environ. Without this snapshot, a dev box with a .env on disk
# looks identical to a keyed CI run, and the live-only guards below would misfire.
HAS_REAL_KEY_AT_STARTUP = bool(os.environ.get("OPENAI_API_KEY"))

# Used in the replay tier so LiteLLM can build a well-formed request for VCR to match.
# It is never sent anywhere: VCR intercepts the call and serves the recorded response.
PLACEHOLDER_KEY = "sk-keyless-replay-placeholder"


def pytest_addoption(parser):
    """Register ``--live``, which switches the suite from cassette replay to real calls."""
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="Call the real provider APIs instead of replaying cassettes (costs money).",
    )


def pytest_configure(config):
    """Declare markers and, in the replay tier, install the placeholder key.

    Overwriting the key during replay is deliberate: it guarantees a laptop with a
    populated ``.env`` takes the exact same code path as keyless CI, so "works on my
    machine" cannot hide a missing cassette.
    """
    config.addinivalue_line("markers", "live_only: needs a real key; skipped in the replay tier")

    if not config.getoption("--live"):
        os.environ["OPENAI_API_KEY"] = PLACEHOLDER_KEY


def pytest_collection_modifyitems(config, items):
    """Skip ``live_only`` tests unless ``--live`` was passed with a real key present."""
    if config.getoption("--live") and HAS_REAL_KEY_AT_STARTUP:
        return
    skip = pytest.mark.skip(reason="live-only: re-run with --live and a real OPENAI_API_KEY")
    for item in items:
        if "live_only" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="module")
def vcr_config():
    """VCR options for the recorded demo turns: redact every credential, replay by default.

    ``filter_headers`` and ``filter_query_parameters`` strip the credential from every
    channel a provider might use, so a committed cassette leaks nothing. ``match_on``
    omits the default ``query`` matcher — because the key query parameter is removed at
    record time, a keyless replay must still resolve to the recorded interaction.
    """
    return {
        "filter_headers": [
            ("authorization", "REDACTED"),
            ("api-key", "REDACTED"),
            ("x-api-key", "REDACTED"),
            ("x-goog-api-key", "REDACTED"),
        ],
        "filter_query_parameters": [("key", "REDACTED"), ("api_key", "REDACTED")],
        "match_on": ["method", "host", "path", "body"],
    }


#: Marks a test that can only run against a real backend (e.g. a hosted trace read-back,
#: where the assertion is "the vendor's API now shows our span" — there is nothing local
#: to record). Everything else should use ``@pytest.mark.vcr`` and replay keylessly.
live_only = pytest.mark.live_only

#: Backwards-compatible alias. The old suite used ``@requires_live_key`` on every test to
#: mean "skip without a key"; tests that have been converted to cassettes no longer need
#: it. Kept so any not-yet-converted test keeps its old skip behaviour instead of failing.
requires_live_key = pytest.mark.skipif(
    not HAS_REAL_KEY_AT_STARTUP,
    reason="not yet converted to a cassette — set OPENAI_API_KEY to run it live",
)


# --------------------------------------------------------------------------------------
# The served /v1 surface (Phases 06-09): one app, one agent, three callers.
#
# The service slices test the envelope around a turn — transports, error model, who may
# call, whose rows come back — so they run on the `echo` engine and need no key, no
# network and no cassette. Everything below is shared by test_service_endpoints.py,
# test_auth_demo.py, test_tenant_isolation_demo.py and test_posture_demo.py so all four
# describe the *same* deployment, and a difference in outcome is visibly the caller or
# the request, never a differently-configured app.
# --------------------------------------------------------------------------------------

#: The keyless agent the service slices serve, loaded from the demo's own spec file.
SERVICE_AGENT = Path(__file__).resolve().parents[1] / "agents" / "service" / "support.yaml"

#: The one browser origin this deployment allows. Any other origin is a CORS denial.
ALLOWED_ORIGIN = "https://demo.example"

#: Three API keys that differ only in *who they are*, which is what makes the auth and
#: tenant demos readable: `acme-key` and `beta-key` can both invoke but belong to
#: different tenants, and `reader-key` is a valid credential of tenant acme that was
#: never granted `agent:support:invoke`.
API_KEY_TABLE = json.dumps(
    [
        {"key": "acme-key", "user": "amy", "tenant": "acme", "scopes": ["agent:*:invoke"]},
        {"key": "beta-key", "user": "ben", "tenant": "beta", "scopes": ["agent:*:invoke"]},
        {"key": "reader-key", "user": "rita", "tenant": "acme", "scopes": ["agent:support:read"]},
    ]
)

ACME = {"x-api-key": "acme-key"}
BETA = {"x-api-key": "beta-key"}
READER = {"x-api-key": "reader-key"}


def build_service_app():
    """Build the served app the Phase 06-09 slices call: one echo agent, API-key auth, CORS.

    Uses the same public ``create_app`` factory ``agentship serve`` uses, so what these
    tests prove is what a deployment actually does.
    """
    agents = AgentRegistry([build_agent(str(SERVICE_AGENT))])
    auth = ApiKeyAuthProvider(EnvApiKeyStore(raw=API_KEY_TABLE))
    return create_app(auth=auth, agents=agents, cors_origins=[ALLOWED_ORIGIN])


@pytest.fixture
def service_client():
    """A client speaking to the served app in-process (no socket)."""
    return TestClient(build_service_app())


@contextmanager
def serve_on_loopback(app):
    """Run ``app`` under uvicorn on a free loopback port, yielding its base URL.

    The endpoint slice boots a real server rather than using ``TestClient`` alone,
    because ``TestClient`` calls the ASGI app directly: it would still pass if the app
    could not actually start, bind, or speak HTTP/1.1 and the WebSocket handshake over a
    socket. Port ``0`` lets the OS pick a free port, so parallel runs never collide.
    """
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("the service did not start within 10s")
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
