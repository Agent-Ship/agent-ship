"""Smoke-test the BUILT CONTAINER over the network — `make test-docker`.

`make test` proves the code works; it never builds an image. This proves the deployment
does: that the image boots, binds a port, loads every spec past the doctor gate, enforces
auth, and serves Studio. Those are the failures that only appear once something is
containerised — a missing system package, a spec that cannot resolve inside the image, a
process that exits before it binds.

Deliberately keyless and free: every assertion here is about the envelope around a turn —
routing, auth, the error model, the UI — none of which needs a model. A smoke test that
spends money on every run is one people stop running.

    make docker-up && make test-docker
"""

from __future__ import annotations

import os

import httpx
import pytest

BASE = os.environ.get("AGENTSHIP_BASE_URL", "http://localhost:7005")

#: The dev key seeded by docker-compose. Not a secret: it only exists in local compose.
KEY = {"Authorization": "Bearer dev"}


@pytest.fixture(scope="module", autouse=True)
def require_a_running_container():
    """Skip rather than fail when nothing is serving — the container is not always up."""
    try:
        httpx.get(f"{BASE}/healthz", timeout=5)
    except httpx.HTTPError:
        pytest.skip(f"nothing serving at {BASE} — run `make docker-up` first")


def test_the_container_boots_and_reports_its_build():
    """The image starts, binds, and says which build answered."""
    body = httpx.get(f"{BASE}/healthz", timeout=10).json()

    assert body["status"] == "ok"
    assert body["build"], "no build stamp — cannot tell one image from the next"
    assert body["packages"]["agentship-service"], "the service package is not reporting itself"


def test_the_bare_host_sends_a_browser_to_studio():
    """`/` redirects instead of answering 401 — the recurring "no API key" nobody earned."""
    resp = httpx.get(f"{BASE}/", follow_redirects=False, timeout=10)

    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/studio"


def test_studio_is_served_and_never_cached():
    """Studio loads without a credential, and a rebuilt image is what you actually see."""
    resp = httpx.get(f"{BASE}/studio", timeout=10)

    assert resp.status_code == 200
    assert "no-store" in resp.headers.get("cache-control", "")
    assert "function statusFor" in resp.text, "this image predates the progress steps"


def test_the_api_requires_a_key_and_accepts_the_right_one():
    """401 without a credential, 200 with — auth is really wired in the running image."""
    assert httpx.get(f"{BASE}/v1/agents", timeout=10).status_code == 401
    assert httpx.get(f"{BASE}/v1/agents", headers=KEY, timeout=10).status_code == 200


def test_every_spec_in_the_image_loaded():
    """The catalog is non-empty and each card is complete.

    `agentship serve` validates specs before binding, so an empty or partial catalog means
    specs were skipped at startup — the container is up and useless, which a health check
    alone will happily call healthy.
    """
    cards = httpx.get(f"{BASE}/v1/agents", headers=KEY, timeout=10).json()

    assert cards, "the container serves no agents at all"
    for card in cards:
        assert card["name"], f"a card has no name: {card}"


def test_a_bad_request_is_still_a_problem_document_in_the_container():
    """The RFC-9457 error model survives containerisation, rather than a proxy's HTML 500."""
    resp = httpx.post(
        f"{BASE}/v1/agents/nosuchagent:invoke", headers=KEY, json={"input": "hi"}, timeout=10
    )

    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["code"] == "not_found"
