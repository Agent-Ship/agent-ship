"""Assemble the cascaded voice pipeline: ears → agent → mouth.

:func:`processor_order` is framework-neutral — it states the cascade and nothing else.
:func:`build_pipecat_pipeline` is named for its framework because it returns a Pipecat
``Pipeline``; a generic name there would hide which framework a caller is committing to.

Assembly is separated from provider construction on purpose:
:func:`build_pipecat_pipeline` takes the STT and TTS services as arguments rather than
building them from config, so the whole loop can
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

__all__ = ["build_pipecat_pipeline", "processor_order"]


def processor_order(
    *,
    transport_in=None,
    vad=None,
    stt=None,
    user_turn=None,
    agent=None,
    tts=None,
    witness=None,
    transport_out=None,
    assistant_turn=None,
) -> list:
    """Return the processors in pipeline order, dropping the stages that are absent.

    Order is the design, so it lives in one readable list rather than being spread through an
    assembly function:

    1. ``transport_in`` — audio arrives
    2. ``vad`` — decides when the human started and stopped talking
    3. ``stt`` — turns the human's audio into a transcript
    4. ``user_turn`` — decides when the human's turn is OVER, and whether a noise was a barge-in
    5. ``agent`` — the AgentShip turn
    6. ``tts`` — turns the reply into audio
    7. ``witness`` — records what TTS actually spoke (must be AFTER tts; see the adapter)
    8. ``transport_out`` — audio leaves
    9. ``assistant_turn`` — records what the agent said, AFTER it was spoken

    **``user_turn`` and ``assistant_turn`` are Pipecat's context aggregators, and they are where
    turn-taking lives.** The user aggregator owns the start/stop/mute strategies: how many words
    count as an interruption, whether the microphone is ignored while the agent is still on its
    first sentence, when a pause means "finished" rather than "thinking".

    They were missing, and we wrote a worse replacement: cancel the reply task when a new
    transcript arrives. That is why the agent treated its own voice through the speakers as the
    human talking, why it interrupted itself, and why cutting in did not stop the audio already
    queued — cancelling our task stops us generating and tells the pipeline nothing.

    The agent node sits where an LLM service would, between the two aggregators, because that is
    exactly the role it plays.

    **The VAD stage is a ``VADProcessor``, not a ``VADAnalyzer``.** The analyzer is not a frame
    processor and cannot be one; the processor wraps it and emits the
    ``VADUserStartedSpeakingFrame``/``VADUserStoppedSpeakingFrame`` pair that a segmented STT
    waits for before it transcribes anything. Passing the analyzer to the transport instead —
    which older Pipecat consumed and 1.8 does not — leaves the VAD never running, and the only
    symptom is a pipeline that hears audio and transcribes silence.

    Stages are optional so the same order can be driven in a test with no transport and no VAD,
    and it stays the one place the sequence is stated.
    """
    stages = [transport_in, vad, stt, user_turn, agent, tts, witness, transport_out, assistant_turn]
    return [stage for stage in stages if stage is not None]


def turn_taking(config=None, vad_analyzer=None):
    """Build Pipecat's turn-taking strategies from a ``voice:`` block.

    These decide the three questions that make a conversation feel natural or awful, and every
    one of them was previously answered by us, badly:

    * **Is this a barge-in, or a noise?** ``MinWordsUserTurnStartStrategy`` needs real words
      before it believes you interrupted. Without a floor, a cough — or the agent's own voice
      arriving back through the speakers — counts as you talking, which is why the agent
      appeared to interrupt itself.
    * **Should the microphone be listened to at all right now?**
      ``MuteUntilFirstBotCompleteUserMuteStrategy`` ignores it until the agent finishes its
      first utterance, so a greeting cannot be cut off by its own echo.
    * **Has the human finished, or just paused?** Left to Pipecat's default, which is the local
      smart-turn model — a model that predicts whether a thought is complete, rather than timing
      silence. Timing silence is what made the agent cut in whenever anyone stopped to think.

    Returns ``None`` when there is nothing to configure, so Pipecat's defaults apply untouched.
    """
    from pipecat.turns.user_mute.mute_until_first_bot_complete_user_mute_strategy import (
        MuteUntilFirstBotCompleteUserMuteStrategy,
    )
    from pipecat.turns.user_start.min_words_user_turn_start_strategy import (
        MinWordsUserTurnStartStrategy,
    )
    from pipecat.turns.user_turn_strategies import UserTurnStrategies

    # Was `getattr(config, "interrupt_min_words", None) or 2` against a field that did not
    # exist, so the floor was always 2 and no agent could change it -- the same defect as the
    # two below, a setting that looks configurable and is not. The field exists now; the
    # fallback stays only for a stand-in config in a test.
    min_words = getattr(config, "interrupt_min_words", 2)
    mute_until_greeted = bool(getattr(config, "greeting", None))
    # Where `allow_interruptions:` actually takes effect. It used to be passed nowhere and only
    # logged -- `_build_transport` even said it was "handed over here too" -- so an agent that
    # turned barge-in OFF was still interruptible, and one that left it on had no interruption
    # raised at all. Pipecat 1.8 has no transport switch for this; the start strategy decides
    # whether the start of a user turn broadcasts an interruption.
    allow_interruptions = getattr(config, "allow_interruptions", True)

    return (
        UserTurnStrategies(
            start=[
                MinWordsUserTurnStartStrategy(
                    min_words=min_words, enable_interruptions=allow_interruptions
                )
            ]
        ),
        # Only when there IS a greeting to protect. Muting the microphone for an agent that
        # opens in silence would just swallow the first thing anybody says.
        [MuteUntilFirstBotCompleteUserMuteStrategy()] if mute_until_greeted else [],
    )


def build_pipecat_pipeline(
    turn: VoiceTurn,
    *,
    stt,
    tts,
    vad=None,
    vad_analyzer=None,
    config=None,
    transport_in=None,
    transport_out=None,
):
    """Build the Pipecat ``Pipeline`` that runs ``turn`` between ``stt`` and ``tts``.

    The agent node and the spoken-text witness come from the adapter, which is what keeps the
    two of them on the correct sides of TTS — the witness must sit downstream of it to see the
    ``TTSTextFrame`` that says what is actually being spoken.

    The agent sits between Pipecat's two context aggregators, where an LLM service normally
    goes, because that is the role it plays. Those aggregators are what turn-taking lives in;
    assembling the pipeline without them is what left this package doing its own, worse.
    """
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMContextAggregatorPair,
        LLMUserAggregatorParams,
    )

    from .adapters.pipecat_adapter import PipecatAdapter

    agent, witness = PipecatAdapter().host(turn)

    strategies, mute = turn_taking(config, vad_analyzer)
    pair = LLMContextAggregatorPair(
        LLMContext(),
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=strategies,
            user_mute_strategies=mute,
            vad_analyzer=vad_analyzer,
        ),
    )

    return Pipeline(
        processor_order(
            transport_in=transport_in,
            vad=vad,
            stt=stt,
            user_turn=pair.user(),
            agent=agent,
            tts=tts,
            witness=witness,
            transport_out=transport_out,
            assistant_turn=pair.assistant(),
        )
    )
