"""The contract a voice framework must satisfy to host an AgentShip agent.

Deliberately narrow. An adapter is asked for two things:

1. **host the agent** — wrap a :class:`~agentship_voice.turn.VoiceTurn` in whatever shape its
   framework requires (a Pipecat ``FrameProcessor``, a LiveKit ``llm.LLM``);
2. **run a session** — assemble transport, VAD, STT and TTS around it and serve until stopped.

Everything else a voice stack does — endpointing, turn detection, barge-in, jitter buffers,
audio codecs — belongs to the framework and is not restated here. Restating it would mean
owning it, and the point of two adapters is that neither framework's model leaks into ours.

**Why this ABC has two implementations from the start.** The engine ABC deliberately waits
for its second engine before freezing (ADR: the shape of an abstraction is a guess until a
second thing fits into it). Voice does not get that luxury cheaply: Pipecat and LiveKit
disagree about something fundamental — where the agent sits — so an ABC drawn against either
one alone would encode that framework's assumptions. Writing both at once is what proved the
seam had to be text-in/text-out rather than a pipeline node.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..config import VoiceConfig
from ..turn import VoiceTurn


class VoiceAdapter(ABC):
    """Host an AgentShip agent inside one voice framework."""

    #: Name used in ``voice.framework`` to select this adapter.
    name: str

    @abstractmethod
    def host(self, turn: VoiceTurn) -> object:
        """Return ``turn`` wrapped in this framework's own agent shape.

        The return type is deliberately ``object``: a Pipecat ``FrameProcessor`` and a
        LiveKit ``llm.LLM`` share no base class, and inventing a common one would mean
        adding a layer neither framework asked for. Callers hand the result straight back
        to the framework that produced its type.
        """

    @abstractmethod
    async def run(self, turn: VoiceTurn, config: VoiceConfig) -> None:
        """Assemble the pipeline around ``turn`` and serve until cancelled.

        Returns only when the session ends. Cancellation is the normal way to stop, so an
        implementation must leave the transport and provider connections closed on the way
        out rather than relying on process exit.
        """

    @abstractmethod
    def missing_dependency(self) -> str | None:
        """Return an actionable message when this framework is not installed, else ``None``.

        Checked before a session is assembled so an absent extra is one clear line naming the
        install, not an ``ImportError`` from three frames inside the framework. This mirrors
        ``agentship doctor``: a capability that cannot run should say so up front.
        """
