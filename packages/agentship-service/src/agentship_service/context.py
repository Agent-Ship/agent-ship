"""Per-request service state: the authenticated caller and the trace id.

The auth middleware binds the :class:`~agentship.context.Caller` for a request here; route
handlers and the :func:`~agentship_service.middleware.auth.require_scope` dependency read it
back. Both live in contextvars so concurrent requests never see each other's identity, and
so a handler need not thread the caller through every signature.

The trace id is minted (or adopted from an inbound ``x-request-id``) per request and echoed
into responses and error bodies, giving one id to correlate a request across logs and — once
P07 lands — the span tree.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

from agentship.context import Caller
from agentship.errors import AgentShipError

_current_caller: ContextVar[Caller | None] = ContextVar("service_current_caller", default=None)
_current_trace_id: ContextVar[str | None] = ContextVar("service_current_trace_id", default=None)


def bind_caller(caller: Caller) -> Token[Caller | None]:
    """Bind ``caller`` for the current request; returns a token to :func:`reset_caller`."""
    return _current_caller.set(caller)


def reset_caller(token: Token[Caller | None]) -> None:
    """Restore the caller binding captured by :func:`bind_caller`."""
    _current_caller.reset(token)


def current_caller() -> Caller:
    """Return the authenticated caller for this request, or raise if none is bound.

    A missing caller means a handler ran outside the auth middleware — a bug to surface,
    never an anonymous fallback.
    """
    caller = _current_caller.get()
    if caller is None:
        raise AgentShipError(
            "no authenticated caller is bound — the request bypassed the auth middleware"
        )
    return caller


def bind_trace_id(trace_id: str) -> Token[str | None]:
    """Bind the trace id for the current request; returns a token to :func:`reset_trace_id`."""
    return _current_trace_id.set(trace_id)


def reset_trace_id(token: Token[str | None]) -> None:
    """Restore the trace-id binding captured by :func:`bind_trace_id`."""
    _current_trace_id.reset(token)


def current_trace_id() -> str | None:
    """Return the trace id bound for this request, or ``None`` outside a request."""
    return _current_trace_id.get()
