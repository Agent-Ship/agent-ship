"""The ``web_search`` built-in tool — Brave web search, carried forward from the old repo.

Searches the web via the Brave Search API when ``BRAVE_API_KEY`` is set, returning a JSON list of
``{title, url, description}`` results. With no key it returns an actionable setup message (never a
hardcoded secret). A faithful carry-forward of ``agent-ship``'s ``WebSearchSkill`` (Brave provider);
registered as a :class:`~agentship.tools.tool.Tool` named ``web_search``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from pydantic import BaseModel, Field

from ..tool import Tool

_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


class WebSearchArgs(BaseModel):
    """Arguments for a web search: a query and how many results to return."""

    query: str = Field(description="Search query string")
    num_results: int = Field(default=5, description="Number of results to return")


def _web_search(query: str, num_results: int = 5) -> str:
    """Search the web via Brave, returning JSON results or an actionable error."""
    query = query.strip()
    if not query:
        return json.dumps({"error": "no search query provided"})

    api_key = os.environ.get("BRAVE_API_KEY")
    if not api_key:
        return json.dumps(
            {
                "error": "web search is not configured: set the BRAVE_API_KEY environment variable",
                "setup": "Get a free BRAVE_API_KEY at https://brave.com/search/api/",
            }
        )

    url = f"{_BRAVE_ENDPOINT}?{urllib.parse.urlencode({'q': query, 'count': num_results})}"
    request = urllib.request.Request(  # noqa: S310 - fixed https endpoint
        url, headers={"Accept": "application/json", "X-Subscription-Token": api_key}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, TimeoutError) as exc:
        return json.dumps({"error": f"web search failed: {exc}"})

    results = [
        {"title": r.get("title"), "url": r.get("url"), "description": r.get("description")}
        for r in data.get("web", {}).get("results", [])[:num_results]
    ]
    return json.dumps({"query": query, "results": results})


#: The built-in web-search tool, registered under the name ``web_search``.
web_search = Tool(
    name="web_search",
    description="Search the web for up-to-date information and return a list of result links.",
    func=_web_search,
    args_schema=WebSearchArgs,
)
