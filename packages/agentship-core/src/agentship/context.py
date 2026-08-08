"""The identity backbone: :class:`RunContext`, :class:`Caller`, ``current_run``.

One compiled agent serves many concurrent turns, so *all* per-turn state lives in
a contextvar rather than on the agent instance. :class:`RunContext` carries the
identity a later pillar reads — who the caller is (:class:`Caller`), which
conversation and turn this is, how it is being driven (:class:`RunMode`), and the
optional trace id — and nothing more. The invariant (architecture §5) is that
``session_id`` is stable across the turns of a conversation and later equals the
engine's thread id and the checkpoint key; ``run_id`` is minted fresh per turn.

**Single-tenant default (DESIGN Clean-Build "reusability fixes").** A project with
no auth just works: :class:`Caller` defaults ``tenant_id="default"``, so nothing
has to plumb a tenant. Tenancy is opt-in hardening, never a baseline tax.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel


class RunMode(StrEnum):
    """How a turn is being driven — engines select a sub-path off this, not a string.

    Kept minimal for now: :attr:`INVOKE` (one-shot request/response) and
    :attr:`STREAM` (token/event streaming). The ``LIVE`` (voice) mode arrives with
    the voice phase; adding it later is non-breaking.
    """

    INVOKE = "invoke"
    STREAM = "stream"


class Caller(BaseModel):
    """The authenticated caller (DESIGN §13.2 canonical, KISS subset).

    The ``(tenant_id, user_id)`` pair scopes long-term memory and the re-identify
    vault (§13.4). ``tenant_id`` defaults to ``"default"`` so a single-tenant
    project needs no auth plumbing; ``user_id`` is required — even in dev it is a
    stable anonymous id — so memory/vault reads are always attributable to a
    caller. Scopes/auth-method land with the auth phase (04); they are omitted now.
    """

    tenant_id: str = "default"
    user_id: str


@dataclass
class RunContext:
    """Per-turn identity and state, carried through the middleware pipeline.

    Held in the :data:`current_run` contextvar so concurrent turns never collide.
    The field set is deliberately small (KISS, grow-per-pillar); later phases read
    these fields but must not invent new identity keys here. ``user_id`` and
    ``tenant_id`` are read-only mirrors of :attr:`caller` — one source of truth.
    """

    #: The authenticated caller. Its ``(tenant_id, user_id)`` scopes memory + vault.
    caller: Caller
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
    #: How this turn is driven (invoke | stream); engines branch off this, not a
    #: made-up key.
    mode: RunMode = RunMode.INVOKE
    #: The observability trace id for this turn; the observer stamps it (phase 05).
    trace_id: str | None = None
    #: The turn's input text, so middleware can read/augment it before the engine.
    input_text: str = ""
    #: The request-time chosen model id (a plain LiteLLM model string such as
    #: ``"openai/gpt-4o-mini"``), stamped by the ``route`` step before the engine
    #: runs (DESIGN §13.5). The engine adapter *reads* this to resolve its model; it
    #: never routes itself. ``None`` until the routing step stamps it — the adapter
    #: then falls back to ``spec.model``. Kept a plain string (KISS): routing is a
    #: choice among model ids, so the chosen id is all the adapter needs.
    routed_model: str | None = None

    @property
    def user_id(self) -> str:
        """The caller's user id — a read-only mirror of ``caller.user_id``."""
        return self.caller.user_id

    @property
    def tenant_id(self) -> str:
        """The caller's tenant id — a read-only mirror of ``caller.tenant_id``."""
        return self.caller.tenant_id

    @property
    def memory_scope(self) -> tuple[str, str]:
        """Return ``(tenant_id, user_id)`` — the scope key for long-term memory.

        Long-term memory and the re-identify vault partition by *tenant* and
        *caller*, never by session or agent (DESIGN §13.4), so a fact learned in
        one session re-identifies when recalled in the next. This pair is the
        canonical scope key every memory/vault backend reads.
        """
        return (self.tenant_id, self.user_id)


#: The active :class:`RunContext` for the current turn, or unset outside a turn.
current_run: ContextVar[RunContext] = ContextVar("current_run")


def get_run_context() -> RunContext | None:
    """Return the active :class:`RunContext`, or ``None`` when there is no run.

    The sanctioned public seam for tools and user code to reach the caller's
    identity without importing the private ``current_run`` contextvar. Inside a
    ``run``/``stream`` turn it returns that turn's context; outside any turn it
    returns ``None`` (never raises), so a helper invoked in isolation degrades
    gracefully rather than crashing.

    .. note::

        **Returns ``None`` between a stream's yields, by design.** While streaming,
        the runtime deliberately restores the caller's previous context *before*
        handing each event out and only re-arms this turn's context while the engine
        produces the next event (leak-safety — see ``_StreamRunContextScope`` in
        :mod:`agentship.runtime`). So code that iterates a stream and calls this
        between events will see ``None``; it is only reliably set *inside* a tool or
        engine step, not in the consumer's loop body. Consumers that need the
        identity should capture it once (e.g. before starting the stream) rather
        than relying on it per event.
    """
    return current_run.get(None)
