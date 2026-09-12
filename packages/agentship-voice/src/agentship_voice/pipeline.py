"""Assemble the cascaded voice pipeline: ears → agent → mouth.

Assembly is separated from provider construction on purpose. :func:`build_pipeline` takes the
STT and TTS services as arguments rather than building them from config, so the whole loop can
be driven end to end with stand-in providers — no Deepgram key, no Cartesia key, no network,
no microphone.

What gets faked in that test is **the provider**, never our own code: a stand-in subclasses
Pipecat's real ``STTService``/``TTSService``, so the pipeline, the frame ordering, the agent
node and the spoken-text witness are all the genuine article. A test that stubbed our
processors instead would prove only that the stubs agree with each other.

:meth:`~agentship_voice.adapters.pipecat_adapter.PipecatAdapter.run` is the thin layer above
this: it resolves real providers from :class:`~agentship_voice.config.VoiceSpec` and hands
them here.
"""

from __future__ import annotations

from .turn import VoiceTurn

__all__ = ["build_pipeline", "processor_order"]


def processor_order(
    *, transport_in=None, vad=None, stt=None, agent=None, tts=None, witness=None, transport_out=None
) -> list:
    """Return the processors in pipeline order, dropping the stages that are absent.

    Order is the design, so it lives in one readable list rather than being spread through an
    assembly function:

    1. ``transport_in`` — audio arrives
    2. ``vad`` — decides when the human stopped talking
    3. ``stt`` — turns their audio into a transcript
    4. ``agent`` — the AgentShip turn
    5. ``tts`` — turns the reply into audio
    6. ``witness`` — records what TTS actually spoke (must be AFTER tts; see the adapter)
    7. ``transport_out`` — audio leaves

    Stages are optional so the same order can be driven in a test with no transport and no VAD,
    and it stays the one place the sequence is stated.
    """
    stages = [transport_in, vad, stt, agent, tts, witness, transport_out]
    return [stage for stage in stages if stage is not None]


def build_pipeline(
    turn: VoiceTurn,
    *,
    stt,
    tts,
    transport_in=None,
    vad=None,
    transport_out=None,
):
    """Build the Pipecat ``Pipeline`` that runs ``turn`` between ``stt`` and ``tts``.

    The agent node and the spoken-text witness come from the adapter, which is what keeps the
    two of them on the correct sides of TTS — the witness must sit downstream of it to see the
    ``TTSTextFrame`` that says what is actually being spoken.
    """
    from pipecat.pipeline.pipeline import Pipeline

    from .adapters.pipecat_adapter import PipecatAdapter

    agent, witness = PipecatAdapter().host(turn)
    return Pipeline(
        processor_order(
            transport_in=transport_in,
            vad=vad,
            stt=stt,
            agent=agent,
            tts=tts,
            witness=witness,
            transport_out=transport_out,
        )
    )
