"""Phase 03 · C2 — the vendor-neutral ``Tool`` + the built-in skills (calculator first).

A ``Tool`` is a name + description + optional args schema wrapping a plain callable; a built-in
skill is just a registered ``Tool``. These tests exercise the abstraction and the calculator
end-to-end offline (no model, no network) — the calculator carries forward the old repo's safe
AST-only evaluation (no ``eval``), so no functionality is lost.
"""

from __future__ import annotations

import json

import pytest
from agentship.errors import SpecError
from agentship.tools import TOOLS, Tool, resolve_tool


async def test_calculator_evaluates_a_safe_expression():
    """The built-in calculator resolves by name and evaluates arithmetic to a numeric result."""
    calc = resolve_tool("calculator")
    assert isinstance(calc, Tool)
    assert calc.name == "calculator"
    out = json.loads(await calc.run(expression="2 + 2 * 10"))
    assert out["result"] == 22


async def test_calculator_rejects_an_unsafe_expression():
    """Only arithmetic is allowed — an attempted call/import is refused, never executed."""
    calc = TOOLS.get("calculator")
    out = json.loads(await calc.run(expression="__import__('os').system('echo hi')"))
    assert "error" in out


async def test_calculator_reports_division_by_zero():
    """A math error returns a clean error payload, not a crash."""
    calc = TOOLS.get("calculator")
    out = json.loads(await calc.run(expression="1/0"))
    assert "error" in out


def test_resolve_unknown_tool_is_a_spec_error():
    """An unresolvable tool reference fails fast with an actionable :class:`SpecError`."""
    with pytest.raises(SpecError, match="nope-not-a-tool"):
        resolve_tool("nope-not-a-tool")


@pytest.fixture
def http_server():
    """A tiny local HTTP server returning JSON on GET — for the http_request tool test."""
    import http.server
    import socketserver
    import threading

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib handler name
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"hello":"world"}')

        def log_message(self, *args):  # silence the server's stderr logging
            pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv.server_address[1]
    finally:
        srv.shutdown()


async def test_http_request_performs_a_get(http_server):
    """The built-in http_request tool GETs a URL and returns status + body."""
    tool = resolve_tool("http_request")
    assert tool.name == "http_request"
    out = json.loads(await tool.run(url=f"http://127.0.0.1:{http_server}/"))
    assert out["status"] == 200
    assert "world" in out["body"]


async def test_http_request_empty_url_is_a_clean_error():
    """A missing URL returns a clean error payload, not a crash."""
    out = json.loads(await resolve_tool("http_request").run(url=""))
    assert "error" in out


async def test_web_search_without_any_key_explains_setup(monkeypatch):
    """With neither provider key set, web_search returns an actionable setup error naming both."""
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    tool = resolve_tool("web_search")
    assert tool.name == "web_search"
    out = json.loads(await tool.run(query="agentship"))
    assert "error" in out
    message = out.get("error", "") + out.get("setup", "")
    assert "FIRECRAWL_API_KEY" in message and "BRAVE_API_KEY" in message


async def test_web_search_uses_firecrawl_when_only_that_key_is_set(monkeypatch):
    """With FIRECRAWL_API_KEY (and no Brave key), web_search uses the Firecrawl backend.

    The Firecrawl fetch is stubbed so the test is offline and deterministic — it pins provider
    selection and result normalization, not the network call.
    """
    import importlib

    # ``builtins/__init__`` binds the name ``web_search`` to the Tool, so ``import … as`` would grab
    # the tool, not the module — import_module reliably returns the module to patch its seam.
    ws = importlib.import_module("agentship.tools.builtins.web_search")

    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setattr(
        ws,
        "_firecrawl_results",
        lambda query, num, key: [{"title": "T", "url": "https://x.test", "description": "d"}],
    )
    out = json.loads(await resolve_tool("web_search").run(query="agentship"))
    assert out["provider"] == "firecrawl"
    assert out["results"][0]["url"] == "https://x.test"


async def test_web_search_without_the_firecrawl_package_says_how_to_install(monkeypatch):
    """A key set but the package missing yields a 'pip install' message, not a crash."""
    import importlib

    ws = importlib.import_module("agentship.tools.builtins.web_search")

    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")

    def _missing(query, num, key):
        raise ImportError("No module named 'firecrawl'")

    monkeypatch.setattr(ws, "_firecrawl_results", _missing)
    out = json.loads(await resolve_tool("web_search").run(query="agentship"))
    assert "error" in out
    assert "firecrawl-py" in (out.get("error", "") + out.get("setup", ""))


async def test_web_search_empty_query_is_a_clean_error():
    """An empty query returns a clean error, not a crash."""
    out = json.loads(await resolve_tool("web_search").run(query="  "))
    assert "error" in out


async def test_scrape_url_without_a_key_explains_setup(monkeypatch):
    """scrape_url resolves and, with no FIRECRAWL_API_KEY, returns an actionable setup error."""
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    tool = resolve_tool("scrape_url")
    assert tool.name == "scrape_url"
    out = json.loads(await tool.run(url="https://example.com"))
    assert "error" in out
    assert "FIRECRAWL_API_KEY" in (out.get("error", "") + out.get("setup", ""))


async def test_scrape_url_returns_markdown_via_firecrawl(monkeypatch):
    """With a key set, scrape_url returns the page markdown; a long page is flagged truncated."""
    import importlib

    sc = importlib.import_module("agentship.tools.builtins.scrape_url")

    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    long_page = "x" * (sc._MAX_MARKDOWN_CHARS + 5)
    monkeypatch.setattr(sc, "_firecrawl_markdown", lambda url, key: long_page)
    out = json.loads(await resolve_tool("scrape_url").run(url="https://example.com"))
    assert out["provider"] == "firecrawl"
    assert len(out["markdown"]) == sc._MAX_MARKDOWN_CHARS
    assert out["truncated"] is True


async def test_scrape_url_empty_url_is_a_clean_error():
    """An empty URL returns a clean error, not a crash."""
    out = json.loads(await resolve_tool("scrape_url").run(url="  "))
    assert "error" in out
