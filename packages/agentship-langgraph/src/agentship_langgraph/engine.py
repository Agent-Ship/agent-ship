"""The LangGraph engine — AgentShip's default single-agent, real-model engine.

:class:`LangGraphEngine` compiles an :class:`~agentship.spec.AgentSpec` into a
LangGraph graph: by default one node that sends ``[system prompt, user input]`` to
a LiteLLM-backed chat model and returns its answer, and — via templates, custom
``build_graph`` authoring, or a declarative ``members:`` team — richer graphs. It
honestly declares what it delivers today: ``streaming``, the LiteLLM ``providers``
it reaches, ``durability="checkpoint"``, ``tool_calling``, ``multi_agent``, and
``hitl="interrupt"``. Structured output is **not** yet built, so it stays off;
declaring it before it works would be the exact over-claim the conformance matrix
exists to catch (*declare, don't fake*), so it remains off until its phase lands
and its conformance cell passes.

**Model injection.** The chat model is resolved through
:func:`agentship_langgraph.models.resolve_model` (referenced via the module, not imported by
name), so offline tests monkeypatch that function to inject a fake model and run
with no network. Production runs resolve a real ``ChatLiteLLM``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, ClassVar

from agentship.engines.base import Engine, EngineCapabilities, Event, Result, ResumeToken
from agentship.errors import CapabilityError, ResumeError
from agentship.observability import NoOpObserver, get_observer, tracing_callback_installed
from agentship.thread_lock import resolve_thread_lock
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from typing_extensions import TypedDict

from . import models
from .agent import LangGraphAgent
from .durability import open_checkpointer
from .templates import resolve_template
from .templates.graph_supervisor import INTERNAL_NODES as SUPERVISOR_INTERNAL_NODES
from .tools import ToolCallLogger
from .tracing import ObservabilityCallback

_engine_logger = logging.getLogger("agentship.engine")

#: Content-block types that carry a model's thinking rather than its reply. Anthropic uses
#: ``thinking``; ``reasoning``/``reasoning_content`` are the names other providers use for the
#: same thing through LiteLLM.
_REASONING_BLOCKS = frozenset({"thinking", "reasoning", "reasoning_content"})


def split_reasoning(message: Any) -> tuple[str, str]:
    """Split a model message into ``(reasoning, answer)``.

    A reasoning model does not return a string. Anthropic-style extended thinking arrives as
    a list of content blocks — ``[{"type": "thinking", ...}, {"type": "text", ...}]`` — and
    DeepSeek and friends report it out-of-band in ``additional_kwargs.reasoning_content``.
    Both must be separated from the reply before it is streamed: emitted whole, the model's
    private deliberation reaches the user as part of its answer.

    Returns empty strings rather than ``None`` so callers can test truthiness without
    juggling two shapes. A plain-string message is all answer and no reasoning, which is
    what every non-reasoning model produces.
    """
    reasoning: list[str] = []
    answer: list[str] = []

    content = getattr(message, "content", None)
    if isinstance(content, str):
        answer.append(content)
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                # A bare string inside a block list is answer text.
                answer.append(str(block))
                continue
            kind = block.get("type")
            if kind in _REASONING_BLOCKS:
                # The payload key matches the block type ("thinking" -> block["thinking"]),
                # but not every provider follows that, so fall back across the known names.
                reasoning.append(
                    str(block.get(kind) or block.get("thinking") or block.get("text") or "")
                )
            elif kind == "text":
                answer.append(str(block.get("text") or ""))

    # Out-of-band reasoning (DeepSeek, some LiteLLM routes) rides alongside string content.
    extra = (getattr(message, "additional_kwargs", None) or {}).get("reasoning_content")
    if extra:
        reasoning.append(str(extra))

    return "".join(reasoning), "".join(answer)


#: A process-wide singleton attached to every run's config. Silent when the ``agentship.tools``
#: logger is at WARNING (the default); enabled by ``--verbose`` so tool calls appear on stderr.
_TOOL_CALL_LOGGER = ToolCallLogger()


def _run_callbacks() -> list:
    """Build the LangChain callback list for one run: the tool logger plus the span tracer.

    The tool logger is always present (its output is gated by log level). When the runtime has a
    live observer active for the turn — read via :func:`get_observer`, set while the root ``agent``
    span is open — a fresh :class:`ObservabilityCallback` is added so the model/tool/MCP/node inside
    LangGraph become spans nested under that root. With no observer, or the no-op one, only the tool
    logger runs, so tracing adds nothing when it is off. A new callback per run keeps per-turn span
    state (the ``run_id`` → span map) isolated across concurrent turns.
    """
    callbacks: list = [_TOOL_CALL_LOGGER]
    observer = get_observer()
    # A nested run — a sub-agent dispatched by a supervisor — shares the parent's observer, and
    # LangChain propagates the parent invoke's callbacks into this one. Installing a second
    # tracing callback would make both record the same model call, duplicating the span and
    # double-counting its tokens and cost. The parent's callback already covers us.
    if tracing_callback_installed.get():
        return callbacks
    if observer is not None and not isinstance(observer, NoOpObserver):
        capture = bool(getattr(observer, "capture_content", False))
        callbacks.append(ObservabilityCallback(observer, capture_content=capture))
    return callbacks


if TYPE_CHECKING:
    from agentship.context import RunContext
    from agentship.spec import AgentSpec
    from langchain_core.language_models.chat_models import BaseChatModel

#: Schema version stamped into every minted ResumeToken blob, so a future change to the
#: blob shape can be detected on resume rather than mis-read.
_TOKEN_SCHEMA_VERSION = 1

#: The checkpoint flush mode passed to LangGraph's ``ainvoke(durability=…)`` for a durable run.
#: Fixed to ``"sync"`` — the strongest crash guarantee: the checkpoint for a completed node lands
#: *before* the next node starts, so a ``kill -9`` mid-run always resumes from a persisted frontier.
#: This is an engine detail, not a user knob: a spec asks for ``durability: checkpoint`` (crash
#: safety), and the engine chooses how to flush. See DESIGN §6.
_CHECKPOINT_FLUSH_MODE = "sync"


class _AgentState(TypedDict):
    """The graph's state: the running list of chat messages for one turn."""

    messages: list


class _CompiledAgent:
    """A built LangGraph agent: the compiled graph plus its system prompt.

    Held opaque by the kernel; only this engine's ``run``/``stream`` read it. The
    system prompt is stored alongside the graph so each turn can seed the message
    list without re-reading the spec.
    """

    def __init__(
        self,
        graph: Any,
        system_prompt: str | None,
        model_id: str,
        *,
        durability: str = "none",
        members: list[str] | None = None,
        bound_tools: list[str] | None = None,
        internal_nodes: frozenset[str] = frozenset(),
    ) -> None:
        """Bind the compiled graph, the optional system prompt, model id, and durability.

        ``internal_nodes`` names nodes whose model output is bookkeeping rather than part of the
        reply. A supervisor's ``classify`` node emits the routing label ("web_researcher"), and
        streaming every node's tokens sent that label to the client glued to the front of the
        answer — "web_researcherHello! How can I assist you today?". A single-agent loop names
        none: there every token IS the reply.

        ``model_id`` is the LiteLLM model string (e.g. ``"openai/gpt-4o-mini"``);
        it is kept so a provider failure at run time can be turned into an
        actionable :class:`~agentship.errors.ModelError` that names the credential.
        ``durability`` (from ``spec.durability``) decides whether ``run`` takes the
        checkpointed path; on that path the engine flushes checkpoints in
        :data:`_CHECKPOINT_FLUSH_MODE` (an engine detail, not a spec field).
        ``members`` names the coordinated sub-agents when this is a declarative
        multi-agent team (empty otherwise) — it makes the ``multi_agent`` capability
        inspectable on the built artifact.
        """
        self.graph = graph
        self.system_prompt = system_prompt
        self.model_id = model_id
        self.durability = durability
        self.members = members or []
        #: Names of the tools bound into this agent (empty when none declared) — makes the
        #: ``tool_calling`` capability inspectable on the built artifact.
        self.bound_tools = bound_tools or []
        self.internal_nodes = internal_nodes

    @property
    def builder(self) -> Any:
        """The uncompiled ``StateGraph`` behind the compiled graph.

        Durable runs recompile this with a live checkpointer (``builder.compile(
        checkpointer=…)``) because LangGraph binds a checkpointer at compile time and
        the Postgres saver's pool is opened per-run. Every ``CompiledStateGraph``
        retains its ``builder``.
        """
        return self.graph.builder

    def initial_messages(self, text: str) -> list:
        """Build the seed message list for one turn: optional system + user input."""
        messages: list = []
        if self.system_prompt:
            messages.append(SystemMessage(content=self.system_prompt))
        messages.append(HumanMessage(content=text))
        return messages


class LangGraphEngine(Engine):
    """AgentShip's default engine — a single-agent LangGraph graph over LiteLLM.

    Declares what the engine honestly delivers today: ``streaming``, the LiteLLM
    ``providers`` it can reach, ``durability="checkpoint"`` (per-node checkpoints via
    a LangGraph saver, so a crashed run resumes to an identical result through
    :meth:`resume`), ``tool_calling`` (declared tools/MCP servers are resolved and
    bound into the graph), ``multi_agent`` (a declarative ``members:`` spec compiles
    a real supervisor team), and ``hitl="interrupt"`` (a durable graph node may call
    ``interrupt()`` to pause for human approval and be resumed with the decision).
    Structured output remains **not** implemented, so it stays off
    (``structured_output="none"``) and the capability gate rejects any spec that asks
    for it. The model is resolved via
    :func:`agentship_langgraph.models.resolve_model`, which offline tests monkeypatch
    to inject a fake chat model.
    """

    name: ClassVar[str] = "langgraph"
    capabilities: ClassVar[EngineCapabilities] = EngineCapabilities(
        providers={"openai", "anthropic", "gemini", "ollama"},
        streaming=True,
        durability="checkpoint",
        multi_agent=True,
        tool_calling=True,
        hitl="interrupt",
    )

    def build(self, spec: AgentSpec, authored: object = None) -> _CompiledAgent:
        """Compile the spec into a runnable graph over the resolved chat model.

        Resolves the chat ``model`` from ``spec.model`` (raising
        :class:`~agentship.errors.SpecError` on an empty model via
        :func:`~agentship_langgraph.models.resolve_model`), threading any
        ``spec.params`` (temperature/max_tokens/api_base/timeout) as generation
        params — ``None`` fields are dropped so the model keeps its own defaults —
        and resolves ``tools`` (empty until Phase 03 wires tool execution).

        The build *body* is chosen so the author never wires a vendor, in order of
        precedence:

        - when ``authored`` is a :class:`~agentship_langgraph.agent.LangGraphAgent`
          (the custom-authoring path, §4.3), its ``build_graph(model, tools)`` is
          called — this is where a developer's native LangGraph runs;
        - else when ``spec.template`` is set, that template's generated
          ``build_graph(model, tools)`` is used (e.g. ``single`` → a prebuilt ReAct
          agent, zero author code);
        - otherwise the engine's own default single-node graph is used.

        Every path hands ``build_graph`` the *same* wired ``model``/``tools`` plain
        params — there is no ``EngineKit`` bundle. A template may return an
        already-compiled graph (``single`` does); the engine detects that and reuses
        it rather than compiling twice. Vendor (LangChain/LangGraph) types are
        confined to this adapter; the core never sees one.
        """
        model = self._resolve_model(spec)
        tools = self._resolve_tools(spec)
        # The default single-node graph and custom-authored graphs consume the
        # ``messages`` the run loop seeds, so the engine must seed the system prompt
        # for them. Templates (``single``/``graph``/``autonomous``) build on
        # ``create_react_agent(prompt=...)``, which injects the system prompt inside
        # the graph itself — seeding it again here would send the system message
        # twice. So only the template path hands prompt ownership to the graph.
        prompt_owned_by_graph = False
        members: list[str] = []
        if isinstance(authored, LangGraphAgent):
            _engine_logger.debug("build path=authored model=%s tools=%d", spec.model, len(tools))
            graph = authored.build_graph(model, tools)
        elif spec.members:
            # Declarative multi-agent: the spec's `members:` (each a ref to a sub-agent YAML or an
            # inline prompt) drive a real supervisor — no code: factory. The supervisor seeds and
            # writes its own messages, so it owns the prompt.
            from .templates.graph_supervisor import build_declarative_supervisor

            _engine_logger.debug(
                "build path=multi-agent members=%d model=%s", len(spec.members), spec.model
            )
            graph, members = build_declarative_supervisor(spec, model)
            prompt_owned_by_graph = True
        else:
            template_body = resolve_template(spec)
            if template_body is not None:
                _engine_logger.debug(
                    "build path=template template=%s model=%s tools=%d",
                    spec.template,
                    spec.model,
                    len(tools),
                )
                graph = template_body(model, tools)
                prompt_owned_by_graph = True
            else:
                _engine_logger.debug("build path=default-graph model=%s", spec.model)
                graph = self._build_graph(model)
        compiled = graph if isinstance(graph, CompiledStateGraph) else graph.compile()
        from agentship.skills import render_agent_prompt

        system_prompt = (
            None if prompt_owned_by_graph else render_agent_prompt(spec.prompt, spec.skills)
        )
        return _CompiledAgent(
            compiled,
            system_prompt,
            spec.model or "",
            durability=spec.durability,
            members=members,
            bound_tools=[t.name for t in tools],
            # BOTH supervisor paths, which is the bit I got wrong twice. A code-authored
            # supervisor declares `internal_nodes` on itself but has no `spec.members`; a
            # declarative one has `spec.members` but never constructs a SupervisorAgent, so
            # there is nothing to ask. Checking only one silently leaves the other leaking the
            # routing label into the answer.
            internal_nodes=(
                getattr(authored, "internal_nodes", None)
                or (SUPERVISOR_INTERNAL_NODES if spec.members else frozenset())
            ),
        )

    def _resolve_model(self, spec: AgentSpec) -> BaseChatModel:
        """Resolve the LiteLLM-backed chat model for ``spec`` (honouring routing).

        Uses ``RunContext.routed_model`` when a ``route`` step has stamped one on
        the current run (the adapter reads routing, never decides it — §13.5),
        falling back to ``spec.model`` otherwise. ``spec.params`` are threaded as
        generation params with ``None`` fields dropped. The routing branch is a
        no-op today (nothing stamps ``routed_model`` yet) but the seam is honoured
        so Phase 01's ``ModelRouter`` wave drops in without touching this method.
        """
        from agentship.context import get_run_context

        ctx = get_run_context()
        routed = getattr(ctx, "routed_model", None) if ctx is not None else None
        model_id = routed or spec.model or ""
        params = spec.params.model_dump(exclude_none=True) if spec.params else {}
        return models.resolve_model(model_id, **params)

    def _resolve_tools(self, spec: AgentSpec) -> list:
        """Resolve ``spec.tools`` references into bound LangChain tools (Phase 03 · C1).

        Each ``tools:`` entry is resolved to a vendor-neutral :class:`agentship.tools.Tool`
        (:func:`~agentship.tools.resolve_tool` — a built-in skill name or a ``module:attr``
        reference) and converted to a LangChain ``StructuredTool`` the ``single`` template binds
        into its ReAct loop. ``None``/empty ``tools`` yields ``[]`` (unchanged). A bad reference
        fails fast via :class:`~agentship.errors.SpecError`.

        When ``spec.mcp`` declares MCP servers, their tools are discovered via
        ``langchain-mcp-adapters`` (:func:`~agentship_langgraph.mcp.discover_mcp_tools_sync`) and
        appended — an MCP tool and a native tool are indistinguishable to the graph. Finally, when
        ``spec.allowed_tools`` is set, the combined list is filtered to those names (the guard
        against many MCP servers flooding the model with tools).
        """
        from agentship.tools import resolve_tool

        from .tools import survive_tool_errors, to_langchain_tool

        confirm = spec.confirm_writes
        tools = [
            to_langchain_tool(resolve_tool(ref), confirm_writes=confirm)
            for ref in (spec.tools or [])
        ]
        if spec.mcp:
            from .mcp import discover_mcp_tools_sync

            # Wrapped, because MCP tools arrive already built from the MCP client and so never
            # pass through to_langchain_tool's error guard. Without this a failing MCP server
            # crashes the turn while a failing native tool degrades — the two are meant to be
            # indistinguishable to the agent, including when they fail.
            tools.extend(
                survive_tool_errors(discovered) for discovered in discover_mcp_tools_sync(spec.mcp)
            )
        if spec.allowed_tools is not None:
            allow = set(spec.allowed_tools)
            before = {t.name for t in tools}
            tools = [t for t in tools if t.name in allow]
            dropped = before - {t.name for t in tools}
            if dropped:
                _engine_logger.warning(
                    "allowed_tools dropped %d tool(s) the model will not see: %s",
                    len(dropped),
                    ", ".join(sorted(dropped)),
                )
        return tools

    def _build_graph(self, model: BaseChatModel) -> Any:
        """Wire and compile the minimal ``START → agent → END`` graph for ``model``."""

        def call_model(state: _AgentState) -> dict:
            """Invoke the chat model on the current messages, appending its reply."""
            reply = model.invoke(state["messages"])
            return {"messages": [*state["messages"], reply]}

        builder = StateGraph(_AgentState)
        builder.add_node("agent", call_model)
        builder.add_edge(START, "agent")
        builder.add_edge("agent", END)
        return builder.compile()

    async def run(self, compiled: _CompiledAgent, text: str, ctx: RunContext) -> Result:
        """Run one turn and return the model's answer as the :class:`Result` output.

        Invokes the compiled graph on ``[system prompt, user input]`` and returns
        the content of the final message (the model's reply). A provider/credential
        failure is turned into an actionable
        :class:`~agentship.errors.ModelError` via
        :func:`agentship_langgraph.models.map_model_error` (which names the missing env var);
        the original exception is chained so ``--debug`` still shows the full cause.

        When the agent declares ``durability="checkpoint"`` the run takes the durable
        path (:meth:`_run_durable`): a per-run checkpointer is opened, the graph is
        checkpointed per node, and a :class:`~agentship.engines.base.ResumeToken` is
        minted so a crash can be resumed to an identical result.
        """
        # Every run goes through the checkpointer, because that is what threads the
        # conversation: LangGraph replays a thread's messages from it, so without one the
        # agent starts blank on every turn. `durability` is a SEPARATE promise — surviving a
        # crash and being resumable — and it is honoured in _run_durable, which additionally
        # mints a ResumeToken. Session memory must not require opting into crash recovery.
        return await self._run_durable(compiled, text, ctx)

    async def _run_without_memory(self, compiled: _CompiledAgent, text: str, ctx: RunContext):
        """The old stateless path: invoke the graph with no checkpointer, keeping no history.

        Retained because a caller that genuinely wants a one-shot turn should not pay for
        state; reachable today only by using a fresh ``session_id`` per turn, which is the
        documented way to opt out of memory.
        """
        try:
            state = await compiled.graph.ainvoke(
                {"messages": compiled.initial_messages(text)},
                config={"callbacks": _run_callbacks()},
            )
        except Exception as exc:
            # Only a genuine provider failure becomes a ModelError. Anything else — a missing
            # checkpoint table, a tool bug — keeps its own type so the message points at the
            # system that actually failed.
            if models.is_provider_error(exc):
                raise models.map_model_error(compiled.model_id, exc) from exc
            raise
        answer = state["messages"][-1].content
        return Result(output=answer)

    @staticmethod
    def _conninfo() -> str | None:
        """The Postgres connection string for durable state, or ``None`` for in-memory.

        Reads ``AGENT_SESSION_STORE_URI``; when unset, durable runs fall back to an
        in-memory saver (single-process durability — fine for dev/tests, not a crash
        guarantee across processes).
        """
        return os.environ.get("AGENT_SESSION_STORE_URI")

    @staticmethod
    def _thread_config(thread_id: str) -> dict:
        """The LangGraph ``configurable`` config that binds a run to its checkpoint thread.

        Carries the run's callbacks (:func:`_run_callbacks`): the tool logger — silent at WARNING,
        visible with ``--verbose`` — plus the span tracer when an observer is active, so durable and
        resumed runs are traced on the same footing as a plain ``run``.
        """
        return {"configurable": {"thread_id": thread_id}, "callbacks": _run_callbacks()}

    def _mint_token(self, thread_id: str, snapshot: Any, *, interrupted: bool) -> ResumeToken:
        """Mint a ``ResumeToken`` from a state snapshot — all LangGraph fields live in ``blob``.

        Core never inspects ``blob``; it round-trips it as JSONB and hands it back on
        :meth:`resume`. ``checkpoint_id`` comes from the snapshot's config so resume
        re-hydrates the exact frontier.
        """
        checkpoint_id = snapshot.config.get("configurable", {}).get("checkpoint_id")
        return ResumeToken(
            engine=self.name,
            blob={
                "thread_id": thread_id,
                "checkpoint_id": checkpoint_id,
                "interrupt": interrupted,
                "schema_version": _TOKEN_SCHEMA_VERSION,
            },
        )

    @staticmethod
    def _interrupt_payload(state: Any) -> dict | None:
        """The payload of a pending HITL ``interrupt(...)``, or ``None`` if the run completed.

        LangGraph returns pending interrupts under the ``__interrupt__`` key; the first one's
        ``value`` is exactly what the node passed to ``interrupt(...)``. Wrapped to a dict so the
        client always gets a structured payload.
        """
        interrupts = state.get("__interrupt__") if isinstance(state, dict) else None
        if not interrupts:
            return None
        payload = interrupts[0].value
        return payload if isinstance(payload, dict) else {"payload": payload}

    async def _invoke_and_finalize(
        self, graph: Any, invoke_input: Any, thread_id: str, model_id: str, *, durable: bool = True
    ) -> Result:
        """Invoke (or resume) the graph, then surface any interrupt and mint a token if durable.

        Shared by :meth:`_run_durable` and :meth:`resume` so both paths finalize identically: on a
        HITL interrupt the :class:`Result` carries the payload plus a token with ``interrupt=True``
        and no output; otherwise it carries the answer plus a terminal token. Checkpoints are
        flushed in :data:`_CHECKPOINT_FLUSH_MODE` (``sync``) so a completed node's state is durable
        before the next node runs.

        ``durable`` separates the two promises that used to travel together. Every run threads a
        checkpointer — that is what gives the conversation memory — but only a run that asked for
        ``durability: checkpoint`` mints a :class:`ResumeToken`, because the token is a claim that
        this run can be resumed after a crash. Handing one to a non-durable agent would advertise
        a guarantee it does not have.
        """
        cfg = self._thread_config(thread_id)
        try:
            state = await graph.ainvoke(invoke_input, config=cfg, durability=_CHECKPOINT_FLUSH_MODE)
        except Exception as exc:
            if models.is_provider_error(exc):
                raise models.map_model_error(model_id, exc) from exc
            raise
        payload = self._interrupt_payload(state)
        token = None
        if durable:
            snapshot = await graph.aget_state(cfg)
            token = self._mint_token(thread_id, snapshot, interrupted=payload is not None)
        if payload is not None:
            return Result(output=None, resume_token=token, interrupt=payload)
        return Result(output=state["messages"][-1].content, resume_token=token)

    async def _run_durable(self, compiled: _CompiledAgent, text: str, ctx: RunContext) -> Result:
        """Run one turn under a per-run checkpointer, finalizing via :meth:`_invoke_and_finalize`.

        Opens a per-run checkpointer (Postgres when ``AGENT_SESSION_STORE_URI`` is set, else
        in-memory), recompiles the graph with it, and invokes with the run's ``thread_id``
        (``ctx.session_id``); checkpoints flush per node in :data:`_CHECKPOINT_FLUSH_MODE`.
        """
        thread_id = ctx.session_id
        conninfo = self._conninfo()
        store = "postgres" if conninfo else "in-memory"
        _engine_logger.info(
            "checkpoint store=%s thread=%s flush=%s", store, thread_id, _CHECKPOINT_FLUSH_MODE
        )
        async with open_checkpointer(conninfo) as saver:
            graph = compiled.builder.compile(checkpointer=saver)
            return await self._invoke_and_finalize(
                graph,
                {"messages": compiled.initial_messages(text)},
                thread_id,
                compiled.model_id,
                durable=compiled.durability == "checkpoint",
            )

    async def resume(
        self,
        compiled: _CompiledAgent,
        token: ResumeToken,
        ctx: RunContext,
        *,
        resume_value: Any = None,
    ) -> Result:
        """Continue a durable run from a ``ResumeToken``, holding the single-owner thread lock.

        Rejects a token minted by another engine, then — under the single-owner
        :class:`~agentship.thread_lock.ThreadLock` for ``(tenant, thread)`` so a replay or a second
        worker cannot double-execute — recompiles the graph with a fresh checkpointer and continues.
        For a plain crash-resume it invokes with ``None`` (LangGraph resumes from the last
        checkpoint; completed nodes are not re-run); for a HITL interrupt it invokes with
        ``Command(resume=resume_value)`` so the human's decision flows back into the paused
        ``interrupt()``. A missing/stale checkpoint surfaces as
        :class:`~agentship.errors.ResumeError`.
        """
        if token.engine != self.name:
            raise CapabilityError(
                f"resume token was minted by engine {token.engine!r} but this is "
                f"engine {self.name!r} — a token can only be resumed on the engine that minted it"
            )
        thread_id = token.blob.get("thread_id")
        if not thread_id:
            raise ResumeError("resume token carries no thread_id — it cannot be resumed")
        was_interrupted = token.blob.get("interrupt", False)
        _engine_logger.info(
            "resume thread=%s interrupted=%s resume_value=%s",
            thread_id,
            was_interrupted,
            repr(resume_value) if resume_value is not None else "None (crash-resume)",
        )
        conninfo = self._conninfo()
        cfg = self._thread_config(thread_id)
        invoke_input = Command(resume=resume_value) if resume_value is not None else None
        async with resolve_thread_lock(ctx.caller.tenant_id, thread_id, conninfo=conninfo):
            async with open_checkpointer(conninfo) as saver:
                graph = compiled.builder.compile(checkpointer=saver)
                existing = await graph.aget_state(cfg)
                if existing is None or existing.created_at is None:
                    raise ResumeError(
                        f"no checkpoint found for thread {thread_id!r} — the run cannot be "
                        f"resumed (it may have been compacted or never started)"
                    )
                # Establish the run context so replayed nodes/tools can read it (idempotency
                # keys, dispatch identity) — resume is entered directly, not via RunnableAgent.run.
                from agentship.context import current_run

                token_ctx = current_run.set(ctx)
                try:
                    return await self._invoke_and_finalize(
                        graph, invoke_input, thread_id, compiled.model_id
                    )
                finally:
                    current_run.reset(token_ctx)

    async def stream(
        self, compiled: _CompiledAgent, text: str, ctx: RunContext
    ) -> AsyncIterator[Event]:
        """Stream the model's answer as token ``content`` chunks, then a terminal ``done``.

        Uses LangGraph's ``stream_mode="messages"`` and keeps only the model's own
        :class:`AIMessageChunk` tokens (never the replayed input messages), each
        emitted as a ``content`` event. A single ``done`` event terminates the
        stream once the graph completes. A provider/credential failure raised while
        streaming is turned into an actionable
        :class:`~agentship.errors.ModelError` via
        :func:`agentship_langgraph.models.map_model_error`, chaining the original cause.

        **No-silent-empty guarantee.** A model that does not stream tokens (e.g. a
        provider/model that ignores ``streaming=True``) surfaces its reply as a
        single whole :class:`~langchain_core.messages.AIMessage`, which is *not* an
        ``AIMessageChunk`` and so passes the token filter untouched. If the whole
        stream produced no chunk content, we fall back to emitting that final
        message's content as one ``content`` event before ``done`` — so ``--stream``
        never silently yields nothing. When real chunks did arrive, no fallback is
        emitted (no double-emit): the token stream is authoritative.
        """
        # Two modes, because they carry different things: "messages" gives the model's
        # token stream, "updates" gives each node's state update — which is the only place
        # the tool node's ToolMessage appears. Listening on messages alone is why tool
        # results were invisible to a streaming client.
        stream = compiled.graph.astream(
            {"messages": compiled.initial_messages(text)},
            stream_mode=["messages", "updates"],
            config={"callbacks": _run_callbacks()},
        )
        streamed_content = False
        last_full_content: Any = None
        try:
            async for mode, payload in stream:
                if mode == "updates":
                    # Each node's completed state update. Tool events come from here rather
                    # than from the token stream because a streaming chunk carries only a
                    # PARTIAL tool call — the name in one chunk, the arguments dribbling in
                    # over later ones — which emitted a duplicate with an empty name.
                    for update in (payload or {}).values():
                        for produced in (update or {}).get("messages", []) or []:
                            if isinstance(produced, ToolMessage):
                                yield Event(
                                    type="tool_result",
                                    data={"tool": produced.name, "result": produced.content},
                                )
                            for call in getattr(produced, "tool_calls", None) or []:
                                yield Event(
                                    type="tool_call",
                                    data={
                                        "tool": call.get("name"),
                                        "args": call.get("args") or {},
                                    },
                                )
                    continue
                message, metadata = payload
                # A supervisor's classify node emits the routing label, which is bookkeeping,
                # not the reply. Streaming it prefixed the answer with "web_researcher".
                if (metadata or {}).get("langgraph_node") in compiled.internal_nodes:
                    continue
                if isinstance(message, AIMessageChunk):
                    if message.content or (message.additional_kwargs or {}).get(
                        "reasoning_content"
                    ):
                        # A reasoning model's content is a list of blocks, not a string.
                        # Emitting it whole put the model's private thinking into the answer.
                        reasoning, answer = split_reasoning(message)
                        if reasoning:
                            yield Event(type="reasoning", data=reasoning)
                        if answer:
                            streamed_content = True
                            yield Event(type="content", data=answer)
                elif isinstance(message, AIMessage) and message.content:
                    # Same split for a whole (non-chunk) reply, so a model that does not
                    # stream still keeps its thinking out of the fallback answer below.
                    _whole_reasoning, _whole_answer = split_reasoning(message)
                    if _whole_reasoning:
                        # A model that returns its thinking in one whole message rather than
                        # in chunks still has to surface it, or reasoning is visible only
                        # from providers that happen to stream.
                        yield Event(type="reasoning", data=_whole_reasoning)
                    if _whole_answer:
                        last_full_content = _whole_answer
                        continue
                    # A whole (non-chunk) model reply — remember its content as the
                    # fallback answer in case no chunk content ever arrives. Gated on
                    # AIMessage so the replayed Human/System input is never mistaken
                    # for the answer.
                    last_full_content = message.content
        except Exception as exc:
            # Only a genuine provider failure becomes a ModelError. Anything else — a missing
            # checkpoint table, a tool bug — keeps its own type so the message points at the
            # system that actually failed.
            if models.is_provider_error(exc):
                raise models.map_model_error(compiled.model_id, exc) from exc
            raise
        if not streamed_content and last_full_content:
            yield Event(type="content", data=last_full_content)
        yield Event(type="done")
