# 0006 — A cascaded voice pipeline, two frameworks, one tiny seam

**Status:** accepted · **Scope:** P07 voice (`agentship-voice`), consumed by the service's `/voice` endpoint

## Context

Making an agent speakable forces three decisions at once, and the tempting answer to each is the
one that quietly makes the agent into a second product.

**Speech-to-speech or a cascade?** A realtime speech-to-speech model (GPT-4o Realtime, Gemini
Live) is one API call and sounds superb. It is also a different agent: the model does the
reasoning, so our engine, tools, MCP, memory, tenancy and tracing are all bypassed. Everything
AgentShip is would sit unused behind a voice agent that shares only its name.

**Which framework?** Pipecat and LiveKit Agents both solve real-time audio well, and they
disagree about almost everything. Pipecat wants a `FrameProcessor` in a frame graph. LiveKit
wants an `llm.LLM` in an `AgentSession`. Committing to one is committing our users to it.

**Where does the authoring surface live?** A voice block is configuration, and configuration that
lives in an optional package cannot be validated without installing it.

The original phase spec assumed a shared `AgentNodeProcessor` — one component both frameworks
would hold. Against the real 1.8 APIs that component cannot exist; it encoded Pipecat's model and
is simply the wrong shape for LiveKit.

## Decision

**A cascade — VAD → STT → the identical agent → TTS — and never speech-to-speech for the core
loop.** The agent that answers the phone is the agent that answers the API, byte for byte. A
latency cost is accepted in exchange, and then managed: the reply is streamed into synthesis
clause by clause so audio starts while the model is still generating, and the whole turn is
measured against an 850 ms first-audio budget that a conformance cell enforces.

**Both frameworks, behind a seam small enough to be honest.** `VoiceAdapter` is three methods,
and the shared contract is one sentence: *given what the human said, stream back what the agent
says.* That is `VoiceTurn`, and it is the entire vendor-free surface of the package — an async
iterator of strings, testable with no framework, no audio device and no key. Each adapter wraps
it in its own idiom. Adapters resolve through the `agentship.voice_frameworks` entry-point group,
the same mechanism engines use, so ours are discovered by the path a third party's would be.

**`VoiceSpec` lives in the kernel**, beside `ObservabilitySpec`, so an agent YAML with a `voice:`
block validates with only `agentship-core` installed. The kernel owns the authoring surface;
adapters own execution.

**Providers are named, not imported.** `stt: deepgram` is a string the spec resolves, and every
provider is consumed from Pipecat — we implement no recognition or synthesis. What we add is the
part that actually fails in practice: a preflight that collects *every* missing SDK and unset key
and names the fix, rather than surfacing an `ImportError` from three frames inside a vendor
package or a 401 halfway through someone's first sentence.

## Consequence

**What this buys.** One agent, two channels, provably: `CONF-VOICE-4` asserts the spoken answer
and the REST answer are the same string, and `CONF-VOICE-3` asserts a voice trace contains the
identical inner span tree with one layer on top. Changing framework, recogniser or synthesiser is
an edit to one line of YAML. The seam's smallness is what keeps the package's tests keyless.

**What it costs.** A cascade is four network hops where speech-to-speech is one, and we live
inside a latency budget rather than under it comfortably. Streaming into TTS is therefore
load-bearing, not an optimisation — a future change that buffers the reply before speaking would
pass every unit test and break the product, which is exactly what `CONF-VOICE-1` exists to catch.

**What a contributor must not break.** The `SpokenWitness` sits *downstream* of TTS. It is the
only place the signal for "this text is actually being spoken" can be observed, and it is what
makes the barge-in clip point honest. Moving it earlier looks like a simplification and silently
turns "what the human heard" back into "what we handed over" — the error the generated/spoken
split exists to prevent.

Speech-to-speech is not rejected forever. It is the right answer for a latency-critical agent
that needs no tools, and it would arrive as another `VoiceAdapter` — not as a change to this one.
