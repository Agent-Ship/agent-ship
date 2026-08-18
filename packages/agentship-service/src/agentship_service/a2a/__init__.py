"""The A2A *server* adapter — exposes a built agent over the A2A protocol (Phase 05 · C4).

This lives in the service package (not core) because it needs the web app: it maps A2A JSON-RPC
methods (``message/send``, ``message/stream``, ``tasks/*``) onto the ``RunnableAgent`` run/stream
port and mounts alongside the ``/v1`` routes, so it inherits P04's auth, TLS, and rate-limit.
The transport-agnostic pieces (wire models, Agent Card generation) live in ``agentship.a2a``.
"""

from __future__ import annotations

from .server import handle_rpc, stream_rpc

__all__ = ["handle_rpc", "stream_rpc"]
