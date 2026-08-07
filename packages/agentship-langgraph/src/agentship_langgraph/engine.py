"""The LangGraph engine — AgentShip's default single-agent, real-model engine.

:class:`LangGraphEngine` compiles an :class:`~agentship.spec.AgentSpec` into a
minimal LangGraph graph: one node that sends ``[system prompt, user input]`` to a
LiteLLM-backed chat model and returns its answer. It honestly declares only what
this minimal graph delivers today — ``streaming`` and the LiteLLM ``providers``
it reaches. **Everything else stays off.** The graph does not yet bind tools
(``tool_calling`` — phase 03), validate a structured target
(``structured_output`` — phase 04), coordinate members (``multi_agent``), or
checkpoint (``durability``); declaring those before they are built would be the
exact over-claim the conformance matrix exists to catch (*declare, don't fake*),
so they remain off until their phases land and their conformance cells pass.

**Model injection.** The chat model is resolved through
:func:`agentship_langgraph.models.resolve_model` (referenced via the module, not imported by
name), so offline tests monkeypatch that function to inject a fake model and run
with no network. Production runs resolve a real ``ChatLiteLLM``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, ClassVar

from agentship.engines.base import Engine, EngineCapabilities, Event, Result
from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from typing_extensions import TypedDict

from . import models
from .agent import LangGraphAgent
from .templates import resolve_template

if TYPE_CHECKING:
    from agentship.context import RunContext
    from agentship.spec import AgentSpec
    from langchain_core.language_models.chat_models import BaseChatModel


class _AgentState(TypedDict):
    """The graph's state: the running list of chat messages for one turn."""

    messages: list


class _CompiledAgent:
    """A built LangGraph agent: the compiled graph plus its system prompt.

    Held opaque by the kernel; only this engine's ``run``/``stream`` read it. The
    system prompt is stored alongside the graph so each turn can seed the message
    list without re-reading the spec.
    """

    def __init__(self, graph: Any, system_prompt: str | None, model_id: str) -> None:
        """Bind the compiled graph, the optional system prompt, and the model id.

        ``model_id`` is the LiteLLM model string (e.g. ``"openai/gpt-4o-mini"``);
        it is kept so a provider failure at run time can be turned into an
        actionable :class:`~agentship.errors.ModelError` that names the credential.
        """
        self.graph = graph
        self.system_prompt = system_prompt
        self.model_id = model_id

    def initial_messages(self, text: str) -> list:
        """Build the seed message list for one turn: optional system + user input."""
        messages: list = []
        if self.system_prompt:
            messages.append(SystemMessage(content=self.system_prompt))
        messages.append(HumanMessage(content=text))
        return messages


class LangGraphEngine(Engine):
    """AgentShip's default engine — a single-agent LangGraph graph over LiteLLM.

    Declares only what this minimal graph honestly delivers today: ``streaming``
    and the LiteLLM ``providers`` it can reach. Tool calling, structured output,
    multi-agent coordination, and durability are **not** implemented by this
    single-node graph yet, so they stay off (``tool_calling=False``,
    ``structured_output="none"``, ``multi_agent=False``, ``durability="none"``) —
    the capability gate therefore rejects any spec that asks for them until their
    phases build them and their conformance cells go green. The model is resolved
    via :func:`agentship_langgraph.models.resolve_model`, which offline tests
    monkeypatch to inject a fake chat model.
    """

    name: ClassVar[str] = "langgraph"
    capabilities: ClassVar[EngineCapabilities] = EngineCapabilities(
        providers={"openai", "anthropic", "gemini", "ollama"},
        streaming=True,
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
        if isinstance(authored, LangGraphAgent):
            graph = authored.build_graph(model, tools)
        else:
            template_body = resolve_template(spec)
            if template_body is not None:
                graph = template_body(model, tools)
            else:
                graph = self._build_graph(model)
        compiled = graph if isinstance(graph, CompiledStateGraph) else graph.compile()
        return _CompiledAgent(compiled, spec.prompt, spec.model or "")

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
        """Resolve the tools to hand ``build_graph`` — empty until Phase 03.

        ``spec.tools`` is parsed and validated by the spec today, but actual MCP /
        python tool *execution* is a Phase 03 deliverable. Until then the engine
        honestly hands ``build_graph`` an empty tool list (declare, don't fake)
        rather than pretending to wire tools it cannot yet execute.
        """
        return []

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
        """
        try:
            state = await compiled.graph.ainvoke(
                {"messages": compiled.initial_messages(text)}
            )
        except Exception as exc:
            raise models.map_model_error(compiled.model_id, exc) from exc
        answer = state["messages"][-1].content
        return Result(output=answer)

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
        """
        stream = compiled.graph.astream(
            {"messages": compiled.initial_messages(text)},
            stream_mode="messages",
        )
        try:
            async for message, _metadata in stream:
                if isinstance(message, AIMessageChunk) and message.content:
                    yield Event(type="content", data=message.content)
        except Exception as exc:
            raise models.map_model_error(compiled.model_id, exc) from exc
        yield Event(type="done")
