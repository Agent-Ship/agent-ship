"""``SupervisorAgent`` — the config-driven durable multi-agent supervisor (Phase 02 · C1).

Assembles the primitives built earlier in this phase into one LangGraph ``StateGraph``:

    classify → lookup_route → dispatch → resolve → (confirm_write?) → safety_gate

``classify`` (the only LLM node) labels the request; ``lookup_route`` (pure) maps the intent to
specialists + a dispatch strategy via :class:`GraphConfig`; ``dispatch`` fans out to those
specialists (C7) and retries failures under a hard cap (C6); ``resolve`` merges their answers
deterministically (C2 ``ConflictResolver``); ``confirm_write`` pauses for human approval when a
write is pending (C5); ``safety_gate`` is the guardrail seam (pass-through until P07) and writes the
final answer. Run under ``durability="checkpoint"`` it checkpoints per node and resumes identically.

Design simplifications kept deliberately readable: the fan-out happens *inside* the dispatch node
(the C7 ``dispatch`` helper uses ``asyncio.gather``), so specialist results are a plain list that is
overwritten per pass — no ``add`` reducer / multi-branch ``InvalidUpdateError`` to reason about —
and ``merge`` folds into ``resolve``. Specialists are supplied as a plain ``{name: agent}`` dict the
author's ``code:`` factory builds; there is no global registry.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated, Any

from agentship.context import get_run_context
from agentship.primitives.conflict_resolver import ConflictPolicy, ConflictResolver
from agentship.primitives.dispatch import AgentRef, dispatch
from agentship.primitives.retry import should_retry, specialists_to_retry
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt
from typing_extensions import TypedDict

from ..agent import LangGraphAgent
from .graph_config import ClassifyConfig, GraphConfig, RouteEntry

if TYPE_CHECKING:
    from agentship.spec import AgentSpec
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.tools import BaseTool

#: Logs the supervisor's routing decisions (classify → route → dispatch → resolve) at INFO. This is
#: the seam that makes the multi-agent orchestration observable: enable it (``logging.getLogger(
#: "agentship.supervisor").setLevel(logging.INFO)`` + a handler) to watch each turn get classified,
#: routed to named sub-agents, and resolved. Silent by default (no handler) so libraries stay quiet.
logger = logging.getLogger("agentship.supervisor")


def _preview(text: Any, limit: int = 80) -> str:
    """A one-line, length-capped preview of a value for a log line (never multi-line)."""
    line = " ".join(str(text).split())
    return line if len(line) <= limit else f"{line[:limit]}…"


class SupervisorState(TypedDict, total=False):
    """The supervisor graph's state — the message history plus the per-turn working fields.

    ``messages`` (seeded by the engine, read back as the answer) uses the ``add_messages`` reducer
    so nodes append. Everything else is filled as the turn flows: ``intent`` (classify), ``route``
    (lookup_route), ``specialist_results`` + ``attempts`` (dispatch/retry), ``resolved`` (resolve),
    and the HITL fields ``pending_write``/``approved``. ``run_ctx_snapshot`` carries the identity
    fields (named this, not ``request_context``, per §13.2).
    """

    messages: Annotated[list, add_messages]
    run_ctx_snapshot: dict[str, Any]
    intent: str | None
    route: dict[str, Any] | None
    specialist_results: list[dict[str, Any]]
    attempts: dict[str, int]
    resolved: dict[str, Any] | None
    pending_write: dict[str, Any] | None
    approved: bool | None


def _user_text(state: SupervisorState) -> str:
    """The user's request for this turn — the content of the latest inbound message."""
    return state["messages"][-1].content


def make_classify(model: BaseChatModel, cfg: GraphConfig):
    """The single LLM node: label the request as one of the configured intents (else ``None``)."""

    async def classify(state: SupervisorState) -> dict:
        intents = ", ".join(cfg.classify.intents)
        # When hints are configured, list "label — what it handles" so the classifier can tell
        # the intents apart; otherwise fall back to the bare label list.
        if cfg.classify.hints:
            described = "\n".join(
                f"- {label}: {cfg.classify.hints[label]}"
                for label in cfg.classify.intents
                if label in cfg.classify.hints
            )
            prompt = (
                f"Classify the user's request into exactly one of these labels:\n{described}\n"
                f"Reply with only the label, nothing else."
            )
        else:
            prompt = (
                f"Classify the user's request into exactly one of these labels: {intents}. "
                f"Reply with only the label, nothing else."
            )
        reply = await model.ainvoke(
            [SystemMessage(content=prompt), HumanMessage(content=_user_text(state))]
        )
        label = (reply.content or "").strip().lower()
        intent = label if label in cfg.classify.intents else None
        logger.info("classify: %r -> intent=%s", _preview(_user_text(state)), intent)
        return {"intent": intent}

    return classify


def make_lookup_route(cfg: GraphConfig):
    """Pure routing: map the intent to its specialists + strategy, falling back to ``_default``."""

    def lookup_route(state: SupervisorState) -> dict:
        entry = cfg.routing.get(state.get("intent")) or cfg.routing["_default"]
        logger.info(
            "route: intent=%s -> specialists=%s strategy=%s",
            state.get("intent"),
            entry.specialists,
            entry.strategy,
        )
        return {"route": {"specialists": entry.specialists, "strategy": entry.strategy}}

    return lookup_route


def make_dispatch(cfg: GraphConfig, specialists: dict[str, Any]):
    """Fan out to the routed specialists (C7); a retry pass re-runs only retryable failures (C6)."""

    async def dispatch_node(state: SupervisorState) -> dict:
        ctx = get_run_context()
        route = state["route"]
        attempts = dict(state.get("attempts") or {})
        prior = {r["name"]: r for r in (state.get("specialist_results") or [])}
        # First pass runs every routed specialist; later passes only the retryable failures.
        to_run = (
            specialists_to_retry(
                list(prior.values()), attempts, cfg.retry.max_attempts, cfg.retry.on
            )
            if prior
            else list(route["specialists"])
        )
        refs = [AgentRef.resolve(name, specialists) for name in to_run]
        logger.info("dispatch: %s -> sub-agents %s", route["strategy"], to_run)
        fresh = await dispatch(route["strategy"], refs, _user_text(state), ctx)
        for result in fresh:
            if result["error"]:
                logger.info("  %s (sub-agent) failed: %s", result["name"], result["error"])
            else:
                answer = result["output"].get("output", result["output"])
                logger.info("  %s (sub-agent) -> %s", result["name"], _preview(answer))
        for name in to_run:
            attempts[name] = attempts.get(name, 0) + 1
        merged = {**prior, **{r["name"]: r for r in fresh}}  # keep the latest result per specialist
        return {"specialist_results": list(merged.values()), "attempts": attempts}

    return dispatch_node


def make_resolve(cfg: GraphConfig):
    """Merge the specialists' answers into one deterministically (C2 ConflictResolver)."""
    resolver = ConflictResolver(cfg.conflict_resolver)

    def resolve(state: SupervisorState) -> dict:
        merged = resolver.resolve(state.get("specialist_results") or [])
        logger.info(
            "resolve: winner=%s considered=%s dropped=%s",
            merged.get("winner"),
            merged.get("considered", []),
            merged.get("dropped", []),
        )
        return {"resolved": merged}

    return resolve


def make_confirm_write(cfg: GraphConfig):
    """Pause for human approval of a pending write (C5); the decision arrives via resume."""

    def confirm_write(state: SupervisorState) -> dict:
        decision = interrupt(state["pending_write"])
        return {"approved": bool(decision and decision.get("approved"))}

    return confirm_write


def make_safety_gate():
    """The guardrail seam (pass-through until P07) — writes the final answer to ``messages``."""

    def safety_gate(state: SupervisorState) -> dict:
        return {"messages": [AIMessage(content=_answer_text(state.get("resolved") or {}))]}

    return safety_gate


def _answer_text(resolved: dict[str, Any]) -> str:
    """Render the resolver's result as the turn's answer string.

    Unwraps the common ``{"output": <text>}`` shape (a scalar specialist answer) so the answer is
    the text itself, not a stringified dict; a richer structured output is stringified as-is.
    """
    if not resolved.get("winner"):
        return resolved.get("status", "no specialist produced a result")
    output = resolved.get("output") or {}
    if isinstance(output, dict) and set(output) == {"output"}:
        return str(output["output"])
    return str(output)


def dispatch_router(cfg: GraphConfig):
    """Conditional edge after dispatch: loop back to retry, or move on to resolve (C6)."""

    def route(state: SupervisorState) -> str:
        results = state.get("specialist_results") or []
        retry = should_retry(
            results, state.get("attempts") or {}, cfg.retry.max_attempts, cfg.retry.on
        )
        return "retry" if retry else "resolve"

    return route


def needs_confirm(cfg: GraphConfig):
    """Conditional edge after resolve: confirm a pending write, else go straight to gate (C5)."""

    def route(state: SupervisorState) -> str:
        if cfg.hitl.confirm_before_write and state.get("pending_write"):
            return "confirm"
        return "gate"

    return route


def build_supervisor_graph(
    model: BaseChatModel, config: GraphConfig, specialists: dict[str, Any]
) -> StateGraph:
    """Assemble the supervisor ``StateGraph`` (classify → route → dispatch → resolve → gate).

    The single graph builder shared by both authoring paths: the ``code:`` factory
    (:class:`SupervisorAgent`) and the declarative ``members:`` path (the ``graph`` template). Given
    the engine-wired ``model``, the parsed :class:`GraphConfig`, and a ``{name: agent}`` dict of
    specialist sub-agents, it wires the nodes (built by the ``make_*`` factories above) into the
    design §4 C1 topology and returns the uncompiled graph for the engine to compile.
    """
    g = StateGraph(SupervisorState)
    g.add_node("classify", make_classify(model, config))
    g.add_node("lookup_route", make_lookup_route(config))
    g.add_node("dispatch", make_dispatch(config, specialists))
    g.add_node("resolve", make_resolve(config))
    g.add_node("confirm_write", make_confirm_write(config))
    g.add_node("safety_gate", make_safety_gate())

    g.add_edge(START, "classify")
    g.add_edge("classify", "lookup_route")
    g.add_edge("lookup_route", "dispatch")
    g.add_conditional_edges(
        "dispatch", dispatch_router(config), {"retry": "dispatch", "resolve": "resolve"}
    )
    g.add_conditional_edges(
        "resolve", needs_confirm(config), {"confirm": "confirm_write", "gate": "safety_gate"}
    )
    g.add_edge("confirm_write", "safety_gate")
    g.add_edge("safety_gate", END)
    return g


def _member_agent(member: Any, default_model: str | None) -> Any:
    """Resolve one :class:`~agentship.spec.MemberSpec` into a built sub-agent.

    A member declared by ``ref`` loads its own agent YAML (already resolved to an absolute path by
    :func:`~agentship.spec.load_spec`); an inline member becomes a plain ``template: single`` agent
    over its ``prompt`` (or a sensible default) and its ``model`` (falling back to the team model).
    Either way the result is a full, independently-runnable agent the supervisor dispatches to.
    """
    from agentship.runtime import build_agent
    from agentship.spec import AgentSpec

    if member.ref:
        return build_agent(member.ref)
    prompt = member.prompt or f"You are {member.name}, a helpful specialist. Answer concisely."
    return build_agent(
        AgentSpec(
            name=member.name,
            engine="langgraph",
            template="single",
            model=member.model or default_model,
            prompt=prompt,
        )
    )


def derive_graph_config(spec: AgentSpec) -> GraphConfig:
    """Derive a default :class:`GraphConfig` from a spec's ``members`` (the declarative path).

    Each member becomes one intent that routes to itself (``single`` strategy); the classifier is
    given each member's ``description`` as a hint so it can pick the right one; the resolver's
    priority follows declaration order; and ``_default`` falls back to the first member. This is the
    zero-config routing a purely declarative ``members:`` team gets; the ``code:`` factory path is
    still available when an author wants explicit routing, parallel fan-out, retry, or HITL.
    """
    members = spec.members or []
    names = [m.name for m in members]
    routing = {m.name: RouteEntry(specialists=[m.name], strategy="single") for m in members}
    routing["_default"] = RouteEntry(specialists=[names[0]], strategy="single")
    hints = {m.name: m.description for m in members if m.description}
    return GraphConfig(
        classify=ClassifyConfig(model=spec.model or "", intents=names, hints=hints),
        routing=routing,
        conflict_resolver=ConflictPolicy(priority=names),
    )


def build_declarative_supervisor(
    spec: AgentSpec, model: BaseChatModel
) -> tuple[StateGraph, list[str]]:
    """Build a supervisor graph straight from a spec's ``members`` — no ``code:`` factory needed.

    Resolves each member into a sub-agent, derives the routing config from the member list, and
    assembles the shared supervisor graph. Returns the uncompiled graph (for the engine to compile,
    attaching any checkpointer) and the coordinated member names (surfaced on the built artifact so
    the ``multi_agent`` capability is inspectable).
    """
    specialists = {m.name: _member_agent(m, spec.model) for m in (spec.members or [])}
    config = derive_graph_config(spec)
    graph = build_supervisor_graph(model, config, specialists)
    return graph, list(specialists)


class SupervisorAgent(LangGraphAgent):
    """A durable, config-driven multi-agent supervisor authored via a ``code:`` factory.

    Constructed with its :class:`GraphConfig` and a ``{name: agent}`` dict of specialists; the
    demo's factory builds those and hands them in. ``build_graph`` delegates to
    :func:`build_supervisor_graph` — the same builder the declarative ``members:`` path uses.
    """

    def __init__(
        self, spec: AgentSpec, *, config: GraphConfig, specialists: dict[str, Any]
    ) -> None:
        """Bind the spec, the parsed graph config, and the specialists to dispatch to."""
        super().__init__(spec)
        self._config = config
        self._specialists = specialists

    def build_graph(self, model: BaseChatModel, tools: list[BaseTool]) -> StateGraph:
        """Assemble the supervisor ``StateGraph`` per design §4 C1."""
        return build_supervisor_graph(model, self._config, self._specialists)
