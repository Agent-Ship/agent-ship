# Voice

Any AgentShip agent can be spoken to. Add a `voice:` block, and the same agent the
REST API serves answers over a microphone — with barge-in, a per-stage latency
trace, and a choice of sixteen speech providers, none of which it has to know
about.

## What it is

- **The same agent, a different channel.** Voice is not a second kind of agent.
  `VoiceTurn` takes the text a recogniser produced, calls the identical
  `RunnableAgent.stream()` the HTTP service calls, and yields the chunks a
  synthesiser should speak. Memory, tools, MCP, tenancy and tracing come along
  unchanged because nothing about them is re-implemented here.
- **A cascade, not a black box.** The pipeline is stated once, in
  `processor_order()`: transport → VAD → STT → agent → TTS → witness →
  transport. Each stage is swappable and each is observable, which is what makes
  a slow turn diagnosable rather than mysterious.
- **Two frameworks behind one seam.** `VoiceAdapter` has three methods, and both
  Pipecat and LiveKit implement them. They are genuinely different shapes —
  Pipecat wants a `FrameProcessor` in a frame graph, LiveKit wants an `llm.LLM`
  in a session — so the seam is deliberately tiny: *given what the human said,
  stream back what the agent says.* Adapters register through the
  `agentship.voice_frameworks` entry-point group, so a third framework is a
  package, not a patch.
- **Sixteen providers, named not imported.** Eight recognisers (Deepgram,
  AssemblyAI, ElevenLabs, Cartesia, Gladia, Groq, Speechmatics, OpenAI) and eight
  synthesisers (Cartesia, ElevenLabs, Rime, LMNT, Inworld, Neuphonic, Deepgram,
  OpenAI). Every one is *consumed* — Pipecat implements the protocols; we map a
  name to a class and check the two things that actually go wrong.
- **Barge-in that tells the truth.** The human can cut the agent off mid-sentence.
  What makes this more than a stop button is the `SpokenWitness`, which sits
  *after* TTS and confirms text as it is synthesised — so history records what was
  heard, not what was planned, and the conversation is corrected to match.
- **A latency trace on every turn.** `asr_ms`, `llm_ttft_ms`, `llm_total_ms`,
  `tts_ms` and the mouth-to-ear `total_ms`, plus the name of the stage that took
  the largest share. Surfaced in Studio as bars and on the `voice.turn` span as
  attributes.

## How to use it

```yaml
# agents/assistant.yaml
name: assistant
engine: langgraph
model: openai/gpt-4o-mini
prompt: You are a helpful voice assistant. Keep answers to one or two sentences.

voice:
  framework: pipecat
  stt: deepgram             # the ears
  stt_model: nova-3
  tts: cartesia             # the mouth
  tts_model: sonic-2
  voice_id: 79a125e8-cd45-4c13-8a67-188112f4dd22
  language: en              # pin it; see below
  endpoint_silence_ms: 700  # how long a pause means "your turn"
  greeting: Hi, what can I help with?
```

Then either serve it over the HTTP service and talk to it in Studio:

```bash
agentship serve agents/ --port 7001       # then open /studio and press the mic
```

…or run a standalone session from the terminal:

```bash
agentship voice serve agents/assistant.yaml
agentship voice serve agents/assistant.yaml --dry-run   # check deps + keys, bind nothing
agentship voice providers                               # what is installed, what has a key
```

Install what the block asks for:

```bash
pip install "agentship-voice[pipecat]"
pip install "pipecat-ai[deepgram,cartesia,silero]"
export DEEPGRAM_API_KEY=... CARTESIA_API_KEY=...
```

### Over LiveKit

Set `framework: livekit` and the same agent is hosted by a LiveKit worker instead: callers
join a LiveKit room over WebRTC (any LiveKit client SDK), and LiveKit dispatches each room to
the worker. The agent sits in the session's LLM slot; recognition, synthesis and VAD are
LiveKit's own plugins, chosen by the same provider names.

```yaml
voice:
  framework: livekit
  stt: deepgram
  tts: cartesia
  greeting: Hi, what can I help with?
```

```bash
pip install "agentship-voice[livekit]" "livekit-agents[deepgram,cartesia,silero]"
export LIVEKIT_URL=wss://<project>.livekit.cloud LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=...
export DEEPGRAM_API_KEY=... CARTESIA_API_KEY=...
agentship voice serve agents/assistant.yaml            # registers the worker
```

`livekit-server --dev` (URL `ws://localhost:7880`, key `devkey`, secret `secret`) is enough
locally. Set `LIVEKIT_AGENT_NAME` to require explicit dispatch; left unset, the worker joins
every new room.

- **One conversation per room.** Each room gets its own `VoiceTurn` and session id
  (`<session_id>-<room>`), so two callers on one worker never share a transcript or a
  barge-in.
- **The same settings mean the same thing.** `endpoint_silence_ms`, `allow_interruptions` and
  `interrupt_min_words` become LiveKit's `turn_handling`; `greeting` is spoken uninterruptibly;
  `max_session_seconds` shuts the job down. `voice_id`, `tts_model`, `tts_speed`, `stt_model`,
  `stt_hint` and `language` are passed under each plugin's own argument name. A setting the
  chosen plugin has no argument for is logged and skipped.
- **Jobs run as threads of the worker process**, not a process each, because the agent is
  built once by the caller and cannot be handed to another process. An agent whose engine
  holds event-loop-bound connections (a pooled checkpoint store, say) must tolerate being
  called from the job's own loop.
- **`--dry-run` and `agentship verify` check LiveKit's plugins**, keys and the three
  `LIVEKIT_*` credentials, not Pipecat's. `agentship serve`'s in-browser mic always runs
  Pipecat, whatever `framework` says.

### The four settings that decide whether it feels right

| Setting | Why it matters |
|---|---|
| `language` | Leave it unset and a recogniser auto-detects *per utterance*, gets short ones wrong, and the agent then answers fluently in a language nobody spoke. Set it whenever you know who you are talking to. |
| `endpoint_silence_ms` | How long a silence means the human is done. The library default is 200 ms — shorter than an ordinary pause for thought, so the agent talks over anyone who hesitates. Ours is 700 ms. |
| `stt_model` | The single biggest lever on quality. A wrong answer to a misheard question is an STT problem, and it is indistinguishable from a bad model until you look. |
| `max_session_seconds` | Off by default, which is right locally and wrong in public: an abandoned browser tab otherwise holds the socket and its provider connections indefinitely. |

### Speaking a structured agent

An agent with an `output_schema` returns an object, and a synthesiser handed an
object reads its JSON aloud — braces, quotes and key names. Name the field to
speak instead:

```yaml
voice:
  speak_field: answer
```

## One runnable example

```bash
cd agentship-demo
python demos/talk.py            # live mic, needs keys
pytest tests/test_voice.py      # the same loop, keyless
```

`agents/voice_assistant.yaml` is the authoring surface end to end. In Studio, the
mic sits above the transcript: press it, speak, and watch the turn's stages
appear as latency bars beneath the answer.

## Observability

A spoken turn produces one trace whose root is `voice.turn`, with the ordinary
`agent <name>` tree nested inside it unchanged — the same tree a REST call
produces, plus one honest layer accounting for what voice added. The turn span
names the providers in play, carries the per-stage timings, and marks a barge-in
with `agentship.voice.cancelled` rather than an error status, because being
interrupted is the feature working.

Recognition and synthesis are attributed as timings rather than given spans of
their own: both are vendor calls made by the framework, so a span we opened
around them would measure our wait rather than their work. See
[`SEMCONV.md`](https://github.com/Agent-Ship/agent-ship/blob/main/packages/agentship-core/src/agentship/observability/SEMCONV.md) §2.4.

## Status & limits

**Proven.** The full loop runs end to end on Pipecat with a verified round trip
inside the 850 ms first-audio budget, measured from the point the human's turn is
judged complete. Deciding that they have finished rather than paused costs the
endpointing wait `endpoint_silence_ms` sets — 700 ms by default — and that is on
top of the budget, not inside it. Six conformance cells (`CONF-VOICE-1..6`)
pin the budget, the trace, the span tree, REST/voice parity, framework parity and
barge-in honesty — all keyless, driving a real pipeline with stand-in providers
that subclass Pipecat's own service classes.

**Proven on a local server.** On LiveKit, the worker has been run against a local
`livekit-server --dev` with real Deepgram recognition and synthesis and a LangGraph agent: it
registers, is dispatched when a caller joins a room, speaks the greeting, hears a spoken
question and answers it aloud — the caller's own transcription of the reply was the right
answer. Two callers in two rooms at once each got their own correct answer, through one worker
and one agent on the thread executor. Time from the caller's last word to the first reply
audio was 2.2–2.7 s measured at the caller, which includes endpointing, recognition and the
model; the 850 ms first-audio budget has **not** been measured on LiveKit, and barge-in has
only been proven by unit tests there, not against a live room.

**Not built.** SIP transport (LiveKit's telephony has not been wired up), and WebRTC on
Pipecat (on LiveKit, WebRTC is how callers join);
the guardrail and safety-interrupt stages, which wait on the guardrails phase —
`processor_order()` leaves their slots free, so adding them is one argument each.
