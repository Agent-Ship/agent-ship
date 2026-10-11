"""T2 proof: ``resolve_model`` builds a configured ``ChatLiteLLM`` (the model seam).

These assert the real mechanism: the returned object is a ``ChatLiteLLM`` carrying
the requested model id and that supplied params (temperature/max_tokens/api_base/
timeout) are threaded onto it, ``None``s are dropped, and an empty/missing model
raises :class:`~agentship.errors.SpecError`.
"""

from __future__ import annotations

import pytest
from agentship.errors import SpecError
from agentship_langgraph.models import resolve_model
from langchain_litellm import ChatLiteLLM


def test_resolve_model_enables_streaming():
    """The result is streaming-enabled so ``stream_mode="messages"`` gets real tokens.

    Without ``streaming=True`` ``ChatLiteLLM`` returns one whole ``AIMessage`` per
    turn, so the engine's ``AIMessageChunk`` filter emits zero content events
    against a real provider. Building it streaming here is what makes ``--stream``
    deliver token-by-token output.
    """
    model = resolve_model("openai/gpt-4o-mini")
    assert model.streaming is True


def test_resolve_model_builds_chatlitellm_with_model_and_temperature():
    """The result is a ChatLiteLLM carrying the requested model id + temperature."""
    model = resolve_model("openai/gpt-4o-mini", temperature=0.2)
    assert isinstance(model, ChatLiteLLM)
    assert model.model == "openai/gpt-4o-mini"
    assert model.temperature == 0.2


def test_resolve_model_threads_supported_params():
    """max_tokens, api_base and timeout thread onto the model when supplied."""
    model = resolve_model(
        "openai/gpt-4o-mini",
        max_tokens=128,
        api_base="https://example.test/v1",
        timeout=30,
    )
    assert model.max_tokens == 128
    assert model.api_base == "https://example.test/v1"
    assert model.request_timeout == 30


def test_resolve_model_drops_none_params():
    """A param passed as None is dropped, so the model matches one built without it.

    Asserts the drop mechanism directly: threading ``temperature=None`` must leave
    the model identical to omitting it (the ChatLiteLLM default), while a real
    value still lands — so the ``None`` was never forwarded as an override.
    """
    default = resolve_model("openai/gpt-4o-mini")
    dropped = resolve_model("openai/gpt-4o-mini", temperature=None, max_tokens=None)
    assert dropped.temperature == default.temperature
    assert dropped.max_tokens == default.max_tokens

    supplied = resolve_model("openai/gpt-4o-mini", temperature=0.7)
    assert supplied.temperature == 0.7


def test_resolve_model_empty_model_raises_spec_error():
    """An empty model string is a spec error, raised before touching the provider."""
    with pytest.raises(SpecError):
        resolve_model("")


def test_resolve_model_whitespace_model_raises_spec_error():
    """A whitespace-only model is also rejected as empty."""
    with pytest.raises(SpecError):
        resolve_model("   ")


def test_streaming_is_on_by_default() -> None:
    """Token-by-token output is the default, because every hosted provider supports it."""
    assert resolve_model("openai/gpt-4o-mini").streaming is True


def test_a_spec_can_turn_streaming_off() -> None:
    """Required for endpoints that do not really stream, and the symptom is not slow output.

    A streamed tool call arrives in fragments the client reassembles. An endpoint that buffers
    its whole reply into one piece gives it nothing to reassemble, so the call is **dropped
    with no error** and the agent answers as though it had no tools. Measured against a
    buffering gateway: streaming on → 0 tool calls, off → 1.

    Before this field existed `streaming=True` was hardcoded, so there was no way to use tools
    against such an endpoint at all.
    """
    assert resolve_model("openai/gpt-4o-mini", streaming=False).streaming is False


def test_turning_streaming_off_does_not_disturb_the_other_params() -> None:
    """It travels as a constructor field, not into ``model_kwargs`` where it would be ignored."""
    model = resolve_model(
        "openai/gpt-4o-mini", streaming=False, temperature=0.2, api_base="http://gw/v1"
    )
    assert model.streaming is False
    assert model.temperature == 0.2
    assert model.api_base == "http://gw/v1"
