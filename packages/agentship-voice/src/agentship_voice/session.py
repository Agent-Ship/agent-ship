"""Run one spoken conversation over a websocket the service already authenticated.

This is the half of the ``/voice`` endpoint that knows about voice. The service route owns
identity, the agent registry and the close-code contract; everything below owns the cascade —
so the service depends on no voice framework, and this module never parses a request.

The turn is created once per socket, not per utterance: a conversation is one session, and the
session id is what gives a voice caller the same short-term memory a REST caller gets from
threading one.
"""

from __future__ import annotations

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
        stt=make_stt(spec.voice),
        tts=make_tts(spec.voice),
        transport_in=transport.input(),
        transport_out=transport.output(),
    )

    logger.info(
        "voice session: agent=%s tenant=%s %s → agent → %s",
        spec.name,
        caller.tenant_id,
        spec.voice.stt,
        spec.voice.tts,
    )
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(PipelineWorker(pipeline))
    await runner.run()
