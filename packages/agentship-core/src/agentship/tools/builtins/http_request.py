"""The ``http_request`` built-in tool — GET/POST any URL, carried forward from the old repo.

Uses Python's stdlib ``urllib`` (no extra dependency), returning a JSON payload of the response
status + body, or a clean ``{"error": ...}`` on failure. A faithful carry-forward of
``agent-ship``'s ``HttpRequestSkill`` (no functionality lost). Registered as a
:class:`~agentship.tools.tool.Tool` named ``http_request``.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from pydantic import BaseModel, Field

from ..tool import Tool


class HttpRequestArgs(BaseModel):
    """Arguments for an HTTP request: a URL plus optional method/params/headers/body."""

    url: str = Field(description="Full URL to request")
    method: str = Field(default="GET", description="HTTP method: GET or POST")
    params: dict[str, Any] = Field(default_factory=dict, description="Query params (GET)")
    headers: dict[str, str] = Field(default_factory=dict, description="Additional HTTP headers")
    body: dict[str, Any] = Field(default_factory=dict, description="JSON body for POST")


def _http_request(
    url: str,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    *,
    timeout: int = 15,
) -> str:
    """Perform the request and return a JSON payload of ``{status, body}`` or ``{error}``."""
    url = url.strip()
    if not url:
        return json.dumps({"error": "no url provided"})
    method = method.upper()
    params, headers, body = params or {}, dict(headers or {}), body or {}

    if params and method == "GET":
        url = f"{url}?{urllib.parse.urlencode(params)}"
    data = None
    if method == "POST" and (body or params):
        data = json.dumps(body or params).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")

    request = urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
            return json.dumps({"status": resp.status, "body": resp.read().decode("utf-8")})
    except urllib.error.HTTPError as exc:
        return json.dumps({"status": exc.code, "error": exc.reason, "body": exc.read().decode()})
    except (urllib.error.URLError, ValueError, TimeoutError) as exc:
        return json.dumps({"error": f"request failed: {exc}"})


#: The built-in HTTP tool, registered under the name ``http_request``.
http_request = Tool(
    name="http_request",
    description="Make an HTTP GET or POST request to a URL and return the response status + body.",
    func=_http_request,
    args_schema=HttpRequestArgs,
)
