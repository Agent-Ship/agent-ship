"""``GET /studio``: the built-in UI is public, is real HTML, and is not part of ``v1``.

The page is only markup and script — the data it shows is fetched by the browser with the
user's own key — so it must load without a credential, or nobody can reach the screen that
asks for one. It must equally stay out of the OpenAPI schema: it is a human UI, not a
contract a generated client should discover.
"""

from __future__ import annotations

import pytest
from agentship.auth import ApiKeyAuthProvider, EnvApiKeyStore
from agentship_service import create_app
from agentship_service.routers.studio import STUDIO_PAGE
from fastapi.testclient import TestClient


def _client() -> TestClient:
    """A client for a service whose only key is ``secret`` — deliberately never sent."""
    auth = ApiKeyAuthProvider(
        EnvApiKeyStore(raw='[{"key": "secret", "user": "u1", "tenant": "t1", "scopes": ["*"]}]')
    )
    return TestClient(create_app(auth=auth))


def test_studio_loads_without_a_credential() -> None:
    """``GET /studio`` with no ``Authorization`` header returns 200 HTML, not a 401."""
    resp = _client().get("/studio")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "<!DOCTYPE html>" in resp.text
    assert "AgentShip Studio" in resp.text


def test_studio_is_not_in_the_openapi_schema() -> None:
    """The page is a human UI, so no generated client sees it as a ``v1`` operation."""
    schema = _client().get("/openapi.json").json()
    assert "/studio" not in schema["paths"]


def test_studio_page_is_self_contained() -> None:
    """The page ships whole: no build step, no npm, and no runtime CDN fetch.

    A Studio that needs the public internet is a Studio that is blank on an air-gapped or
    egress-filtered deployment — exactly where a debug UI matters most.
    """
    page = STUDIO_PAGE.read_text(encoding="utf-8")
    assert "<style>" in page and "<script>" in page
    assert "cdn." not in page
    assert "https://fonts." not in page
    assert "<script src=" not in page


def test_studio_drives_the_real_v1_surface() -> None:
    """The page calls the endpoints this service actually exposes, not invented ones."""
    page = STUDIO_PAGE.read_text(encoding="utf-8")
    for path in ('"/healthz"', '"/v1/agents"', '":invoke"', '":stream"', '":resume"'):
        assert path in page


def test_studio_does_not_call_the_api_before_it_has_a_key() -> None:
    """Opening Studio with no key saved must not fire a call it knows will 401.

    ``start()`` used to call ``loadAgents()`` unconditionally and only then open the key
    dialog. With an empty localStorage — a fresh browser, a private window, or the other
    of ``localhost``/``127.0.0.1`` (separate origins, separate storage) — that call went
    out with no Authorization header, so the first thing Studio painted was a raw
    RFC-9457 problem document about a missing API key. The user had done nothing wrong
    and the fix was already on screen behind the banner.

    The catalog load is now conditional on having a key.
    """
    page = STUDIO_PAGE.read_text()

    start = page[page.index("function start()") :]
    assert "if (apiKey()) {" in start, "the catalog load must be gated on having a key"
    unconditional = "\n  loadAgents();\n  if (!apiKey()) openKeyDialog();"
    assert unconditional not in start, "loadAgents() is still called before a key exists"


def test_studio_explains_a_401_instead_of_showing_the_problem_json() -> None:
    """A missing/rejected key reads as an instruction, not as a wire-format error body.

    ``{"type": "about:blank", "title": "Unauthorized", ...}`` is the right thing to send a
    client and the wrong thing to show a person. When the cause is the key, Studio says so
    in words and reopens the dialog that fixes it.
    """
    page = STUDIO_PAGE.read_text()

    assert "needs your API key" in page or "Enter your API key" in page


def test_the_bare_root_sends_a_browser_to_studio() -> None:
    """``GET /`` redirects to ``/studio`` instead of answering 401.

    Typing the host with no path is the first thing anyone does with a running service.
    There was no ``/`` route, and the auth middleware runs ahead of routing, so the root
    came back as an RFC-9457 "no API key" document rather than a 404 — telling the user
    they were unauthenticated when the real answer was "the UI is over here". That is the
    401 people kept hitting without ever having called an API.

    Unauthenticated on purpose: it is a redirect to a public page, and a redirect that
    demanded a credential would put the same wall back one hop later.
    """
    resp = _client().get("/", follow_redirects=False)

    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/studio"


def test_studio_shows_what_the_agent_is_doing_while_it_works() -> None:
    """A turn in progress says what it is doing, not just that something is happening.

    Studio showed a blinking caret and nothing else. Tool calls went to the trace panel,
    which is collapsed by default, so a turn that spent fifteen seconds on a web search
    looked identical to one that was hung: no tool name, no thinking, no progress.

    Every frame needed was already on the wire — `reasoning`, `tool_call`, `tool_result`
    are all in the stream contract. This just renders them where the user is looking.
    """
    page = STUDIO_PAGE.read_text()

    assert "function statusFor" in page, "no status line is derived from the stream frames"
    for frame_type in ("reasoning", "tool_call", "tool_result"):
        assert f'"{frame_type}"' in page, f"{frame_type} frames are not turned into a status"
    assert "Thinking" in page and "Calling" in page


def test_the_status_line_names_the_tool_being_called() -> None:
    """ "Calling web_search…", not "Running a tool…".

    Which tool is the whole point: it is the difference between "this agent is searching
    the web" and "this agent is stuck". The name is already in the frame's data.
    """
    page = STUDIO_PAGE.read_text()

    status = page[page.index("function statusFor") :]
    status = status[: status.index("\n}")]
    assert "data.tool" in status, "the tool name in the frame is not used in the status"


def test_studio_is_never_served_from_a_stale_browser_cache() -> None:
    """``GET /studio`` forbids caching, so a rebuilt image is what you actually see.

    The page carries no version in its URL, so with no cache headers a browser is free to
    heuristically cache it — and did. A user who rebuilt the container kept being served
    the previous UI and reasonably concluded the change had not shipped. The handler
    already re-reads the file per request for exactly this reason; without the header that
    only defeats the server's cache, not the browser's.
    """
    resp = _client().get("/studio")

    assert "no-store" in resp.headers.get("cache-control", "")


def test_each_step_is_kept_on_its_own_line_not_overwritten() -> None:
    """The steps stack up, so a finished turn shows what it actually did.

    A single line that rewrites itself loses the history: a turn that thought, searched
    the web, then read the result showed only whichever step happened to be last, and a
    fast step was overwritten before it could be read. Stacking them leaves a record —
    Thinking, Calling web_search, Reading web_search result — one under the other.
    """
    page = STUDIO_PAGE.read_text()

    assert "function addStep" in page, "steps still replace one another instead of stacking"
    steps = page[page.index("function addStep") :]
    steps = steps[: steps.index("\n}")]
    assert "appendChild" in steps, "a new step must be appended, not overwrite the previous"


def test_the_first_thing_a_turn_says_is_that_it_is_thinking() -> None:
    """The opening status is "Thinking…" — what the model is actually doing first."""
    page = STUDIO_PAGE.read_text()

    assert "Thinking" in page
    assert 'addStep(body, "Working' not in page, "the opening step should say Thinking"


def test_a_non_streaming_turn_also_says_it_is_working() -> None:
    """With streaming off there are no frames — and so, previously, no status at all.

    The status was driven entirely by stream frames, so turning streaming off in Studio
    took the whole feature with it: the request sat there with a caret and nothing else,
    which is the exact complaint the status line exists to answer.
    """
    page = STUDIO_PAGE.read_text()

    invoke = page[page.index("async function invokeTurn") :]
    invoke = invoke[: invoke.index("\n}\n")]
    assert "addStep" in invoke, "the non-streaming path shows no status"


def test_studio_only_offers_the_microphone_to_an_agent_that_declares_voice() -> None:
    """The mic is gated on the agent's own spec, not shown for every agent and left to fail.

    It used to be enabled everywhere, so pressing it on a text-only agent opened a socket whose
    only possible outcome was a refusal — `agent has no voice: block`. Studio already knows
    which agents declare voice (it is where the `voice` badge comes from), so it had the answer
    before the click and asked the server anyway. An affordance that can only fail reads as a
    broken feature rather than an absent one.

    Asserted against the shipped asset because there is no build step: this file *is* Studio.
    """
    studio = STUDIO_PAGE.read_text(encoding="utf-8")

    assert "function showVoiceAffordance" in studio, "the gate must exist"
    assert "mic.disabled = !speakable" in studio, "a non-speakable agent's mic is disabled"
    assert "Boolean((card.spec || {}).voice)" in studio, (
        "speakable is read from the agent's own spec, not from engine capabilities — every "
        "agent on one engine shares those, which is how the badges got this wrong before"
    )
    assert "showVoiceAffordance(card)" in studio, "and it has to run on agent selection"


def _render_markdown(text: str) -> str:
    """Return what Studio's own renderer produces for ``text``.

    The renderer is JavaScript inside the page, so it is extracted and run under node.
    Asserting on the page SOURCE instead -- which is what the tests above do, for good reasons
    of their own -- cannot catch a rendering bug: the strings were all present and correct the
    whole time a numbered list was arriving on screen as "1. 1. **[Deep Dive...]**".
    """
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("needs node to run the page's own JavaScript")

    page = STUDIO_PAGE.read_text(encoding="utf-8")
    start = page.index("function renderMarkdown(text) {")
    end = page.index("\n}\n", page.index("function inlineMarkdown(text) {")) + 2
    escape = page[page.index("function escapeHtml") :]
    escape = escape[: escape.index("\n}\n") + 2]
    call = f"process.stdout.write(renderMarkdown({json.dumps(text)}))"
    script = f"{escape}\n{page[start:end]}\n{call}"
    return subprocess.run(
        [node, "-e", script], capture_output=True, text=True, check=True, timeout=30
    ).stdout


def test_a_numbered_list_renders_as_a_list() -> None:
    """Models number their sources, and a numbered list must not arrive as prose.

    Only ``-``, ``*`` and ``+`` were recognised, so ``1.`` fell through to a paragraph and a
    list of citations ran together on one line with its own markers showing.
    """
    html = _render_markdown("Sources:\n\n1. [One](https://a.example)\n2. Two")
    assert "<ol>" in html and html.count("<li>") == 2
    assert '<a href="https://a.example"' in html
    assert "1." not in html, "the marker is the list's job to draw, not the text's"


def test_an_unfinished_code_fence_still_renders_as_code() -> None:
    """Every fence is unterminated while a reply streams, and must not leak its marker.

    The closing ``` has not been generated yet, so a regex needing both ends matched nothing and
    the reader watched "```javascript" sit on screen as literal text.
    """
    html = _render_markdown("Config:\n\n```javascript\nPods:\n  - name: vllm\n")
    assert "<pre><code>" in html
    assert "```" not in html


def test_a_heading_does_not_need_a_blank_line_before_it() -> None:
    """``## Heading`` on the line after prose is still a heading.

    Blocks used to be split on blank lines and matched whole, so a heading that followed a
    sentence directly was rendered as part of that sentence, hashes and all.
    """
    html = _render_markdown("Some text\n## A Heading\nmore text")
    assert "<h2>A Heading</h2>" in html
    assert "##" not in html


def test_a_nested_list_is_nested_inside_its_parent_item() -> None:
    """Nesting must produce valid HTML, not a list as a sibling of a list item.

    A nested ``<ol>`` placed next to ``<li>`` rather than inside it is invalid, and browsers
    indent it inconsistently instead of refusing it.
    """
    html = _render_markdown("1. Outer\n  - inner\n2. Second")
    assert "<li>Outer<ul>" in html, "the child list must open inside the parent's item"
    assert "</ul></li>" in html, "and closing it must close that item"


def test_markup_in_a_reply_cannot_become_markup_on_the_page() -> None:
    """Model output is escaped before any rule runs, so a reply cannot inject HTML."""
    html = _render_markdown("<img src=x onerror=alert(1)> and <b>bold</b>")
    assert "<img" not in html and "<b>" not in html
    assert "&lt;img" in html
