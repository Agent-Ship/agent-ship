"""The ``scrape_url`` built-in tool — fetch a web page's full content as clean markdown.

This is what makes deep research actually deep: ``web_search`` returns short snippets, but an agent
that needs to reason over the *content* of a page can call ``scrape_url`` to read it. Backed by
Firecrawl (https://firecrawl.dev), which renders and cleans the page into LLM-ready markdown.

Needs ``FIRECRAWL_API_KEY``; without it the tool returns an actionable setup message (never a
crash, never a hardcoded secret). The markdown is truncated to keep a single page from blowing the
model's context — the ``truncated`` flag says whether that happened.
"""

from __future__ import annotations

import json
import os

from pydantic import BaseModel, Field

from ..tool import Tool
from ._firecrawl import firecrawl_client

#: Cap a scraped page so one long article can't swamp the model's context window.
_MAX_MARKDOWN_CHARS = 8000


class ScrapeUrlArgs(BaseModel):
    """Arguments for a scrape: the URL to fetch and read."""

    url: str = Field(description="The URL of the page to fetch and read as markdown")


def _firecrawl_markdown(url: str, api_key: str) -> str:
    """Fetch a page's markdown via Firecrawl (import kept lazy; may raise on network/API errors)."""
    client = firecrawl_client(api_key)
    document = client.scrape(url, formats=["markdown"])
    return getattr(document, "markdown", None) or ""


def _scrape_url(url: str) -> str:
    """Scrape ``url`` to markdown via Firecrawl, returning JSON content or a setup/error payload."""
    url = url.strip()
    if not url:
        return json.dumps({"error": "no url provided"})

    api_key = os.environ.get("FIRECRAWL_API_KEY")
    if not api_key:
        return json.dumps(
            {
                "error": "scraping is not configured: set the FIRECRAWL_API_KEY env variable",
                "setup": "Get a free key at https://firecrawl.dev",
            }
        )

    try:
        markdown = _firecrawl_markdown(url, api_key)
    except ImportError:
        return json.dumps(
            {
                "error": "scraping needs the 'firecrawl-py' package",
                "setup": "pip install firecrawl-py",
            }
        )
    except Exception as exc:  # noqa: BLE001 - any API/network failure becomes a clean payload
        return json.dumps({"error": f"scrape failed: {exc}"})

    truncated = len(markdown) > _MAX_MARKDOWN_CHARS
    return json.dumps(
        {
            "url": url,
            "provider": "firecrawl",
            "markdown": markdown[:_MAX_MARKDOWN_CHARS],
            "truncated": truncated,
        }
    )


#: The built-in page-scraping tool, registered under the name ``scrape_url``.
scrape_url = Tool(
    name="scrape_url",
    description="Fetch the full text content of a web page (given its URL) as clean markdown.",
    func=_scrape_url,
    args_schema=ScrapeUrlArgs,
)
