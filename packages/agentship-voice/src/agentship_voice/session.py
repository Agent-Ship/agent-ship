"""Run one spoken conversation over a websocket the service already authenticated.

This is the half of the ``/voice`` endpoint that knows about voice. The service route owns
identity, the agent registry and the close-code contract; everything below owns the cascade —
so the service depends on no voice framework, and this module never parses a request.

The turn is created once per socket, not per utterance: a conversation is one session, and the
session id is what gives a voice caller the same short-term memory a REST caller gets from
threading one.
"""

from __future__ import annotations

import asyncio
import logging

from agentship.context import Caller

from .pipeline import build_pipecat_pipeline
from .turn import VoiceTurn

logger = logging.getLogger("agentship.voice")


async def run_browser_session(websocket, agent, caller: Caller, sample_rate: int) -> None:
    """Serve one browser voice session until the socket closes.

    ``websocket`` is already accepted and authenticated. Providers come from the agent's own
    ``voice:`` block, so the same spec that describes a voice agent on the command line
    describes it here — there is no second configuration surface for the served path.
    """
    from pipecat.pipeline.worker import PipelineWorker
    from pipecat.processors.audio.vad_processor import VADProcessor
    from pipecat.transports.websocket.fastapi import (
        FastAPIWebsocketParams,
        FastAPIWebsocketTransport,
    )
    from pipecat.workers.runner import WorkerRunner

    from .browser import RawAudioSerializer
    from .factories import make_stt, make_tts, make_vad, preflight

    spec = agent.spec
    problems = preflight(spec.voice)
    if problems:
        # Close rather than accept a session that can only fail at the first word: the human
        # would hear silence and have nothing to report but "it didn't work".
        raise RuntimeError("voice cannot start: " + "; ".join(problems))

    stt = make_stt(spec.voice)
    tts = make_tts(spec.voice)

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=sample_rate,
            audio_out_sample_rate=sample_rate,
            serializer=RawAudioSerializer(sample_rate=sample_rate),
        ),
    )

    turn = VoiceTurn(agent, caller=caller, session_id=f"voice-{id(websocket):x}")
    pipeline = build_pipecat_pipeline(
        turn,
        # A pipeline STAGE, not a transport parameter: 1.8 moved the VAD into the graph, and an
        # analyzer handed to the transport is simply never consulted — the pipeline then hears
        # audio and transcribes silence, with nothing in any log to say why.
        vad=VADProcessor(vad_analyzer=make_vad(spec.voice)),
        stt=stt,
        tts=tts,
        transport_in=transport.input(),
        transport_out=transport.output(),
    )

    # Naming the MODELS, not just the providers. "openai → agent → openai" says nothing about
    # what is doing the listening, and the listening is where a voice agent goes wrong first:
    # everything downstream reasons about whatever words came back, so a misheard sentence is
    # answered confidently and wrongly, and the log gave no way to tell.
    logger.info(
        "voice session: agent=%s tenant=%s | ears %s/%s | brain %s | mouth %s/%s (%s)",
        spec.name,
        caller.tenant_id,
        spec.voice.stt,
        getattr(stt._settings, "model", "default"),
        spec.model or "unset",
        spec.voice.tts,
        getattr(tts._settings, "model", "default"),
        getattr(tts._settings, "voice", spec.voice.voice_id or "default"),
    )
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(PipelineWorker(pipeline))

    if spec.voice.greeting:
        # Queued before the runner starts so it is the first thing synthesised. A voice agent
        # that waits in silence gives the human no way to tell "connected and listening" from
        # "broken", and the usual response to that ambiguity is to hang up.
        await _greet(pipeline, spec.voice.greeting)

    await serve_until_done(runner.run(), spec.voice.max_session_seconds, spec.name)


async def serve_until_done(session, limit: int | None, agent_name: str) -> None:
    """Await ``session``, cutting it off after ``limit`` seconds when one is set.

    Hitting the ceiling is a normal ending, not a failure: the socket is closed and the
    providers released, which is the entire point. A browser tab left open otherwise holds this
    session and its provider connections for as long as the process lives, with nobody on the
    other end.

    ``None`` means no limit, which is right for a local ``voice serve`` and wrong for anything
    public — so it is the deployment's call rather than a number we pick for them.
    """
    if limit is None:
        await session
        return
    try:
        await asyncio.wait_for(session, timeout=limit)
    except TimeoutError:
        logger.info("voice session for agent=%s hit max_session_seconds=%s", agent_name, limit)


async def _greet(pipeline, greeting: str) -> None:
    """Speak ``greeting`` before the human has said anything.

    Framed as a complete LLM response because that is what a TTS service is waiting for: it
    aggregates text and needs an explicit end before it will synthesise, so a bare text frame
    sits in its buffer and is never spoken.
    """
    from pipecat.frames.frames import (
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        TextFrame,
    )

    await pipeline.queue_frames(
        [LLMFullResponseStartFrame(), TextFrame(greeting), LLMFullResponseEndFrame()]
    )
