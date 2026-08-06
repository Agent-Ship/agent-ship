"""The LangGraph engine — AgentShip's default single-agent, real-model engine.

:class:`LangGraphEngine` compiles an :class:`~agentship.spec.AgentSpec` into a
minimal LangGraph graph: one node that sends ``[system prompt, user input]`` to a
LiteLLM-backed chat model and returns its answer. It declares ``streaming`` only —
``tool_calling``/``structured_output``/``multi_agent`` arrive in later phases, and
the kernel's capability gate rejects specs that ask for them until then.

**Model injection.** The chat model is resolved through
:func:`agentship.models.resolve_model` (referenced via the module, not imported by
name), so offline tests monkeypatch that function to inject a fake model and run
with no network. Production runs resolve a real ``ChatLiteLLM``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, ClassVar

from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from ... import models
from ..base import Engine, EngineCapabilities, Event, Result

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

    from ...context import RunContext
    from ...spec import AgentSpec


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

    Declares ``streaming`` only for now; ``tool_calling``/``structured_output``/
    ``multi_agent`` stay ``False`` so the capability gate honestly rejects those
    (they arrive in later phases). The model is resolved via
    :func:`agentship.models.resolve_model`, which offline tests monkeypatch to
    inject a fake chat model.
    """

    name: ClassVar[str] = "langgraph"
    capabilities: ClassVar[EngineCapabilities] = EngineCapabilities(streaming=True)

    def build(self, spec: AgentSpec) -> _CompiledAgent:
        """Compile the spec into a single-node graph over the resolved chat model.

        Resolves the chat model from ``spec.model`` (raising
        :class:`~agentship.errors.SpecError` on an empty model via
        :func:`~agentship.models.resolve_model`), then wires one ``agent`` node
        that invokes it. Returns the compiled artifact the kernel hands back to
        ``run``/``stream``.
        """
        model = models.resolve_model(spec.model or "")
        graph = self._build_graph(model)
        return _CompiledAgent(graph, spec.prompt, spec.model or "")

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
        :func:`agentship.models.map_model_error` (which names the missing env var);
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
        :func:`agentship.models.map_model_error`, chaining the original cause.
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
