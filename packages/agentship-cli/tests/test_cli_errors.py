"""T7 proof (G3): a failed ``agentship run`` prints a clean error, never a traceback.

These tests are fully offline — no network, no real provider. They register a tiny
test-only engine whose ``run``/``stream`` raise on demand (a
:class:`~agentship.errors.ModelError` for the credential case, a generic
``RuntimeError`` for the unexpected case), drive the CLI with click's ``CliRunner``,
and assert:

- a failed run exits ``1`` with a single ``Error: …`` line and NO
  ``Traceback (most recent call last)`` in the output;
- the ``ModelError`` message (which names the provider env var) reaches the user;
- an unexpected error shows the ``--debug`` hint, and ``--debug`` surfaces the trace.

A separate unit test proves the credential-mapping helper turns a LiteLLM-style
auth error into an actionable ``ModelError`` naming the env var — without any
network call.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from agentship.engines.base import ENGINES, Engine, EngineCapabilities, Event, Result
from agentship.errors import ModelError
from agentship_cli.main import main
from click.testing import CliRunner


class _RaisingEngine(Engine):
    """A test-only engine whose ``run``/``stream`` raise a preconfigured exception.

    The exception to raise is read from the class attribute :attr:`to_raise` so a
    test can pick the failure mode (a ``ModelError`` vs. a generic ``RuntimeError``)
    without a live provider. ``build`` returns a trivial marker; it is never used
    beyond being handed back to ``run``/``stream``.
    """

    name = "raising"
    capabilities = EngineCapabilities(streaming=True)
    #: The exception instance ``run``/``stream`` raise; set per test.
    to_raise: Exception = RuntimeError("boom")

    def build(self, spec):
        """Return a trivial compiled marker (this engine holds no real state)."""
        return object()

    async def run(self, compiled, text, ctx) -> Result:
        """Raise the configured exception to exercise the CLI's error handling."""
        raise self.to_raise

    async def stream(self, compiled, text, ctx) -> AsyncIterator[Event]:
        """Raise the configured exception while streaming (before yielding)."""
        raise self.to_raise
        yield  # pragma: no cover - makes this an async generator


@pytest.fixture
def raising_engine():
    """Register ``_RaisingEngine`` for the duration of a test, then unregister it.

    Yields the class so a test can set :attr:`_RaisingEngine.to_raise`. Cleans up
    the registry entry afterwards so no other test sees the test-only engine.
    """
    ENGINES.register(_RaisingEngine.name, _RaisingEngine)
    try:
        yield _RaisingEngine
    finally:
        ENGINES._providers.pop(_RaisingEngine.name, None)  # type: ignore[attr-defined]


def _spec_file(tmp_path):
    """Write a minimal spec on the test-only ``raising`` engine and return its path."""
    path = tmp_path / "raising.yaml"
    path.write_text("name: r\nengine: raising\nmodel: openai/gpt-4o-mini\n")
    return str(path)


def test_model_error_prints_clean_actionable_message_no_traceback(raising_engine, tmp_path):
    """A ``ModelError`` from the engine → exit 1, its message on stderr, no traceback."""
    raising_engine.to_raise = ModelError(
        "No API credentials for model 'openai/gpt-4o-mini'. Set OPENAI_API_KEY "
        "(export it or add it to a .env you source) and retry."
    )
    result = CliRunner().invoke(main, ["run", _spec_file(tmp_path), "--input", "hi"])

    assert result.exit_code == 1
    assert "Traceback (most recent call last)" not in result.output
    assert "Error: No API credentials for model 'openai/gpt-4o-mini'." in result.stderr
    assert "Set OPENAI_API_KEY" in result.stderr


def test_generic_error_shows_debug_hint_and_no_traceback(raising_engine, tmp_path):
    """A generic ``RuntimeError`` → exit 1, concise message + --debug hint, no traceback."""
    raising_engine.to_raise = RuntimeError("something odd happened")
    result = CliRunner().invoke(main, ["run", _spec_file(tmp_path), "--input", "hi"])

    assert result.exit_code == 1
    assert "Traceback (most recent call last)" not in result.output
    assert "Error: something odd happened" in result.stderr
    assert "run with --debug for the full traceback" in result.stderr


def test_debug_flag_surfaces_the_full_traceback(raising_engine, tmp_path):
    """With ``--debug`` the original exception propagates so the traceback shows."""
    raising_engine.to_raise = RuntimeError("something odd happened")
    result = CliRunner().invoke(main, ["run", _spec_file(tmp_path), "--input", "hi", "--debug"])

    assert result.exit_code != 0
    assert result.exception is not None
    assert isinstance(result.exception, RuntimeError)


def test_credential_mapping_raises_model_error_naming_the_env_var():
    """A LiteLLM-style auth error maps to a ``ModelError`` naming the provider env var.

    Simulates the underlying failure without a network call by handing the mapping
    helper an exception whose message is the real LiteLLM 'Missing credentials'
    text, and asserts the mapped error is actionable and names ``OPENAI_API_KEY``.
    """
    from agentship_langgraph.models import map_model_error

    underlying = RuntimeError(
        "litellm.InternalServerError: OpenAIException - Missing credentials. "
        "Please pass an `api_key`, or set the `OPENAI_API_KEY` environment variable."
    )
    mapped = map_model_error("openai/gpt-4o-mini", underlying)

    assert isinstance(mapped, ModelError)
    assert "No API credentials for model 'openai/gpt-4o-mini'." in str(mapped)
    assert "OPENAI_API_KEY" in str(mapped)
