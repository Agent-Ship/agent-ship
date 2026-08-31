"""Serve AgentShip Studio — the built-in chat/debug page for a running service.

The page is one self-contained HTML file shipped inside this package: inline CSS and
vanilla JS, no build step and no CDN, so a deployment serves the same UI it was built
with and works on a network that can reach nothing but this origin.

``GET /studio`` is **public**, like ``/healthz`` and ``/docs``: the response is markup and
script, never tenant data. Every ``/v1`` call the page makes carries the user's own API
key, so the data path stays authenticated exactly as any other client's would be.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

#: The shipped page. Read per request rather than at import so editing it during
#: development shows up on a refresh without restarting the server.
STUDIO_PAGE = Path(__file__).resolve().parent.parent / "static" / "studio.html"


@router.get("/studio", include_in_schema=False, response_class=HTMLResponse)
async def studio() -> HTMLResponse:
    """Return the Studio page.

    Kept out of the OpenAPI schema: it is a human UI, not part of the ``v1`` contract a
    generated client should see.
    """
    return HTMLResponse(STUDIO_PAGE.read_text(encoding="utf-8"))
