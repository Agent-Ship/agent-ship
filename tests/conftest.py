"""Shared pytest configuration — VCR settings for the demo's live-proof cassette.

This mirrors the AgentShip framework's own ``vcr_config`` so the demo's smoke test
records a real provider round-trip once and replays it keyless in CI. The fixture
is the safety net that makes a committed cassette safe: it redacts every credential
(header *and* URL query parameter) so no secret is written to disk, and leaves
``record_mode`` at pytest-recording's default so replay never silently reaches the
network.

Because the ``key`` query parameter is stripped from stored cassettes, ``match_on``
deliberately omits the default ``query`` matcher and matches on
method + host + path + body — a keyless request still resolves to the recording.
"""

from __future__ import annotations

import os

import litellm
import pytest

# LiteLLM's async path defaults to an aiohttp transport that vcrpy (httpx/urllib3
# based) cannot intercept. Forcing the plain httpx transport lets cassettes both
# record and replay the real round-trip. Set once here for every cassette test.
litellm.disable_aiohttp_transport = True

# Keep the cost map local so no extra HTTP fetch pollutes any cassette (and so
# replay never reaches for githubusercontent).
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")


@pytest.fixture
def openai_key_for_replay(monkeypatch):
    """Inject a placeholder OpenAI key when none is set, so replay's client builds.

    The OpenAI SDK refuses to construct a client without a key — even just to replay
    a recorded response. When no real key is present (CI / keyless replay), inject a
    harmless placeholder; VCR matches on URI + body, not the (redacted) auth header,
    so replay is unaffected. When a real key *is* present (recording), it is left
    untouched. Shared by every cassette-backed slice (assistant, graph, custom).
    """
    if not os.environ.get("OPENAI_API_KEY"):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-placeholder-for-replay")


@pytest.fixture(scope="module")
def vcr_config():
    """VCR options for the recorded live test: redact every credential, replay by default.

    ``filter_headers`` replaces credential-bearing request headers with a constant
    placeholder, and ``filter_query_parameters`` strips the Gemini/LiteLLM ``key`` /
    ``api_key`` URL parameters — so a committed cassette leaks nothing. ``match_on``
    is method + host + path + body (omitting ``query``) so a keyless replay still
    matches even though the ``key`` parameter was removed at record time. The record
    mode is left to pytest-recording's default (``none``), so a test without a
    cassette fails loudly rather than reaching for the network; recording is done
    explicitly by re-running with ``--record-mode=once``.
    """
    return {
        "filter_headers": [
            ("authorization", "REDACTED"),
            ("api-key", "REDACTED"),
            ("x-api-key", "REDACTED"),
            ("x-goog-api-key", "REDACTED"),
        ],
        "filter_query_parameters": [
            ("key", "REDACTED"),
            ("api_key", "REDACTED"),
        ],
        "match_on": ["method", "host", "path", "body"],
    }
