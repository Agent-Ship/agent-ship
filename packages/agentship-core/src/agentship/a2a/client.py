"""The A2A client: send ``message/send`` to a remote agent over a pluggable transport (§C3/C6).

:class:`RemoteA2aAgent` speaks the client side of A2A — it builds the JSON-RPC envelope, attaches
the outbound credential + tenant/trace headers, hands the request to a :class:`A2ATransport`, and
unwraps the agent's reply. The transport is an injection seam: production uses
:class:`HttpxTransport` (a thin ``httpx.AsyncClient`` wrapper); tests pass a fake, so the client
logic is exercised with no network. Any transport-level failure becomes a retryable
:class:`~agentship.errors.A2AUnavailable` — the framework never silently swaps a remote for a local.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from ..context import RunContext
from ..errors import A2AUnavailable, CapabilityError
from .models import JsonRpcRequest, JsonRpcResponse, Message, RemoteSpec
from .security import build_outbound_auth, propagation_headers


class A2ATransport(Protocol):
    """Carry one JSON-RPC call to ``url`` and return the parsed JSON response body."""

    async def rpc(self, url: str, payload: dict, headers: dict) -> dict:  # noqa: D102 - stub
        ...


class HttpxTransport:
    """The production transport: POST the JSON-RPC body with ``httpx`` (imported lazily)."""

    def __init__(self, *, timeout_seconds: float = 60.0) -> None:
        """Bind the per-call timeout; ``httpx`` is imported on first use so core stays light."""
        self._timeout = timeout_seconds

    async def rpc(self, url: str, payload: dict, headers: dict) -> dict:
        """POST ``payload`` as JSON to ``url`` and return the decoded response body."""
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()


class RemoteA2aAgent:
    """A networked specialist reached over A2A, satisfying the same ``send`` port as a local one."""

    def __init__(self, remote: RemoteSpec, *, transport: A2ATransport) -> None:
        """Bind the remote's location/auth and the transport that carries calls to it."""
        self._remote = remote
        self._transport = transport
        self._auth = build_outbound_auth(remote.auth)

    async def send(self, text: str, ctx: RunContext) -> str:
        """Send one ``message/send`` turn and return the remote agent's text reply.

        Builds the JSON-RPC envelope, layers the credential over the tenant/trace headers, and
        unwraps the result Message. A transport failure or a JSON-RPC error becomes
        :class:`A2AUnavailable` (retryable) / :class:`CapabilityError` respectively.
        """
        request = JsonRpcRequest(
            id=uuid.uuid4().hex,
            method="message/send",
            params={"message": Message.user(text).model_dump(by_alias=True)},
        )
        headers = {**propagation_headers(ctx), **self._auth.headers(ctx)}
        try:
            raw = await self._transport.rpc(
                self._remote.url, request.model_dump(by_alias=True), headers
            )
        except Exception as exc:  # noqa: BLE001 — any transport failure is a retryable A2A outage
            raise A2AUnavailable(
                f"A2A call to {self._remote.url!r} failed: {exc}"
            ) from exc
        return _unwrap(raw)


def _unwrap(raw: dict[str, Any]) -> str:
    """Turn a JSON-RPC response body into the agent's reply text, or raise on a protocol error."""
    response = JsonRpcResponse.model_validate(raw)
    if response.error is not None:
        raise CapabilityError(
            f"remote A2A agent returned error {response.error.code}: {response.error.message}"
        )
    result = response.result or {}
    parts = result.get("parts") or []
    return "".join(part.get("text", "") for part in parts if part.get("kind", "text") == "text")
