"""T9 demo proof (G5): every ``examples/providers/*.yaml`` builds and runs.

Two paths, mirroring the assistant example:

- **Offline** — each per-provider example is loaded and run with a fake chat model
  injected (no network), proving the YAML's wiring end to end for every provider in
  the matrix. This exercises each provider's model id through ``build_agent`` /
  the LangGraph engine without a key.
- **Live** — for a provider whose cassette has been recorded, the same example is
  run against the real provider (replayed keyless via its cassette). Providers
  without a cassette skip with an actionable reason instead of failing.

The example files and the :data:`~providers.LIVE_PROVIDERS` matrix are kept
in lockstep: every provider has one ``examples/providers/<name>.yaml``.
"""

from __future__ import annotations

import os
from pathlib import Path

import agentship_langgraph.models as models_module
import litellm
import pytest
from agentship.runtime import build_agent
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from providers import LIVE_PROVIDERS, Provider

# See tests/test_live_model.py for why these two lines are needed for VCR replay.
litellm.disable_aiohttp_transport = True
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "examples" / "providers"
CASSETTE_DIR = Path(__file__).resolve().parent / "cassettes" / "test_providers"


def _example_path(provider: Provider) -> str:
    """Return the ``examples/providers/<name>.yaml`` path for ``provider``."""
    return str(EXAMPLES_DIR / f"{provider.name}.yaml")


def test_every_provider_has_an_example_file():
    """Each provider in the matrix ships a matching examples/providers/<name>.yaml."""
    for provider in LIVE_PROVIDERS:
        assert Path(_example_path(provider)).exists(), (
            f"missing example file for provider {provider.name!r}"
        )


@pytest.mark.parametrize(
    "provider", LIVE_PROVIDERS, ids=[p.name for p in LIVE_PROVIDERS]
)
async def test_provider_example_runs_offline_with_fake_model(provider, monkeypatch):
    """Each provider example builds and runs, returning the injected fake's answer."""
    fake = FakeListChatModel(responses=["pong"])
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: fake)

    agent = build_agent(_example_path(provider))
    assert agent.spec.engine == "langgraph"
    assert agent.spec.model == provider.model
    result = await agent.run("ping")
    assert result.output == "pong"


@pytest.fixture
def provider_key_for_replay(request, monkeypatch):
    """Inject a placeholder key for the provider under test when none is set.

    Lets the provider SDK build its client during keyless replay; VCR matches on
    method+host+path+body (not the redacted credential), so replay is unaffected.
    ``request.param`` is the :class:`Provider` supplied by the parametrization.
    """
    provider: Provider = request.param
    if not os.environ.get(provider.env_var):
        monkeypatch.setenv(provider.env_var, "placeholder-key-for-replay")
    return provider


@pytest.fixture
def vcr_cassette_dir():
    """Point replay at the shared per-provider cassette dir (``test_providers``).

    pytest-recording defaults the directory to this module's name; override it so
    the example's live path reuses the very cassettes recorded by
    ``test_providers.py`` instead of expecting a second copy.
    """
    return str(CASSETTE_DIR)


@pytest.fixture
def default_cassette_name(request):
    """Reuse the per-provider cassette recorded by ``test_providers.py``.

    Both the live matrix test and this example test replay the same
    ``tests/cassettes/test_providers/<name>.yaml`` — recorded once per provider —
    so the example's live path needs no separate recording.
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
async def test_provider_example_runs_live_via_cassette(request, provider_key_for_replay):
    """Each provider example runs against its real provider (replayed via cassette).

    A provider whose cassette is not yet recorded is skipped with an actionable
    reason rather than failing the keyless suite.
    """
    provider: Provider = provider_key_for_replay
    cassette = CASSETTE_DIR / f"{provider.name}.yaml"
    recording = request.config.getoption("--record-mode") not in (None, "none")
    if not cassette.exists() and not recording:
        pytest.skip(
            f"no cassette for provider {provider.name!r} yet — set {provider.env_var} "
            f"and run `pytest tests/test_providers.py --record-mode=once` to record it"
        )
    agent = build_agent(_example_path(provider))
    result = await agent.run("ping")
    assert isinstance(result.output, str)
    assert result.output.strip() != ""
