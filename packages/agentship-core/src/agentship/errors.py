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


class ResumeError(AgentShipError):
    """A durable run cannot be resumed from its :class:`~agentship.engines.base.ResumeToken`.

    Raised by an engine's ``resume`` when the referenced checkpoint is missing, stale, or otherwise
    unusable (e.g. the graph shape changed under it) — distinct from :class:`ThreadBusyError`, which
    means the thread is *owned elsewhere*, not that the resume itself is invalid. Actionable so the
    caller can decide to restart the run rather than retry a doomed resume.
    """


class AuthError(AgentShipError):
    """A request could not be authenticated (missing, malformed, or invalid credentials).

    Raised by an :class:`~agentship.auth.AuthProvider` when it cannot turn a request
    into a :class:`~agentship.context.Caller` — an unknown API key, an expired or
    badly-signed JWT, or no credentials at all. Carries a stable machine ``code``
    (e.g. ``"invalid_api_key"``, ``"no_credentials"``, ``"expired_token"``) so the
    service can put it in a 401 problem+json body without string-matching the message.
    The human-readable ``message`` defaults to the code when not given.
    """

    def __init__(self, code: str, message: str | None = None) -> None:
        """Create an auth failure with a machine ``code`` and optional human ``message``."""
        self.code = code
        super().__init__(message or code)


class TenantViolation(AgentShipError):
    """A request tried to reach a resource owned by a different tenant.

    Raised by :func:`~agentship.tenancy.guard_tenant` when the tenant bound for the
    current request does not own the resource being read or written — the core tenant
    isolation invariant (DESIGN §13.4). The service maps it to **404** on a read (so a
    resource's existence is not leaked across tenants) and **403** on a write. Carries the
    acting and owning tenant ids for audit, never exposed in the client-facing message.
    """

    def __init__(self, acting_tenant: str, owner_tenant: str) -> None:
        """Record the acting tenant and the resource's owning tenant for the audit trail."""
        self.acting_tenant = acting_tenant
        self.owner_tenant = owner_tenant
        super().__init__(
            f"tenant {acting_tenant!r} may not access a resource owned by {owner_tenant!r}"
        )


class ModelError(AgentShipError):
    """A model/provider call failed (missing credentials, a provider error, …).

    Raised when the underlying LiteLLM/provider call cannot complete — most
    commonly missing or invalid API credentials, but also any other provider-side
    failure. The message is actionable: for a credential failure it names the exact
    environment variable to set. The original provider exception is always chained
    (``raise … from``) so the full cause is available under ``--debug``.
    """


class A2AUnavailable(AgentShipError):
    """A networked (A2A) specialist could not be reached or answered (retryable).

    Raised by the A2A client path (P05) when a remote agent's Agent Card cannot be
    fetched or a ``message/send`` call fails at the transport level. It is *retryable*
    — the outer agent's graph decides whether to retry or degrade; the framework never
    silently swaps a remote specialist for a local one. Distinct from
    :class:`CapabilityError`, which means the remote genuinely cannot do what was asked.
    """

    #: A transport failure is worth retrying; the flag lets callers branch without
    #: string-matching the message.
    retryable = True
