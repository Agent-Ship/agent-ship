"""Offline proof of the chat UI's plumbing — the machinery ``demos/chat_ui.py`` owns.

The browser app is a thin shell around one async handler, ``respond(message, history,
agent_label, state)``. These tests drive that handler and its helpers directly — no browser — to
pin the three things the UI is responsible for:

* it runs a **real** agent and keeps ONE ``session_id`` across turns (so durable agents remember);
* it holds a pending resume token between turns and feeds a yes/no reply back into the agent's
  ``interrupt()`` as the right resume value (``{"approved": bool}`` for a confirm-before-write);
* a build/run failure is shown in the chat, not crashed.

The agent's own model behaviour is exercised live in ``test_deep_research`` and the slice tests;
here everything is offline (a fake chat model, or a fake agent) so it stays deterministic.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# The demo repo ships YAML + tests (not an installed package), so put its root on the path for the
# ``demos`` import (pytest only adds ``tests/`` by default).
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

gr = pytest.importorskip("gradio", reason="the chat UI needs gradio (a dev dependency)")

import agentship_langgraph.models as models_module  # noqa: E402
from langchain_core.language_models.fake_chat_models import FakeListChatModel  # noqa: E402


@pytest.fixture
def offline_env(monkeypatch):
    """Open the UI's key gate and drop any Postgres store so runs stay in-memory and offline."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-so-the-ui-gate-opens")
    monkeypatch.delenv("AGENT_SESSION_STORE_URI", raising=False)


def test_every_agent_in_the_picker_builds():
    """Every YAML in the picker must build, so the dropdown never offers a broken entry.

    Building resolves the spec, model, and tools but makes no model call, so this is offline. A
    picker entry that needs external setup (an MCP server) is deliberately excluded from ``AGENTS``
    — this test is what keeps that list honest.
    """
    from agentship import build_agent
    from demos.chat_ui import AGENTS

    for label, path in AGENTS.items():
        agent = build_agent(path)
        assert agent.spec.name, f"{label} ({path}) built without a name"


def test_resume_value_maps_confirm_write_and_passes_other_text_through():
    """A yes/no reply to a write pause becomes ``{"approved": bool}``; other text passes through."""
    from demos.chat_ui import _resume_value

    write = {"action": "confirm_write", "tool": "save_note", "args": {"text": "buy milk"}}
    assert _resume_value("yes", write) == {"approved": True}
    assert _resume_value("no", write) == {"approved": False}
    # A free-form (non confirm-write) interrupt gets the raw text, so the UI drives any interrupt.
    assert _resume_value("use 3 sources", {"question": "How many sources?"}) == "use 3 sources"


def test_pause_message_renders_a_write_approval_prompt():
    """A confirm-write interrupt is rendered as a clear approve/reject prompt naming the tool."""
    from demos.chat_ui import _pause_message

    msg = _pause_message({"action": "confirm_write", "tool": "save_note", "args": {"text": "hi"}})
    assert "Approve write" in msg
    assert "save_note" in msg
    assert "yes" in msg


async def test_a_real_agent_answers_and_the_thread_is_stable_across_turns(offline_env, monkeypatch):
    """respond() runs a real ReAct agent and reuses one session_id across turns (no per-turn reset).

    Reusing the thread is exactly what gives a durable agent conversation memory; here a fake model
    stands in for the LLM so the assertion is deterministic and offline.
    """
    from demos.chat_ui import _new_state, respond

    fake = FakeListChatModel(responses=["Hello! How can I help?", "The sky is blue."])
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: fake)
    label = "assistant — plain single agent"
    state = _new_state()

    history, box, state, _ = await respond("hi", [], label, state)
    assert box == "" and state["token"] is None
    assert history[-1]["content"] == "Hello! How can I help?"
    first_session = state["session_id"]
    assert first_session, "a conversation thread should be opened on the first turn"

    # A second turn on the same agent keeps the SAME thread — the source of conversation memory.
    history, _, state, _ = await respond("why is the sky blue?", history, label, state)
    assert state["session_id"] == first_session
    assert history[-1]["content"] == "The sky is blue."


async def test_ui_holds_the_token_then_resumes_a_paused_run(offline_env, monkeypatch):
    """A paused run's token is held between turns and the next reply resumes it (UI plumbing only).

    A fake agent stands in for the note-taker: its first run pauses with a confirm-write interrupt,
    and ``engine.resume`` completes when approved. This pins that the UI carries the token across
    turns and feeds the mapped ``{"approved": True}`` decision back in — no model, fully offline.
    """
    import demos.chat_ui as chat_ui
    from demos.chat_ui import _new_state, respond

    pause = SimpleNamespace(
        interrupt={"action": "confirm_write", "tool": "save_note", "args": {"text": "buy milk"}},
        resume_token="tok-123",
        output=None,
    )
    done = SimpleNamespace(interrupt=None, resume_token=None, output="saved: buy milk")

    class _FakeEngine:
        """Stand-in engine whose ``resume`` asserts it got the approved decision + held token."""

        async def resume(self, compiled, token, ctx, *, resume_value):
            """Complete the paused run once the human approves the write."""
            assert token == "tok-123"
            assert resume_value == {"approved": True}
            return done

    async def _run(message, *, session_id=None):
        """First (and only) run pauses for approval."""
        return pause

    fake_agent = SimpleNamespace(
        spec=SimpleNamespace(name="note-taker"),
        compiled=object(),
        engine=_FakeEngine(),
        run=_run,
    )
    monkeypatch.setattr(chat_ui, "build_agent", lambda path: fake_agent)
    label = "note-taker — HITL: pauses for approval before a write"
    state = _new_state()

    # Turn 1: the agent pauses for approval; the UI holds the resume token, nothing completes.
    history, _, state, _ = await respond("save a note: buy milk", [], label, state)
    assert state["token"] == "tok-123"
    assert "Approve write" in history[-1]["content"]

    # Turn 2: replying "yes" resumes the run to completion and clears the pending token.
    history, _, state, _ = await respond("yes", history, label, state)
    assert state["token"] is None
    assert history[-1]["content"] == "saved: buy milk"


async def test_a_build_failure_is_shown_in_chat_not_crashed(offline_env, monkeypatch):
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
