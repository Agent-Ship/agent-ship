"""Generate an A2A Agent Card from an ``AgentSpec`` + the engine's real capabilities (§C4).

The author never writes a card by hand. :func:`build_agent_card` reads the declarative spec and
the engine's :class:`~agentship.engines.base.EngineCapabilities`, so every claim on the card is
one the agent can actually honour — ``streaming`` is true only when the engine streams, the
advertised ``securitySchemes`` are exactly the ones the service enforces. That "declare, don't
fake" honesty is the whole point of generating rather than hand-writing (DESIGN §3 honesty).
"""

from __future__ import annotations

from ..engines.base import EngineCapabilities
from ..spec import AgentSpec
from .models import AgentCapabilities, AgentCard, SecurityScheme

#: Human-readable descriptions for the auth schemes an agent can advertise. The card names the
#: scheme; the service enforces exactly this set (they come from one config, so they can't drift).
_SECURITY_SCHEME_DESCRIPTIONS = {
    "oauth2": "OAuth 2.0 bearer token (client credentials or authorization code).",
    "apiKey": "Static API key in the X-API-Key header.",
    "mtls": "Mutual TLS — a client certificate verified at the ingress.",
}


def _security_scheme(name: str) -> SecurityScheme:
    """Build the advertised :class:`SecurityScheme` for one scheme name.

    The ``apiKey`` scheme additionally declares *where* the key travels (``in: header``,
    ``name: X-API-Key``) — the A2A/OpenAPI ``APIKeySecurityScheme`` requires both so a client
    knows how to authenticate. (Full ``oauth2`` flow URLs and the ``mutualTLS`` shape are a
    tracked follow-up; today those advertise type + description only.)
    """
    description = _SECURITY_SCHEME_DESCRIPTIONS.get(name)
    if name == "apiKey":
        return SecurityScheme(
            type="apiKey", name="X-API-Key", location="header", description=description
        )
    return SecurityScheme(type=name, description=description)


def build_agent_card(
    spec: AgentSpec,
    capabilities: EngineCapabilities,
    *,
    base_url: str,
    security: list[str] | None = None,
) -> AgentCard:
    """Build the A2A :class:`AgentCard` for ``spec`` served under ``base_url``.

    ``base_url`` is the service root (e.g. ``https://host``); the card's ``url`` is the agent's
    A2A endpoint ``{base_url}/a2a/{name}``. ``capabilities`` is the built engine's declared
    capabilities — ``streaming`` on the card mirrors ``capabilities.streaming`` exactly, never the
    spec's *request* to stream. ``security`` is the list of scheme names the agent enforces
    (from its ``a2a.security`` block); each becomes both a ``securitySchemes`` entry and a
    ``security`` requirement so the card advertises precisely what the inbound router checks.
    """
    root = base_url.rstrip("/")
    schemes = {name: _security_scheme(name) for name in (security or [])}
    return AgentCard(
        name=spec.name,
        description=spec.prompt,
        url=f"{root}/a2a/{spec.name}",
        capabilities=AgentCapabilities(streaming=capabilities.streaming),
        security_schemes=schemes,
        security=[{name: []} for name in (security or [])],
    )
