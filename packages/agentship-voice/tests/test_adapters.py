"""Choosing an adapter by name, and what happens when the framework is absent."""

from __future__ import annotations

import pytest
from agentship_voice.adapters import VoiceAdapter, get_adapter


@pytest.mark.parametrize("name", ["pipecat", "livekit"])
def test_a_framework_name_resolves_to_its_adapter(name: str) -> None:
    """``voice.framework`` selects the adapter; both implement the same contract."""
    pytest.importorskip(
        {"pipecat": "pipecat", "livekit": "livekit.agents"}[name],
        reason=f"needs the [{name}] extra",
    )
    adapter = get_adapter(name)
    assert isinstance(adapter, VoiceAdapter)
    assert adapter.name == name


def test_an_unknown_framework_names_the_real_choices() -> None:
    """A typo fails here, listing what exists, rather than deep inside a session."""
    with pytest.raises(ValueError) as raised:
        get_adapter("pipcat")
    message = str(raised.value)
    assert "pipcat" in message
    assert "pipecat" in message and "livekit" in message
