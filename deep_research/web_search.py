"""The web search the deep-research loop calls each round — Brave when configured, else a stub.

The deep-research graph calls :func:`search_web` directly from its nodes (it *owns* the loop, so
it does not route search through model tool-calling). This mirrors the built-in ``web_search`` tool
(Brave Search API, carried over from the old repo) but returns a plain list of result dicts —
``{title, url, snippet}`` — because the graph consumes results as data, not as a JSON string handed
back to a model.

With ``BRAVE_API_KEY`` set it hits Brave for real, up-to-date results. Without a key it returns a
single clearly-labelled stub result (never a hardcoded secret, never a silent empty list) so the
loop still runs end-to-end offline; the offline tests monkeypatch this function with a deterministic
fake instead. This keeps the demo honest: the plumbing (rounds, checkpoints, human pause, resume)
is fully exercised with or without a search key.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


def search_web(query: str, num_results: int = 5) -> list[dict]:
    """Return up to ``num_results`` web results for ``query`` as ``{title, url, snippet}`` dicts.

    Uses the Brave Search API when ``BRAVE_API_KEY`` is set. When no key is present — or the
    request fails — it returns a single stub result describing what happened, so the research loop
    always has *something* to reflect on and never crashes on a missing key or a flaky network. The
    stub's ``url`` is empty so callers can tell a real hit from a placeholder.
    """
    query = query.strip()
    if not query:
        return []

    api_key = os.environ.get("BRAVE_API_KEY")
    if not api_key:
        return [
            {
                "title": f"[no BRAVE_API_KEY] would search: {query}",
                "url": "",
                "snippet": (
                    "Web search is not configured. Set BRAVE_API_KEY "
                    "(https://brave.com/search/api/) to get real results for this query."
                ),
            }
        ]

    url = f"{_BRAVE_ENDPOINT}?{urllib.parse.urlencode({'q': query, 'count': num_results})}"
    request = urllib.request.Request(  # noqa: S310 - fixed https endpoint
        url, headers={"Accept": "application/json", "X-Subscription-Token": api_key}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, TimeoutError) as exc:
        return [{"title": f"[search failed] {query}", "url": "", "snippet": str(exc)}]

    return [
        {
            "title": result.get("title"),
            "url": result.get("url"),
            "snippet": result.get("description"),
        }
        for result in data.get("web", {}).get("results", [])[:num_results]
    ]
