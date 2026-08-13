"""The durability error types (§10 error model): they exist, subclass the base, and carry a message.

`ThreadBusyError` (single-owner lock contention → HTTP 409) and `ResumeError` (a resume that
cannot proceed — stale/missing checkpoint) are part of the exactly-once durability story. This
pins that both are :class:`AgentShipError` subclasses (so the service's one handler catches them),
are distinct types, and preserve an actionable message. The concrete 409 mapping lands with the
P04 service.
"""

from __future__ import annotations

from agentship.errors import AgentShipError, ResumeError, ThreadBusyError


def test_thread_busy_error_is_an_agentship_error() -> None:
    """ThreadBusyError subclasses the base so the service's single handler catches it."""
    assert issubclass(ThreadBusyError, AgentShipError)


def test_resume_error_is_an_agentship_error() -> None:
    """ResumeError subclasses the base for the same uniform handling."""
    assert issubclass(ResumeError, AgentShipError)


def test_errors_are_distinct_types() -> None:
    """The two are different types so callers can distinguish contention from a bad resume."""
    assert ThreadBusyError is not ResumeError
    assert not issubclass(ThreadBusyError, ResumeError)


def test_errors_preserve_their_message() -> None:
    """The message a raiser gives survives to the handler (actionable §10 errors)."""
    assert str(ResumeError("stale checkpoint")) == "stale checkpoint"
    assert str(ThreadBusyError("thread busy")) == "thread busy"
