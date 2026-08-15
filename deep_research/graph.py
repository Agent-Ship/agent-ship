"""The deep-research agent: a native LangGraph loop that plans, searches, deepens, and pauses.

This is the "long agent" half of the flagship demo (its fast counterpart is
``agents/quick_search.yaml``, a single ReAct turn). It is authored natively — a
:class:`~agentship_langgraph.LangGraphAgent` whose :meth:`build_graph` returns a real
``StateGraph`` — so AgentShip supplies the wired model and drives durability/interrupt/resume,
while this graph owns the research *strategy*:

    plan → search → (round < max_auto_rounds ?) → deepen → search → … → ask_human → synthesize

It runs a few rounds automatically, then **interrupts** to ask the human "go deeper?" before
spending more effort — the human can approve another round (loop) or stop (synthesize the report).
Because the spec declares ``durability: checkpoint``, every round is checkpointed, so the run
survives a crash or a long wait for the human and resumes from the exact frontier. The final report
is appended as an ``AIMessage`` so the engine's answer contract (``messages[-1].content``) returns
it verbatim.

The web search is :func:`deep_research.web_search.search_web`; the model is used only to plan
queries, refine follow-ups, and write the final report — the loop control is plain Python here, not
model tool-calling, which is exactly what makes the depth and the checkpoints deterministic.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Annotated

from agentship.spec import AgentSpec
from agentship_langgraph import LangGraphAgent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt
from typing_extensions import TypedDict

# The demo repo is not an installable package, and AgentShip loads a ``code:`` factory by *file
# path* (as an anonymous module with no parent package), so a normal ``from .web_search import`` is
# not available when ``agentship run`` loads this file. Put this file's own directory on the path
# and import the sibling by name — this works both when loaded as a package module and standalone.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from web_search import search_web  # noqa: E402  (see sys.path note above)

#: Logs to the ``agentship.*`` tree so ``--verbose`` shows the research rounds unfolding.
_log = logging.getLogger("agentship.deep_research")

#: How many rounds run automatically before the agent pauses to ask the human "go deeper?".
#: Overridable via the ``DEEP_RESEARCH_AUTO_ROUNDS`` env var so a manual tester (or a test) can
#: make the pause come sooner or later without editing code.
_DEFAULT_AUTO_ROUNDS = 2


class ResearchState(TypedDict, total=False):
    """The evolving state of one research run, threaded node-to-node and checkpointed per round.

    ``messages`` carries the chat transcript (seeded with the user's question, ending with the
    final report). ``question`` is the extracted research topic. ``subqueries`` is the current
    round's search queries; ``findings`` accumulates every round's results. ``round`` counts
    rounds done, ``max_auto_rounds`` gates the automatic phase, and ``go_deeper`` records the
    human's decision at the pause.
    """

    messages: Annotated[list, add_messages]
    question: str
    subqueries: list[str]
    findings: list[dict]
    round: int
    max_auto_rounds: int
    go_deeper: bool


def _parse_queries(text: str, fallback: str, limit: int = 3) -> list[str]:
    """Turn a model's line-per-query reply into a clean list, falling back to ``fallback``.

    The planner/deepen prompts ask for one search query per line; this splits on newlines, strips
    bullet/number prefixes and blank lines, and caps the count. If the model returned nothing
    usable (e.g. an offline fake), it degrades to a single query — the plain ``fallback`` topic — so
    the loop always has something to search rather than stalling on an empty plan.
    """
    queries: list[str] = []
    for line in text.splitlines():
        cleaned = line.strip().lstrip("-*0123456789. ").strip()
        if cleaned:
            queries.append(cleaned)
    return queries[:limit] if queries else [fallback]


def _question_from(messages: list) -> str:
    """Extract the user's research topic from the seeded message list (the last human turn).

    The engine seeds ``messages`` with the system prompt plus the user's input; this returns the
    content of the most recent human message, or the last message's content as a fallback, so the
    graph never assumes a fixed position for the question.
    """
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return str(messages[-1].content) if messages else ""


class DeepResearchAgent(LangGraphAgent):
    """A durable, iterative, human-in-the-loop research agent authored in native LangGraph.

    The graph runs ``max_auto_rounds`` search rounds automatically, then interrupts to ask the
    human whether to keep going; each approval adds one more round, a decline ends the loop and
    synthesizes the report. ``max_auto_rounds`` is read once at construction (see
    :func:`build_agent`) so the same class can be tuned shallow or deep without code changes.
    """

    def __init__(self, spec: AgentSpec, *, max_auto_rounds: int = _DEFAULT_AUTO_ROUNDS) -> None:
        """Bind the spec and record how many rounds to run before pausing for the human."""
        super().__init__(spec)
        self.max_auto_rounds = max_auto_rounds

    def build_graph(self, model, tools) -> StateGraph:
        """Build the native ``plan → search → deepen/ask_human → synthesize`` research loop.

        ``model`` is the wired chat model (used only to plan queries and write the report);
        ``tools`` is unused because this loop calls :func:`search_web` directly rather than routing
        search through model tool-calling. The engine compiles the returned graph with the
        checkpointer, so the ``interrupt()`` in ``ask_human`` and the per-round checkpoints work
        without any wiring here.
        """
        max_auto_rounds = self.max_auto_rounds

        def plan(state: ResearchState) -> dict:
            """Turn the user's question into the first round of search queries."""
            question = _question_from(state["messages"])
            prompt = [
                SystemMessage(
                    "You are a research planner. Given a topic, list up to 3 focused web "
                    "search queries that would help research it — one query per line, no prose."
                ),
                HumanMessage(f"Topic: {question}"),
            ]
            subqueries = _parse_queries(model.invoke(prompt).content, fallback=question)
            _log.info("plan question=%r subqueries=%d", question, len(subqueries))
            return {
                "question": question,
                "subqueries": subqueries,
                "findings": [],
                "round": 0,
                "max_auto_rounds": max_auto_rounds,
            }

        def search(state: ResearchState) -> dict:
            """Run every current sub-query through the web and accumulate the results."""
            current_round = state["round"] + 1
            new_findings = list(state.get("findings", []))
            for query in state["subqueries"]:
                results = search_web(query)
                new_findings.append({"round": current_round, "query": query, "results": results})
                _log.info("search round=%d query=%r hits=%d", current_round, query, len(results))
            return {"findings": new_findings, "round": current_round}

        def deepen(state: ResearchState) -> dict:
            """Ask the model for sharper follow-up queries based on what has been found so far."""
            covered = "\n".join(f"- {f['query']}" for f in state["findings"])
            prompt = [
                SystemMessage(
                    "You are refining a research plan. Given the topic and the queries already "
                    "run, list up to 3 NEW follow-up web search queries that dig deeper or fill "
                    "gaps — one per line, no prose, no repeats."
                ),
                HumanMessage(f"Topic: {state['question']}\nAlready searched:\n{covered}"),
            ]
            subqueries = _parse_queries(model.invoke(prompt).content, fallback=state["question"])
            _log.info("deepen round=%d next_subqueries=%d", state["round"], len(subqueries))
            return {"subqueries": subqueries}

        def ask_human(state: ResearchState) -> dict:
            """Pause the run and ask the human whether to research another round.

            Calling ``interrupt`` checkpoints the run and returns control to the caller with the
            payload below; the run stays paused (durably — across a crash or hours of waiting)
            until it is resumed with ``{"go_deeper": true/false}``. On resume, ``interrupt``
            returns that decision and the node records it for routing.
            """
            decision = interrupt(
                {
                    "question": "Go deeper with another research round?",
                    "round": state["round"],
                    "queries_run": sum(1 for _ in state["findings"]),
                    "topic": state["question"],
                }
            )
            go_deeper = bool(decision and decision.get("go_deeper"))
            _log.info("ask_human round=%d go_deeper=%s", state["round"], go_deeper)
            return {"go_deeper": go_deeper}

        def synthesize(state: ResearchState) -> dict:
            """Write the final research report from all findings and append it as the answer."""
            digest = "\n".join(
                f"[round {f['round']}] {f['query']}: "
                + "; ".join(r.get("title", "") for r in f["results"])
                for f in state["findings"]
            )
            prompt = [
                SystemMessage(
                    "You are a research analyst. Using the collected findings, write a concise, "
                    "well-structured research report that answers the topic. Cite what was found."
                ),
                HumanMessage(f"Topic: {state['question']}\n\nFindings:\n{digest}"),
            ]
            report = model.invoke(prompt).content
            _log.info("synthesize rounds=%d findings=%d", state["round"], len(state["findings"]))
            return {"messages": [AIMessage(content=report)]}

        def route_after_search(state: ResearchState) -> str:
            """Keep auto-searching until the round budget is spent, then pause for the human."""
            if state["round"] < state["max_auto_rounds"]:
                return "deepen"
            return "ask_human"

        def route_after_human(state: ResearchState) -> str:
            """Loop into another round if the human approved, otherwise write the report."""
            return "deepen" if state.get("go_deeper") else "synthesize"

        graph = StateGraph(ResearchState)
        graph.add_node("plan", plan)
        graph.add_node("search", search)
        graph.add_node("deepen", deepen)
        graph.add_node("ask_human", ask_human)
        graph.add_node("synthesize", synthesize)

        graph.add_edge(START, "plan")
        graph.add_edge("plan", "search")
        graph.add_conditional_edges("search", route_after_search, ["deepen", "ask_human"])
        graph.add_edge("deepen", "search")
        graph.add_conditional_edges("ask_human", route_after_human, ["deepen", "synthesize"])
        graph.add_edge("synthesize", END)
        return graph


def build_agent() -> DeepResearchAgent:
    """``code:`` factory for ``agents/deep_research.yaml`` — the durable deep-research agent.

    Reads ``DEEP_RESEARCH_AUTO_ROUNDS`` (default 2) so the automatic-round budget is tunable from
    the environment. The returned agent carries its own durable spec; ``agentship run
    agents/deep_research.yaml`` loads and drives *this* graph.
    """
    auto_rounds = int(os.environ.get("DEEP_RESEARCH_AUTO_ROUNDS", _DEFAULT_AUTO_ROUNDS))
    spec = AgentSpec(
        name="deep-research",
        engine="langgraph",
        model="openai/gpt-4o-mini",
        durability="checkpoint",
        prompt="You are a deep-research agent that iteratively searches the web.",
    )
    return DeepResearchAgent(spec, max_auto_rounds=auto_rounds)
