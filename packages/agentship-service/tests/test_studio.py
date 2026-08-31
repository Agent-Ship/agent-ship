"""``GET /studio``: the built-in UI is public, is real HTML, and is not part of ``v1``.

The page is only markup and script — the data it shows is fetched by the browser with the
user's own key — so it must load without a credential, or nobody can reach the screen that
asks for one. It must equally stay out of the OpenAPI schema: it is a human UI, not a
contract a generated client should discover.
"""

from __future__ import annotations

from agentship.auth import ApiKeyAuthProvider, EnvApiKeyStore
from agentship_service import create_app
from agentship_service.routers.studio import STUDIO_PAGE
from fastapi.testclient import TestClient


def _client() -> TestClient:
    """A client for a service whose only key is ``secret`` — deliberately never sent."""
    auth = ApiKeyAuthProvider(
        EnvApiKeyStore(raw='[{"key": "secret", "user": "u1", "tenant": "t1", "scopes": ["*"]}]')
    )
    return TestClient(create_app(auth=auth))


def test_studio_loads_without_a_credential() -> None:
    """``GET /studio`` with no ``Authorization`` header returns 200 HTML, not a 401."""
    resp = _client().get("/studio")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "<!DOCTYPE html>" in resp.text
    assert "AgentShip Studio" in resp.text


def test_studio_is_not_in_the_openapi_schema() -> None:
    """The page is a human UI, so no generated client sees it as a ``v1`` operation."""
    schema = _client().get("/openapi.json").json()
    assert "/studio" not in schema["paths"]


def test_studio_page_is_self_contained() -> None:
    """The page ships whole: no build step, no npm, and no runtime CDN fetch.

    A Studio that needs the public internet is a Studio that is blank on an air-gapped or
    egress-filtered deployment — exactly where a debug UI matters most.
    """
    page = STUDIO_PAGE.read_text(encoding="utf-8")
    assert "<style>" in page and "<script>" in page
    assert "cdn." not in page
    assert "https://fonts." not in page
    assert "<script src=" not in page


def test_studio_drives_the_real_v1_surface() -> None:
    """The page calls the endpoints this service actually exposes, not invented ones."""
    page = STUDIO_PAGE.read_text(encoding="utf-8")
    for path in ('"/healthz"', '"/v1/agents"', '":invoke"', '":stream"', '":resume"'):
        assert path in page
