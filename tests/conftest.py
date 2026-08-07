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

import pytest


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
