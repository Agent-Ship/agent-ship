"""The optional in-process rate limiter: off by default, throttles by key when enabled.

These prove the safety-net contract — a disabled limiter is transparent, an enabled one
admits a burst then returns ``429`` with ``Retry-After`` and a problem+json body, refills
over time, and keys requests independently — without pretending to be a distributed
rate-limit system (that is agentgateway's job, P05).
"""

from __future__ import annotations

import json

from agentship.auth import ApiKeyAuthProvider, EnvApiKeyStore
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from agentship_service import AgentRegistry, create_app
from agentship_service.middleware.ratelimit import TokenBucket
from fastapi.testclient import TestClient

_KEYS = json.dumps([{"key": "k1", "user": "u1", "tenant": "acme", "scopes": ["*"]}])


def _client(**kwargs) -> TestClient:
    """Build a client over an app serving one ``support`` agent with the given app kwargs."""
    agents = AgentRegistry([build_agent(AgentSpec(name="support", engine="echo"))])
    auth = ApiKeyAuthProvider(EnvApiKeyStore(raw=_KEYS))
    return TestClient(create_app(auth=auth, agents=agents, **kwargs))


def test_token_bucket_admits_a_burst_then_refuses() -> None:
    """A bucket admits up to its capacity, refuses when empty, and refills over time."""
    bucket = TokenBucket(capacity=2, rate=1.0, now=0.0)
    assert bucket.try_consume(0.0) is True
    assert bucket.try_consume(0.0) is True
    assert bucket.try_consume(0.0) is False
    # One second later, one token has refilled.
    assert bucket.try_consume(1.0) is True
    assert bucket.try_consume(1.0) is False


def test_disabled_limiter_is_transparent() -> None:
    """With rate limiting off (the default), many rapid requests all pass."""
    client = _client()
    for _ in range(30):
        resp = client.get("/healthz")
        assert resp.status_code == 200


def test_enabled_limiter_throttles_after_burst() -> None:
    """Enabled with a small burst, requests beyond it get 429 + Retry-After + problem+json."""
    client = _client(rate_limit=True, requests_per_second=0.01, rate_limit_burst=3)
    headers = {"x-api-key": "k1"}
    ok = [
        client.post("/v1/agents/support:invoke", headers=headers, json={"input": "hi"})
        for _ in range(3)
    ]
    assert all(r.status_code == 200 for r in ok)

    throttled = client.post("/v1/agents/support:invoke", headers=headers, json={"input": "hi"})
    assert throttled.status_code == 429
    assert throttled.headers["retry-after"]
    assert throttled.headers["content-type"] == "application/problem+json"
    assert throttled.json()["code"] == "rate_limited"


def test_limiter_keys_are_independent() -> None:
    """Two credentials get independent buckets — one exhausting does not block the other."""
    keys = json.dumps(
        [
            {"key": "k1", "user": "u1", "tenant": "acme", "scopes": ["*"]},
            {"key": "k2", "user": "u2", "tenant": "acme", "scopes": ["*"]},
        ]
    )
    agents = AgentRegistry([build_agent(AgentSpec(name="support", engine="echo"))])
    auth = ApiKeyAuthProvider(EnvApiKeyStore(raw=keys))
    client = TestClient(
        create_app(
            auth=auth, agents=agents, rate_limit=True, requests_per_second=0.01, rate_limit_burst=1
        )
    )
    first = client.post(
        "/v1/agents/support:invoke", headers={"x-api-key": "k1"}, json={"input": "hi"}
    )
    assert first.status_code == 200
    # k1 is now exhausted, but k2 has its own full bucket.
    other = client.post(
        "/v1/agents/support:invoke", headers={"x-api-key": "k2"}, json={"input": "hi"}
    )
    assert other.status_code == 200
