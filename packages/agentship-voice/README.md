# agentship-voice

Run the same AgentShip agent over a live audio stream.

```bash
pip install "agentship-voice[pipecat]"   # or [livekit]
```

The agent is the one the REST service runs — same spec, memory, tools and tracing. A voice
framework supplies the ears (transport, VAD, STT) and the mouth (TTS); this package supplies
the agent in the shape that framework expects.

See [`docs/capabilities/voice.md`](../../docs/capabilities/voice.md).
