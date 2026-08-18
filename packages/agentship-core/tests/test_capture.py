"""Record/replay capture: stable request hash + the PHI content gate (P07 · §4.10).

Pure functions, so these pin the contract P12 depends on: identical requests hash equal, any
output-affecting change hashes different, and request/response content is emitted only when
``capture_content`` is set.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentship.observability import replay_attributes, request_hash, semconv


@dataclass
class _Message:
    role: str
    content: str


@dataclass
class _Choice:
    message: _Message


@dataclass
class _Response:
    choices: list[_Choice]


def _kwargs(**over):
    """A representative LiteLLM success ``kwargs`` with an optional override."""
    base = {
        "model": "openai/gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "optional_params": {"temperature": 0.2},
    }
    base.update(over)
    return base


def _response() -> _Response:
    """A minimal response carrying one assistant message."""
    return _Response(choices=[_Choice(message=_Message(role="assistant", content="hi there"))])


def test_identical_requests_hash_equal() -> None:
    """The same request produces the same hash — the cassette key is stable."""
    assert request_hash(_kwargs()) == request_hash(_kwargs())


def test_message_change_changes_hash() -> None:
    """A different prompt yields a different hash."""
    other = _kwargs(messages=[{"role": "user", "content": "goodbye"}])
    assert request_hash(_kwargs()) != request_hash(other)


def test_param_change_changes_hash() -> None:
    """A different sampling parameter yields a different hash."""
    other = _kwargs(optional_params={"temperature": 0.9})
    assert request_hash(_kwargs()) != request_hash(other)


def test_hash_ignores_incidental_metadata() -> None:
    """Non-output-affecting callback metadata does not perturb the hash."""
    noisy = _kwargs(litellm_call_id="abc-123", start_time="now")
    assert request_hash(_kwargs()) == request_hash(noisy)


def test_capture_off_emits_only_the_hash() -> None:
    """The PHI default records the hash but never the request/response content."""
    attrs = replay_attributes(_kwargs(), _response(), capture_content=False)
    assert semconv.AS_REPLAY_REQUEST_HASH in attrs
    assert semconv.GEN_AI_INPUT_MESSAGES not in attrs
    assert semconv.GEN_AI_OUTPUT_MESSAGES not in attrs


def test_capture_on_emits_request_and_response() -> None:
    """With capture enabled, the canonicalized request and response are included for replay."""
    attrs = replay_attributes(_kwargs(), _response(), capture_content=True)
    assert semconv.AS_REPLAY_REQUEST_HASH in attrs
    assert "hello" in attrs[semconv.GEN_AI_INPUT_MESSAGES]
    assert "hi there" in attrs[semconv.GEN_AI_OUTPUT_MESSAGES]
