"""Shared pytest configuration — VCR settings for the live-proof cassettes.

``pytest-recording`` (vcrpy) records each real provider round-trip once into a
committed cassette and replays it in CI with no key. The ``vcr_config`` fixture
below is the safety net that makes committed cassettes safe: it **redacts the
``authorization`` header** (and a couple of other credential headers) so no secret
is ever written to disk, and pins ``record_mode`` so replay never silently calls
the network.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def vcr_config():
    """VCR options for recorded live tests: redact auth, replay by default.

    ``filter_headers`` replaces credential-bearing request headers with a constant
    placeholder in the stored cassette, so committing it leaks nothing. The record
    mode is left to pytest-recording's default (``none``) so a test without a
    cassette fails loudly rather than reaching for the network; recording is done
    explicitly by re-running with ``--record-mode=once`` (which overrides only when
    this fixture does not pin ``record_mode``).
    """
    return {
        "filter_headers": [
            ("authorization", "REDACTED"),
            ("api-key", "REDACTED"),
            ("x-api-key", "REDACTED"),
        ],
    }
