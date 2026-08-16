"""The ``web_search`` built-in tool — searches the web via Brave or Firecrawl.

Provider is chosen by which key is set, so an agent's ``web_search`` "just works" for free:

* ``BRAVE_API_KEY`` → the Brave Search API (carried forward from the old repo);
* else ``FIRECRAWL_API_KEY`` → Firecrawl's search (free tier, https://firecrawl.dev);
* else an actionable message naming both keys (never a hardcoded secret).

Either way it returns a JSON ``{query, provider, results: [{title, url, description}]}`` payload.
Registered as a :class:`~agentship.tools.tool.Tool` named ``web_search``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from pydantic import BaseModel, Field

from ..tool import Tool
from ._firecrawl import firecrawl_client

_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


class WebSearchArgs(BaseModel):
    """Arguments for a web search: a query and how many results to return."""

    query: str = Field(description="Search query string")
    num_results: int = Field(default=5, description="Number of results to return")


def _brave_results(query: str, num_results: int, api_key: str) -> list[dict]:
    """Normalize Brave results to ``[{title, url, description}]`` (may raise on network errors)."""
    url = f"{_BRAVE_ENDPOINT}?{urllib.parse.urlencode({'q': query, 'count': num_results})}"
    request = urllib.request.Request(  # noqa: S310 - fixed https endpoint
        url, headers={"Accept": "application/json", "X-Subscription-Token": api_key}
    )
    with urllib.request.urlopen(request, timeout=10) as resp:  # noqa: S310
        data = json.loads(resp.read().decode("utf-8"))
    return [
        {"title": r.get("title"), "url": r.get("url"), "description": r.get("description")}
        for r in data.get("web", {}).get("results", [])[:num_results]
    ]


def _firecrawl_results(query: str, num_results: int, api_key: str) -> list[dict]:
    """Fetch and normalize Firecrawl web results to ``[{title, url, description}]``.

    Raises :class:`ImportError` if ``firecrawl-py`` is missing (the caller renders a setup message)
    and may raise on network/API errors (the caller renders a clean error payload).
    """
    client = firecrawl_client(api_key)
    data = client.search(query, limit=num_results, sources=["web"])
    web = getattr(data, "web", None) or []
    return [
        {
            "title": getattr(r, "title", None),
            "url": getattr(r, "url", None),
            "description": getattr(r, "description", None),
        }
        for r in web[:num_results]
    ]


def _web_search(query: str, num_results: int = 5) -> str:
    """Search the web via Brave (if keyed) or Firecrawl, returning JSON results or a setup error."""
    query = query.strip()
    if not query:
        return json.dumps({"error": "no search query provided"})

    brave_key = os.environ.get("BRAVE_API_KEY")
    if brave_key:
        try:
            results = _brave_results(query, num_results, brave_key)
        except (urllib.error.URLError, ValueError, TimeoutError) as exc:
            return json.dumps({"error": f"web search failed: {exc}"})
        return json.dumps({"query": query, "provider": "brave", "results": results})

    firecrawl_key = os.environ.get("FIRECRAWL_API_KEY")
    if firecrawl_key:
        try:
            results = _firecrawl_results(query, num_results, firecrawl_key)
        except ImportError:
            return json.dumps(
                {
                    "error": "the Firecrawl backend needs the 'firecrawl-py' package",
                    "setup": "pip install firecrawl-py",
                }
            )
        except Exception as exc:  # noqa: BLE001 - any API/network failure becomes a clean payload
            return json.dumps({"error": f"web search failed: {exc}"})
        return json.dumps({"query": query, "provider": "firecrawl", "results": results})

    return json.dumps(
        {
            "error": "web search is not configured: set FIRECRAWL_API_KEY or BRAVE_API_KEY",
            "setup": "Get a free Firecrawl key at https://firecrawl.dev "
            "(or a Brave key at https://brave.com/search/api/)",
        }
    )


#: The built-in web-search tool, registered under the name ``web_search``.
web_search = Tool(
    name="web_search",
    description="Search the web for up-to-date information and return a list of result links.",
    func=_web_search,
    args_schema=WebSearchArgs,
)
