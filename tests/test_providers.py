"""T9 live proof (G5): the "swap any provider" claim, across the whole matrix.

For every :class:`~tests.providers.Provider` in
:data:`~tests.providers.LIVE_PROVIDERS` this parametrizes one live turn: build a
single ``langgraph`` agent on that provider's model, ``run("ping")``, and assert a
non-empty string answer. Each provider records to its own cassette under
``tests/cassettes/test_providers/<name>.yaml`` and replays **keyless** in CI, so
the matrix proves the wiring reaches OpenAI, Anthropic, and Gemini — not just one.

Recording (one short call per provider, no looping):

    set -a; source ../agent-ship/.env; set +a
    pytest tests/test_providers.py --record-mode=once
"""

from __future__ import annotations

import os
from pathlib import Path

import litellm
import pytest
from tests.providers import LIVE_PROVIDERS, Provider

from agentship.runtime import build_agent
from agentship.spec import AgentSpec

#: Directory holding one recorded cassette per provider (``<name>.yaml``).
CASSETTE_DIR = Path(__file__).resolve().parent / "cassettes" / "test_providers"

# LiteLLM's async path defaults to an aiohttp transport vcrpy cannot intercept;
# forcing the plain httpx transport lets every provider cassette record and replay.
litellm.disable_aiohttp_transport = True

# Keep the cost map local so no extra HTTP fetch pollutes any cassette.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")


@pytest.fixture
def provider_key_for_replay(request, monkeypatch):
    """Inject a harmless placeholder key for the provider under test when none is set.

    Provider SDKs refuse to construct a client without a key — even to replay a
    recorded response. On keyless replay (CI) this sets a placeholder in the
    provider's env var so the client builds; VCR matches on method+host+path+body
    (not the redacted credential), so replay is unaffected. When a real key is
    present (recording), it is left untouched. ``request.param`` is the
    :class:`Provider` supplied by the test's parametrization.
    """
    provider: Provider = request.param
    if not os.environ.get(provider.env_var):
        monkeypatch.setenv(provider.env_var, "placeholder-key-for-replay")
    return provider


@pytest.fixture
def default_cassette_name(request):
    """Name each provider's cassette after the provider, not the parametrized id.

    Overrides pytest-recording's default (which would embed the full ``[param]``
    id) so cassettes land at ``tests/cassettes/test_providers/<name>.yaml`` — one
    stable file per provider, matching the recipe in ``examples/README.md``.
    """
    provider: Provider = request.getfixturevalue("provider_key_for_replay")
    return provider.name


@pytest.mark.vcr
@pytest.mark.parametrize(
    "provider_key_for_replay",
    LIVE_PROVIDERS,
    ids=[p.name for p in LIVE_PROVIDERS],
    indirect=True,
)
async def test_provider_returns_a_non_empty_answer(request, provider_key_for_replay):
    """Each live provider returns a non-empty string answer (replayed via cassette).

    A provider whose cassette has not been recorded yet (e.g. no real key was
    available at record time) is skipped with an actionable reason rather than
    failing the keyless suite — so the matrix documents the gap honestly instead of
    silently dropping the provider or reaching for the network. During an explicit
    recording run (``--record-mode``) the test proceeds so the cassette can be
    created.
    """
    provider: Provider = provider_key_for_replay
    cassette = CASSETTE_DIR / f"{provider.name}.yaml"
    recording = request.config.getoption("--record-mode") not in (None, "none")
    if not cassette.exists() and not recording:
        pytest.skip(
            f"no cassette for provider {provider.name!r} yet — set {provider.env_var} "
            f"and run `pytest tests/test_providers.py --record-mode=once` to record it"
        )
    agent = build_agent(
        AgentSpec(
            name=provider.name,
            engine="langgraph",
            model=provider.model,
            prompt="Reply with the single word: pong.",
        )
    )
    result = await agent.run("ping")
    assert isinstance(result.output, str)
    assert result.output.strip() != ""
