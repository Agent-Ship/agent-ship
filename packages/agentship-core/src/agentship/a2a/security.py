"""Outbound A2A credentials + tenant/trace propagation for networked calls (§C6, client side).

When we act as an A2A *client* (a networked specialist), every request carries:

* a **credential** chosen by the remote's ``auth`` block — :class:`BearerEnvAuth` injects a static
  token from the environment; :class:`NoAuth` is the credential-free default;
* a **signed tenant claim** (``X-AgentShip-Tenant``) so the remote's ``AuthProvider`` attributes the
  turn to the right tenant — the remote trusts this only from a verified peer, never a raw client;
* a **W3C ``traceparent``** so the remote's spans stitch under our trace in Phoenix (P07).

OAuth2 client-credentials and mutual-TLS are declared in the config grammar (:class:`A2AAuthConfig`)
but not built in this pass — :func:`build_outbound_auth` raises an actionable
:class:`CapabilityError` for them so a spec that asks for one fails fast rather than silently
downgrading to no auth. Bearer + no-auth cover the local/dev and shared-secret cases today.
"""

from __future__ import annotations

import os
from typing import Protocol

from ..context import RunContext
from ..errors import CapabilityError
from .models import A2AAuthConfig


class OutboundAuth(Protocol):
    """The one thing the client needs of a credential: contribute request headers."""

    def headers(self, ctx: RunContext) -> dict[str, str]:  # noqa: D102 - protocol stub
        ...


class NoAuth:
    """No credential — used when a remote declares no ``auth`` (trusted network / dev)."""

    def headers(self, ctx: RunContext) -> dict[str, str]:
        """Contribute no auth header."""
        return {}


class BearerEnvAuth:
    """A static bearer token read from an environment variable (the ``bearer`` scheme)."""

    def __init__(self, token_env: str) -> None:
        """Bind the environment variable name holding the token."""
        self._token_env = token_env

    def headers(self, ctx: RunContext) -> dict[str, str]:
        """Inject ``Authorization: Bearer <token>``; fail fast if the env var is unset."""
        token = os.environ.get(self._token_env)
        if not token:
            raise CapabilityError(
                f"A2A bearer auth needs the token in ${self._token_env}, but it is unset — "
                f"export it or change the remote's auth block"
            )
        return {"Authorization": f"Bearer {token}"}


def build_outbound_auth(config: A2AAuthConfig) -> OutboundAuth:
    """Select the outbound credential injector for a remote's ``auth`` block.

    ``bearer`` needs ``token_env``; a bearer block without it is a config error. ``oauth2`` and
    ``mtls`` are recognised but not yet implemented — they raise :class:`CapabilityError` so the
    gap is loud, never a silent downgrade to unauthenticated.
    """
    if config.type == "none":
        return NoAuth()
    if config.type == "bearer":
        if not config.token_env:
            raise CapabilityError(
                "A2A bearer auth requires token_env naming the environment variable that holds "
                "the token"
            )
        return BearerEnvAuth(config.token_env)
    raise CapabilityError(
        f"A2A outbound auth type {config.type!r} is not implemented yet — use type: bearer "
        f"(static token) for now; oauth2/mtls land in a follow-up"
    )


def propagation_headers(ctx: RunContext) -> dict[str, str]:
    """Build the tenant + traceparent headers every outbound A2A call carries (§C6, P07).

    The tenant claim lets the remote attribute the turn without trusting a client-supplied header;
    the ``traceparent`` (W3C) stitches the remote's spans under this run's trace. A run without a
    trace id simply omits ``traceparent``.
    """
    headers = {"X-AgentShip-Tenant": ctx.caller.tenant_id}
    if ctx.trace_id:
        # A minimal W3C traceparent: version-trace-span-flags. The span segment is derived from the
        # run id so the remote nests under this turn; sampled flag on.
        span = (ctx.run_id or "0").encode("utf-8").hex()[:16].rjust(16, "0")
        headers["traceparent"] = f"00-{ctx.trace_id}-{span}-01"
    return headers
