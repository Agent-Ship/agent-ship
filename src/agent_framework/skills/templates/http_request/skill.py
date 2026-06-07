"""HTTP request skill — make GET/POST requests to external APIs."""

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict

from pydantic import BaseModel, Field

from src.agent_framework.skills.base_skill import BaseSkill


class HttpRequestInput(BaseModel):
    url: str = Field(description="Full URL to request")
    method: str = Field(default="GET", description="HTTP method: GET or POST (default GET)")
    params: Dict[str, Any] = Field(default_factory=dict, description="Query parameters (GET) or form body (POST)")
    headers: Dict[str, str] = Field(default_factory=dict, description="Additional HTTP headers")
    body: Dict[str, Any] = Field(default_factory=dict, description="JSON body for POST requests")


class HttpRequestSkill(BaseSkill):
    """Make HTTP GET or POST requests to external URLs.

    Uses Python's stdlib ``urllib`` — no extra dependencies required.

    Config options (set in agent YAML ``skills[].config``):

    - ``timeout``: request timeout in seconds (default 15)
    - ``default_headers``: dict of headers to include in every request
    - ``method``: default HTTP method if not specified in input (default GET)

    Input: JSON with ``url``, optional ``method``, ``params``, ``headers``, ``body``.
    """

    skill_version = "1.0.0"
    input_schema = HttpRequestInput

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(
            name="http_request",
            description=(
                "Make an HTTP GET or POST request to any URL and return the response. "
                "Input: {'url': '...', 'method': 'GET', 'params': {}, 'headers': {}, 'body': {}}"
            ),
            config=config,
        )

    def run(self, input: str) -> str:
        try:
            params = json.loads(input) if input.strip().startswith("{") else {"url": input.strip()}

            url = params.get("url", "").strip()
            if not url:
                return json.dumps({"error": "No URL provided"})

            method = (params.get("method") or self.config.get("method", "GET")).upper()
            query_params: Dict = params.get("params", {})
            extra_headers: Dict = params.get("headers", {})
            body: Dict = params.get("body", {})
            timeout = int(self.config.get("timeout", 15))

            # Merge config-level default headers
            headers = {**self.config.get("default_headers", {}), **extra_headers}

            # Append query string for GET
            if query_params and method == "GET":
                url = f"{url}?{urllib.parse.urlencode(query_params)}"

            # Encode body for POST
            data = None
            if method == "POST" and (body or query_params):
                payload = body or query_params
                data = json.dumps(payload).encode("utf-8")
                headers.setdefault("Content-Type", "application/json")

            req = urllib.request.Request(url, data=data, headers=headers, method=method)

            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                    status = resp.status
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8", errors="replace")
                status = e.code

            # Try to parse JSON response, fall back to raw text
            try:
                body_parsed = json.loads(raw)
            except json.JSONDecodeError:
                body_parsed = raw

            return json.dumps({"url": url, "method": method, "status": status, "body": body_parsed})

        except urllib.error.URLError as e:
            return json.dumps({"error": f"Request failed: {e.reason}"})
        except Exception as e:
            return json.dumps({"error": f"HTTP request failed: {e}"})
