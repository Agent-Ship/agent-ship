"""Offline proof that the chat UI drives an agent through a full pause→resume→report cycle.

The browser app (``demos/chat_ui.py``) is just a thin shell around one async handler,
``respond(message, history, agent_label, state)``. This test drives that handler directly — no
browser, no live model — exactly as three chat turns would:

    turn 1: ask a question        → the deep agent runs a round and PAUSES ("go deeper?")
    turn 2: reply "yes"           → one more round, pauses again (depth is unbounded)
    turn 3: reply "no"            → the run resumes to a synthesized report, token cleared

It asserts the *machinery* the UI owns: a pending resume token is held between turns, a yes/no
reply is mapped to the agent's ``{"go_deeper": bool}`` resume value, and the final report lands
back in the chat history. Uses the same scripted fake model + fake search as
``test_deep_research`` so it stays deterministic and offline.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# The demo repo ships YAML + tests (not an installed package), so put its root on the path for the
# ``deep_research`` package and ``demos`` imports (pytest only adds ``tests/`` by default).
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

gr = pytest.importorskip("gradio", reason="the chat UI needs gradio (a dev dependency)")

import agentship_langgraph.models as models_module  # noqa: E402
import deep_research.graph as graph_module  # noqa: E402
from langchain_core.language_models.chat_models import SimpleChatModel  # noqa: E402


class _ScriptedModel(SimpleChatModel):
    """A deterministic chat model that replies by which research node's prompt is calling it."""

    @property
    def _llm_type(self) -> str:
        """Identify this fake to LangChain's model machinery."""
        return "scripted-chat-ui"

    def _call(self, messages, stop=None, run_manager=None, **kwargs) -> str:
        """Return canned text keyed off the role phrase in the node's system prompt."""
        text = " ".join(str(m.content) for m in messages)
        if "research planner" in text:
            return "initial query one\ninitial query two"
        if "refining a research plan" in text:
            return "deeper query one\ndeeper query two"
        if "research analyst" in text:
            return "REPORT: findings synthesized across all rounds."
        return "ok"


def _fake_search(query: str, num_results: int = 5) -> list[dict]:
    """A deterministic stand-in for web search: one labelled hit per query, no network."""
    return [{"title": f"hit for {query}", "url": "https://example.test", "snippet": "s"}]


@pytest.fixture
def wired(monkeypatch):
    """Wire the scripted model + fake search, gate the key check open, and force a single round."""
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: _ScriptedModel())
    monkeypatch.setattr(graph_module, "search_web", _fake_search)
    monkeypatch.delenv("AGENT_SESSION_STORE_URI", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-so-the-ui-gate-opens")
    monkeypatch.setenv("DEEP_RESEARCH_AUTO_ROUNDS", "1")


def test_every_agent_in_the_picker_builds():
    """Every YAML in the picker must build, so the dropdown never offers a broken entry.

    Building resolves the spec, model, and tools but makes no model call, so this is offline. A
    picker entry that needs external setup (an MCP server, an unregistered tool) is deliberately
    excluded from ``AGENTS`` — this test is what keeps that list honest.
    """
    from agentship import build_agent
    from demos.chat_ui import AGENTS

    for label, path in AGENTS.items():
        agent = build_agent(path)
        assert agent.spec.name, f"{label} ({path}) built without a name"


def test_resume_value_maps_yes_no_and_passes_other_text_through():
    """A yes/no reply becomes ``{"go_deeper": bool}``; any other reply passes through verbatim."""
    from demos.chat_ui import _resume_value

    boolean_payload = {"question": "Go deeper?", "go_deeper": None}
    assert _resume_value("yes", boolean_payload) == {"go_deeper": True}
    assert _resume_value("no", boolean_payload) == {"go_deeper": False}
    # A non-boolean HITL prompt gets the raw text, so the same UI drives arbitrary interrupts.
    assert _resume_value("use 3 sources", {"question": "How many sources?"}) == "use 3 sources"


async def test_chat_turns_pause_then_resume_to_a_report(wired):
    """Three chat turns drive the deep agent: ask → pause → yes → pause → no → report."""
    from demos.chat_ui import AGENTS, _new_state, respond

    deep_label = next(iter(AGENTS))  # the deep-research agent is listed first
    state = _new_state()

    # Turn 1: a fresh question runs one automatic round and pauses for the human.
    history, cleared_box, state, trace = await respond("SMRs in 2026", [], deep_label, state)
    assert cleared_box == ""  # the input box is cleared after each turn
    assert state["token"] is not None, "the agent should pause and hold a resume token"
    assert "Go deeper" in history[-1]["content"]
    # The Trace panel captured the agent's own INFO log lines for the turn.
    assert "deep_research:" in trace

    # Turn 2: approving runs another round and pauses again — the token is still held.
    history, _, state, _ = await respond("yes", history, deep_label, state)
    assert state["token"] is not None
    assert "Go deeper" in history[-1]["content"]

    # Turn 3: declining resumes to the synthesized report and clears the pending token.
    history, _, state, _ = await respond("no", history, deep_label, state)
    assert state["token"] is None
    assert "REPORT" in history[-1]["content"]


async def test_a_build_failure_is_shown_in_chat_not_crashed(wired, monkeypatch):
    """If building/running an agent raises, the error is surfaced in the chat, not propagated."""
    import demos.chat_ui as chat_ui
    from demos.chat_ui import _new_state, respond

    def _boom(*a, **k):
        raise RuntimeError("no MCP server running")

    monkeypatch.setattr(chat_ui, "build_agent", _boom)
    label = next(iter(chat_ui.AGENTS))
    history, box, state, _ = await respond("hello", [], label, _new_state())
    assert box == "" and state["token"] is None
    assert "RuntimeError" in history[-1]["content"] and "no MCP server" in history[-1]["content"]
