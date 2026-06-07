"""Web search skill — stub with configurable provider support."""

import json
import os
from typing import Any, Dict

from pydantic import BaseModel, Field

from src.agent_framework.skills.base_skill import BaseSkill


class WebSearchInput(BaseModel):
    query: str = Field(description="Search query string")
    num_results: int = Field(default=5, description="Number of results to return (default 5)")


class WebSearchSkill(BaseSkill):
    """Search the web for information.

    Out of the box this is a stub that explains how to configure a real provider.
    To enable live search, set ``provider`` and the corresponding API key env var
    in the skill config:

    .. code-block:: yaml

        skills:
          - id: search
            template: web_search
            config:
              provider: brave          # currently supported: brave
              api_key_env: BRAVE_API_KEY

    The skill is intentionally structured so that adding a new provider
    only requires implementing ``_search_<provider>()`` below.
    """

    skill_version = "1.0.0"
    input_schema = WebSearchInput

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(
            name="web_search",
            description=(
                "Search the web for up-to-date information. "
                "Input: {'query': '<search terms>', 'num_results': 5}"
            ),
            config=config,
        )

    def run(self, input: str) -> str:
        try:
            params = json.loads(input) if input.strip().startswith("{") else {"query": input.strip()}
            query = params.get("query", "").strip()
            num_results = int(params.get("num_results", 5))

            if not query:
                return json.dumps({"error": "No search query provided"})

            provider = self.config.get("provider", "stub")

            if provider == "brave":
                return self._search_brave(query, num_results)
            else:
                return self._stub_response(query)

        except Exception as e:
            return json.dumps({"error": f"Web search failed: {e}"})

    def _search_brave(self, query: str, num_results: int) -> str:
        api_key_env = self.config.get("api_key_env", "BRAVE_API_KEY")
        api_key = os.environ.get(api_key_env)
        if not api_key:
            return json.dumps({
                "error": f"Missing API key. Set the {api_key_env} environment variable.",
                "setup": "Get a free key at https://brave.com/search/api/",
            })

        import urllib.request
        import urllib.parse

        url = f"https://api.search.brave.com/res/v1/web/search?q={urllib.parse.quote(query)}&count={num_results}"
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": api_key,
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        results = [
            {"title": r.get("title"), "url": r.get("url"), "description": r.get("description")}
            for r in data.get("web", {}).get("results", [])
        ]
        return json.dumps({"query": query, "results": results, "provider": "brave"})

    def _stub_response(self, query: str) -> str:
        return json.dumps({
            "query": query,
            "results": [],
            "note": (
                "web_search skill is running in stub mode. "
                "To enable live search, configure a provider in your agent YAML:\n"
                "  skills:\n"
                "    - id: search\n"
                "      template: web_search\n"
                "      config:\n"
                "        provider: brave\n"
                "        api_key_env: BRAVE_API_KEY"
            ),
        })
