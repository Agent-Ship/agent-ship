"""The LiveKit session runner: provider plugins, preflight, and one conversation per room.

No LiveKit server and no provider is contacted: plugin classes, the session and the worker are
replaced by recorders, so these prove what AgentShip hands LiveKit, not what LiveKit does with it.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from agentship.errors import CapabilityError
from agentship.spec import VoiceSpec

pytest.importorskip("livekit.agents", reason="needs the [livekit] extra")

import livekit.agents as lk  # noqa: E402
from agentship_voice import factories, livekit_providers  # noqa: E402
from agentship_voice.adapters.livekit_adapter import LiveKitAdapter  # noqa: E402
from agentship_voice.turn import VoiceTurn  # noqa: E402

_CREDENTIALS = {
    "LIVEKIT_URL": "ws://localhost:7880",
    "LIVEKIT_API_KEY": "devkey",
    "LIVEKIT_API_SECRET": "secret",
}


class _Recorder:
    """A stand-in plugin class that keeps the arguments it was built with."""

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    @classmethod
    def load(cls, **kwargs):
        """Silero's constructor."""
        return cls(**kwargs)


@pytest.fixture
def plugins(monkeypatch):
    """Make every provider's plugin a :class:`_Recorder`, with its key set."""
    monkeypatch.setattr(livekit_providers, "_load", lambda plugin, kind, name: _Recorder)
    for table in (livekit_providers.STT_PLUGINS, livekit_providers.TTS_PLUGINS):
        for plugin in table.values():
            monkeypatch.setenv(plugin.env_var, f"key-for-{plugin.env_var}")


def _voice(**overrides) -> VoiceSpec:
    return VoiceSpec(framework="livekit", **overrides)


def test_tts_settings_go_under_the_plugins_own_names(plugins) -> None:
    """Cartesia calls the voice ``voice``; the spec's ``voice_id`` must arrive there."""
    tts = livekit_providers.make_tts(
        _voice(tts="cartesia", voice_id="v-1", tts_model="sonic-3", tts_speed=1.2, language="en")
    )
    assert tts.kwargs == {
        "voice": "v-1",
        "model": "sonic-3",
        "speed": 1.2,
        "language": "en",
        "api_key": "key-for-CARTESIA_API_KEY",
    }


def test_each_provider_spells_the_voice_its_own_way(plugins) -> None:
    """ElevenLabs takes ``voice_id``, Rime ``speaker``: one spec field, two argument names."""
    assert livekit_providers.make_tts(_voice(tts="elevenlabs", voice_id="x")).kwargs["voice_id"]
    assert livekit_providers.make_tts(_voice(tts="rime", voice_id="x")).kwargs["speaker"] == "x"


def test_a_setting_the_plugin_lacks_is_logged_not_passed(plugins, caplog) -> None:
    """Deepgram's TTS has no voice argument; passing one under a guessed name would break it."""
    with caplog.at_level(logging.WARNING, logger="agentship.voice"):
        tts = livekit_providers.make_tts(_voice(tts="deepgram", voice_id="ignored"))
    assert "voice" not in tts.kwargs and "voice_id" not in tts.kwargs
    assert "voice.voice_id" in caplog.text


def test_unset_settings_leave_the_plugin_defaults(plugins) -> None:
    """Nothing the spec leaves unset is passed, so each plugin keeps its own default."""
    stt = livekit_providers.make_stt(_voice(stt="deepgram"))
    assert stt.kwargs == {"api_key": "key-for-DEEPGRAM_API_KEY"}


def test_openai_realtime_selects_the_plugins_realtime_mode(plugins) -> None:
    """One plugin, two names: the mode is a fixed argument, and the hint becomes ``prompt``."""
    stt = livekit_providers.make_stt(
        _voice(stt="openai-realtime", stt_hint="Metformin", language="en")
    )
    assert stt.kwargs["use_realtime"] is True
    assert stt.kwargs["prompt"] == "Metformin"
    assert livekit_providers.make_stt(_voice(stt="openai")).kwargs["use_realtime"] is False


def test_vad_is_built_with_silero_load(plugins) -> None:
    """Silero is built through ``VAD.load()``, not its constructor."""
    assert isinstance(livekit_providers.make_vad(_voice()), _Recorder)


def test_an_unknown_provider_names_the_real_choices() -> None:
    with pytest.raises(CapabilityError, match="available: .*deepgram"):
        livekit_providers.make_stt(_voice(stt="nope"))


def test_a_missing_key_names_the_variable(plugins, monkeypatch) -> None:
    monkeypatch.delenv("CARTESIA_API_KEY")
    with pytest.raises(CapabilityError, match="CARTESIA_API_KEY"):
        livekit_providers.make_tts(_voice(tts="cartesia"))


def test_turn_taking_settings_reach_livekit() -> None:
    """Silence, barge-in and its word floor: the three settings that shape a conversation."""
    options = livekit_providers.turn_handling(
        _voice(endpoint_silence_ms=900, allow_interruptions=False, interrupt_min_words=3)
    )
    assert options == {
        "endpointing": {"min_delay": 0.9},
        "interruption": {"enabled": False, "min_words": 3},
    }


def test_preflight_dispatches_on_the_framework(monkeypatch) -> None:
    """A LiveKit spec is checked against LiveKit's plugins and credentials, not Pipecat's."""
    for var in _CREDENTIALS:
        monkeypatch.delenv(var, raising=False)
    problems = factories.preflight(_voice())
    assert any("LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET" in p for p in problems)


def test_preflight_without_environment_checks_names_only(monkeypatch) -> None:
    """``agentship verify`` style: a typo is caught, a missing key is not."""
    for var in _CREDENTIALS:
        monkeypatch.delenv(var, raising=False)
    assert factories.preflight(_voice(), check_environment=False) == []
    problems = factories.preflight(_voice(tts="nope"), check_environment=False)
    assert len(problems) == 1 and "unknown tts provider 'nope' on livekit" in problems[0]


def test_preflight_is_clean_when_everything_is_present(plugins, monkeypatch) -> None:
    for var, value in _CREDENTIALS.items():
        monkeypatch.setenv(var, value)
    assert livekit_providers.preflight(_voice()) == []


class _Session:
    """Records how the adapter builds and drives LiveKit's ``AgentSession``."""

    built: list[_Session] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.started_with: dict | None = None
        self.said: list[tuple[str, dict]] = []
        self.handlers: dict[str, object] = {}
        _Session.built.append(self)

    def on(self, event, handler):
        self.handlers[event] = handler

    async def start(self, agent, **kwargs):
        self.started_with = {"agent": agent, **kwargs}

    def say(self, text, **kwargs):
        self.said.append((text, kwargs))


class _Room:
    def __init__(self, name: str) -> None:
        self.name = name


class _JobContext:
    def __init__(self, room: str) -> None:
        self.room = _Room(room)
        self.connected = False
        self.shutdown_reason: str | None = None

    async def connect(self) -> None:
        self.connected = True

    def shutdown(self, reason: str = "") -> None:
        self.shutdown_reason = reason


@pytest.fixture
def session(monkeypatch, plugins):
    _Session.built = []
    monkeypatch.setattr(lk, "AgentSession", _Session)
    return _Session


def _turn() -> VoiceTurn:
    return VoiceTurn(object(), session_id="voice-demo")


@pytest.mark.asyncio
async def test_a_room_gets_the_agent_in_the_llm_slot(session) -> None:
    ctx = _JobContext("room-a")
    await LiveKitAdapter().serve_room(ctx, _turn(), _voice(endpoint_silence_ms=500))

    built = session.built[0]
    assert ctx.connected
    assert isinstance(built.kwargs["llm"], lk.llm.LLM)
    assert {"stt", "tts", "vad"} <= built.kwargs.keys()
    assert built.kwargs["turn_handling"]["endpointing"] == {"min_delay": 0.5}
    assert built.started_with["room"] is ctx.room
    assert "conversation_item_added" in built.handlers, "spoken text must be witnessed"


@pytest.mark.asyncio
async def test_each_room_is_its_own_conversation(session, monkeypatch) -> None:
    """Two callers on one worker must not share a transcript or a session id."""
    seen: list[VoiceTurn] = []
    adapter = LiveKitAdapter()
    real_host = adapter.host
    monkeypatch.setattr(adapter, "host", lambda t: seen.append(t) or real_host(t))
    turn = _turn()

    await adapter.serve_room(_JobContext("a"), turn, _voice())
    await adapter.serve_room(_JobContext("b"), turn, _voice())

    assert seen[0] is not seen[1] and turn not in seen
    assert [t.session_id for t in seen] == ["voice-demo-a", "voice-demo-b"]
    assert all(t.agent is turn.agent for t in seen)


@pytest.mark.asyncio
async def test_the_greeting_is_said_and_cannot_be_cut_off(session) -> None:
    await LiveKitAdapter().serve_room(_JobContext("r"), _turn(), _voice(greeting="Hi there"))
    assert session.built[0].said == [("Hi there", {"allow_interruptions": False})]


@pytest.mark.asyncio
async def test_no_greeting_means_silence(session) -> None:
    await LiveKitAdapter().serve_room(_JobContext("r"), _turn(), _voice())
    assert session.built[0].said == []


@pytest.mark.asyncio
async def test_max_session_seconds_ends_the_job(session) -> None:
    ctx = _JobContext("r")
    await LiveKitAdapter().serve_room(ctx, _turn(), _voice(max_session_seconds=1))
    assert ctx.shutdown_reason is None
    await asyncio.sleep(1.05)
    assert ctx.shutdown_reason == "max_session_seconds"


@pytest.mark.asyncio
async def test_run_reports_every_problem_before_starting(monkeypatch) -> None:
    for var in _CREDENTIALS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(lk, "AgentServer", lambda **_: pytest.fail("worker must not start"))
    with pytest.raises(CapabilityError, match="LIVEKIT_URL"):
        await LiveKitAdapter().run(_turn(), _voice())


@pytest.mark.asyncio
async def test_run_registers_a_threaded_worker_and_closes_it(monkeypatch) -> None:
    events: list[object] = []

    class _Server:
        def __init__(self, **kwargs) -> None:
            events.append(kwargs)

        def rtc_session(self, func, **kwargs):
            events.append(("registered", kwargs))

        async def run(self) -> None:
            events.append("run")

        async def aclose(self) -> None:
            events.append("closed")

    monkeypatch.setattr(factories, "preflight", lambda config: [])
    monkeypatch.setattr(lk, "AgentServer", _Server)
    monkeypatch.setenv("LIVEKIT_AGENT_NAME", "health")

    await LiveKitAdapter().run(_turn(), _voice())

    assert events == [
        {"job_executor_type": lk.JobExecutorType.THREAD},
        ("registered", {"agent_name": "health"}),
        "run",
        "closed",
    ]
