"""Offline proof of the deep-research loop: multi-round search, HITL pause, and resume.

Unlike the rest of this demo suite (which runs live against OpenAI), this test is **deterministic
and offline** on purpose. The behaviours it proves — an iterative multi-round loop, a durable
pause at ``interrupt()``, and a resume that continues from the exact frontier — are control-flow
guarantees, not model quality; testing them against a real model would be slow and flaky and could
not reliably reproduce "the run paused, then resumed after the human replied". So it drives the
graph with a scripted fake model and a fake web search, and asserts the *machinery*:

* the automatic phase runs exactly ``max_auto_rounds`` search rounds before pausing;
* reaching the human checkpoint returns a resume token + interrupt payload and **no output**;
* resuming with ``{"go_deeper": false}`` synthesizes the final report;
* resuming with ``{"go_deeper": true}`` runs another round and pauses again (unbounded depth).

The live end-to-end (real model + real Brave) is exercised by hand via ``agentship run
agents/deep_research.yaml`` — see MANUAL_TESTING.md §11.
"""

from __future__ import annotations

import sys
from pathlib import Path

import agentship_langgraph.models as models_module
import pytest
from agentship.context import Caller, RunContext, RunMode
from agentship.spec import AgentSpec
from agentship_langgraph.engine import LangGraphEngine
from langchain_core.language_models.chat_models import SimpleChatModel

# Put the demo repo root on the path so the ``deep_research`` package imports (the repo ships YAML +
# tests, not an installed package, so pytest only adds ``tests/`` to the path by default).
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import deep_research.graph as graph_module  # noqa: E402  (needs the sys.path line above)
from deep_research.graph import DeepResearchAgent  # noqa: E402


class _ScriptedModel(SimpleChatModel):
    """A deterministic chat model that answers by which research node is calling it.

    Each node's prompt carries a distinct role phrase ("research planner", "refining a research
    plan", "research analyst"); this returns the right canned reply for each, so the loop is fully
    reproducible without a real model. Query replies are one-per-line (the format the graph parses).
    """

    @property
    def _llm_type(self) -> str:
        """Identify this fake to LangChain's model machinery."""
        return "scripted-research"

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
    return [{"title": f"hit for {query}", "url": "https://example.test", "snippet": "snippet"}]


def _ctx(session_id: str) -> RunContext:
    """A RunContext whose ``session_id`` is the checkpoint thread id for this run."""
    return RunContext(
        caller=Caller(user_id="researcher"),
        session_id=session_id,
        run_id="r1",
        agent_name="deep-research",
        mode=RunMode.INVOKE,
    )


@pytest.fixture
def wired(monkeypatch):
    """Wire the scripted model and fake search, and force the in-memory saver (no DB)."""
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: _ScriptedModel())
    monkeypatch.setattr(graph_module, "search_web", _fake_search)
    monkeypatch.delenv("AGENT_SESSION_STORE_URI", raising=False)


def _build(max_auto_rounds: int):
    """Build a durable deep-research agent with the given automatic-round budget."""
    spec = AgentSpec(
        name="deep-research", engine="langgraph", model="x", durability="checkpoint"
    )
    agent = DeepResearchAgent(spec, max_auto_rounds=max_auto_rounds)
    engine = LangGraphEngine()
    return engine, engine.build(agent.spec, agent)


async def test_auto_rounds_then_pause_for_human(wired):
    """It runs exactly ``max_auto_rounds`` rounds automatically, then pauses to ask the human."""
    engine, compiled = _build(max_auto_rounds=2)
    result = await engine.run(compiled, "small modular reactors", _ctx("dr-auto"))

    # Paused at the human checkpoint: no output, a resume token, and the "go deeper?" payload.
    assert result.output is None
    assert result.resume_token is not None and result.resume_token.blob["interrupt"] is True
    assert result.interrupt["question"].startswith("Go deeper")
    assert result.interrupt["round"] == 2  # two automatic rounds ran before the pause


async def test_resume_decline_synthesizes_report(wired):
    """Resuming with go_deeper=false ends the loop and returns the synthesized report."""
    engine, compiled = _build(max_auto_rounds=1)
    ctx = _ctx("dr-decline")
    paused = await engine.run(compiled, "small modular reactors", ctx)
    assert paused.interrupt is not None  # it paused after the single automatic round

    done = await engine.resume(
        compiled, paused.resume_token, ctx, resume_value={"go_deeper": False}
    )
    assert done.interrupt is None
    assert "REPORT" in done.output


async def test_resume_approve_runs_another_round_and_pauses_again(wired):
    """Resuming with go_deeper=true runs one more round and pauses again — depth is unbounded."""
    engine, compiled = _build(max_auto_rounds=1)
    ctx = _ctx("dr-approve")
    paused = await engine.run(compiled, "small modular reactors", ctx)
    assert paused.interrupt["round"] == 1

    deeper = await engine.resume(
        compiled, paused.resume_token, ctx, resume_value={"go_deeper": True}
    )
    # Another round ran (round advanced) and it paused again for the next decision.
    assert deeper.output is None
    assert deeper.interrupt is not None
    assert deeper.interrupt["round"] == 2


async def test_resume_is_a_fresh_engine_instance(wired):
    """A brand-new engine resumes the paused run from its checkpoint — the crash-resume guarantee.

    Dropping the engine that started the run and resuming from a fresh one (same in-process saver)
    mimics a worker restart: the run continues from the checkpoint rather than starting over.
    """
    engine, compiled = _build(max_auto_rounds=1)
    ctx = _ctx("dr-crash")
    paused = await engine.run(compiled, "small modular reactors", ctx)

    fresh_engine, fresh_compiled = _build(max_auto_rounds=1)
    done = await fresh_engine.resume(
        fresh_compiled, paused.resume_token, ctx, resume_value={"go_deeper": False}
    )
    assert "REPORT" in done.output
