"""Shared pytest configuration — VCR settings for the live-proof cassettes.

``pytest-recording`` (vcrpy) records each real provider round-trip once into a
committed cassette and replays it in CI with no key. The ``vcr_config`` fixture
below is the safety net that makes committed cassettes safe: it **redacts every
provider's credential** — whether carried in a header or a URL query parameter —
so no secret is ever written to disk, and leaves ``record_mode`` at the default so
replay never silently calls the network.

Credentials travel differently per provider, so all channels are scrubbed:

- OpenAI (and most): ``Authorization: Bearer …`` header.
- Anthropic: ``x-api-key`` header (not ``authorization``).
- Gemini (LiteLLM → generativelanguage.googleapis.com): the key rides as an
  ``x-goog-api-key`` header *and/or* a ``?key=`` / ``api_key=`` query parameter on
  the request URI.

Because the ``key`` query parameter is stripped from stored cassettes, replay must
not depend on it to find a recording. So ``match_on`` deliberately excludes the
default ``query`` matcher and matches on **method + host + path + body** — a
keyless request still resolves to the recorded interaction.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def vcr_config():
    """VCR options for recorded live tests: redact every credential, replay by default.

    ``filter_headers`` replaces credential-bearing request headers
    (``authorization``, ``x-api-key``, ``x-goog-api-key``, ``api-key``) with a
    constant placeholder, and ``filter_query_parameters`` strips the Gemini/LiteLLM
    ``key`` / ``api_key`` URL parameters — so a committed cassette leaks nothing.
    ``match_on`` is set to method + host + path + body (omitting the default
    ``query`` matcher) so a keyless replay still matches even though the ``key``
    query parameter was removed at record time. The record mode is left to
    pytest-recording's default (``none``) so a test without a cassette fails loudly
    rather than reaching for the network; recording is done explicitly by
    re-running with ``--record-mode=once``.
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
