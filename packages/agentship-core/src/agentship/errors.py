"""The AgentShip error taxonomy.

Errors are the only control-flow signal the kernel uses across seam boundaries:
the spec loader, the registry, and the engines raise these; the CLI prints them.
Every error carries an actionable message. Catch :class:`AgentShipError` to handle
any harness failure generically, or a subclass to react to a specific mode.
"""

from __future__ import annotations


class AgentShipError(Exception):
    """Base class for every error the harness raises.

    Catch this to handle any AgentShip failure generically; catch a subclass to
    react to a specific failure mode.
    """


class SpecError(AgentShipError):
    """An agent spec (YAML or Python-authored) is invalid or unloadable.

    Raised by the spec loader when YAML is malformed, a required field is
    missing, an unknown field is present (specs are ``extra="forbid"``), or a
    ``code:`` reference cannot be resolved.
    """


class EngineNotFoundError(AgentShipError):
    """The engine named in an :class:`~agentship.spec.AgentSpec` is not registered.

    The message lists the engines that *are* available so the fix is obvious.
    """


class CapabilityError(AgentShipError):
    """A spec asked for a capability its engine does not declare (fail-fast).

    Raised at build time — never mid-run — so a misconfiguration surfaces before
    the agent serves traffic, rather than as a confusing failure partway through.
    """


class ThreadBusyError(AgentShipError):
    """Another owner already holds the single-owner lock for this ``thread_id``.

    Raised by :class:`~agentship.thread_lock.ThreadLock` when a second worker tries to acquire a
    thread already locked by another session — the guarantee that a durable run has exactly one
    owner, so a replay or a racing worker never double-executes. The service maps this to HTTP 409.
    """


class ModelError(AgentShipError):
    """A model/provider call failed (missing credentials, a provider error, …).

    Raised when the underlying LiteLLM/provider call cannot complete — most
    commonly missing or invalid API credentials, but also any other provider-side
    failure. The message is actionable: for a credential failure it names the exact
    environment variable to set. The original provider exception is always chained
    (``raise … from``) so the full cause is available under ``--debug``.
    """
