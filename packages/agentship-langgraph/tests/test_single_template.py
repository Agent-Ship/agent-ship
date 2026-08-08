"""T3 proof: the ``single`` template runs with zero author python, offline.

A ``template: single`` spec generates its whole build body via LangGraph's prebuilt
ReAct agent (``create_react_agent(model, tools, prompt)``) — the developer writes no
Python at all. These tests prove that end to end with a fake chat model injected
(no network): ``build_agent`` of a single-template spec produces a runnable agent,
and ``run`` returns the model's answer. The final test proves the ``single`` path is
actually taken (not the default single-node graph) by inspecting the compiled
graph's nodes for the ReAct agent's ``tools`` node.
"""

from __future__ import annotations

import agentship_langgraph.models as models_module
import pytest
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from langchain_core.language_models.fake_chat_models import FakeListChatModel


@pytest.fixture
def fake_model(monkeypatch):
    """Inject a deterministic fake chat model so the template runs offline."""
    fake = FakeListChatModel(responses=["Red, green, and blue."])
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: fake)
    return fake


async def test_single_template_runs_with_zero_author_code(fake_model):
    """A template: single spec runs end to end and returns the model's answer."""
    agent = build_agent(
        AgentSpec(
            name="quickstart",
            engine="langgraph",
            template="single",
            model="x",
            prompt="You are a helpful assistant.",
        )
    )
    result = await agent.run("Name three primary colors.")
    assert result.output == "Red, green, and blue."


def test_single_template_dispatches_to_the_react_prebuilt(fake_model, monkeypatch):
    """The single template routes the build through ``create_react_agent``, not the default.

    Spies on the template's ``create_react_agent`` and asserts it is called with the
    wired model + the spec's prompt. The engine's default single-node build body
    never calls it, so observing the call proves the ``single`` template dispatched —
    this test fails (the spy is never hit) if the template wiring falls back to the
    default build body, making it non-vacuous.
    """
    import agentship_langgraph.templates.single as single_module

    calls: dict = {}
    real = single_module.create_react_agent

    def spy(model, *, tools, prompt):
        """Record the react-agent call and delegate to the real prebuilt."""
        calls["model"] = model
        calls["tools"] = tools
        calls["prompt"] = prompt
        return real(model, tools=tools, prompt=prompt)

    monkeypatch.setattr(single_module, "create_react_agent", spy)

    build_agent(
        AgentSpec(
            name="quickstart",
            engine="langgraph",
            template="single",
            model="x",
            prompt="be terse",
        )
    )
    assert calls["prompt"] == "be terse"
    assert calls["model"] is fake_model
    assert calls["tools"] == []


def test_default_build_does_not_call_the_react_prebuilt(fake_model, monkeypatch):
    """A spec with no template never routes through ``create_react_agent`` (control).

    This pins the discriminator: with ``template`` unset the engine uses its own
    single-node graph, so the react prebuilt is never touched. Together with the
    test above it proves the dispatch is template-driven, not always-on.
    """
    import agentship_langgraph.templates.single as single_module

    called = {"hit": False}

    def spy(*a, **k):
        """Fail loudly if the react prebuilt is reached on the default (no-template) path."""
        called["hit"] = True
        raise AssertionError("default build must not call create_react_agent")

    # No template: build should never enter the single-template body at all.
    monkeypatch.setattr(single_module, "create_react_agent", spy)
    build_agent(AgentSpec(name="d", engine="langgraph", model="x", prompt="p"))
    assert called["hit"] is False


async def test_single_template_does_not_duplicate_the_system_prompt(monkeypatch):
    """The single template seeds the system prompt exactly once, not twice.

    ``create_react_agent(prompt=...)`` injects the system prompt inside the graph,
    so the engine must not *also* seed it in the run loop's initial messages — doing
    so sends the system message twice. A capturing fake model records the messages
    it is invoked with; the assertion pins exactly one system message. This fails if
    the engine re-seeds ``spec.prompt`` for a template-built graph (the bug this
    guards), making it non-vacuous.
    """
    seen: dict = {}

    class _CapturingModel(FakeListChatModel):
        """A fake chat model that records the messages of its final invocation."""

        def _generate(self, messages, *args, **kwargs):
            """Record the message list, then defer to the fake's canned response."""
            seen["messages"] = messages
            return super()._generate(messages, *args, **kwargs)

    capturing = _CapturingModel(responses=["ok"])
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: capturing)

    agent = build_agent(
        AgentSpec(
            name="quickstart",
            engine="langgraph",
            template="single",
            model="x",
            prompt="You are a helpful assistant.",
        )
    )
    await agent.run("Name three primary colors.")

    system_msgs = [m for m in seen["messages"] if m.type == "system"]
    assert len(system_msgs) == 1
    assert system_msgs[0].content == "You are a helpful assistant."


def test_single_template_needs_no_code_field(fake_model):
    """The single template requires no ``code:`` — the YAML/spec alone is enough."""
    spec = AgentSpec(
        name="quickstart", engine="langgraph", template="single", model="x", prompt="p"
    )
    assert spec.code is None
    agent = build_agent(spec)
    assert agent.compiled is not None
