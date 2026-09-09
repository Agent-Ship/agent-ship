"""Where a spoken turn's time went. Emitted on every turn, not only when debugging.

A voice agent is a latency pipeline, and the number a listener actually feels is
**time-to-first-audio** — the gap between the end of their sentence and the first syllable of
the reply. Total time barely registers by comparison: a reply that starts in 400ms and runs
for four seconds feels fast, and one that starts in three seconds feels broken however
quickly it finishes.

So the trace keeps ``llm_ttft_ms`` separate from ``llm_total_ms``. That delta *is* the value
of streaming, and collapsing the two would hide the only measurement that says whether the
pipeline is overlapped or merely fast.

Fields are ``None`` until the stage that owns them exists. A stage that has not been built
reports nothing rather than zero — zero is a measurement, and claiming one we never took is
the same class of lie as an engine over-declaring a capability.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class LatencyTrace:
    """Per-stage timings for one spoken turn, in milliseconds."""

    asr_ms: float | None = None
    rag_ms: float | None = None
    #: Time to the FIRST chunk the agent produced — what the listener waits through.
    llm_ttft_ms: float | None = None
    #: Time until the agent finished. Always ≥ ttft; the gap is what streaming buys.
    llm_total_ms: float | None = None
    tool_ms: float | None = None
    tts_ms: float | None = None
    #: Audio deliberately buffered before playback, so smoothness is a visible trade, not lag.
    buffer_ms: float | None = None
    #: Wall clock for the turn. In an overlapped pipeline this is LESS than the sum of stages.
    total_ms: float | None = None

    def as_dict(self) -> dict[str, float | None]:
        """Return the trace as a plain dict, including stages that have not run."""
        return {
            "asr_ms": self.asr_ms,
            "rag_ms": self.rag_ms,
            "llm_ttft_ms": self.llm_ttft_ms,
            "llm_total_ms": self.llm_total_ms,
            "tool_ms": self.tool_ms,
            "tts_ms": self.tts_ms,
            "buffer_ms": self.buffer_ms,
            "total_ms": self.total_ms,
        }

    def biggest_contributor(self) -> tuple[str, float] | None:
        """Return the measured stage that cost the most, or ``None`` if nothing was measured.

        The next optimisation goes wherever this points; without it a latency readout is a
        wall of numbers nobody acts on. ``total_ms`` is excluded — it is the sum, not a stage.
        """
        stages = {
            name: value
            for name, value in self.as_dict().items()
            if value is not None and name != "total_ms"
        }
        if not stages:
            return None
        worst = max(stages, key=lambda name: stages[name])
        return worst, stages[worst]


@dataclass
class _Stopwatch:
    """Monotonic elapsed-time helper. Monotonic so a clock adjustment cannot make time flow back."""

    started: float = field(default_factory=time.monotonic)

    def ms(self) -> float:
        """Milliseconds since this stopwatch was created."""
        return (time.monotonic() - self.started) * 1000
