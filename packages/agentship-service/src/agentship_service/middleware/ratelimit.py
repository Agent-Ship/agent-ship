"""An optional, off-by-default in-process rate limiter — the gateway-free safety net.

This is deliberately **not** the production rate-limit story. In a real deployment the app
runs behind agentgateway (P05), which owns edge rate-limiting, mTLS, RBAC, and routing.
This limiter exists only so a naked ``agentship serve`` node isn't defenceless: it is a
per-worker, best-effort token bucket, **off unless explicitly enabled**, with a single
documented remedy for anything more — put agentgateway in front. There is no Redis or other
distributed backend by design (a multi-node deploy that needs shared limits uses the
gateway, not this).

It sits between the security-headers layer and auth (see the documented middleware order),
so it throttles by a cheaply-extractable key — the raw API-key header if present, else the
client IP — *before* any authentication work is done, and a known-throttled sprayer of bad
keys is rejected with ``429`` without touching the auth provider.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable

from ..context import bind_trace_id, current_trace_id, reset_trace_id
from ..errors import problem_dict


class TokenBucket:
    """A single refilling token bucket: ``capacity`` tokens, refilled at ``rate`` per second.

    Each admitted request consumes one token; when the bucket is empty the request is
    refused. Tokens refill continuously (fractional) up to ``capacity``, so a burst of up
    to ``capacity`` is allowed and the steady-state throughput settles at ``rate``.
    """

    def __init__(self, *, capacity: float, rate: float, now: float) -> None:
        """Start full (``capacity`` tokens) as of monotonic time ``now``."""
        self._capacity = capacity
        self._rate = rate
        self._tokens = capacity
        self._updated = now

    def try_consume(self, now: float) -> bool:
        """Refill for the elapsed time, then take one token; return whether one was available."""
        elapsed = max(0.0, now - self._updated)
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._updated = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    def retry_after(self) -> int:
        """Whole seconds until at least one token is available (for a ``Retry-After`` header)."""
        if self._rate <= 0:
            return 1
        needed = 1.0 - self._tokens
        return max(1, int(needed / self._rate + 0.999))


class RateLimitMiddleware:
    """Throttle requests per key with a token bucket; disabled unless ``enabled=True``.

    When disabled (the default) it is a transparent pass-through. When enabled it keys each
    request by its raw API-key header (``x-api-key`` or a bearer token) if present, else by
    client IP, and refuses an over-limit request with a ``429`` problem+json carrying
    ``Retry-After``. It is per-worker and best-effort — for real, distributed rate-limiting
    put agentgateway in front (P05).
    """

    def __init__(
        self,
        app,
        *,
        enabled: bool = False,
        requests_per_second: float = 10.0,
        burst: int = 20,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        """Wrap ``app``; ``burst`` is the bucket capacity and ``requests_per_second`` its refill."""
        self.app = app
        self._enabled = enabled
        self._rate = requests_per_second
        self._burst = float(burst)
        self._now = now
        self._buckets: dict[str, TokenBucket] = {}

    async def __call__(self, scope, receive, send) -> None:
        """Admit or refuse an HTTP request by its bucket; pass everything else straight through."""
        if not self._enabled or scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        key = _rate_key(scope)
        bucket = self._buckets.get(key)
        now = self._now()
        if bucket is None:
            bucket = self._buckets[key] = TokenBucket(
                capacity=self._burst, rate=self._rate, now=now
            )
        if bucket.try_consume(now):
            await self.app(scope, receive, send)
            return
        await self._reject(scope, send, bucket.retry_after())

    async def _reject(self, scope, send, retry_after: int) -> None:
        """Send a ``429`` problem+json with ``Retry-After``, binding a trace id if unbound."""
        # RateLimit sits below SecurityHeaders which binds the trace id, but bind a fallback
        # so a standalone mount (e.g. a unit test) still yields a trace_id in the body.
        token = bind_trace_id(current_trace_id())
        try:
            body = problem_dict(
                status=429,
                title="Too Many Requests",
                code="rate_limited",
                detail="rate limit exceeded — retry later or front the service with a gateway",
            )
        finally:
            reset_trace_id(token)
        payload = json.dumps(body).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/problem+json"),
                    (b"content-length", str(len(payload)).encode("ascii")),
                    (b"retry-after", str(retry_after).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})


def _rate_key(scope) -> str:
    """Return the throttle key: the raw API-key header if present, else the client IP.

    Keying on the credential (before auth verifies it) means an abusive key is throttled
    even while invalid, and a spray of different bad keys still shares the caller's IP
    bucket. This is intentionally coarse — the gateway does precise per-identity limiting.
    """
    headers = dict(scope.get("headers", []))
    api_key = headers.get(b"x-api-key")
    if api_key:
        return "key:" + api_key.decode("latin-1")
    authorization = headers.get(b"authorization")
    if authorization:
        return "auth:" + authorization.decode("latin-1")
    client = scope.get("client")
    return "ip:" + (client[0] if client else "unknown")
