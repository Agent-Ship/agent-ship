# The voice agent — ready to run, off by default

`agent.yaml` is a complete, working voice harness, and the demo image already installs
everything it needs (`agentship-voice[pipecat]` plus the Deepgram, OpenAI and Silero
providers). It sits one directory down from the other agents on purpose: `agentship serve`
reads the specs **directly under** `agents/`, so a spec in here is present in the repo and
invisible to the running service.

## Why it is off rather than on

A `voice:` block is the one kind of spec whose **provider keys are checked before the socket
binds**. That check is right — a voice agent that fails at the first word leaves a human
listening to silence with nothing to report — but it means an unset `DEEPGRAM_API_KEY` stops
the *whole service* rather than that one agent.

That is exactly what happened: served from `agents/`, this spec failed the gate, the container
crash-looped, and `make ui` opened a dead tab with no explanation. One agent nobody had keys
for took down the nine that needed none.

The rest of the demo starts with no keys at all, and that property is worth keeping.

## Turning it on

Put a Deepgram key (it hears) and an OpenAI key (it speaks and thinks) in `.env`:

```bash
DEEPGRAM_API_KEY=...
OPENAI_API_KEY=...
```

then move the spec up one level and restart:

```bash
mv agents/voice/agent.yaml agents/voice_assistant.yaml
make docker-reload
```

Open Studio, pick `voice-assistant`, and press the microphone above the transcript. To check
the stack before starting anything:

```bash
agentship voice providers     # what is installed, and what has a key
```
