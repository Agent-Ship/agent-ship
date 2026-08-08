"""T3 proof: the ``deepagents`` template builds against a fake model + the doctor guard.

``template: deepagents`` configures the deepagents library's ``create_deep_agent``
over the wired model/tools with zero author python. This wave proves the template
*builds and compiles* offline (a full autonomous turn needs a tool-calling model and
lands with tool execution in Phase 03 — see the module docstring). It also pins the
``agentship doctor`` version guard: the template is written against a specific pinned
deepagents version and the guard flags drift, since deepagents is pre-1.0.
"""

from __future__ import annotations

import agentship_langgraph.models as models_module
import pytest
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from langchain_core.language_models.fake_chat_models import FakeListChatModel

deepagents = pytest.importorskip("deepagents")


@pytest.fixture
def fake_model(monkeypatch):
    """Inject a deterministic fake chat model so the template builds offline."""
    fake = FakeListChatModel(responses=["ok"])
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: fake)
    return fake


def test_deepagents_template_builds_and_compiles(fake_model):
    """A template: deepagents spec builds into a compiled deep-agent graph, offline.

    ``create_deep_agent`` returns an already-compiled LangGraph graph; the engine
    detects that and reuses it. Non-vacuous: if the template did not route through
    deepagents, the compiled artifact would be the engine's own single-node graph,
    not a deep agent — the next test pins that the deepagents path actually ran.
    """
    agent = build_agent(
        AgentSpec(
            name="researcher",
            engine="langgraph",
            template="deepagents",
            model="x",
            prompt="You are an autonomous researcher.",
        )
    )
    assert agent.compiled is not None


def test_deepagents_template_calls_create_deep_agent(fake_model, monkeypatch):
    """The deepagents template routes the build through ``create_deep_agent``.

    Spies on ``deepagents.create_deep_agent`` and asserts it is called with the
    wired model and the spec's prompt as ``system_prompt``. The engine's default
    build never calls it, so observing the call proves the deepagents template
    dispatched (non-vacuous).
    """
    import agentship_langgraph.templates.deepagents_tpl as tpl

    calls: dict = {}
    real = deepagents.create_deep_agent

    def spy(*args, **kwargs):
        """Record the create_deep_agent call and delegate to the real builder."""
        calls.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(tpl, "create_deep_agent", spy, raising=False)
    # create_deep_agent is imported lazily inside the build body, so patch the name
    # on the deepagents module the body imports from.
    monkeypatch.setattr(deepagents, "create_deep_agent", spy)

    build_agent(
        AgentSpec(
            name="researcher",
            engine="langgraph",
            template="deepagents",
            model="x",
            prompt="be autonomous",
        )
    )
    assert calls["model"] is fake_model
    assert calls["system_prompt"] == "be autonomous"


def test_doctor_version_guard_matches_pinned_install():
    """The doctor guard reports OK for the pinned deepagents version.

    The langgraph package pins deepagents to the version the template targets, so on
    a correct install the guard is green and names that exact version. This is the
    signal ``agentship doctor`` surfaces; a drifted install flips it to not-OK.
    """
    from agentship_langgraph.templates.deepagents_tpl import (
        PINNED_DEEPAGENTS_VERSION,
        deepagents_version_ok,
    )

    ok, installed = deepagents_version_ok()
    assert installed == PINNED_DEEPAGENTS_VERSION
    assert ok is True
