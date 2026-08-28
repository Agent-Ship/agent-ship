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

import os

import litellm
import pytest

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
