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
