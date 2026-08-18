"""A2A (agent-to-agent) interop — the optional networked-specialist layer (Phase 05).

This package is **Layer 1**: it is imported only when an agent opts into A2A (a networked
``specialists[].a2a:`` block or an ``a2a.expose: true`` block in YAML). Layer 0 — in-process
specialists (P02) and direct MCP (P03) — never touches it, so the whole ``agentship.a2a``
subtree can be absent and the default runtime stays green (the ``interop.optional`` invariant).

It carries the transport-agnostic pieces that live in core: the A2A wire models
(:mod:`agentship.a2a.models`), Agent Card generation (:mod:`agentship.a2a.card`), and the
:class:`~agentship.a2a.resolver.SpecialistResolver` that turns one ``AgentRef`` into either an
in-process or a networked specialist — so a supervisor's ``kit.specialist(name)`` call is
identical whichever side of the wire the specialist lives on. The FastAPI server adapter that
*exposes* an agent over A2A lives in ``agentship_service.a2a`` (it needs the web app), not here.
"""

from __future__ import annotations

from .card import build_agent_card
from .models import (
    AgentCapabilities,
    AgentCard,
    AgentRef,
    AgentSkill,
    JsonRpcRequest,
    JsonRpcResponse,
    Message,
    RemoteSpec,
    TextPart,
)

__all__ = [
    "AgentCapabilities",
    "AgentCard",
    "AgentRef",
    "AgentSkill",
    "JsonRpcRequest",
    "JsonRpcResponse",
    "Message",
    "RemoteSpec",
    "TextPart",
    "build_agent_card",
]
