"""Shared Firecrawl backend for the ``web_search`` and ``scrape_url`` built-ins.

Firecrawl (https://firecrawl.dev) is a free-tier web search + scrape API that returns clean,
LLM-ready content. Both built-ins reach it through this one thin helper so the import of the
optional ``firecrawl-py`` package lives in a single place and stays lazy — installing AgentShip
does not require Firecrawl unless an agent actually uses these tools.
"""

from __future__ import annotations

from typing import Any


def firecrawl_client(api_key: str) -> Any:
    """Construct a Firecrawl v2 client, importing ``firecrawl-py`` lazily.

    Raises :class:`ImportError` when the optional ``firecrawl-py`` package is not installed, so the
    calling tool can turn that into an actionable "pip install firecrawl-py" message rather than a
    crash.
    """
    from firecrawl import Firecrawl

    return Firecrawl(api_key=api_key)
