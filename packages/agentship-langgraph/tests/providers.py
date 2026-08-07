"""The live-provider test matrix — proof that AgentShip can swap any provider.

Each :class:`Provider` names one LiteLLM provider we exercise end to end: the
LiteLLM ``model`` id passed to a ``langgraph`` agent, and the environment variable
that carries its API credential. :data:`LIVE_PROVIDERS` is the source of truth the
parametrized live tests iterate over; each entry records its own cassette under
``tests/cassettes/test_providers/<name>.yaml`` and replays it keyless in CI.

Adding a provider is a two-step recipe (see ``examples/README.md``):

1. Append a :class:`Provider` here with the provider name, a cheap LiteLLM model
   id, and its credential env var.
2. Record its cassette once (``set -a; source <keys>.env; set +a`` then
   ``pytest tests/test_providers.py --record-mode=once``), grep the cassette for
   leaked key material, and commit it. CI then replays it with no key.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    """One provider in the live matrix: its name, LiteLLM model id, and key env var.

    ``name`` is a short slug used for the cassette filename and test id.
    ``model`` is the LiteLLM model id handed to the ``langgraph`` agent (e.g.
    ``"anthropic/claude-3-5-haiku-latest"``). ``env_var`` is the environment
    variable that carries the provider's API credential — set for recording,
    absent (or a harmless placeholder) for keyless replay.
    """

    name: str
    model: str
    env_var: str


#: The providers we record live cassettes for and replay keyless in CI. Each has a
#: real recorded round-trip; the list is the single place to extend coverage.
LIVE_PROVIDERS: list[Provider] = [
    Provider(name="openai", model="openai/gpt-4o-mini", env_var="OPENAI_API_KEY"),
    Provider(
        name="anthropic",
        model="anthropic/claude-3-5-haiku-latest",
        env_var="ANTHROPIC_API_KEY",
    ),
    Provider(
        name="gemini", model="gemini/gemini-2.0-flash-lite", env_var="GEMINI_API_KEY"
    ),
    # To add a provider: append an entry, record its cassette (see module
    # docstring / examples/README.md), grep for key leakage, and commit. The two
    # below are wired but have no cassette yet — add a key to record them:
    # Provider(name="groq", model="groq/llama-3.1-8b-instant", env_var="GROQ_API_KEY"),
    # Provider(name="mistral", model="mistral/mistral-small-latest", env_var="MISTRAL_API_KEY"),
]
