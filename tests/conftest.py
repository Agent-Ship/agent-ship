"""Shared pytest setup for the LIVE demo suite.

Every test in this repo makes a real call to the OpenAI API — there are no fakes,
no recordings, and no offline stand-ins. This module holds the two small pieces of
setup that every live test shares:

* the ``requires_live_key`` marker helper, which skips a test cleanly when no
  ``OPENAI_API_KEY`` is set (so the suite never fake-passes and never hard-errors
  without a key), and
* the LiteLLM transport tweak the framework needs to talk to the provider.

With a key set, every test runs for real against ``gpt-4o-mini``. Without a key,
every test skips.
"""

from __future__ import annotations

import os

import litellm
import pytest

# LiteLLM's async path defaults to an aiohttp transport; force the plain httpx
# transport so real streaming turns behave consistently across environments.
litellm.disable_aiohttp_transport = True

# Keep the model cost map local so no extra HTTP fetch is needed at import time.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")


#: Reusable skip guard: with a key the test runs live; without one it skips cleanly.
#: Apply it as ``@requires_live_key`` on any test that calls the real API.
requires_live_key = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="the demo is live — set OPENAI_API_KEY to run it",
)
