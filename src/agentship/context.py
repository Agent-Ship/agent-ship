"""The identity backbone: :class:`RunContext` and the ``current_run`` contextvar.

One compiled agent serves many concurrent turns, so *all* per-turn state lives in
a contextvar rather than on the agent instance. :class:`RunContext` carries the
four identity fields every later pillar reads — ``user_id``, ``session_id``,
``run_id``, ``agent_name`` — and nothing more. The invariant (architecture §5) is
that ``session_id`` is stable across the turns of a conversation and later equals
the engine's thread id and the checkpoint key; ``run_id`` is minted fresh per turn.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class RunContext:
    """Per-turn identity and state, carried through the middleware pipeline.

    Held in the :data:`current_run` contextvar so concurrent turns never collide.
    The field set is deliberately small; later phases read these fields (memory
    scope, trace ids, durable resume) but must not invent new identity keys here.
    """

    #: The authenticated caller. Stable across a caller's sessions; scopes memory.
    user_id: str
    #: Stable across the turns of one conversation. Later equals the engine thread
    #: id and the checkpoint key (architecture §5). Minted once when the caller
    #: passes none, then threaded by the caller on subsequent turns.
    session_id: str
    #: Minted fresh for every ``run``/``stream`` call — identifies a single turn
    #: (distinct from ``session_id``, which spans many turns). Load-bearing for
    #: per-turn observability, durable checkpoints, and cost slicing.
    run_id: str
    #: The name of the agent serving this turn.
    agent_name: str
    #: The turn's input text, so middleware can read/augment it before the engine.
    input_text: str = ""

    @property
    def memory_scope(self) -> tuple[str, str]:
        """Return ``(user_id, agent_name)`` — the scope key for long-term memory.

        Long-term memory partitions by *caller* and *agent*, never by session, so
        that facts learned in one session are recalled in the next. This pair is
        the canonical scope key every memory backend reads.
        """
        return (self.user_id, self.agent_name)


#: The active :class:`RunContext` for the current turn, or unset outside a turn.
current_run: ContextVar[RunContext] = ContextVar("current_run")


def get_run_context() -> RunContext | None:
    """Return the active :class:`RunContext`, or ``None`` when there is no run.

    The sanctioned public seam for tools and user code to reach the caller's
    identity without importing the private ``current_run`` contextvar. Inside a
    ``run``/``stream`` turn it returns that turn's context; outside any turn it
    returns ``None`` (never raises), so a helper invoked in isolation degrades
    gracefully rather than crashing.
    """
    return current_run.get(None)
