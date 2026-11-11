"""Declarative multi-agent: a supervisor authored purely in YAML via ``members:`` + ``ref:``.

No ``code:`` factory and no hand-written graph — a team YAML lists its members, each a ``ref:`` to
its own sub-agent YAML (the old-repo layout). The engine resolves the members, derives the routing
from them, and coordinates a real classify → route → dispatch → resolve turn. These offline tests
use ``echo`` sub-agents so no network is needed; a fake classifier picks the member.
"""

from __future__ import annotations

import textwrap

import agentship_langgraph.models as models_module
from agentship.runtime import build_agent
from langchain_core.language_models.fake_chat_models import FakeListChatModel


def _team(tmp_path) -> str:
    """Write two echo sub-agent YAMLs + a team YAML that references them; return the team path."""
    (tmp_path / "billing.yaml").write_text("name: billing\nengine: echo\n")
    (tmp_path / "faq.yaml").write_text("name: faq\nengine: echo\n")
    team = tmp_path / "team.yaml"
    team.write_text(
        textwrap.dedent(
            """
            name: triage
            engine: langgraph
            model: x
            members:
              - name: billing
                ref: billing.yaml
                description: billing, invoices, and payments
              - name: faq
                ref: faq.yaml
                description: general questions about the service
            """
        )
    )
    return str(team)


async def test_declarative_members_route_and_answer_through_the_engine(tmp_path, monkeypatch):
    """A YAML-only supervisor routes to the classified member sub-agent and returns its answer."""
    monkeypatch.setattr(
        models_module, "resolve_model", lambda *a, **k: FakeListChatModel(responses=["billing"])
    )
    agent = build_agent(_team(tmp_path))  # no code:, no template — members drive coordination

    result = await agent.run("I was double charged on my invoice", user_id="alice")
    # The billing member (an echo sub-agent) handled it — its echo is in the answer.
    assert "echo: I was double charged on my invoice" in result.output


async def test_declarative_supervisor_exposes_members(tmp_path, monkeypatch):
    """The built team surfaces its coordinated members (what the multi_agent cell checks)."""
    monkeypatch.setattr(
        models_module, "resolve_model", lambda *a, **k: FakeListChatModel(responses=["faq"])
    )
    agent = build_agent(_team(tmp_path))
    assert agent.compiled.members  # non-empty coordinated team
    assert set(agent.compiled.members) == {"billing", "faq"}


async def test_each_turn_in_a_thread_is_routed_afresh(tmp_path, monkeypatch):
    """A follow-up turn in the same thread is classified, dispatched and answered on its own.

    The supervisor's per-turn fields are checkpointed with the thread. Before they were reset at
    the start of a turn, turn two inherited turn one's ``specialist_results``: dispatch saw a retry
    pass with nothing to retry, ran no member, and the team returned turn one's answer again.
    """
    monkeypatch.setattr(
        models_module,
        "resolve_model",
        lambda *a, **k: FakeListChatModel(responses=["billing", "faq"]),
    )
    agent = build_agent(_team(tmp_path))

    first = await agent.run("I was double charged", user_id="alice", session_id="thread-1")
    second = await agent.run("How do I reset my password?", user_id="alice", session_id="thread-1")

    assert "echo: I was double charged" in first.output
    assert "echo: How do I reset my password?" in second.output


async def test_team_params_reach_the_classifier_and_inline_members(tmp_path, monkeypatch):
    """A team's ``params`` apply to every model it builds: the classifier and inline members too.

    The classifier used to re-resolve the team's model id without its params, and inline members
    were built without them. A team pointed at a local server with ``api_base``, or told
    ``reasoning_effort: none``, then classified and answered with the provider defaults.
    """
    calls = []

    def fake_resolve(model, **params):
        calls.append(params)
        return FakeListChatModel(responses=["writer", "Hello there."])

    monkeypatch.setattr(models_module, "resolve_model", fake_resolve)
    team = tmp_path / "team.yaml"
    team.write_text(
        textwrap.dedent(
            """
            name: team
            engine: langgraph
            model: x
            params:
              api_base: http://localhost:9999/v1
            members:
              - name: writer
                description: greetings and small talk
                prompt: Answer in one sentence.
            """
        )
    )
    agent = build_agent(str(team))
    await agent.run("Hi!", user_id="alice")

    assert calls, "no model was resolved"
    assert all(c.get("api_base") == "http://localhost:9999/v1" for c in calls), calls
